"""Record Manager: maintains execution record, merges with ipynb, writes .py and JSON."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import nbformat

from .file_lock import notebook_lock

logger = logging.getLogger(__name__)


def _wrap_error_code(code: str) -> str:
    """Wrap code in try/except for error cells."""
    lines = code.rstrip().split("\n")
    indented = "\n".join("    " + line for line in lines)
    return f"try:\n{indented}\nexcept Exception as e:\n    print(e)"


def _is_empty_code(code: str) -> bool:
    """Return True if code is empty or only whitespace."""
    return not code or not code.strip()


def _normalize_code_for_match(code: str) -> str:
    """Normalize code for deduplication (same cell content in different sources)."""
    return (code or "").rstrip()


def _is_ide_injected_code(code: str, cell_id: str | None = None) -> bool:
    """Return True if code appears to be IDE-injected (VS Code/Cursor setup), not user content."""
    if not code or not code.strip():
        return True
    # VS Code/Cursor injects setup code with these patterns
    markers = (
        "_VSCODE_",
        "__VSCODE_",
        "__vsc_ipynb_file__",
        "%config Completer.use_jedi",
        "__jupyter_exec_background__",  # VS Code autocomplete background execution
    )
    return any(m in code for m in markers)


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes (e.g. [31m for red)."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _format_error_comment(error_info: dict[str, Any]) -> str:
    """Format error info as comment lines."""
    lines = []
    if error_info.get("ename") and error_info.get("evalue"):
        lines.append(f"# {error_info['ename']}: {error_info['evalue']}")
    if error_info.get("traceback"):
        for tb_line in error_info["traceback"]:
            lines.append(f"# {_strip_ansi(tb_line)}")
    return "\n".join(lines) if lines else ""


class RecordManager:
    """Manages execution record, merges kernel results with ipynb, writes output files."""

    def __init__(self, notebook_path: str | Path | None = None):
        self.notebook_path = Path(notebook_path) if notebook_path else None
        self._execution_records: list[dict[str, Any]] = []  # each execution in order (repeats kept)
        self._execution_log: list[dict[str, str]] = []  # ordered execution timeline

    def set_notebook_path(self, path: str | Path) -> None:
        """Set the notebook path (from magic or env)."""
        self.notebook_path = Path(path).resolve()

    def load_from_record_file(self) -> bool:
        """Load execution data from existing record JSON. Returns True if loaded."""
        if not self.notebook_path:
            return False
        json_path = self.notebook_path.parent / f"{self.notebook_path.stem}_record.json"
        if not json_path.exists():
            return False
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        cells = data.get("cells", [])
        self._execution_log = data.get("execution_log", [])
        # Rebuild _execution_records from cells that have exec_order (were executed)
        # Sort by exec_order in case JSON was edited or cells reordered
        self._execution_records = []
        executed = [c for c in cells if c.get("exec_order") is not None]
        for c in sorted(executed, key=lambda x: x["exec_order"]):
            rec = {
                "id": c.get("id", c.get("cell_id")),
                "cell_id": c.get("id", c.get("cell_id")),
                "code": c.get("code", ""),
                "status": c.get("status", "ok"),
                "editable": False,
                "exec_order": c["exec_order"],
            }
            if c.get("error_info"):
                rec["error_info"] = c["error_info"]
                rec["original_code"] = c.get("original_code", c.get("code", ""))
            if c.get("output") is not None:
                rec["output"] = c["output"]
            if c.get("execution_count") is not None:
                rec["execution_count"] = c["execution_count"]
            self._execution_records.append(rec)
        return len(self._execution_records) > 0

    def add_execution(
        self,
        cell_id: str,
        code: str,
        status: str,
        error_info: dict[str, Any] | None = None,
        output: list[dict[str, Any]] | str | None = None,
        execution_count: int | None = None,
    ) -> None:
        """Add an execution result from kernel."""
        data: dict[str, Any] = {
            "id": cell_id,
            "cell_id": cell_id,
            "code": code,
            "status": status,
            "editable": False,
        }
        if status == "error" and error_info:
            data["error_info"] = error_info
            data["original_code"] = code  # for code-based matching
            data["code"] = _wrap_error_code(code)
        if output:
            data["output"] = output
        if execution_count is not None:
            data["execution_count"] = execution_count
        data["exec_order"] = len(self._execution_records) + 1
        self._execution_records.append(data)
        self._execution_log.append({"cell_id": cell_id, "status": status})

    def get_output(
        self,
        cell_id: str,
        execution_count: int | None = None,
    ) -> list[dict[str, Any]] | None:
        """Get output for a cell by cell_id and optional execution_count.

        Returns a list of output message dicts (stream, execute_result, display_data, error).
        If execution_count is specified, returns the output for that exact execution.
        Otherwise returns the most recent execution output for the cell.
        """
        matches = [r for r in self._execution_records if r["cell_id"] == cell_id]
        if not matches:
            return None
        if execution_count is not None:
            for r in matches:
                if r.get("execution_count") == execution_count:
                    return r.get("output")
            return None
        # Most recent: last match in _execution_records (execution order)
        return matches[-1].get("output")

    @staticmethod
    def get_output_from_record_file(
        notebook_path: str | Path,
        cell_id: str,
        execution_count: int | None = None,
    ) -> list[dict[str, Any]] | None:
        """Load output from record JSON file (for CLI use without kernel).

        Returns a list of output message dicts, or None if not found.
        """
        path = Path(notebook_path).resolve()
        json_path = path.parent / f"{path.stem}_record.json"
        if not json_path.exists():
            return None
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        cells = data.get("cells", [])
        matches = [c for c in cells if c.get("id") == cell_id or c.get("cell_id") == cell_id]
        if not matches:
            return None
        if execution_count is not None:
            for c in matches:
                if c.get("execution_count") == execution_count:
                    return c.get("output")
            return None
        return matches[-1].get("output")

    @staticmethod
    def update_cell_output(
        notebook_path: str | Path,
        cell_id: str,
        output: list[dict[str, Any]],
        execution_count: int | None = None,
    ) -> bool:
        """Update output for the most recent execution of a cell (e.g. from CLI execute).

        Kernel may not capture stream output (sent via session.send); CLI has it.
        Returns True if updated.
        """
        path = Path(notebook_path).resolve()
        json_path = path.parent / f"{path.stem}_record.json"
        if not json_path.exists():
            return False
        with notebook_lock(path):
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                return False
            cells = data.get("cells", [])
            for i in range(len(cells) - 1, -1, -1):
                c = cells[i]
                if (c.get("id") == cell_id or c.get("cell_id") == cell_id) and c.get(
                    "exec_order"
                ):
                    if execution_count is not None and c.get("execution_count") != execution_count:
                        continue
                    c["output"] = output
                    json_path.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    return True
        return False

    def _get_ipynb_cells(self) -> list[dict[str, Any]]:
        """Read code and markdown cells from ipynb in order, including empty cells for layout.

        Uses NO_CONVERT to preserve cell ids from file - nbformat convert would
        generate new random ids for old-format notebooks, causing mismatch with
        frontend's cellId.
        """
        if not self.notebook_path or not self.notebook_path.exists():
            return []
        try:
            try:
                nb = nbformat.read(self.notebook_path, as_version=nbformat.NO_CONVERT)
            except Exception:
                nb = nbformat.read(self.notebook_path, as_version=4)
            result = []
            cell_idx = 0
            for cell in nb.cells:
                cell_type = cell.get("cell_type", "code")
                if cell_type not in ("code", "markdown"):
                    continue
                source = cell.get("source", "")
                if isinstance(source, list):
                    source = "".join(source)
                cell_id = cell.get("id")
                if not cell_id:
                    cell_id = f"cell_{cell_idx}"
                cell_idx += 1
                result.append({
                    "id": cell_id,
                    "code": source,
                    "cell_type": cell_type,
                })
            return result
        except Exception:
            return []

    def _build_cells_list(self) -> list[dict[str, Any]]:
        """Build cells list: executed blocks in order (with repeats), then pending from ipynb.

        Normalizes cell_id: when a record's code matches an ipynb cell, use that cell's id
        (fixes VS Code URI vs nbformat id mismatch). Multiple executions of same cell are kept.
        Markdown cells are always included inline at their notebook position.
        """
        ipynb_cells = self._get_ipynb_cells()
        nb_id_by_code: dict[str, str] = {
            _normalize_code_for_match(c.get("code", "")): c["id"]
            for c in ipynb_cells
            if c.get("cell_type") == "code" and c.get("id")
        }

        executed_blocks: list[dict[str, Any]] = []
        for r in self._execution_records:
            raw_code = r.get("original_code", r.get("code", ""))
            if _is_empty_code(raw_code):
                continue
            if _is_ide_injected_code(raw_code, r.get("cell_id")):
                continue
            block = r.copy()
            # Normalize cell_id: use nbformat id when code matches (fixes VS Code vs nbformat mismatch)
            norm = _normalize_code_for_match(raw_code)
            if norm in nb_id_by_code:
                block["id"] = nb_id_by_code[norm]
                block["cell_id"] = nb_id_by_code[norm]
            executed_blocks.append(block)

        executed_cell_ids = {c.get("id") or c.get("cell_id") for c in executed_blocks}
        executed_code_set = {
            _normalize_code_for_match(r.get("original_code", r.get("code", "")))
            for r in executed_blocks
        }

        pending_blocks: list[dict[str, Any]] = []
        for nb_cell in ipynb_cells:
            if nb_cell.get("cell_type") == "markdown":
                pending_blocks.append({
                    "id": nb_cell["id"],
                    "code": nb_cell["code"],
                    "cell_type": "markdown",
                    "status": "markdown",
                    "editable": True,
                })
                continue
            if nb_cell["id"] in executed_cell_ids:
                continue
            if _normalize_code_for_match(nb_cell["code"]) in executed_code_set:
                continue
            status = "empty" if _is_empty_code(nb_cell["code"]) else "pending"
            pending_blocks.append({
                "id": nb_cell["id"],
                "code": nb_cell["code"],
                "cell_type": "code",
                "status": status,
                "editable": True,
            })

        return executed_blocks + pending_blocks

    def write_record(self) -> None:
        """Write record .py and .json files."""
        if not self.notebook_path:
            return
        stem = self.notebook_path.stem
        base_dir = self.notebook_path.parent
        py_path = base_dir / f"{stem}_record.py"
        json_path = base_dir / f"{stem}_record.json"

        cells = self._build_cells_list()
        # Build execution_log from cells (uses normalized cell_ids; raw _execution_log may have vscode URIs)
        execution_log_filtered = [
            {"cell_id": c.get("id") or c.get("cell_id"), "status": c.get("status", "ok")}
            for c in cells
            if c.get("exec_order") is not None
        ]

        py_lines: list[str] = []
        for c in cells:
            cell_id = c.get("id") or c.get("cell_id", "unknown")
            cell_type = c.get("cell_type", "code")
            exec_order = c.get("exec_order")

            if cell_type == "markdown":
                py_lines.append(f"# %% [markdown] {cell_id}")
                py_lines.append("# [markdown - editable]")
                for line in (c.get("code", "") or "").split("\n"):
                    py_lines.append(f"# {line}" if line else "#")
                py_lines.append("")
                continue

            if exec_order is not None:
                py_lines.append(f"# %% {cell_id}  # exec_order: {exec_order}")
            else:
                py_lines.append(f"# %% {cell_id}")
            if c["status"] == "ok":
                py_lines.append("# [executed - do not modify]")
            elif c["status"] == "error":
                py_lines.append("# [error - do not modify]")
                if c.get("error_info"):
                    for line in _format_error_comment(c["error_info"]).split("\n"):
                        py_lines.append(line)
            elif c["status"] == "empty":
                py_lines.append("# [empty - editable]")
            else:
                py_lines.append("# [pending - editable]")
            py_lines.append(c.get("code", ""))
            py_lines.append("")

        with notebook_lock(self.notebook_path):
            py_path.write_text("\n".join(py_lines).rstrip() + "\n", encoding="utf-8")

            payload = {
                "notebook_path": str(self.notebook_path.resolve()),
                "execution_log": execution_log_filtered,
                "cells": cells,
            }
            json_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

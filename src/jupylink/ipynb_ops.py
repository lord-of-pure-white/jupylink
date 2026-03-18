"""Operations on ipynb files: read, write cell, create, delete."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import nbformat
from nbformat.notebooknode import from_dict
from nbformat.v4 import nbbase

from .file_lock import notebook_lock
from .notify_ide import request_notebook_refresh

logger = logging.getLogger(__name__)


def _normalize_source(source: str | list[str]) -> str:
    """Normalize cell source to string."""
    if isinstance(source, list):
        return "".join(source)
    return source or ""


def _read_nb(path: Path) -> nbformat.NotebookNode:
    """Read notebook, preferring NO_CONVERT to preserve cell ids."""
    try:
        return nbformat.read(path, as_version=nbformat.NO_CONVERT)
    except Exception:
        return nbformat.read(path, as_version=4)


def _to_source(value: str, existing_source: str | list[str] | None = None) -> str | list[str]:
    """Convert content to notebook source format, preserving the existing format.

    If the cell previously stored source as a string, keeps string format.
    If it stored as a list of lines, converts to list format.
    Defaults to list format for new cells (VS Code convention).
    """
    if existing_source is not None and isinstance(existing_source, str):
        return value or ""
    text = value or ""
    return text.splitlines(keepends=True) if text else []


def write_cell(notebook_path: str | Path, cell_id: str, content: str) -> bool:
    """Write content to the specified cell by cell_id.

    Returns True on success, False if cell not found.
    """
    path = Path(notebook_path).resolve()
    if not path.exists():
        return False
    with notebook_lock(path):
        nb = _read_nb(path)
        for cell in nb.cells:
            if cell.get("id") == cell_id:
                cell["source"] = _to_source(content, cell.get("source"))
                nbformat.write(nb, path)
                request_notebook_refresh(path)
                return True
    return False


def create_cell(
    notebook_path: str | Path,
    cell_type: Literal["code", "markdown", "raw"] = "code",
    index: int | None = None,
    source: str = "",
) -> str | None:
    """Create a new cell in the notebook.

    Returns the new cell's id on success, None on failure.
    """
    path = Path(notebook_path).resolve()
    if not path.exists():
        return None
    with notebook_lock(path):
        nb = _read_nb(path)

        if cell_type == "code":
            cell = nbbase.new_code_cell(source=source)
        elif cell_type == "markdown":
            cell = nbbase.new_markdown_cell(source=source)
        elif cell_type == "raw":
            cell = nbbase.new_raw_cell(source=source)
        else:
            return None

        cell_id = cell.get("id")
        if index is not None:
            idx = max(0, min(index, len(nb.cells)))
            nb.cells.insert(idx, cell)
        else:
            nb.cells.append(cell)

        nbformat.write(nb, path)
        request_notebook_refresh(path)
        return cell_id


def delete_cell(notebook_path: str | Path, cell_id: str) -> bool:
    """Delete the cell with the given cell_id.

    Returns True on success, False if cell not found.
    """
    path = Path(notebook_path).resolve()
    if not path.exists():
        return False
    with notebook_lock(path):
        nb = _read_nb(path)
        for i, cell in enumerate(nb.cells):
            if cell.get("id") == cell_id:
                nb.cells.pop(i)
                nbformat.write(nb, path)
                request_notebook_refresh(path)
                return True
    return False


def get_cell_source(notebook_path: str | Path, cell_id: str) -> str | None:
    """Get the source of a cell by cell_id. Returns None if not found."""
    path = Path(notebook_path).resolve()
    if not path.exists():
        return None
    try:
        nb = nbformat.read(path, as_version=nbformat.NO_CONVERT)
    except Exception:
        nb = nbformat.read(path, as_version=4)

    for cell in nb.cells:
        if cell.get("id") == cell_id:
            return _normalize_source(cell.get("source", ""))
    return None


def _captured_to_nbformat_output(item: dict) -> dict:
    """Convert executor captured output to nbformat cell output format."""
    msg_type = item.get("msg_type", "")
    if msg_type == "stream":
        text = item.get("text", "")
        return {
            "output_type": "stream",
            "name": item.get("name", "stdout"),
            "text": [text] if isinstance(text, str) else text,
        }
    if msg_type == "error":
        return {
            "output_type": "error",
            "ename": item.get("ename", ""),
            "evalue": item.get("evalue", ""),
            "traceback": item.get("traceback", []),
        }
    if msg_type in ("execute_result", "display_data"):
        out: dict = {
            "output_type": "execute_result" if msg_type == "execute_result" else "display_data",
            "data": item.get("data", {}),
            "metadata": item.get("metadata", {}),
        }
        if item.get("execution_count") is not None:
            out["execution_count"] = item["execution_count"]
        return out
    return {}


def update_cell_output(
    notebook_path: str | Path,
    cell_id: str,
    output: list[dict],
    execution_count: int | None = None,
) -> bool:
    """Write execution output to the ipynb cell so IDE displays it.

    Returns True on success. Used when CLI/MCP executes a cell.
    """
    path = Path(notebook_path).resolve()
    if not path.exists() or path.suffix != ".ipynb":
        return False
    with notebook_lock(path):
        nb = _read_nb(path)
        for cell in nb.cells:
            if cell.get("id") == cell_id and cell.get("cell_type") == "code":
                nb_outputs = [_captured_to_nbformat_output(o) for o in output if o.get("msg_type")]
                cell["outputs"] = [from_dict(o) for o in nb_outputs if o]
                if execution_count is not None:
                    cell["execution_count"] = execution_count
                nbformat.write(nb, path)
                request_notebook_refresh(path)
                return True
    return False


def list_cells(notebook_path: str | Path) -> list[dict]:
    """List all cells with id, cell_type, source preview, and empty flag."""
    path = Path(notebook_path).resolve()
    if not path.exists():
        return []
    try:
        nb = nbformat.read(path, as_version=nbformat.NO_CONVERT)
    except Exception:
        nb = nbformat.read(path, as_version=4)

    result = []
    for i, cell in enumerate(nb.cells):
        source = _normalize_source(cell.get("source", ""))
        cell_id = cell.get("id") or f"cell_{i}"
        result.append({
            "index": i,
            "id": cell_id,
            "cell_type": cell.get("cell_type", "code"),
            "source": source,
            "source_preview": (source[:80] + "..." if len(source) > 80 else source),
            "empty": not source or not source.strip(),
        })
    return result

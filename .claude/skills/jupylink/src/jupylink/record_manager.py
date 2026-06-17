"""Record Manager: maintains execution record, merges with ipynb, writes .py, JSON and CSV."""

import base64
import csv
import json
import logging
import os
import re

import nbformat

from .file_lock import notebook_lock
from .kernel_registry import resolve_notebook_filesystem_path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers (no pathlib / f-strings / annotations — works on Py2 and Py3)
# ---------------------------------------------------------------------------
def _path_stem(p):
    return os.path.splitext(os.path.basename(str(p)))[0]


def _path_parent(p):
    return os.path.dirname(str(p))


def _path_is_file(p):
    return os.path.isfile(str(p))


def _path_exists(p):
    return os.path.exists(str(p))


def _path_mtime(p):
    return os.stat(str(p)).st_mtime


try:
    from io import open as _io_open  # Py3 compat shim
except ImportError:
    _io_open = open


def _read_json(p):
    with _io_open(str(p), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(p, data):
    with _io_open(str(p), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def _read_text(p):
    with _io_open(str(p), "r", encoding="utf-8") as fh:
        return fh.read()


def _write_text(p, s):
    with _io_open(str(p), "w", encoding="utf-8") as fh:
        fh.write(s)


def _wrap_error_code(code):
    lines = code.rstrip().split("\n")
    indented = "\n".join("    " + line for line in lines)
    return "try:\n{}\nexcept Exception as e:\n    print(e)".format(indented)


def _is_empty_code(code):
    return not code or not code.strip()


def _normalize_code_for_match(code):
    return (code or "").rstrip()


def _is_ide_injected_code(code, cell_id=None):
    if not code or not code.strip():
        return True
    markers = (
        "_VSCODE_",
        "__VSCODE_",
        "__vsc_ipynb_file__",
        "%config Completer.use_jedi",
        "__jupyter_exec_background__",
    )
    return any(m in code for m in markers)


def _strip_ansi(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _format_error_comment(error_info):
    lines = []
    if error_info.get("ename") and error_info.get("evalue"):
        lines.append("# {}: {}".format(error_info["ename"], error_info["evalue"]))
    if error_info.get("traceback"):
        for tb_line in error_info["traceback"]:
            lines.append("# {}".format(_strip_ansi(tb_line)))
    return "\n".join(lines) if lines else ""


# ---------------------------------------------------------------------------
# Rich output extraction (images, HTML → files)
# ---------------------------------------------------------------------------
_IMAGE_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
    "image/webp": ".webp",
}


def extract_rich_output(captured, notebook_path, cell_id=None):
    """Save images / HTML from captured output to files next to the notebook.

    Modifies *captured* in place: replaces base64 blobs with ``rich_paths``
    references and strips binary mime types from ``data``, keeping only
    ``text/plain`` for agent readability.
    """
    if not notebook_path:
        return
    stem = os.path.splitext(os.path.basename(str(notebook_path)))[0]
    base_dir = os.path.dirname(str(notebook_path))
    short_id = (cell_id or "unknown")[:12]
    file_index = 0

    for item in captured:
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        if not data:
            continue
        rich_paths = []
        for mime, ext in _IMAGE_MIME.items():
            payload = data.get(mime)
            if not payload:
                continue
            if isinstance(payload, (list, tuple)):
                payload = "".join(payload)
            if not isinstance(payload, (str, bytes)):
                continue
            if isinstance(payload, str):
                try:
                    raw = base64.b64decode(payload)
                except Exception:
                    continue
            else:
                raw = payload

            fname = "{}_{}_{}{}".format(stem, short_id, file_index, ext)
            fpath = os.path.join(base_dir, fname)
            with open(fpath, "wb") as fh:
                fh.write(raw)
            rich_paths.append(fpath)
            file_index += 1

        html = data.get("text/html")
        if html:
            fname = "{}_{}_{}.html".format(stem, short_id, file_index)
            fpath = os.path.join(base_dir, fname)
            with open(fpath, "w", encoding="utf-8") as fh:
                fh.write(html if isinstance(html, str) else "".join(html))
            rich_paths.append(fpath)
            file_index += 1

        if rich_paths:
            slim_data = {}
            if data.get("text/plain"):
                slim_data["text/plain"] = data["text/plain"]
            item["data"] = slim_data
            item["rich_paths"] = rich_paths


# ---------------------------------------------------------------------------
class RecordManager:
    """Manages execution record, merges kernel results with ipynb, writes output files."""

    def __init__(self, notebook_path=None):
        self.notebook_path = None
        if notebook_path:
            try:
                self.notebook_path = str(resolve_notebook_filesystem_path(notebook_path))
            except (OSError, ValueError) as e:
                logger.warning("Ignoring invalid initial notebook path %r: %s", notebook_path, e)
        self._execution_records = []
        self._execution_log = []
        self._last_ipynb_mtime = 0.0

    def set_notebook_path(self, path):
        try:
            self.notebook_path = str(resolve_notebook_filesystem_path(path))
            self._track_mtime()
        except (OSError, ValueError) as e:
            logger.warning("Ignoring invalid notebook path %r: %s", path, e)

    def _track_mtime(self):
        if self.notebook_path and _path_is_file(self.notebook_path):
            self._last_ipynb_mtime = _path_mtime(self.notebook_path)

    def sync_if_ipynb_changed(self):
        if not self.notebook_path or not _path_is_file(self.notebook_path):
            return False
        try:
            current = _path_mtime(self.notebook_path)
        except OSError:
            return False
        if abs(current - self._last_ipynb_mtime) < 0.001:
            return False
        self.merge_ipynb_execution_state()
        self.write_record()
        self._last_ipynb_mtime = current
        return True

    def _sync_notebook_path_for_fs(self):
        if not self.notebook_path:
            return
        try:
            self.notebook_path = str(resolve_notebook_filesystem_path(self.notebook_path))
        except (OSError, ValueError) as e:
            logger.warning("Could not normalize notebook path %r: %s", self.notebook_path, e)

    def load_from_record_file(self):
        if not self.notebook_path:
            return False
        self._sync_notebook_path_for_fs()
        stem = _path_stem(self.notebook_path)
        json_path = os.path.join(_path_parent(self.notebook_path), "{}_record.json".format(stem))
        if not _path_exists(json_path):
            return False
        try:
            data = _read_json(json_path)
        except Exception:
            return False
        cells = data.get("cells", [])
        self._execution_log = data.get("execution_log", [])
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

    def merge_ipynb_execution_state(self):
        if not self.notebook_path:
            return 0
        self._sync_notebook_path_for_fs()
        if not _path_exists(self.notebook_path):
            return 0
        try:
            nb = nbformat.read(str(self.notebook_path), as_version=nbformat.NO_CONVERT)
        except Exception:
            return 0
        recorded_ids = {r.get("cell_id") or r.get("id") for r in self._execution_records}
        merged = 0
        for cell in nb.cells:
            if cell.get("cell_type") != "code":
                continue
            cell_id = cell.get("id")
            if not cell_id or cell_id in recorded_ids:
                continue
            outputs = cell.get("outputs", [])
            exec_count = cell.get("execution_count")
            if not outputs and exec_count is None:
                continue
            source = cell.get("source", "")
            if isinstance(source, list):
                source = "".join(source)
            if _is_empty_code(source) or _is_ide_injected_code(source, cell_id):
                continue
            status = "error" if any(o.get("output_type") == "error" for o in outputs) else "ok"
            error_info = None
            output_list = None
            if outputs:
                output_list = []
                for o in outputs:
                    if o.get("output_type") == "stream":
                        output_list.append({
                            "msg_type": "stream",
                            "name": o.get("name", "stdout"),
                            "text": "".join(o.get("text", [])),
                        })
                    elif o.get("output_type") == "error":
                        output_list.append({
                            "msg_type": "error",
                            "ename": o.get("ename", ""),
                            "evalue": o.get("evalue", ""),
                            "traceback": o.get("traceback", []),
                        })
                    elif o.get("output_type") in ("execute_result", "display_data"):
                        output_list.append({
                            "msg_type": o["output_type"],
                            "data": o.get("data", {}),
                            "metadata": o.get("metadata", {}),
                        })
            if status == "error" and output_list:
                for o in output_list:
                    if o.get("msg_type") == "error":
                        error_info = {
                            "ename": o.get("ename", ""),
                            "evalue": o.get("evalue", ""),
                            "traceback": o.get("traceback", []),
                        }
                        break
            rec = {
                "id": cell_id,
                "cell_id": cell_id,
                "code": source,
                "status": status,
                "editable": False,
                "exec_order": len(self._execution_records) + 1,
            }
            if error_info:
                rec["error_info"] = error_info
                rec["original_code"] = source
                rec["code"] = _wrap_error_code(source)
            if output_list:
                rec["output"] = output_list
            if exec_count is not None:
                rec["execution_count"] = exec_count
            self._execution_records.append(rec)
            self._execution_log.append({"cell_id": cell_id, "status": status})
            recorded_ids.add(cell_id)
            merged += 1
        return merged

    def add_execution(self, cell_id, code, status, error_info=None, output=None, execution_count=None):
        data = {
            "id": cell_id,
            "cell_id": cell_id,
            "code": code,
            "status": status,
            "editable": False,
        }
        if status == "error" and error_info:
            data["error_info"] = error_info
            data["original_code"] = code
            data["code"] = _wrap_error_code(code)
        if output:
            data["output"] = output
        if execution_count is not None:
            data["execution_count"] = execution_count
        data["exec_order"] = len(self._execution_records) + 1
        self._execution_records.append(data)
        self._execution_log.append({"cell_id": cell_id, "status": status})

    def get_output(self, cell_id, execution_count=None):
        matches = [r for r in self._execution_records if r["cell_id"] == cell_id]
        if not matches:
            return None
        if execution_count is not None:
            for r in matches:
                if r.get("execution_count") == execution_count:
                    return r.get("output")
            return None
        return matches[-1].get("output")

    @staticmethod
    def _output_from_ipynb_cell(cell):
        outputs = cell.get("outputs", [])
        if not outputs:
            return None
        result = []
        for o in outputs:
            if o.get("output_type") == "stream":
                text = o.get("text", [])
                if isinstance(text, list):
                    text = "".join(text)
                result.append({
                    "msg_type": "stream",
                    "name": o.get("name", "stdout"),
                    "text": text,
                })
            elif o.get("output_type") == "error":
                result.append({
                    "msg_type": "error",
                    "ename": o.get("ename", ""),
                    "evalue": o.get("evalue", ""),
                    "traceback": o.get("traceback", []),
                })
            elif o.get("output_type") in ("execute_result", "display_data"):
                result.append({
                    "msg_type": o["output_type"],
                    "data": o.get("data", {}),
                    "metadata": o.get("metadata", {}),
                })
        return result if result else None

    @staticmethod
    def get_output_from_record_file(notebook_path, cell_id, execution_count=None):
        path = str(resolve_notebook_filesystem_path(notebook_path))
        stem = _path_stem(path)
        json_path = os.path.join(_path_parent(path), "{}_record.json".format(stem))
        if _path_exists(json_path):
            try:
                data = _read_json(json_path)
                cells = data.get("cells", [])
                matches = [c for c in cells if c.get("id") == cell_id or c.get("cell_id") == cell_id]
                if matches:
                    if execution_count is not None:
                        for c in matches:
                            if c.get("execution_count") == execution_count:
                                out = c.get("output")
                                if out is not None:
                                    return out
                        return None
                    out = matches[-1].get("output")
                    if out is not None:
                        return out
            except Exception:
                pass

        if not _path_exists(path) or os.path.splitext(path)[1] != ".ipynb":
            return None
        try:
            nb = nbformat.read(path, as_version=nbformat.NO_CONVERT)
        except Exception:
            return None
        for cell in nb.cells:
            if cell.get("id") == cell_id and cell.get("outputs"):
                return RecordManager._output_from_ipynb_cell(dict(cell))
        return None

    @staticmethod
    def update_cell_output(notebook_path, cell_id, output, execution_count=None):
        path = str(resolve_notebook_filesystem_path(notebook_path))
        stem = _path_stem(path)
        json_path = os.path.join(_path_parent(path), "{}_record.json".format(stem))
        if not _path_exists(json_path):
            return False
        with notebook_lock(path):
            try:
                data = _read_json(json_path)
            except Exception:
                return False
            cells = data.get("cells", [])
            for i in range(len(cells) - 1, -1, -1):
                c = cells[i]
                if (c.get("id") == cell_id or c.get("cell_id") == cell_id) and c.get("exec_order"):
                    if execution_count is not None and c.get("execution_count") != execution_count:
                        continue
                    c["output"] = output
                    _write_json(json_path, data)
                    return True
        return False

    @staticmethod
    def sync_record(notebook_path):
        path = str(resolve_notebook_filesystem_path(notebook_path))
        if not _path_exists(path) or os.path.splitext(path)[1] != ".ipynb":
            return
        rm = RecordManager(path)
        rm.load_from_record_file()
        rm.merge_ipynb_execution_state()
        rm.write_record()

    def _get_ipynb_cells(self):
        if not self.notebook_path:
            return []
        self._sync_notebook_path_for_fs()
        if not _path_exists(self.notebook_path):
            return []
        try:
            try:
                nb = nbformat.read(str(self.notebook_path), as_version=nbformat.NO_CONVERT)
            except Exception:
                nb = nbformat.read(str(self.notebook_path), as_version=4)
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
                    cell_id = "cell_{}".format(cell_idx)
                cell_idx += 1
                result.append({
                    "id": cell_id,
                    "code": source,
                    "cell_type": cell_type,
                })
            return result
        except Exception:
            return []

    def _build_cells_list(self):
        ipynb_cells = self._get_ipynb_cells()
        nb_cell_ids = {c["id"] for c in ipynb_cells if c.get("id")}
        nb_id_by_code = {}
        for c in ipynb_cells:
            if c.get("cell_type") != "code" or not c.get("id"):
                continue
            norm = _normalize_code_for_match(c.get("code", ""))
            if norm not in nb_id_by_code:
                nb_id_by_code[norm] = c["id"]

        executed_blocks = []
        for r in self._execution_records:
            raw_code = r.get("original_code", r.get("code", ""))
            if _is_empty_code(raw_code):
                continue
            if _is_ide_injected_code(raw_code, r.get("cell_id")):
                continue
            block = r.copy()
            rec_id = r.get("cell_id") or r.get("id")
            if rec_id not in nb_cell_ids:
                norm = _normalize_code_for_match(raw_code)
                if norm in nb_id_by_code:
                    block["id"] = nb_id_by_code[norm]
                    block["cell_id"] = nb_id_by_code[norm]
            executed_blocks.append(block)

        executed_cell_ids = {c.get("id") or c.get("cell_id") for c in executed_blocks}

        pending_blocks = []
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
            status = "empty" if _is_empty_code(nb_cell["code"]) else "pending"
            pending_blocks.append({
                "id": nb_cell["id"],
                "code": nb_cell["code"],
                "cell_type": "code",
                "status": status,
                "editable": True,
            })

        return executed_blocks + pending_blocks

    def write_record(self):
        if not self.notebook_path:
            return
        self._sync_notebook_path_for_fs()
        cells = self._build_cells_list()
        self._sync_notebook_path_for_fs()
        stem = _path_stem(self.notebook_path)
        base_dir = _path_parent(self.notebook_path)
        py_path = os.path.join(base_dir, "{}_record.py".format(stem))
        json_path = os.path.join(base_dir, "{}_record.json".format(stem))

        execution_log_filtered = [
            {"cell_id": c.get("id") or c.get("cell_id"), "status": c.get("status", "ok")}
            for c in cells
            if c.get("exec_order") is not None
        ]

        py_lines = []
        for c in cells:
            cell_id = c.get("id") or c.get("cell_id", "unknown")
            cell_type = c.get("cell_type", "code")
            exec_order = c.get("exec_order")

            if cell_type == "markdown":
                py_lines.append("# %% [markdown] {}".format(cell_id))
                py_lines.append("# [markdown - editable]")
                for line in (c.get("code", "") or "").split("\n"):
                    py_lines.append("# {}".format(line) if line else "#")
                py_lines.append("")
                continue

            if exec_order is not None:
                py_lines.append("# %% {}  # exec_order: {}".format(cell_id, exec_order))
            else:
                py_lines.append("# %% {}".format(cell_id))
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
            _write_text(py_path, "\n".join(py_lines).rstrip() + "\n")

            payload = {
                "notebook_path": str(os.path.abspath(self.notebook_path)),
                "execution_log": execution_log_filtered,
                "cells": cells,
            }
            _write_json(json_path, payload)

            csv_path = os.path.join(base_dir, "{}_record.csv".format(stem))
            _write_record_csv(csv_path, payload)

        self._track_mtime()


def _write_record_csv(csv_path, payload):
    cells = payload.get("cells", [])
    fieldnames = ["id", "cell_type", "status", "exec_order", "execution_count", "code", "error_ename", "error_evalue"]
    if not cells:
        _write_text(csv_path, ",".join(fieldnames) + "\n")
        return
    rows = []
    for c in cells:
        err = c.get("error_info") or {}
        code = (c.get("code") or "").replace("\r\n", "\n").replace("\n", " ")[:500]
        rows.append({
            "id": c.get("id") or c.get("cell_id", ""),
            "cell_type": c.get("cell_type", "code"),
            "status": c.get("status", ""),
            "exec_order": c.get("exec_order", ""),
            "execution_count": c.get("execution_count", ""),
            "code": code,
            "error_ename": err.get("ename", ""),
            "error_evalue": err.get("evalue", ""),
        })
    import io as _csv_io
    with _csv_io.open(str(csv_path), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

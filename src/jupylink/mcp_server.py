"""MCP server for Cursor integration."""

from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .executor import execute_cell, execute_cells
from .ipynb_ops import create_cell, delete_cell, get_cell_source, list_cells, write_cell
from .kernel_registry import list_kernels
from .record_manager import RecordManager

mcp = FastMCP("JupyLink", json_response=True)

# URI scheme for record resources (when notebook is bound)
JUPYLINK_RECORD_JSON_URI = "jupylink://record/json"
JUPYLINK_RECORD_CSV_URI = "jupylink://record/csv"

# Default notebook when started with --notebook or JUPYLINK_DEFAULT_NOTEBOOK env
_bound_notebook: Path | None = None


def _active_notebook_from_env_or_file() -> Path | None:
    """Optional notebook path for agents/IDE integration (not known to MCP protocol natively)."""
    p = os.environ.get("JUPYLINK_ACTIVE_NOTEBOOK", "").strip()
    if p:
        exp = Path(p).expanduser()
        if exp.is_file() and exp.suffix.lower() == ".ipynb":
            return exp.resolve()
    fp = os.environ.get("JUPYLINK_ACTIVE_NOTEBOOK_FILE", "").strip()
    if fp:
        try:
            line = Path(fp).expanduser().read_text(encoding="utf-8").splitlines()[0].strip()
            if line.endswith(".ipynb"):
                exp = Path(line).expanduser()
                if exp.is_file():
                    return exp.resolve()
        except OSError:
            pass
    for rel in (Path(".jupylink") / "active_notebook", Path("jupylink-active-notebook")):
        try:
            cand = (Path.cwd() / rel).resolve()
            if cand.is_file():
                line = cand.read_text(encoding="utf-8").splitlines()[0].strip()
                if line.endswith(".ipynb"):
                    exp = Path(line).expanduser()
                    if exp.is_file():
                        return exp.resolve()
        except OSError:
            pass
    return None


def _effective_default_notebook() -> Path | None:
    """Notebook used for tools/resources when no path is passed: CLI ``-n``, env, or active hint."""
    if _bound_notebook:
        return _bound_notebook
    return _active_notebook_from_env_or_file()


def _get_notebook_path(notebook_path: str | None) -> Path:
    """Resolve notebook path: use arg, else bound default, else active hint, else raise."""
    if notebook_path and str(notebook_path).strip():
        return Path(notebook_path).resolve()
    eff = _effective_default_notebook()
    if eff is not None:
        return eff
    raise ValueError(
        "notebook_path is required. Start server with --notebook, set JUPYLINK_DEFAULT_NOTEBOOK, "
        "or set JUPYLINK_ACTIVE_NOTEBOOK / .jupylink/active_notebook (see docs)."
    )


def _resolve_notebook(notebook_path: str | None) -> Path:
    p = _get_notebook_path(notebook_path)
    if not p.exists():
        raise ValueError(f"Notebook not found: {p}")
    if p.suffix != ".ipynb":
        raise ValueError(f"Not a notebook file: {p}")
    return p


@mcp.tool()
def jupylink_get_output(
    cell_id: str,
    notebook_path: str | None = None,
    execution_count: int | None = None,
) -> str:
    """Get output for a cell by cell_id and optional execution_count.

    Args:
        cell_id: The cell ID
        notebook_path: Path to the notebook file (optional if server started with --notebook)
        execution_count: Optional execution count (In[N]) for specific execution
    """
    path = _resolve_notebook(notebook_path)
    output = RecordManager.get_output_from_record_file(path, cell_id, execution_count)
    if output is None:
        return json.dumps({"error": "No output found"})
    return json.dumps(output, ensure_ascii=False, indent=2)


@mcp.tool()
def jupylink_write_cell(cell_id: str, content: str, notebook_path: str | None = None) -> str:
    """Write content to the specified cell.

    Args:
        cell_id: The cell ID
        content: Content to write
        notebook_path: Path to the notebook file (optional if server started with --notebook)
    """
    path = _resolve_notebook(notebook_path)
    if not write_cell(path, cell_id, content):
        return json.dumps({"error": f"Cell not found: {cell_id}"})
    return json.dumps({"status": "ok"})


@mcp.tool()
def jupylink_create_cell(
    cell_type: str = "code",
    index: int | None = None,
    source: str = "",
    notebook_path: str | None = None,
) -> str:
    """Create a new cell in the notebook.

    Args:
        cell_type: Cell type: code, markdown, or raw
        index: Index to insert at (default: append to end)
        source: Initial source content
        notebook_path: Path to the notebook file (optional if server started with --notebook)
    """
    path = _resolve_notebook(notebook_path)
    if cell_type not in ("code", "markdown", "raw"):
        return json.dumps({"error": f"Invalid cell type: {cell_type}"})
    new_id = create_cell(path, cell_type=cell_type, index=index, source=source)
    if not new_id:
        return json.dumps({"error": "Failed to create cell"})
    return json.dumps({"cell_id": new_id})


@mcp.tool()
def jupylink_delete_cell(cell_id: str, notebook_path: str | None = None) -> str:
    """Delete the cell with the given cell_id.

    Args:
        cell_id: The cell ID to delete
        notebook_path: Path to the notebook file (optional if server started with --notebook)
    """
    path = _resolve_notebook(notebook_path)
    if not delete_cell(path, cell_id):
        return json.dumps({"error": f"Cell not found: {cell_id}"})
    return json.dumps({"status": "ok"})


@mcp.tool()
def jupylink_execute_cell(cell_id: str, notebook_path: str | None = None) -> str:
    """Execute the specified cell.

    Args:
        cell_id: The cell ID to execute
        notebook_path: Path to the notebook file (optional if server started with --notebook)
    """
    path = _resolve_notebook(notebook_path)
    result = execute_cell(path, cell_id)
    if result is None:
        return json.dumps({"error": f"Cell not found or execution failed: {cell_id}"})
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def jupylink_execute_cells(
    cell_ids: list[str],
    notebook_path: str | None = None,
) -> str:
    """Execute multiple cells in sequence, reusing the same kernel.

    Use when cells depend on each other (e.g. def then call). Guarantees kernel reuse.

    Args:
        cell_ids: List of cell IDs to execute in order
        notebook_path: Path to the notebook file (optional if server started with --notebook)
    """
    path = _resolve_notebook(notebook_path)
    results = execute_cells(path, cell_ids)
    if not results:
        return json.dumps({"error": "No cells executed or cells not found"})
    return json.dumps(results, ensure_ascii=False, indent=2)


@mcp.tool()
def jupylink_list_cells(notebook_path: str | None = None) -> str:
    """List all cells with id, type, and source preview.

    Args:
        notebook_path: Path to the notebook file (optional if server started with --notebook)
    """
    path = _resolve_notebook(notebook_path)
    cells = list_cells(path)
    return json.dumps(cells, ensure_ascii=False, indent=2)


@mcp.tool()
def jupylink_list_kernels() -> str:
    """List running JupyLink kernels and their associated notebook files.

    Returns notebook_path and connection_file for each kernel. Useful to see
    which notebooks have active kernels (e.g. from MCP execute without opening).
    """
    kernels = list_kernels()
    return json.dumps(kernels, ensure_ascii=False, indent=2)


@mcp.tool()
def jupylink_get_record(notebook_path: str | None = None) -> str:
    """Get the agent-friendly record (.py content) for the notebook.

    Args:
        notebook_path: Path to the notebook file (optional if server started with --notebook)
    """
    path = _resolve_notebook(notebook_path)
    stem = path.stem
    base = path.parent
    py_path = base / f"{stem}_record.py"
    if py_path.exists():
        return py_path.read_text(encoding="utf-8")
    rm = RecordManager(path)
    rm.load_from_record_file()
    rm.write_record()
    if not py_path.exists():
        return json.dumps({"error": "Record file not generated"})
    return py_path.read_text(encoding="utf-8")


@mcp.tool()
def jupylink_sync_record(notebook_path: str | None = None) -> str:
    """Sync the record files with the current notebook state.

    Re-merges ipynb cells with execution history and rewrites _record.py and _record.json.
    Use after external edits to the notebook, or when the record seems stale.

    Args:
        notebook_path: Path to the notebook file (optional if server started with --notebook)
    """
    path = _resolve_notebook(notebook_path)
    rm = RecordManager(path)
    rm.load_from_record_file()
    rm.merge_ipynb_execution_state()
    rm.write_record()
    stem = path.stem
    base = path.parent
    py_path = base / f"{stem}_record.py"
    if not py_path.exists():
        return json.dumps({"error": "Record file not generated"})
    return json.dumps({"status": "ok", "record_path": str(py_path)})


@mcp.tool()
def jupylink_get_status(notebook_path: str | None = None) -> str:
    """Get a lightweight status summary of notebook cells (read-only, no side effects).

    Returns each cell's id, status (ok/pending/empty/error/markdown), editable flag,
    and exec_order if executed. Much lighter than get_record for quick state checks.

    Args:
        notebook_path: Path to the notebook file (optional if server started with --notebook)
    """
    path = _resolve_notebook(notebook_path)
    json_path = path.parent / f"{path.stem}_record.json"
    if not json_path.exists():
        cells = list_cells(path)
        summary = [
            {
                "id": c["id"],
                "cell_type": c["cell_type"],
                "status": "empty" if c["empty"] else "pending",
                "editable": True,
                "source_preview": c["source_preview"],
            }
            for c in cells
        ]
        return json.dumps(summary, ensure_ascii=False, indent=2)
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return json.dumps({"error": "Failed to read record file"})
    summary = []
    for c in data.get("cells", []):
        entry: dict = {
            "id": c.get("id") or c.get("cell_id"),
            "cell_type": c.get("cell_type", "code"),
            "status": c.get("status", "pending"),
            "editable": c.get("editable", True),
        }
        if c.get("exec_order") is not None:
            entry["exec_order"] = c["exec_order"]
        code = c.get("code", "")
        entry["source_preview"] = code[:80] + "..." if len(code) > 80 else code
        summary.append(entry)
    return json.dumps(summary, ensure_ascii=False, indent=2)


def _record_json_to_csv(data: dict) -> str:
    """Convert record JSON payload to CSV string."""
    cells = data.get("cells", [])
    if not cells:
        return "id,cell_type,status,exec_order,execution_count,code,error_ename,error_evalue\n"
    fieldnames = ["id", "cell_type", "status", "exec_order", "execution_count", "code", "error_ename", "error_evalue"]
    rows = []
    for c in cells:
        err = c.get("error_info") or {}
        rows.append({
            "id": c.get("id") or c.get("cell_id", ""),
            "cell_type": c.get("cell_type", "code"),
            "status": c.get("status", ""),
            "exec_order": c.get("exec_order", ""),
            "execution_count": c.get("execution_count", ""),
            "code": (c.get("code") or "").replace("\r\n", "\n").replace("\n", " ")[:500],
            "error_ename": err.get("ename", ""),
            "error_evalue": err.get("evalue", ""),
        })
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


@mcp.resource(
    JUPYLINK_RECORD_JSON_URI,
    name="record_json",
    title="Notebook Record (JSON)",
    description="Structured execution record: execution_log, cells with output, status, exec_order.",
    mime_type="application/json",
)
def _resource_record_json() -> str:
    """MCP resource: read _record.json for the bound notebook."""
    eff = _effective_default_notebook()
    if eff is None:
        return json.dumps({
            "error": "No notebook bound. Use --notebook, JUPYLINK_DEFAULT_NOTEBOOK, or JUPYLINK_ACTIVE_NOTEBOOK."
        })
    path = eff.resolve()
    json_path = path.parent / f"{path.stem}_record.json"
    if not json_path.exists():
        return json.dumps({"error": f"Record not found: {json_path}"})
    return json_path.read_text(encoding="utf-8")


@mcp.resource(
    JUPYLINK_RECORD_CSV_URI,
    name="record_csv",
    title="Notebook Record (CSV)",
    description="Flattened CSV view of cells: id, cell_type, status, exec_order, code, errors.",
    mime_type="text/csv",
)
def _resource_record_csv() -> str:
    """MCP resource: read _record.csv for the bound notebook (generates from JSON if needed)."""
    eff = _effective_default_notebook()
    if eff is None:
        return "error,No notebook bound. Use --notebook, JUPYLINK_DEFAULT_NOTEBOOK, or JUPYLINK_ACTIVE_NOTEBOOK."
    path = eff.resolve()
    csv_path = path.parent / f"{path.stem}_record.csv"
    json_path = path.parent / f"{path.stem}_record.json"
    if csv_path.exists():
        return csv_path.read_text(encoding="utf-8")
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            return _record_json_to_csv(data)
        except Exception:
            pass
    return "error,Record not found"


def run_mcp_server(port: int = 0, notebook_path: str | None = None) -> None:
    """Run the MCP server. Uses stdio transport for Cursor (port=0)."""
    import sys

    global _bound_notebook
    if notebook_path and str(notebook_path).strip():
        _bound_notebook = Path(notebook_path).resolve()
    else:
        env_path = os.environ.get("JUPYLINK_DEFAULT_NOTEBOOK", "").strip()
        _bound_notebook = Path(env_path).resolve() if env_path else None

    print("JupyLink MCP server starting (stdio mode)...", file=sys.stderr)
    eff = _effective_default_notebook()
    if eff:
        print(f"Default notebook: {eff}", file=sys.stderr)
    print("Connect via Cursor MCP. Press Ctrl+C to exit.", file=sys.stderr)
    mcp.run(transport="stdio")

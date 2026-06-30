"""CLI for JupyLink."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from typing import Optional

import typer

from .ipynb_ops import create_cell, delete_cell, get_cell_source, list_cells, write_cell
from .kernel_registry import (
    cleanup_stale,
    get_connection_file,
    list_kernels,
    probe_kernel,
    read_active_notebook_hint,
    resolve_notebook_filesystem_path,
)
from .notify_ide import request_notebook_refresh, set_refresh_disabled
from .record_manager import RecordManager

app = typer.Typer(help="JupyLink - Jupyter kernel proxy and CLI for agent-friendly notebook operations")

# Sentinel for "notebook path not explicitly provided"
_ARG_NOT_PROVIDED = "__not_provided__"


def _default_notebook():
    """Resolve the default notebook from env or active-notebook hint."""
    path = os.environ.get("JUPYLINK_DEFAULT_NOTEBOOK", "").strip()
    if path and os.path.isfile(path) and path.endswith(".ipynb"):
        return path
    hint = read_active_notebook_hint()
    if hint:
        return str(hint)
    return None


def _require_notebook(path):
    """Resolve and validate a notebook path for commands that need one."""
    if path is _ARG_NOT_PROVIDED or not path:
        path = _default_notebook()
    if not path:
        raise typer.BadParameter(
            "No notebook specified. Provide a path, set JUPYLINK_DEFAULT_NOTEBOOK, "
            "or run from a directory with an active notebook."
        )
    p = resolve_notebook_filesystem_path(path)
    if not p.exists():
        try:
            p = resolve_notebook_filesystem_path(path)
        except Exception:
            pass
    if not os.path.exists(str(p)):
        raise typer.BadParameter("Notebook not found: {}".format(path))
    if not str(p).endswith(".ipynb"):
        raise typer.BadParameter("Not a notebook file: {}".format(path))
    return p


def _optional_notebook(path):
    """Resolve notebook path or return None."""
    if path is _ARG_NOT_PROVIDED or not path:
        return None
    try:
        return _require_notebook(path)
    except typer.BadParameter:
        return None


# ---------------------------------------------------------------------------
# Helper for --json flag
# ---------------------------------------------------------------------------
_json_opt = typer.Option(False, "--json", help="Output in structured JSON format")


# ---------------------------------------------------------------------------
@app.command(name="get-output")
def get_output(
    notebook: str = typer.Argument(_ARG_NOT_PROVIDED, help="Path to notebook (optional if default is set)"),
    cell_id: str = typer.Argument(..., help="Cell ID (supports prefix matching)"),
    execution_count: Optional[int] = typer.Option(None, "--execution-count", "-e", help="Execution count (In[N])"),
) -> None:
    """Get output for a cell by cell_id and optional execution_count."""
    path = _require_notebook(notebook)
    output = RecordManager.get_output_from_record_file(path, cell_id, execution_count)
    if output is None:
        typer.echo("No output found.", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(output, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
@app.command(name="write-cell")
def write_cell_cmd(
    cell_id: str = typer.Argument(..., help="Cell ID (supports prefix matching)"),
    content: Optional[str] = typer.Argument(None, help="Content to write (omit if --file is used)"),
    notebook: str = typer.Argument(_ARG_NOT_PROVIDED, help="Path to notebook (optional if default is set)"),
    source_file: Optional[str] = typer.Option(None, "--file", help="Read cell content from file (avoids shell escaping issues)"),
    no_refresh: bool = typer.Option(False, "--no-refresh", help="Do not request IDE to refresh"),
) -> None:
    """Write content to the specified cell. Use --file to read from a file."""
    if no_refresh:
        set_refresh_disabled(True)
    if source_file:
        try:
            with open(source_file, "r", encoding="utf-8") as fh:
                content = fh.read()
        except OSError as e:
            typer.echo("Cannot read file: {}".format(e), err=True)
            raise typer.Exit(1)
    if content is None:
        typer.echo("Must provide content or --file.", err=True)
        raise typer.Exit(1)
    path = _require_notebook(notebook)
    if not write_cell(path, cell_id, content):
        typer.echo("Cell not found: {}".format(cell_id), err=True)
        raise typer.Exit(1)
    typer.echo("OK")


# ---------------------------------------------------------------------------
@app.command(name="create-cell")
def create_cell_cmd(
    notebook: str = typer.Argument(_ARG_NOT_PROVIDED, help="Path to notebook (optional if default is set)"),
    at: Optional[int] = typer.Option(None, "--at", "-a", help="Index to insert at (default: append)"),
    cell_type: str = typer.Option("code", "--type", "-t", help="Cell type: code, markdown, raw"),
    source: str = typer.Option("", "--source", "-s", help="Initial source content"),
    source_file: Optional[str] = typer.Option(None, "--file", help="Read cell content from file (avoids shell escaping)"),
    no_refresh: bool = typer.Option(False, "--no-refresh", help="Do not request IDE to refresh"),
) -> None:
    """Create a new cell in the notebook. Use --file to read from a file."""
    if no_refresh:
        set_refresh_disabled(True)
    if source_file:
        try:
            with open(source_file, "r", encoding="utf-8") as fh:
                source = fh.read()
        except OSError as e:
            typer.echo("Cannot read file: {}".format(e), err=True)
            raise typer.Exit(1)
    path = _require_notebook(notebook)
    if cell_type not in ("code", "markdown", "raw"):
        typer.echo("Invalid cell type: {}".format(cell_type), err=True)
        raise typer.Exit(1)
    new_id = create_cell(path, cell_type=cell_type, index=at, source=source)
    if not new_id:
        typer.echo("Failed to create cell.", err=True)
        raise typer.Exit(1)
    typer.echo(new_id)


# ---------------------------------------------------------------------------
@app.command(name="delete-cell")
def delete_cell_cmd(
    cell_id: str = typer.Argument(..., help="Cell ID (supports prefix matching)"),
    notebook: str = typer.Argument(_ARG_NOT_PROVIDED, help="Path to notebook (optional if default is set)"),
    no_refresh: bool = typer.Option(False, "--no-refresh", help="Do not request IDE to refresh"),
) -> None:
    """Delete the cell with the given cell_id."""
    if no_refresh:
        set_refresh_disabled(True)
    path = _require_notebook(notebook)
    if not delete_cell(path, cell_id):
        typer.echo("Cell not found: {}".format(cell_id), err=True)
        raise typer.Exit(1)
    typer.echo("OK")


# ---------------------------------------------------------------------------
@app.command(name="list-cells")
def list_cells_cmd(
    notebook: str = typer.Argument(_ARG_NOT_PROVIDED, help="Path to notebook (optional if default is set)"),
    json_output: bool = _json_opt,
) -> None:
    """List all cells with id, type, and source preview."""
    path = _require_notebook(notebook)
    cells = list_cells(path)

    if json_output:
        for c in cells:
            c.pop("source_preview", None)
        typer.echo(json.dumps(cells, ensure_ascii=False, indent=2))
        return

    for c in cells:
        empty = " (empty)" if c["empty"] else ""
        line = "  [{}] {} ({}){}: {!r}".format(
            c["index"], c["id"], c["cell_type"], empty, c["source_preview"]
        )
        typer.echo(line)


# ---------------------------------------------------------------------------
@app.command(name="list-kernels")
def list_kernels_cmd(
    json_output: bool = typer.Option(False, "--json", help="Output in structured JSON format"),
) -> None:
    """List registered kernels with live/dead status (heartbeat probe)."""
    kernels = list_kernels()
    if not kernels:
        if json_output:
            typer.echo(json.dumps([], ensure_ascii=False))
        else:
            typer.echo("No registered kernels.")
        return

    if json_output:
        typer.echo(json.dumps(kernels, ensure_ascii=False, indent=2))
        return

    for k in kernels:
        status = "[LIVE]" if k.get("alive") else "[DEAD]"
        typer.echo("  {} {}".format(status, k["notebook_path"]))
        typer.echo("    -> {}".format(k["connection_file"]))


# ---------------------------------------------------------------------------
@app.command(name="ping")
def ping_cmd(
    notebook: str = typer.Argument(_ARG_NOT_PROVIDED, help="Path to notebook (optional if default is set)"),
) -> None:
    """Check if the notebook's kernel is alive and responding."""
    path = _require_notebook(notebook)
    cf = get_connection_file(path)
    if not cf:
        typer.echo(json.dumps({
            "status": "no_kernel",
            "message": "No kernel registered for {}".format(str(path)),
        }, ensure_ascii=False, indent=2))
        raise typer.Exit(1)

    alive = probe_kernel(cf, timeout=2.0)
    if alive:
        typer.echo(json.dumps({
            "status": "ok",
            "connection_file": cf,
            "notebook": str(path),
        }, ensure_ascii=False, indent=2))
    else:
        typer.echo(json.dumps({
            "status": "unreachable",
            "connection_file": cf,
            "notebook": str(path),
            "message": "Kernel registered but not responding — may be dead",
        }, ensure_ascii=False, indent=2))
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
@app.command(name="cleanup-kernels")
def cleanup_kernels_cmd() -> None:
    """Remove stale kernel registry entries (e.g. after SIGKILL)."""
    n = cleanup_stale()
    typer.echo("Removed {} stale kernel(s).".format(n))


# ---------------------------------------------------------------------------
@app.command()
def record(
    notebook: str = typer.Argument(_ARG_NOT_PROVIDED, help="Path to notebook (optional if default is set)"),
) -> None:
    """Sync record from ipynb: merge ipynb cells with existing execution data.

    If record.json exists, preserves execution history and updates pending cells from ipynb.
    """
    path = _require_notebook(notebook)
    rm = RecordManager(path)
    loaded = rm.load_from_record_file()
    merged = rm.merge_ipynb_execution_state()
    rm.write_record()
    stem = os.path.splitext(os.path.basename(str(path)))[0]
    base = os.path.dirname(str(path))
    py_path = os.path.join(base, "{}_record.py".format(stem))
    if loaded:
        typer.echo("Synced: preserved execution data, updated from ipynb -> {}".format(py_path))
    else:
        typer.echo("Wrote {} and {}_record.json (no prior execution)".format(py_path, stem))


# ---------------------------------------------------------------------------
@app.command()
def execute(
    cell_ids: list[str] = typer.Argument(..., help="Cell ID(s) to execute (supports prefix matching)"),
    notebook: Optional[str] = typer.Option(None, "--notebook", "-n", help="Path to notebook (uses default if omitted)"),
    no_refresh: bool = typer.Option(False, "--no-refresh", help="Do not request IDE to refresh"),
    stdout_only: bool = typer.Option(False, "--stdout", help="Print only stdout text (no JSON wrapper)"),
) -> None:
    """Execute the specified cell(s). Multiple cells run in sequence, reusing the same kernel.

    Examples:
      jupylink execute abc123               # use default notebook
      jupylink execute abc123 def456         # multiple cells, same kernel
      jupylink execute -n book.ipynb abc123  # explicit notebook
      jupylink execute abc123 --stdout       # print only stdout text
    """
    from .executor import execute_cell, execute_cells

    if no_refresh:
        set_refresh_disabled(True)
    path = _require_notebook(notebook)
    if len(cell_ids) == 1:
        result = execute_cell(path, cell_ids[0])
        if result is None:
            typer.echo("Cell not found or execution failed: {}".format(cell_ids[0]), err=True)
            raise typer.Exit(1)
        if stdout_only:
            for item in (result.get("output") or []):
                if item.get("msg_type") == "stream" and item.get("name") == "stdout":
                    typer.echo(item.get("text", ""), nl=False)
                elif item.get("msg_type") == "error":
                    typer.echo("{}: {}".format(item.get("ename", ""), item.get("evalue", "")), err=True)
            typer.echo("")  # trailing newline
        else:
            typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        results = execute_cells(path, cell_ids)
        if not results:
            typer.echo("No cells executed or cells not found", err=True)
            raise typer.Exit(1)
        if stdout_only:
            for r in results:
                for item in (r.get("output") or []):
                    if item.get("msg_type") == "stream" and item.get("name") == "stdout":
                        typer.echo(item.get("text", ""), nl=False)
                    elif item.get("msg_type") == "error":
                        typer.echo("{}: {}".format(item.get("ename", ""), item.get("evalue", "")), err=True)
            typer.echo("")
        else:
            for r in results:
                typer.echo(json.dumps(r, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
@app.command()
def view(
    notebook: str = typer.Argument(_ARG_NOT_PROVIDED, help="Path to notebook (optional if default is set)"),
    pending: bool = typer.Option(False, "--pending", help="Only show editable cells (pending / empty)"),
    errors: bool = typer.Option(False, "--errors", help="Only show cells with execution errors"),
    cell: Optional[str] = typer.Option(None, "--cell", "-c", help="Show a single cell by ID"),
    json_output: bool = _json_opt,
) -> None:
    """Print the notebook as agent-friendly Python code (from _record.py).

    Executed cells are locked. Pending, empty, and markdown cells are editable.
    Error cells show the traceback as comments with a try/except wrapper.
    """
    path = _require_notebook(notebook)
    cells = _load_record_cells(path, pending=pending, errors=errors, cell=cell)

    if json_output:
        typer.echo(json.dumps(_cells_to_json(cells), ensure_ascii=False, indent=2))
        return

    lines = _format_record_py(cells)
    typer.echo("\n".join(lines))


# ---------------------------------------------------------------------------
@app.command()
def status(
    notebook: str = typer.Argument(_ARG_NOT_PROVIDED, help="Path to notebook (optional if default is set)"),
    json_output: bool = _json_opt,
) -> None:
    """Show notebook summary: cell counts, errors, kernel connection."""
    path = _require_notebook(notebook)
    cells = _load_record_cells(path)

    counts = {"total": 0, "executed": 0, "pending": 0, "error": 0, "empty": 0, "markdown": 0}
    error_list = []
    _status_label = {"ok": "executed"}
    for c in cells:
        s = c.get("status", "pending")
        s = _status_label.get(s, s)
        counts["total"] += 1
        counts[s] = counts.get(s, 0) + 1
        if s == "error":
            err = c.get("error_info", {})
            error_list.append({
                "cell_id": c.get("id", ""),
                "exec_order": c.get("exec_order"),
                "error": "{}: {}".format(err.get("ename", ""), err.get("evalue", "")),
            })

    kernel_state = "none"
    kernel_cf = get_connection_file(path)
    if kernel_cf:
        if probe_kernel(kernel_cf, timeout=1.0):
            kernel_state = "connected"
        else:
            kernel_state = "unreachable"

    if json_output:
        typer.echo(json.dumps({
            "notebook": str(path),
            "kernel": kernel_state,
            "connection_file": kernel_cf,
            "counts": counts,
            "errors": error_list,
        }, ensure_ascii=False, indent=2))
        return

    lines = ["Notebook: {}".format(str(path))]
    lines.append("  Kernel: {}".format(kernel_state))
    if kernel_cf and kernel_state == "unreachable":
        lines.append("    (registered but not responding — may need restart)")
    lines.append("  Cells: {} total | {} executed | {} error | {} pending | {} empty | {} markdown".format(
        counts["total"], counts["executed"], counts["error"], counts["pending"], counts["empty"], counts["markdown"]
    ))
    if error_list:
        lines.append("  Errors:")
        for e in error_list:
            lines.append("    [{}] exec#{}: {}".format(e["cell_id"][:12], e["exec_order"], e["error"]))
    typer.echo("\n".join(lines))


# ---------------------------------------------------------------------------
@app.command()
def serve(
    port: int = typer.Option(0, "--port", "-p", help="Port (0 = stdio for MCP)"),
    notebook: Optional[str] = typer.Option(None, "--notebook", "-n", help="Optional notebook path to bind"),
) -> None:
    """Start MCP server for Cursor integration."""
    try:
        from .mcp_server import run_mcp_server
    except ImportError:
        typer.echo(
            "MCP server requires Python >= 3.10 and 'mcp' package.\n"
            "Install: pip install jupylink[mcp]",
            err=True,
        )
        raise typer.Exit(1)

    run_mcp_server(port=port, notebook_path=notebook)


# ---------------------------------------------------------------------------
@app.command("install-kernelspec")
def install_kernelspec_cmd(
    user: bool = typer.Option(
        True,
        "--user/--system",
        help="Install for current user (default) or system-wide",
    ),
    replace: bool = typer.Option(
        True,
        "--replace/--no-replace",
        help="Replace an existing kernelspec",
    ),
    name: str = typer.Option(
        "jupylink",
        "--name",
        help="Kernel name: jupylink (Py3, default) or jupylink2 (Py2)",
    ),
) -> None:
    """Install Jupyter kernelspec. Use --name jupylink2 for Python 2 notebooks."""
    from jupyter_client.kernelspec import KernelSpecManager

    if name not in ("jupylink", "jupylink2"):
        typer.echo("Invalid kernel name: {}. Use jupylink or jupylink2.".format(name), err=True)
        raise typer.Exit(1)

    if name == "jupylink2":
        python_exe = (
            shutil.which("python2")
            or shutil.which("python2.7")
            or "python2"
        )
        display = "JupyLink (Python 2)"
    else:
        python_exe = sys.executable
        display = "JupyLink"

    tmp_root = Path(tempfile.mkdtemp(prefix="jupylink-kspec-"))
    spec_dir = tmp_root / name
    try:
        spec_dir.mkdir(parents=True)
        kernel_json = {
            "argv": [python_exe, "-m", "jupylink", "-f", "{connection_file}"],
            "display_name": display,
            "language": "python",
        }
        (spec_dir / "kernel.json").write_text(
            json.dumps(kernel_json, indent=2),
            encoding="utf-8",
        )
        KernelSpecManager().install_kernel_spec(
            str(spec_dir),
            kernel_name=name,
            user=user,
            replace=replace,
        )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    typer.echo("Installed kernelspec '{}' ({}) using: {}".format(name, display, python_exe))


# ===========================================================================
# Shared helpers for view / status
# ===========================================================================

def _load_record_cells(notebook_path, pending=False, errors=False, cell=None):
    """Load cells from record JSON (fall back to ipynb). Returns list of cell dicts."""
    path = _require_notebook(notebook_path)
    rm = RecordManager(path)
    rm.load_from_record_file()
    rm.merge_ipynb_execution_state()
    all_cells = rm._build_cells_list()

    if cell:
        all_cells = [c for c in all_cells
                     if c.get("id") == cell or (c.get("id") or "").startswith(cell)]
        if len(all_cells) > 1:
            all_cells = all_cells[:1]
    if pending:
        all_cells = [c for c in all_cells
                     if c.get("status") in ("pending", "empty", "markdown")]
    if errors:
        all_cells = [c for c in all_cells if c.get("status") == "error"]
    return all_cells


def _cells_to_json(cells):
    """Convert cell list to JSON-able dicts for structured output."""
    result = []
    for c in cells:
        entry = {
            "id": c.get("id") or c.get("cell_id"),
            "cell_type": c.get("cell_type", "code"),
            "status": c.get("status", "unknown"),
            "code": c.get("code", ""),
            "editable": c.get("editable", False),
        }
        if c.get("exec_order") is not None:
            entry["exec_order"] = c["exec_order"]
        if c.get("execution_count") is not None:
            entry["execution_count"] = c["execution_count"]
        if c.get("error_info"):
            err = c["error_info"]
            entry["error"] = "{}: {}".format(err.get("ename", ""), err.get("evalue", ""))
        result.append(entry)
    return result


def _format_record_py(cells):
    """Format cells as annotated Python (same format as _record.py)."""
    py_lines = []
    for c in cells:
        cell_id = c.get("id") or c.get("cell_id", "unknown")
        cell_type = c.get("cell_type", "code")
        exec_order = c.get("exec_order")
        code = c.get("code", "")

        if cell_type == "markdown":
            py_lines.append("# %% [markdown] {}".format(cell_id))
            py_lines.append("# [markdown - editable]")
            for line in (code or "").split("\n"):
                py_lines.append("# {}".format(line) if line else "#")
        else:
            if exec_order is not None:
                py_lines.append("# %% {}  # exec_order: {}".format(cell_id, exec_order))
            else:
                py_lines.append("# %% {}".format(cell_id))

            s = c.get("status", "pending")
            if s == "ok":
                py_lines.append("# [executed - do not modify]")
            elif s == "error":
                py_lines.append("# [error - do not modify]")
                err = c.get("error_info", {})
                if err:
                    if err.get("ename") and err.get("evalue"):
                        py_lines.append("# {}: {}".format(err["ename"], err["evalue"]))
                    tb = err.get("traceback", [])
                    if tb:
                        import re as _re
                        for tb_line in tb:
                            py_lines.append("# {}".format(_re.sub(r"\x1b\[[0-9;]*m", "", tb_line)))
            elif s == "empty":
                py_lines.append("# [empty - editable]")
            else:
                py_lines.append("# [pending - editable]")
            py_lines.append(code)
        py_lines.append("")
    return py_lines


# ===========================================================================

def main() -> None:
    app()


if __name__ == "__main__":
    main()

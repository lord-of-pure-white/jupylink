"""CLI for JupyLink."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import typer

from .ipynb_ops import create_cell, delete_cell, get_cell_source, list_cells, write_cell
from .kernel_registry import cleanup_stale, list_kernels, resolve_notebook_filesystem_path
from .notify_ide import request_notebook_refresh, set_refresh_disabled
from .record_manager import RecordManager

app = typer.Typer(help="JupyLink - Jupyter kernel proxy and CLI for agent-friendly notebook operations")


def _resolve_notebook(path: str) -> Path:
    p = resolve_notebook_filesystem_path(path)
    if not p.exists():
        raise typer.BadParameter(f"Notebook not found: {path}")
    if p.suffix != ".ipynb":
        raise typer.BadParameter(f"Not a notebook file: {path}")
    return p


@app.command(name="get-output")
def get_output(
    notebook: str = typer.Argument(..., help="Path to notebook"),
    cell_id: str = typer.Argument(..., help="Cell ID"),
    execution_count: int | None = typer.Option(None, "--execution-count", "-e", help="Execution count (In[N])"),
) -> None:
    """Get output for a cell by cell_id and optional execution_count."""
    path = _resolve_notebook(notebook)
    output = RecordManager.get_output_from_record_file(path, cell_id, execution_count)
    if output is None:
        typer.echo("No output found.", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(output, ensure_ascii=False, indent=2))


@app.command(name="write-cell")
def write_cell_cmd(
    notebook: str = typer.Argument(..., help="Path to notebook"),
    cell_id: str = typer.Argument(..., help="Cell ID"),
    content: str = typer.Argument(..., help="Content to write"),
    no_refresh: bool = typer.Option(False, "--no-refresh", help="Do not request IDE to refresh"),
) -> None:
    """Write content to the specified cell."""
    if no_refresh:
        set_refresh_disabled(True)
    path = _resolve_notebook(notebook)
    if not write_cell(path, cell_id, content):
        typer.echo(f"Cell not found: {cell_id}", err=True)
        raise typer.Exit(1)
    typer.echo("OK")


@app.command(name="create-cell")
def create_cell_cmd(
    notebook: str = typer.Argument(..., help="Path to notebook"),
    at: int | None = typer.Option(None, "--at", "-a", help="Index to insert at (default: append)"),
    cell_type: str = typer.Option("code", "--type", "-t", help="Cell type: code, markdown, raw"),
    source: str = typer.Option("", "--source", "-s", help="Initial source content"),
    no_refresh: bool = typer.Option(False, "--no-refresh", help="Do not request IDE to refresh"),
) -> None:
    """Create a new cell in the notebook."""
    if no_refresh:
        set_refresh_disabled(True)
    path = _resolve_notebook(notebook)
    if cell_type not in ("code", "markdown", "raw"):
        typer.echo(f"Invalid cell type: {cell_type}", err=True)
        raise typer.Exit(1)
    new_id = create_cell(path, cell_type=cell_type, index=at, source=source)
    if not new_id:
        typer.echo("Failed to create cell.", err=True)
        raise typer.Exit(1)
    typer.echo(new_id)


@app.command(name="delete-cell")
def delete_cell_cmd(
    notebook: str = typer.Argument(..., help="Path to notebook"),
    cell_id: str = typer.Argument(..., help="Cell ID"),
    no_refresh: bool = typer.Option(False, "--no-refresh", help="Do not request IDE to refresh"),
) -> None:
    """Delete the cell with the given cell_id."""
    if no_refresh:
        set_refresh_disabled(True)
    path = _resolve_notebook(notebook)
    if not delete_cell(path, cell_id):
        typer.echo(f"Cell not found: {cell_id}", err=True)
        raise typer.Exit(1)
    typer.echo("OK")


@app.command(name="list-cells")
def list_cells_cmd(
    notebook: str = typer.Argument(..., help="Path to notebook"),
) -> None:
    """List all cells with id, type, and source preview."""
    path = _resolve_notebook(notebook)
    cells = list_cells(path)
    for c in cells:
        empty = " (empty)" if c["empty"] else ""
        typer.echo(f"  [{c['index']}] {c['id']} ({c['cell_type']}){empty}: {c['source_preview']!r}")


@app.command(name="list-kernels")
def list_kernels_cmd() -> None:
    """List running kernels and their associated notebook files."""
    kernels = list_kernels()
    if not kernels:
        typer.echo("No running kernels.")
        return
    for k in kernels:
        typer.echo(f"  {k['notebook_path']}")
        typer.echo(f"    -> {k['connection_file']}")


@app.command(name="cleanup-kernels")
def cleanup_kernels_cmd() -> None:
    """Remove stale kernel registry entries (e.g. after SIGKILL)."""
    n = cleanup_stale()
    typer.echo(f"Removed {n} stale kernel(s).")


@app.command()
def record(
    notebook: str = typer.Argument(..., help="Path to notebook"),
) -> None:
    """Sync record from ipynb: merge ipynb cells with existing execution data.

    If record.json exists, preserves execution history and updates pending cells from ipynb.
    Use with kernel (and %notebook_path) for live execution recording.
    """
    path = _resolve_notebook(notebook)
    rm = RecordManager(path)
    loaded = rm.load_from_record_file()
    merged = rm.merge_ipynb_execution_state()
    rm.write_record()
    stem = path.stem
    base = path.parent
    if loaded:
        typer.echo(f"Synced: preserved execution data, updated from ipynb -> {base / (stem + '_record.py')}")
    else:
        typer.echo(f"Wrote {base / (stem + '_record.py')} and {base / (stem + '_record.json')} (no prior execution)")


@app.command()
def execute(
    notebook: str = typer.Argument(..., help="Path to notebook"),
    cell_ids: list[str] = typer.Argument(..., help="Cell ID(s) to execute (multiple = same kernel)"),
    no_refresh: bool = typer.Option(False, "--no-refresh", help="Do not request IDE to refresh"),
) -> None:
    """Execute the specified cell(s). Multiple cells run in sequence, reusing the same kernel."""
    from .executor import execute_cell, execute_cells

    if no_refresh:
        set_refresh_disabled(True)
    path = _resolve_notebook(notebook)
    if len(cell_ids) == 1:
        result = execute_cell(path, cell_ids[0])
        if result is None:
            typer.echo(f"Cell not found or execution failed: {cell_ids[0]}", err=True)
            raise typer.Exit(1)
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        results = execute_cells(path, cell_ids)
        if not results:
            typer.echo("No cells executed or cells not found", err=True)
            raise typer.Exit(1)
        for r in results:
            typer.echo(json.dumps(r, ensure_ascii=False, indent=2))


@app.command()
def serve(
    port: int = typer.Option(0, "--port", "-p", help="Port (0 = stdio for MCP)"),
    notebook: str | None = typer.Option(None, "--notebook", "-n", help="Optional notebook path to bind"),
) -> None:
    """Start MCP server for Cursor integration."""
    from .mcp_server import run_mcp_server

    run_mcp_server(port=port, notebook_path=notebook)


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
        help="Replace an existing jupylink kernelspec",
    ),
) -> None:
    """Install Jupyter kernelspec using sys.executable (avoids wrong 'python' outside venv)."""
    from jupyter_client.kernelspec import KernelSpecManager

    tmp_root = Path(tempfile.mkdtemp(prefix="jupylink-kspec-"))
    spec_dir = tmp_root / "jupylink"
    try:
        spec_dir.mkdir(parents=True)
        kernel_json = {
            "argv": [sys.executable, "-m", "jupylink", "-f", "{connection_file}"],
            "display_name": "JupyLink",
            "language": "python",
        }
        (spec_dir / "kernel.json").write_text(
            json.dumps(kernel_json, indent=2),
            encoding="utf-8",
        )
        KernelSpecManager().install_kernel_spec(
            str(spec_dir),
            kernel_name="jupylink",
            user=user,
            replace=replace,
        )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    typer.echo(f"Installed kernelspec 'jupylink' using: {sys.executable}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()

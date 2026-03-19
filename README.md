# JupyLink

Jupyter kernel proxy that generates agent-friendly execution records from ipynb. Also provides a CLI and MCP server for Cursor integration.

## Features

- Intercepts cell execution and records results (including output by cell_id and execution_count)
- Success cells: raw code
- Error cells: code wrapped in `try/except` with error info as comments
- Unexecuted/empty cells: preserved in layout, marked as editable
- Output: `.py` (for agents), `.json` (for programs), `.csv` (flattened table)
- Execution timeline in JSON
- **CLI**: get-output, write-cell, create-cell, delete-cell, list-cells, execute, record, cleanup-kernels, serve
- **MCP Server**: Cursor integration for agent tools
- **IDE refresh**: Auto-request Cursor/VS Code to refresh notebook when modified via CLI or MCP

## Install

```bash
pip install -e .
jupyter kernelspec install kernels/jupylink
```

## Usage

### Jupyter Lab / VS Code

1. Open a notebook in Jupyter Lab or VS Code
2. Select the **JupyLink** kernel
3. (Optional) In the first cell, run: `%notebook_path ./your_notebook.ipynb`
4. Execute cells as usual — records are written to `{notebook_stem}_record.py`, `{notebook_stem}_record.json`, and `{notebook_stem}_record.csv`

### CLI

```bash
# Get output for a cell (optionally by execution_count)
jupylink get-output notebook.ipynb cell_id [-e N]

# Write content to a cell
jupylink write-cell notebook.ipynb cell_id "content"

# Create a new cell
jupylink create-cell notebook.ipynb [--at INDEX] [--type code|markdown|raw] [--source "..."]

# Delete a cell
jupylink delete-cell notebook.ipynb cell_id

# List all cells
jupylink list-cells notebook.ipynb

# Execute cell(s) — multiple cells run in same kernel
jupylink execute notebook.ipynb cell_id [cell_id ...]

# Sync record: merge ipynb with existing execution data (preserves history)
jupylink record notebook.ipynb

# List running kernels and their notebook files
jupylink list-kernels

# Cleanup stale kernel registry entries
jupylink cleanup-kernels

# Start MCP server (Cursor auto-starts when configured; manual: -p PORT, -n notebook)
jupylink serve [-p PORT] [-n notebook.ipynb]

# Disable IDE refresh (e.g. in scripts)
jupylink write-cell notebook.ipynb cell_id "content" --no-refresh
# Or: JUPYLINK_NO_REFRESH=1 jupylink write-cell ...
```

### Cursor Integration (MCP)

**Workflow**:

1. With MCP configured, Cursor **auto-starts** the MCP process when tools are invoked; you can also run `jupylink serve` manually in a terminal to keep it running
2. Open ipynb in Cursor
3. Select **JupyLink** kernel for the notebook
4. (If IDE doesn't auto-pass path) Run `%notebook_path ./current_notebook.ipynb` in the first cell to register kernel for CLI connection
5. After selecting JupyLink, **recording is auto-enabled**; CLI/MCP **connects to the same kernel** (shared variable state during execution)

**%notebook_path vs jupylink record**:
- `%notebook_path`: Sets path in kernel for recording + CLI connection; must run in notebook
- `jupylink record`: Syncs record from CLI, merges ipynb with existing execution data; does not overwrite execution history

Configure `~/.cursor/mcp.json` or `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "jupylink": {
      "command": "jupylink",
      "args": ["serve"],
      "env": {
        "JUPYLINK_REFRESH_SKIP_REMOTE": "1"
      }
    }
  }
}
```

- **Windows** (with venv): `"command": ".venv\\Scripts\\jupylink.exe"`
- **env**: 环境变量，如 `JUPYLINK_REFRESH_SKIP_REMOTE=1` 禁用 Remote SSH 刷新、`JUPYLINK_NO_REFRESH=1` 禁用所有刷新
- **Linux/macOS**: `"command": ".venv/bin/jupylink"` or `"command": "jupylink"` (if installed via pip)
- **Default notebook**: Add `args: ["serve", "-n", "test.ipynb"]` or set `JUPYLINK_DEFAULT_NOTEBOOK` env var so tools can omit the `notebook_path` argument

**MCP tools**:

- `jupylink_get_output` — Get output by cell_id and execution_count
- `jupylink_write_cell` — Write content to cell
- `jupylink_create_cell` — Create new cell
- `jupylink_delete_cell` — Delete cell
- `jupylink_execute_cell` — Execute single cell
- `jupylink_execute_cells` — Execute multiple cells (same kernel, for dependent cells)
- `jupylink_list_cells` — List all cells
- `jupylink_list_kernels` — List running kernels and their notebook files
- `jupylink_get_record` — Get agent-friendly .py record content
- `jupylink_sync_record` — Sync record (merge ipynb with execution history; same as CLI `record`)
- `jupylink_get_status` — Lightweight status summary (read-only, no side effects)

**MCP Resources** (when notebook is bound): Use `list_mcp_resources` / `fetch_mcp_resource` to view `_record.json` and `_record.csv`:
- `jupylink://record/json` — Structured JSON record
- `jupylink://record/csv` — Flattened CSV record

## Record Format

### .py

```python
# %% cell_abc123
# [executed - do not modify]
x = 1

# %% cell_def456
# [error - do not modify]
# NameError: name 'x' is not defined
try:
    y = x + 1
except Exception as e:
    print(e)

# %% cell_xyz789
# [empty - editable]


# %% cell_abc789
# [pending - editable]
some_unexecuted_code()
```

### .json and .csv

**JSON** (`{stem}_record.json`):

```json
{
  "notebook_path": "/path/to/notebook.ipynb",
  "execution_log": [
    { "cell_id": "cell_abc123", "status": "ok" },
    { "cell_id": "cell_def456", "status": "error" }
  ],
  "cells": [
    { "id": "cell_abc123", "code": "...", "status": "ok", "output": [...], "execution_count": 1 }
  ]
}
```

**CSV** (`{stem}_record.csv`): Flattened table with columns `id`, `cell_type`, `status`, `exec_order`, `execution_count`, `code`, `error_ename`, `error_evalue`.

## IDE Refresh

When CLI or MCP modifies the ipynb (write-cell, create-cell, delete-cell) or executes a cell, JupyLink requests the IDE (Cursor/VS Code) to refresh the notebook from disk. This uses `cursor` or `code` CLI with `-r` (reuse window).

- **Disable**: `--no-refresh` on CLI, or `JUPYLINK_NO_REFRESH=1` env var
- **Requires**: `cursor` or `code` in PATH (install "Shell Command" from Command Palette)
- **Delay**: Waits 0.2s (default) before invoking CLI so the filesystem can flush. Override with `JUPYLINK_REFRESH_DELAY=0.5` (seconds) if refresh is still delayed.
- **Debouncing**: Rapid successive operations (e.g. execute_cells) coalesce into a single refresh, scheduled after the last operation.
- **Dual method**: CLI (`cursor path -r`) + optional URL scheme. URL refresh disabled by default; set `JUPYLINK_REFRESH_USE_URL=1` to enable.
- **Temp paths**: Refresh is skipped for notebooks under temp directories (`pytest-of-*`, `Temp`, `tmp`) to avoid slowness when viewing test artifacts.
- **Remote SSH**: When MCP runs on the server (SSH_CONNECTION set), JupyLink uses `vscode://vscode-remote/ssh-remote+host/path` URI to trigger client refresh. Host from `JUPYLINK_REMOTE_SSH_HOST` or derived from SSH_CONNECTION. Requires X11 forwarding or similar for `webbrowser.open` to reach the client. Set `JUPYLINK_REFRESH_SKIP_REMOTE=1` to disable. Fallback: [Notebook Hot Reload](https://marketplace.visualstudio.com/items?itemName=kdkyum.notebook-hot-reload).

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `JUPYLINK_EXEC_TIMEOUT` | 60 | Execution timeout (seconds) for cell runs |
| `JUPYLINK_NO_REFRESH` | — | Set to `1` to disable IDE refresh |
| `JUPYLINK_REFRESH_SKIP_REMOTE` | — | Set `1` to skip refresh when using Remote SSH |
| `JUPYLINK_REMOTE_SSH_HOST` | — | SSH host for vscode-remote URI (e.g. `myserver`); fallback: from SSH_CONNECTION |
| `JUPYLINK_REFRESH_DELAY` | 0.2 | Delay (seconds) before invoking IDE refresh |
| `JUPYLINK_REFRESH_USE_URL` | 0 | Set `1` to use URL scheme for refresh (default: disabled) |
| `JUPYLINK_REFRESH_USE_VSCODE` | — | Set `1` to use `vscode://` (default: `cursor://` for Cursor) |
| `JUPYLINK_LOCK_TIMEOUT` | 10 | File lock timeout (seconds); reduce for faster failure when contested |
| `JUPYLINK_DEFAULT_NOTEBOOK` | — | Default notebook path when MCP tools omit `notebook_path` |

## Notebook Path

Resolved in order:

1. `%notebook_path /path/to/notebook.ipynb` (magic)
2. `JUPYTER_NOTEBOOK_PATH` env var
3. `JPY_SESSION_NAME` env var (if it ends with .ipynb)
4. **Auto from execute_request** (VS Code/Cursor): when you run a cell, the kernel extracts the notebook path from the cellId metadata (`vscode-notebook-cell:URI#...`). No `%notebook_path` needed.

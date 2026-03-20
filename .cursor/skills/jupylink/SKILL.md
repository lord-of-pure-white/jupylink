---
name: jupylink
description: >-
  Operates Jupyter notebooks via JupyLink: CLI, MCP tools, and kernel. Use when
  editing .ipynb files, executing cells, getting outputs, creating/writing/deleting
  cells, syncing records, or integrating with Cursor for notebook workflows.
---

# JupyLink Usage

JupyLink provides three usage modes: **CLI**, **MCP** (Cursor tools), and **Kernel** (Jupyter/VS Code). All operate on `.ipynb` files and produce `{stem}_record.py`, `{stem}_record.json`, and `{stem}_record.csv`.

## Agent Workflow — IMPORTANT

**All notebook operations MUST use `jupylink_*` MCP tools. Do NOT use `EditNotebook` or directly edit `.ipynb` JSON.** JupyLink manages cell ids, execution state, and IDE refresh — bypassing it causes state corruption.

### Inspecting Notebook State (Read Flow)

When viewing Jupyter notebook state, follow this flow:

1. **Get ipynb structure** — `jupylink_list_cells(notebook_path)` → cells with id, type, source preview, index
2. **Get execution status** — Use MCP resources or tools:
   - `fetch_mcp_resource(jupylink://record/json)` or `jupylink_get_record()` — structured execution_log, cells with actual executed Python code (including try/except-wrapped error cells)
   - `fetch_mcp_resource(jupylink://record/csv)` — flattened view
   - `jupylink_get_status()` — lightweight summary
3. **Extract cell_ids** — From record JSON/CSV or list_cells; needed for output lookup
4. **Get cell output** — `jupylink_get_output(cell_id="...")` for a specific cell's stdout, execute_result, or error

### Code Placement (Write Flow) — MANDATORY

**All code written into ipynb MUST go through JupyLink MCP tools.** Never use EditNotebook or direct ipynb JSON edits.

- **Write to existing cell**: `jupylink_write_cell(cell_id="...", content="...")`
- **Create new cell**: `jupylink_create_cell(source="...", index=N, cell_type="code")`
- **Delete cell**: `jupylink_delete_cell(cell_id="...")`

### Understanding Notebook State

The `{stem}_record.py` file is the agent's primary view of the notebook. **Always read it first** before making any changes. It contains every cell with clear status markers:

```python
# %% abc123  # exec_order: 1
# [executed - do not modify]
x = 42

# %% [markdown] def456
# [markdown - editable]
# ## Data Processing
# Load and clean the dataset

# %% ghi789
# [pending - editable]
print(x)

# %% jkl012
# [empty - editable]

```

**Status markers and what they mean for the agent:**

| Marker | Meaning | Agent Action |
|--------|---------|-------------|
| `[executed - do not modify]` | Cell has been run; variables are in kernel memory | Do NOT edit — changing it would desync code from kernel state |
| `[error - do not modify]` | Cell ran but raised an exception | Read the error comment below it; fix in a new or pending cell |
| `[pending - editable]` | Cell exists but hasn't been executed | Safe to edit via `jupylink_write_cell`, then execute |
| `[empty - editable]` | Empty cell placeholder | Safe to write code into via `jupylink_write_cell` |
| `[markdown - editable]` | Markdown cell (shown as `# ` prefixed lines) | Provides context about notebook structure and intent |

**The `exec_order: N` tag** shows the global execution sequence. Use this to understand variable dependencies — cell with exec_order 3 can reference variables from exec_order 1 and 2.

### Locating Cells for Operations

Every MCP tool operates on **cell_id** (the hex string after `# %%`). To find the right cell:

1. **Read `_record.py`** — scan for the code you want to modify; the cell_id is on the `# %%` line
2. **Or call `jupylink_list_cells()`** — returns all cells with id, type, source preview, and index
3. **Or call `jupylink_get_status()`** — lightweight status summary without side effects

**Important**: cell_ids in `_record.py` may be long VS Code URIs (like `vscode-notebook-cell:/...#W0s...`). Use the full string as-is when calling MCP tools — do not truncate it.

### Typical Workflows

**1. Understand the notebook before acting:**
```
jupylink_get_record()  or  Read {stem}_record.py
→ Understand what's executed, what's pending, what the structure is
```

**2. Edit and run an existing pending cell:**
```
jupylink_write_cell(cell_id="abc123", content="new code here")
jupylink_execute_cell(cell_id="abc123")
→ Check returned output for errors
```

**3. Write code into an empty cell:**
```
Find an [empty - editable] cell in _record.py → get its cell_id
jupylink_write_cell(cell_id="<empty_cell_id>", content="my_code()")
jupylink_execute_cell(cell_id="<empty_cell_id>")
```

**4. Create a new cell and execute it:**
```
jupylink_create_cell(source="import pandas as pd", index=2)
→ Returns {"cell_id": "new_id"}
jupylink_execute_cell(cell_id="new_id")
```

**5. Run multiple dependent cells together:**
```
jupylink_execute_cells(cell_ids=["cell_def", "cell_call"])
→ Guarantees same kernel; cell_call can use variables from cell_def
```

**6. Fix a failed cell:**
```
Read error from _record.py or jupylink_get_output(cell_id="err_cell")
jupylink_write_cell(cell_id="err_cell", content="fixed_code()")
jupylink_execute_cell(cell_id="err_cell")
```

**7. Check execution output after the fact:**
```
jupylink_get_output(cell_id="abc123")                    → latest output
jupylink_get_output(cell_id="abc123", execution_count=3) → specific execution
```

**8. Refresh record after external edits (user ran cells manually):**
```
jupylink_sync_record()
→ Re-merges ipynb with execution history; then re-read _record.py
```

### Key Rules

1. **ipynb writes are MANDATORY via MCP** — use `jupylink_write_cell`, `jupylink_create_cell`, `jupylink_delete_cell`; never EditNotebook or direct ipynb edit
2. **Read `_record.py` first** — always understand current state before acting
3. **Never modify `[executed]` cells** — they represent committed kernel state
4. **Use `execute_cells` for dependencies** — single call with multiple cell_ids ensures same kernel
5. **Prefer empty cells** for new code — look for `[empty - editable]` slots before creating new cells
6. **Use `create_cell(index=N)` for insertion** — 0-based; inserts before position N; omit to append
7. **Check output after execution** — `execute_cell` returns output directly; use `get_output` for historical data
8. **Sync when stale** — if user ran cells manually in the notebook UI, call `jupylink_sync_record()` to update

---

## Setup

```bash
pip install -e .
jupyter kernelspec install kernels/jupylink
```

## 1. CLI

Entry: `jupylink <command> <notebook> [args]`

| Command | Args | Options | Output |
|---------|------|---------|--------|
| `get-output` | notebook, cell_id | `-e N` (execution_count) | JSON output |
| `write-cell` | notebook, cell_id, content | `--no-refresh` | OK |
| `create-cell` | notebook | `-a INDEX`, `-t code\|markdown\|raw`, `-s "..."`, `--no-refresh` | new cell_id |
| `delete-cell` | notebook, cell_id | `--no-refresh` | OK |
| `list-cells` | notebook | — | cell list |
| `execute` | notebook, cell_id [cell_id ...] | `--no-refresh` | JSON result(s); multiple cells = same kernel |
| `record` | notebook | — | sync message |
| `list-kernels` | — | — | running kernels: notebook_path, connection_file |
| `cleanup-kernels` | — | — | removed count |
| `serve` | — | `-p PORT`, `-n notebook` | MCP server |

**Create-cell index**: 0-based; omit to append. `index=N` inserts before cell N. Clamped to [0, len(cells)].

**Disable IDE refresh**: `--no-refresh` or `JUPYLINK_NO_REFRESH=1`.

## 2. MCP (Cursor)

Configure `~/.cursor/mcp.json` or `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "jupylink": {
      "command": "path/to/jupylink",
      "args": ["serve"]
    }
  }
}
```

- **Windows**: `"command": ".venv\\Scripts\\jupylink.exe"`
- **Linux/macOS**: `"command": ".venv/bin/jupylink"`

**Default notebook (optional)**: Add `-n notebook.ipynb` to args or set `JUPYLINK_DEFAULT_NOTEBOOK` in env. When set, tools can omit `notebook_path` — agent can call `jupylink_list_cells()` without passing the path.

**Project-level** `.cursor/mcp.json` can override with `args: ["serve", "-n", "test.ipynb"]` so the current project's notebook is automatic.

**Tools**:

| Tool | Args | Returns |
|------|------|---------|
| `jupylink_get_output` | notebook_path, cell_id, execution_count? | JSON output or `{"error":"..."}` |
| `jupylink_write_cell` | notebook_path, cell_id, content | `{"status":"ok"}` or error |
| `jupylink_create_cell` | notebook_path, cell_type?, index?, source? | `{"cell_id":"..."}` or error |
| `jupylink_delete_cell` | notebook_path, cell_id | `{"status":"ok"}` or error |
| `jupylink_execute_cell` | notebook_path, cell_id | JSON result or error |
| `jupylink_execute_cells` | notebook_path, cell_ids | JSON list of results; use for dependent cells |
| `jupylink_list_cells` | notebook_path | JSON cell list |
| `jupylink_list_kernels` | — | JSON list of running kernels: notebook_path, connection_file |
| `jupylink_get_record` | notebook_path | .py record content (read-only if file exists) |
| `jupylink_sync_record` | notebook_path | Re-merges ipynb with history, rewrites record files |
| `jupylink_get_status` | notebook_path | Lightweight JSON summary of cell statuses (read-only) |

**MCP Resources** (when notebook is bound via `-n` or `JUPYLINK_DEFAULT_NOTEBOOK`):

Use `list_mcp_resources` / `fetch_mcp_resource` to view record files:

| URI | Name | Description |
|-----|------|-------------|
| `jupylink://record/json` | record_json | Structured JSON: execution_log, cells with output, status |
| `jupylink://record/csv` | record_csv | Flattened CSV: id, cell_type, status, exec_order, code, errors |

## 3. Kernel (Jupyter Lab / VS Code)

1. Open notebook → select **JupyLink** kernel
2. (Optional) First cell: `%notebook_path ./notebook.ipynb` — sets path for recording + CLI connection
3. Run cells normally → `{stem}_record.py`, `{stem}_record.json`, and `{stem}_record.csv` are written

**Path resolution**: `%notebook_path` > env vars > **auto from execute_request** (VS Code/Cursor extracts path from cellId on first run; no magic needed).

### MCP first, then IDE kernel (reuse same process)

If **MCP/CLI already started** a JupyLink kernel and registered it, the IDE can attach to that process instead of starting a second one:

1. On startup, `python -m jupylink` checks whether to **bridge** the IDE’s new connection file to an existing kernel (ZMQ proxy with HMAC re-signing).
2. **Match rules** (first hit wins after `JUPYLINK_IDE_REUSE` is on):
   - `JUPYLINK_IDE_CONNECTION_FILE` = path to the **upstream** `kernel-*.json` (e.g. from `jupylink list-kernels`).
   - Or resolve via registry: set **`JUPYLINK_IDE_NOTEBOOK_PATH`** (or `JUPYTER_NOTEBOOK_PATH`) to the **absolute** `.ipynb` path that MCP uses.
   - Or `JUPYLINK_IDE_REUSE_UNIQUE=1` when **exactly one** kernel is listed in the registry (single-notebook workflows).
3. Configure these on the **Jupyter kernel** environment (VS Code: Jupyter env / `.env` for the interpreter), not only on the MCP server.
4. Disable bridging: `JUPYLINK_IDE_REUSE=0`. Verbose stderr logs: `JUPYLINK_IDE_PROXY_LOG=1`.

## Execution Flow

- **CLI `execute`** / **MCP `jupylink_execute_cell`**: Tries to connect to existing JupyLink kernel; if none, spawns kernel and keeps it alive for reuse (`independent=True`).
- **MCP `jupylink_execute_cells`** / **CLI `execute cell1 cell2 ...`**: Runs multiple cells in one call, guaranteeing kernel reuse. Use when cells depend on each other (e.g. def then call).
- **Kernel registration**: When notebook uses JupyLink and path is set, kernel registers in `~/.jupylink/kernels.json`. CLI/MCP use this to connect.
- **IDE after MCP (same kernel)**: If MCP already started a kernel for a notebook and it is registered, selecting **JupyLink** in the IDE can **bridge** to that process instead of starting a second one. On startup, `python -m jupylink` checks reuse rules and, if matched, runs a ZMQ proxy that re-signs messages between the IDE’s connection file and the existing kernel. Set `JUPYLINK_IDE_REUSE=0` on the kernel process to force a normal local JupyLink kernel.
- **Auto-reuse without env (two tiers)**:
  1. **Registry single (preferred, reliable)** — data is in the same persistent user directory as ``kernels.json`` (e.g. ``~/.jupylink/`` or ``%APPDATA%/jupylink/`` on Windows), **not** under ``/tmp``. If there is **exactly one** live registered kernel, the IDE bridge uses it. No dependency on IDE cwd. Disable with ``JUPYLINK_IDE_REGISTRY_SINGLE=0`` if you often have stray registrations.
  2. **Workspace sidecar (fallback)** — On register, JupyLink also writes ``{notebook_stem}.jupylink_kernel.json`` next to the ``.ipynb``. The IDE process scans downward from its **cwd** (depth-limited); if **exactly one** valid sidecar appears under that tree, it is used. This is weaker (cwd varies by editor/settings) but helps when multiple kernels are registered and only one notebook has a sidecar. Disable with ``JUPYLINK_IDE_SIDECAR=0``.

  **Why not ``/tmp``?** System temp is cleared on reboot, shared by all users/processes, and not tied to a stable notebook identity without encoding paths in filenames. JupyLink keeps authoritative state next to the user registry (persistent) and optionally beside the ``.ipynb`` (visible, gitignored).
- **`jupylink record`** / **`jupylink_sync_record`**: Merges ipynb with existing `_record.json`; preserves execution history; writes `_record.py`, `_record.json`, `_record.csv`.

## Record Format

- **.py**: `# %% cell_id` blocks with status markers:
  - `[executed - do not modify]` — ran successfully, kernel state depends on it
  - `[error - do not modify]` — ran but failed, error details in comments below
  - `[pending - editable]` — not yet executed, safe to modify
  - `[empty - editable]` — empty cell, available for new code
  - `[markdown - editable]` — markdown cell as `# %% [markdown] cell_id` with `# ` prefixed content
- **.json**: `execution_log`, `cells` with `output`, `execution_count`, `status`, `cell_type`
- **.csv**: Flattened table: `id`, `cell_type`, `status`, `exec_order`, `execution_count`, `code`, `error_ename`, `error_evalue` — for spreadsheet/analysis

## Configuration

- `JUPYLINK_EXEC_TIMEOUT`: Execution timeout in seconds (default: 60). Set higher for long-running cells.
- `JUPYLINK_NO_REFRESH`: Set to `1` to disable IDE refresh notifications.
- `JUPYLINK_REFRESH_SKIP_REMOTE`: Set to `1` to skip refresh when using Remote SSH.
- `JUPYLINK_REMOTE_SSH_HOST`: SSH host for vscode-remote URI (e.g. `myserver`); auto-derived from SSH_CONNECTION when unset.
- `JUPYLINK_REFRESH_USE_VSCODE`: Set to `1` to use `vscode://` instead of `cursor://` for refresh URLs.
- `JUPYLINK_REFRESH_USE_URL`: Set to `1` to enable URL-based refresh (default: 0, disabled).
- `JUPYLINK_REFRESH_DELAY`: Delay in seconds before invoking IDE refresh (default: 0.1). Increase if the IDE file does not update in time.
- `JUPYLINK_LOCK_TIMEOUT`: File lock timeout in seconds (default: 10). On Windows, filelock has ~1s delay per failed acquisition; reduce for faster failure when contested.
- **IDE kernel bridge (reuse MCP kernel)** — add these to the **Jupyter kernel environment** (e.g. workspace `.env` loaded by Python/Jupyter, or Cursor/VS Code Jupyter env settings):
  - `JUPYLINK_IDE_NOTEBOOK_PATH`: Absolute path to the `.ipynb` (must match the notebook MCP uses). Registry lookup then finds the MCP kernel.
  - `JUPYLINK_IDE_CONNECTION_FILE`: Optional; absolute path to the existing kernel’s `kernel-*.json` (skips registry; use `jupylink list-kernels` to copy).
  - `JUPYLINK_IDE_REUSE_UNIQUE=1`: If exactly one kernel is registered, bridge to it (use only when a single notebook session is active).
  - `JUPYLINK_IDE_REUSE=0`: Disable bridging; always start a full in-IDE JupyLink kernel.
  - `JUPYLINK_IDE_PROXY_LOG=1`: Verbose proxy logging on stderr.
  - `JUPYLINK_IDE_REGISTRY_SINGLE=0`: Do not auto-pick the sole kernel from ``kernels.json`` (default is on: single registration → bridge).
  - `JUPYLINK_IDE_SIDECAR=0`: Do not scan for ``*.jupylink_kernel.json`` under cwd.
  - `JUPYLINK_IDE_SIDECAR_DEPTH`: Max directory depth from cwd for sidecar scan (default `12`).

## IDE Refresh

After write-cell, create-cell, delete-cell, or execute, JupyLink requests IDE refresh via `cursor`/`code -r`. Uses debouncing: rapid successive ops coalesce into one refresh (0.1s after the last). **Refresh is skipped for paths under temp directories** (e.g. `pytest-of-*`, `Temp`, `tmp`). Requires `cursor` or `code` in PATH. Disable with `--no-refresh` or `JUPYLINK_NO_REFRESH=1`.

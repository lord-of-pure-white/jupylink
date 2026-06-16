---
name: jupylink
description: >-
  Operates Jupyter notebooks via JupyLink CLI. Use when the user asks to
  look at, edit, create cells in, execute, or understand .ipynb files.
  Never read .ipynb directly — always use jupylink commands.
---

# JupyLink

Jupyter kernel proxy that generates agent-friendly execution records from `.ipynb` files.

## Core Rule

**Never read or edit `.ipynb` files directly.** Always use `jupylink` CLI commands. Notebook state lives in `_record.py` / `_record.json`, which jupylink manages.

## Installation

```bash
# Clone into .claude/skills/
git clone <repo> .claude/skills/jupylink
cd .claude/skills/jupylink

# Install (requires Python >= 3.9)
pip install -e .

# Optional: MCP server requires Python >= 3.10
pip install -e ".[mcp]"

# Install jupylink kernel into Jupyter (so Jupyter Lab/VS Code can select it)
jupylink install-kernelspec
```

## Default Notebook

Set once to avoid passing the notebook path every time:

```bash
export JUPYLINK_DEFAULT_NOTEBOOK=/path/to/notebook.ipynb
```

Or write an active-notebook hint (auto-created when executing cells):

```bash
echo "/path/to/notebook.ipynb" > .jupylink/active_notebook
```

When a default is set, the `notebook` argument can be omitted from all commands.

## Command Reference

### `jupylink view` — Read notebook as Python

Primary way to understand notebook state. Prints annotated Python code to stdout.

```bash
jupylink view [notebook]              # full notebook as Python
jupylink view [notebook] --pending    # only editable cells (pending / empty / markdown)
jupylink view [notebook] --errors     # only cells with execution errors
jupylink view [notebook] --cell <id>  # single cell by ID (prefix match)
jupylink view [notebook] --json       # structured JSON output
```

Output uses status markers:

```
# %% abc123  # exec_order: 1
# [executed - do not modify]       ← ran successfully, don't edit
import pandas as pd

# %% def456
# [pending - editable]             ← hasn't run, safe to edit
print(x)

# %% ghi789  # exec_order: 3
# [error - do not modify]          ← ran and failed
# ZeroDivisionError: division by zero
try:
    1/0
except Exception as e:
    print(e)

# %% [markdown] jkl012
# [markdown - editable]            ← markdown, always editable
# ## Analysis

# %% mno345
# [empty - editable]               ← empty slot for new code
```

**Markers and what they mean:**

| Marker | Meaning | Action |
|--------|---------|--------|
| `[executed - do not modify]` | Cell ran, variables in kernel memory | Do NOT edit |
| `[error - do not modify]` | Cell ran but raised an exception | Read the error, fix in another cell |
| `[pending - editable]` | Cell exists but hasn't executed | Safe to edit and execute |
| `[empty - editable]` | Empty cell placeholder | Safe to write code into |
| `[markdown - editable]` | Markdown cell | Context / documentation |

### `jupylink status` — Quick summary

```bash
jupylink status [notebook]          # human-readable
jupylink status [notebook] --json   # structured JSON
```

Example output:
```
Notebook: /path/to/notebook.ipynb
  Kernel: none
  Cells: 12 total | 8 executed | 2 error | 1 pending | 0 empty | 1 markdown
  Errors:
    [9b779c39] exec#3: ZeroDivisionError: division by zero
    [92f3f301] exec#6: TypeError: invalid keyword argument
```

### `jupylink execute` — Run cells

```bash
jupylink execute [notebook] <cell_id>                  # single cell
jupylink execute [notebook] <cell_id1> <cell_id2> ...   # multiple cells, same kernel
```

Returns JSON: `{"status": "ok", "execution_count": N, "output": [...], "ename": null}`.

- Connects to an existing registered kernel if available, otherwise spawns one
- Multiple cell_ids run sequentially in the same kernel (cells can depend on each other)
- Cell IDs support **prefix matching** (min 4 chars, must be unique)

### `jupylink write-cell` — Edit cell content

```bash
jupylink write-cell [notebook] <cell_id> "source code here"
```

Returns `OK`. Supports prefix matching on cell_id.

### `jupylink create-cell` — Add a new cell

```bash
jupylink create-cell [notebook]                           # append empty code cell
jupylink create-cell [notebook] --type markdown            # append markdown
jupylink create-cell [notebook] --at 3 --source "x = 1"   # insert before index 3
```

Returns the new cell's ID. `--at N` is 0-based (inserts before cell N).

### `jupylink delete-cell` — Remove a cell

```bash
jupylink delete-cell [notebook] <cell_id>
```

Returns `OK`. Supports prefix matching.

### `jupylink list-cells` — List all cells

```bash
jupylink list-cells [notebook]          # human-readable
jupylink list-cells [notebook] --json   # structured
```

Shows index, cell_id, type, and source preview.

### `jupylink get-output` — Get cell execution output

```bash
jupylink get-output [notebook] <cell_id>            # latest output
jupylink get-output [notebook] <cell_id> -e 3       # output from execution #3
```

Returns JSON array of output messages (stream, execute_result, error).

### `jupylink record` — Sync record files

```bash
jupylink record [notebook]
```

Re-merges ipynb cells with execution history. Use when the user edited cells manually in Jupyter Lab.

### `jupylink list-kernels` — Show running kernels

```bash
jupylink list-kernels
```

### `jupylink cleanup-kernels` — Remove stale entries

```bash
jupylink cleanup-kernels
```

Removes registry entries whose connection files no longer exist (e.g. after SIGKILL).

### `jupylink serve` — Start MCP server (Py3.10+)

```bash
jupylink serve                          # stdio mode for Cursor MCP
jupylink serve --port 8765              # HTTP mode
jupylink serve --notebook test.ipynb    # bind to specific notebook
```

Requires `pip install jupylink[mcp]`.

### `jupylink install-kernelspec` — Install kernel into Jupyter

```bash
jupylink install-kernelspec            # user install (default)
jupylink install-kernelspec --system   # system-wide
```

## Agent Workflow

### Step 1: Assess
```bash
jupylink status              # quick overview
jupylink view --errors        # what's broken?
jupylink view --pending       # what can I edit?
```

### Step 2: Edit
```bash
# Edit a pending or empty cell
jupylink write-cell <cell_id> "new code"

# Or create a fresh cell
jupylink create-cell --at 3 --source "x = fn()"
```

### Step 3: Execute
```bash
jupylink execute <cell_id>         # single
jupylink execute <id1> <id2>       # dependent cells together
```

### Step 4: Verify
```bash
jupylink view --cell <cell_id>     # check the cell's new state
jupylink get-output <cell_id>      # check the execution output
```

### Step 5: Sync (if needed)
```bash
jupylink record                    # if user edited in Jupyter Lab
```

## Key Rules

1. **View first, then act** — always `jupylink view` or `jupylink status` before making changes
2. **Never touch `[executed]` cells** — they reflect committed kernel state
3. **Prefer `[empty]` and `[pending]` cells** for new code — they're explicitly editable
4. **Use prefix matching** — `9b779c39` is enough, no need for the full UUID
5. **Execute dependent cells together** — `jupylink execute cell_a cell_b` ensures same kernel
6. **Sync when stale** — after the user manually runs cells, `jupylink record` to refresh

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `JUPYLINK_DEFAULT_NOTEBOOK` | Default notebook path |
| `JUPYLINK_EXEC_TIMEOUT` | Execution timeout in seconds (default: 60) |
| `JUPYLINK_LOCK_TIMEOUT` | File lock timeout (default: 10) |
| `JUPYLINK_NO_REFRESH` | Set to `1` to skip IDE refresh notifications |

# JupyLink

Jupyter kernel proxy that generates agent-friendly execution records from ipynb. Also provides a CLI and MCP server for Cursor integration.

## Features

- Intercepts cell execution and records results (including output by cell_id and execution_count)
- Success cells: raw code
- Error cells: code wrapped in `try/except` with error info as comments
- Unexecuted/empty cells: preserved in layout, marked as editable
- Output: `.py` (for agents) + `.json` (for programs)
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
4. Execute cells as usual — records are written to `{notebook_stem}_record.py` and `{notebook_stem}_record.json`

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

# Cleanup stale kernel registry entries
jupylink cleanup-kernels

# Start MCP server (Cursor auto-starts when configured; manual: -p PORT, -n notebook)
jupylink serve [-p PORT] [-n notebook.ipynb]

# Disable IDE refresh (e.g. in scripts)
jupylink write-cell notebook.ipynb cell_id "content" --no-refresh
# Or: JUPYLINK_NO_REFRESH=1 jupylink write-cell ...
```

### Cursor Integration (MCP)

**正确工作流程**：

1. 配置 MCP 后，Cursor 会在调用工具时**自动启动** MCP 进程；也可在终端手动运行 `jupylink serve` 保持常驻
2. 在 Cursor 中打开 ipynb
3. 为 notebook 选择 **JupyLink** kernel
4. （如 IDE 未自动传入路径）在首个 cell 运行 `%notebook_path ./当前notebook.ipynb`，以注册 kernel 供 CLI 连接
5. 选择 JupyLink 后，**记录功能自动开启**；CLI/MCP 会**连接到同一 kernel**（执行时共享变量状态）

**%notebook_path vs jupylink record**：
- `%notebook_path`：在 kernel 内设置路径，用于记录 + CLI 连接；需在 notebook 中运行
- `jupylink record`：从 CLI 同步 record，合并 ipynb 与已有执行数据；不会覆盖执行历史

配置 `~/.cursor/mcp.json` 或 `.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "jupylink": {
      "command": "jupylink",
      "args": ["serve"]
    }
  }
}
```

- **Windows**（使用 venv 时）：`"command": ".venv\\Scripts\\jupylink.exe"`
- **Linux/macOS**：`"command": ".venv/bin/jupylink"` 或 `"command": "jupylink"`（若已 pip 安装）
- **默认 notebook**：添加 `args: ["serve", "-n", "test.ipynb"]` 或设置 `JUPYLINK_DEFAULT_NOTEBOOK` 环境变量，则工具可省略 `notebook_path` 参数

**MCP 暴露的工具**：

- `jupylink_get_output` — 按 cell_id 和 execution_count 获取 output
- `jupylink_write_cell` — 向 cell 写入内容
- `jupylink_create_cell` — 创建新 cell
- `jupylink_delete_cell` — 删除 cell
- `jupylink_execute_cell` — 执行单个 cell
- `jupylink_execute_cells` — 批量执行多个 cell（同一 kernel，适用于有依赖的 cell）
- `jupylink_list_cells` — 列出所有 cells
- `jupylink_get_record` — 获取 agent 适用的 .py 记录内容
- `jupylink_sync_record` — 同步 record（合并 ipynb 与执行历史，对应 CLI 的 `record`）
- `jupylink_get_status` — 轻量状态摘要（只读，无副作用）

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

### .json

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

## IDE Refresh

When CLI or MCP modifies the ipynb (write-cell, create-cell, delete-cell) or executes a cell, JupyLink requests the IDE (Cursor/VS Code) to refresh the notebook from disk. This uses `cursor` or `code` CLI with `-r` (reuse window).

- **Disable**: `--no-refresh` on CLI, or `JUPYLINK_NO_REFRESH=1` env var
- **Requires**: `cursor` or `code` in PATH (install "Shell Command" from Command Palette)
- **Tip**: Install [Notebook Hot Reload](https://marketplace.visualstudio.com/items?itemName=kdkyum.notebook-hot-reload) for more reliable auto-reload

## Notebook Path

Resolved in order:

1. `%notebook_path /path/to/notebook.ipynb` (magic)
2. `JUPYTER_NOTEBOOK_PATH` env var
3. `JPY_SESSION_NAME` env var (if it ends with .ipynb)
4. **Auto from execute_request** (VS Code/Cursor): when you run a cell, the kernel extracts the notebook path from the cellId metadata (`vscode-notebook-cell:URI#...`). No `%notebook_path` needed.

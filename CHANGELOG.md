# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Changed

- `JUPYLINK_REFRESH_USE_URL` default changed from 1 to 0 (URL-based refresh disabled by default)

## [0.1.0] - 2025-03-19

### Added

- **IDE Refresh improvements**
  - Configurable delay (`JUPYLINK_REFRESH_DELAY`, default 0.2s) before invoking IDE refresh
  - Debouncing: rapid successive operations coalesce into a single refresh
  - Dual refresh method: CLI (`cursor path -r`) + `vscode://file/path` URL scheme
  - Skip refresh for temp paths (`pytest-of-*`, `Temp`, `tmp`) to avoid slowness with test artifacts
- **Configuration**
  - `JUPYLINK_REFRESH_USE_URL`: enable URL-based refresh (default: 0, disabled)
  - `JUPYLINK_LOCK_TIMEOUT`: file lock timeout (default: 10s)
- **Record format**
  - CSV output (`{stem}_record.csv`) for flattened table view
  - MCP resources: `jupylink://record/json`, `jupylink://record/csv`

### Changed

- Kernel: improved notebook path resolution from VS Code/Cursor `execute_request` metadata
- File locking: configurable timeout, better Windows compatibility
- Record manager: enhanced merge logic for execution history

"""Registry mapping notebook paths to kernel connection files.

When a JupyLink kernel starts with a notebook, it registers here.
CLI/MCP can then connect to that same kernel instead of spawning a new one.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from filelock import FileLock

logger = logging.getLogger(__name__)


def _registry_path() -> Path:
    """Path to the registry file.

    - Windows: %APPDATA%/jupylink/
    - macOS:   ~/.jupylink/
    - Linux:   $XDG_DATA_HOME/jupylink/ (default ~/.local/share/jupylink/)
               Falls back to ~/.jupylink/ if it already exists (backward compat).
    """
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", os.path.expanduser("~")))
        dir_path = base / "jupylink"
    elif sys.platform == "darwin":
        dir_path = Path.home() / ".jupylink"
    else:
        # Linux: prefer XDG, but honor existing ~/.jupylink for backward compat
        legacy = Path.home() / ".jupylink"
        if legacy.exists():
            dir_path = legacy
        else:
            xdg_data = os.environ.get("XDG_DATA_HOME", "")
            base = Path(xdg_data) if xdg_data else Path.home() / ".local" / "share"
            dir_path = base / "jupylink"
    dir_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return dir_path / "kernels.json"


def _normalize(path: str | Path) -> str:
    """Normalize notebook path for consistent lookup.

    Uses normcase on Windows so E:\\x and e:\\x map to the same key.
    """
    return os.path.normcase(str(Path(path).resolve()))


def _lock_path() -> Path:
    """Path to the lock file for the registry."""
    return _registry_path().with_suffix(".json.lock")


def _spawn_lock_path() -> Path:
    """Path to the global spawn lock (serializes kernel spawns per machine)."""
    return _registry_path().parent / "spawn.lock"


@contextmanager
def spawn_lock(timeout: float = 30.0) -> Iterator[None]:
    """Acquire exclusive lock before spawning a kernel.

    Ensures only one kernel is spawned per notebook when multiple MCP requests
    arrive concurrently (e.g. without opening the notebook first).
    """
    lock = FileLock(_spawn_lock_path(), timeout=timeout)
    try:
        lock.acquire()
        yield
    finally:
        lock.release()


def _read_registry() -> dict[str, str]:
    """Read the registry from disk."""
    p = _registry_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("kernels", {})
    except Exception:
        return {}


def _write_registry(kernels: dict[str, str]) -> None:
    """Write the registry to disk."""
    p = _registry_path()
    p.write_text(
        json.dumps({"kernels": kernels}, indent=2),
        encoding="utf-8",
    )


def _with_registry_lock(operation):
    """Run a read-modify-write operation under an exclusive lock."""
    lock = FileLock(_lock_path(), timeout=10)
    with lock:
        return operation()


def register(notebook_path: str | Path, connection_file: str) -> None:
    """Register a kernel for the given notebook.

    Called by JupyLink kernel when it has a notebook path.
    """
    nb = _normalize(notebook_path)
    cf = str(Path(connection_file).resolve())

    def _do():
        kernels = _read_registry()
        kernels[nb] = cf
        _write_registry(kernels)

    _with_registry_lock(_do)
    logger.debug("Registered kernel for %s -> %s", nb, cf)


def unregister(notebook_path: str | Path) -> None:
    """Unregister the kernel for the given notebook.

    Called when the kernel shuts down.
    """
    nb = _normalize(notebook_path)

    def _do():
        kernels = _read_registry()
        kernels.pop(nb, None)
        _write_registry(kernels)

    _with_registry_lock(_do)


def get_connection_file(notebook_path: str | Path) -> str | None:
    """Get the connection file for a notebook, if a kernel is registered.

    Returns None if no kernel is registered or the connection file is gone.
    Automatically removes stale entries when connection file is missing.
    """
    nb = _normalize(notebook_path)

    def _do():
        kernels = _read_registry()
        cf = kernels.get(nb)
        if not cf:
            return None
        if not Path(cf).exists():
            kernels.pop(nb, None)
            _write_registry(kernels)
            return None
        return cf

    return _with_registry_lock(_do)


def list_kernels() -> list[dict[str, str]]:
    """List all registered kernels: notebook_path and connection_file.

    Automatically removes stale entries (connection file gone).
    Returns list of {"notebook_path": str, "connection_file": str}.
    """
    def _do():
        kernels = _read_registry()
        result = []
        stale = []
        for nb, cf in kernels.items():
            if not Path(cf).exists():
                stale.append(nb)
                continue
            result.append({"notebook_path": nb, "connection_file": cf})
        if stale:
            for nb in stale:
                kernels.pop(nb, None)
            _write_registry(kernels)
        return result

    return _with_registry_lock(_do)


def cleanup_stale() -> int:
    """Remove registry entries whose connection files no longer exist.

    Call when kernel was killed (SIGKILL) without running atexit.
    Returns the number of entries removed.
    """
    def _do():
        kernels = _read_registry()
        stale = [nb for nb, cf in kernels.items() if not Path(cf).exists()]
        for nb in stale:
            kernels.pop(nb, None)
        if stale:
            _write_registry(kernels)
        return len(stale)

    return _with_registry_lock(_do)

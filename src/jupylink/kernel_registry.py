"""Registry mapping notebook paths to kernel connection files.

When a JupyLink kernel starts with a notebook, it registers here.
CLI/MCP can then connect to that same kernel instead of spawning a new one.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import urllib.parse
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from filelock import FileLock

logger = logging.getLogger(__name__)

# Next to foo.ipynb: foo.jupylink_kernel.json — lets IDE kernel auto-find MCP kernel without env vars.
KERNEL_SIDECAR_SUFFIX = ".jupylink_kernel.json"


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


_SSH_REMOTE_PATH_PREFIX = re.compile(r"^/ssh-remote\+[^/]+")


def _strip_vscode_remote_filesystem_path(path_str: str) -> str:
    """Map Remote SSH / WSL vscode-remote URIs to the on-disk path on that machine.

    VS Code / Cursor may report the same notebook as:
    - ``/share/home/user/x.ipynb`` (plain)
    - ``/ssh-remote+7b22.../share/home/user/x.ipynb`` (authority embedded in path)
    - ``vscode-remote://ssh-remote+7b22.../share/home/user/x.ipynb``

    Registry keys must match across these forms so CLI/MCP reuse the IDE kernel.
    """
    s = path_str.strip()
    if not s:
        return s
    if s.lower().startswith("vscode-remote:"):
        try:
            parsed = urllib.parse.urlparse(s)
            netloc = (parsed.netloc or "").lower()
            if netloc.startswith("ssh-remote+") or netloc.startswith("wsl+"):
                p = urllib.parse.unquote(parsed.path or "")
                p = _fix_windows_leading_slash_drive(p)
                return p if p else s
        except Exception:
            pass
    m = _SSH_REMOTE_PATH_PREFIX.match(s)
    if m:
        tail = s[m.end() :]
        if not tail.startswith("/"):
            tail = f"/{tail}"
        tail = _fix_windows_leading_slash_drive(tail)
        return tail
    return s


def _fix_windows_leading_slash_drive(p: str) -> str:
    """``/C:/Users/...`` is wrong for pathlib on Windows; normalize to ``C:/Users/...``."""
    if os.name != "nt" or len(p) < 4:
        return p
    if p.startswith("/") and p[2] == ":" and p[3] in "/\\":
        return p[1] + ":" + p[3:]
    return p


def _fix_windows_drive_relative(p: str) -> str:
    """``C:Users\\...`` (missing ``\\`` after drive) resolves relative to cwd; make absolute."""
    if os.name != "nt" or len(p) < 3:
        return p
    if p[1] == ":" and p[2] not in "\\/":
        return p[:2] + "\\" + p[2:]
    return p


def _normalize(path: str | Path) -> str:
    """Normalize notebook path for consistent lookup.

    Strips vscode-remote / ssh-remote path prefixes so the same file on a remote
    host shares one registry entry. Uses normcase on Windows so E:\\x and e:\\x
    map to the same key.
    """
    raw = str(path).strip()
    stripped = _strip_vscode_remote_filesystem_path(raw)
    stripped = _fix_windows_drive_relative(stripped)
    return os.path.normcase(str(Path(stripped).resolve()))


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


def _resolved_notebook_file(notebook_path: str | Path) -> Path | None:
    """Filesystem path to the .ipynb for sidecar placement; None if missing or not a file."""
    raw = str(notebook_path).strip()
    stripped = _strip_vscode_remote_filesystem_path(raw)
    try:
        p = Path(stripped).expanduser().resolve()
    except OSError:
        return None
    if p.suffix.lower() != ".ipynb" or not p.is_file():
        return None
    return p


def sidecar_path_for_notebook(notebook_path: str | Path) -> Path | None:
    """Path to the kernel pointer file next to the notebook, if the notebook exists on disk."""
    nb = _resolved_notebook_file(notebook_path)
    if nb is None:
        return None
    return nb.with_name(nb.stem + KERNEL_SIDECAR_SUFFIX)


def _write_kernel_sidecar(notebook_path: str | Path, connection_file: str) -> None:
    sp = sidecar_path_for_notebook(notebook_path)
    if sp is None:
        return
    nb = _resolved_notebook_file(notebook_path)
    data = {
        "connection_file": str(Path(connection_file).resolve()),
        "notebook_path": str(nb) if nb else str(notebook_path),
    }
    try:
        sp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        logger.debug("Could not write kernel sidecar %s", sp, exc_info=True)


def _sidecar_path_from_notebook_path(notebook_path: str | Path) -> Path | None:
    raw = str(notebook_path).strip()
    stripped = _strip_vscode_remote_filesystem_path(raw)
    p = Path(stripped)
    if p.suffix.lower() != ".ipynb":
        return None
    return p.parent / f"{p.stem}{KERNEL_SIDECAR_SUFFIX}"


def _remove_kernel_sidecar(notebook_path: str | Path) -> None:
    sp = _sidecar_path_from_notebook_path(notebook_path)
    if sp is not None and sp.is_file():
        try:
            sp.unlink()
        except OSError:
            pass


def register(notebook_path: str | Path, connection_file: str) -> None:
    """Register a kernel for the given notebook.

    Called by JupyLink kernel when it has a notebook path.
    """
    nb = _normalize(notebook_path)
    cf = str(Path(connection_file).resolve())

    def _do():
        kernels = _read_registry()
        for k in list(kernels):
            if k != nb and _normalize(k) == nb:
                kernels.pop(k, None)
        kernels[nb] = cf
        _write_registry(kernels)

    _with_registry_lock(_do)
    _write_kernel_sidecar(notebook_path, cf)
    logger.debug("Registered kernel for %s -> %s", nb, cf)


def unregister(notebook_path: str | Path) -> None:
    """Unregister the kernel for the given notebook.

    Called when the kernel shuts down.
    """
    nb = _normalize(notebook_path)

    def _do():
        kernels = _read_registry()
        for k in list(kernels):
            if _normalize(k) == nb:
                kernels.pop(k, None)
        _write_registry(kernels)

    _with_registry_lock(_do)
    _remove_kernel_sidecar(notebook_path)


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
            for k, v in kernels.items():
                if _normalize(k) == nb:
                    cf = v
                    break
        if not cf:
            return None
        if not Path(cf).exists():
            to_drop = [k for k in kernels if _normalize(k) == nb]
            for k in to_drop:
                kernels.pop(k, None)
                _remove_kernel_sidecar(k)
            _write_registry(kernels)
            return None
        return cf

    return _with_registry_lock(_do)


def list_kernels() -> list[dict[str, str]]:
    """List all registered kernels: notebook_path and connection_file.

    Automatically removes stale entries (connection file gone).
    Returns list of {"notebook_path": str, "connection_file": str}.
    Notebook paths are canonical (remote URI prefixes stripped).
    """
    def _do():
        kernels = _read_registry()
        by_canon: dict[str, dict[str, str]] = {}
        stale = []
        for nb, cf in kernels.items():
            if not Path(cf).exists():
                stale.append(nb)
                continue
            canon = _normalize(nb)
            by_canon[canon] = {"notebook_path": canon, "connection_file": cf}
        if stale:
            for nb in stale:
                kernels.pop(nb, None)
                _remove_kernel_sidecar(nb)
            _write_registry(kernels)
        return list(by_canon.values())

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
            _remove_kernel_sidecar(nb)
        if stale:
            _write_registry(kernels)
        return len(stale)

    return _with_registry_lock(_do)

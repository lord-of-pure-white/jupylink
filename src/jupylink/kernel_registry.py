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
from typing import Any, Iterator

from filelock import FileLock

logger = logging.getLogger(__name__)

# Next to foo.ipynb: foo.jupylink_kernel.json — lets IDE kernel auto-find MCP kernel without env vars.
KERNEL_SIDECAR_SUFFIX = ".jupylink_kernel.json"

# Single line: absolute ``.ipynb`` path last used by MCP/CLI (same dir as ``kernels.json``).
_LAST_ACTIVE_NOTEBOOK_NAME = "last_active_notebook"


def user_jupylink_dir() -> Path:
    """Per-user state: ``kernels.json``, ``last_active_notebook``, locks.

    Windows: ``%APPDATA%/jupylink/`` · macOS: ``~/.jupylink/`` · Linux: XDG data or ``~/.jupylink/``.
    """
    return _registry_path().parent


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
# Same token, but some clients omit the leading slash (rare).
_SSH_REMOTE_PATH_PREFIX_REL = re.compile(r"^ssh-remote\+[^/]+")


def _strip_vscode_remote_filesystem_path(path_str: str) -> str:
    """Map Remote SSH / WSL vscode-remote URIs to the on-disk path on that machine.

    VS Code / Cursor may report the same notebook as:
    - ``/share/home/user/x.ipynb`` (plain)
    - ``/ssh-remote+7b22.../share/home/user/x.ipynb`` (authority embedded in path)
    - ``vscode-remote://ssh-remote+7b22.../share/home/user/x.ipynb``
    - ``file:///ssh-remote+7b22.../share/.../x.ipynb`` (file URL wrapping the pseudo path)

    Registry keys must match across these forms so CLI/MCP reuse the IDE kernel.
    """
    s = path_str.strip()
    if not s:
        return s
    # Peel file: (so /ssh-remote+... embedded in file:// path is visible to rules below)
    if s.lower().startswith("file:"):
        try:
            parsed = urllib.parse.urlparse(s)
            inner = urllib.parse.unquote(parsed.path or "")
            if inner and inner != s:
                s = inner
        except Exception:
            pass
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
    m2 = _SSH_REMOTE_PATH_PREFIX_REL.match(s)
    if m2:
        tail = s[m2.end() :]
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


_MAX_ACTIVE_NOTEBOOK_WALK = 12


def read_active_notebook_hint(*, cwd: Path | None = None) -> Path | None:
    """Resolve ``.ipynb`` from env or ``.jupylink/active_notebook`` (walk upward from *cwd*).

    Used by MCP default notebook resolution and by the IDE bridge at process start (before
    any ``execute_request``). ``write_active_notebook_hint`` updates these files when
    MCP/CLI runs cells so the IDE can attach without manual env vars.
    """
    raw = os.environ.get("JUPYLINK_ACTIVE_NOTEBOOK", "").strip()
    if raw.endswith(".ipynb"):
        try:
            exp = resolve_notebook_filesystem_path(raw)
            if exp.is_file():
                return exp
        except (OSError, ValueError):
            pass

    fp = os.environ.get("JUPYLINK_ACTIVE_NOTEBOOK_FILE", "").strip()
    if fp:
        try:
            line = Path(fp).expanduser().read_text(encoding="utf-8").splitlines()[0].strip()
            if line.endswith(".ipynb"):
                try:
                    exp = resolve_notebook_filesystem_path(line)
                    if exp.is_file():
                        return exp
                except (OSError, ValueError):
                    pass
        except OSError:
            pass

    try:
        lastp = user_jupylink_dir() / _LAST_ACTIVE_NOTEBOOK_NAME
        if lastp.is_file():
            line = lastp.read_text(encoding="utf-8").splitlines()[0].strip()
            if line.endswith(".ipynb"):
                try:
                    exp = resolve_notebook_filesystem_path(line)
                    if exp.is_file():
                        return exp
                except (OSError, ValueError):
                    pass
    except OSError:
        pass

    cur = (cwd or Path.cwd()).resolve()
    for _ in range(_MAX_ACTIVE_NOTEBOOK_WALK + 1):
        for cand in (cur / ".jupylink" / "active_notebook", cur / "jupylink-active-notebook"):
            try:
                if not cand.is_file():
                    continue
                line = cand.read_text(encoding="utf-8").splitlines()[0].strip()
                if not line.endswith(".ipynb"):
                    continue
                try:
                    exp = resolve_notebook_filesystem_path(line)
                    if exp.is_file():
                        return exp
                except (OSError, ValueError):
                    pass
            except OSError:
                pass
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def write_active_notebook_hint(notebook_path: str | Path) -> None:
    """Record *notebook_path* so the IDE kernel can resolve it before the first cell runs."""
    try:
        p = resolve_notebook_filesystem_path(notebook_path)
    except (OSError, ValueError):
        return
    if not p.is_file() or p.suffix.lower() != ".ipynb":
        return
    line = str(p) + "\n"
    try:
        ud = user_jupylink_dir()
        ud.mkdir(mode=0o700, parents=True, exist_ok=True)
        (ud / _LAST_ACTIVE_NOTEBOOK_NAME).write_text(line, encoding="utf-8")
    except OSError:
        logger.debug("Could not write %s in user jupylink dir", _LAST_ACTIVE_NOTEBOOK_NAME, exc_info=True)
    for base in (p.parent.resolve(), Path.cwd().resolve()):
        try:
            d = base / ".jupylink"
            d.mkdir(parents=True, exist_ok=True)
            (d / "active_notebook").write_text(line, encoding="utf-8")
        except OSError:
            logger.debug("Could not write active notebook hint under %s", base, exc_info=True)


def resolve_notebook_filesystem_path(path: str | Path) -> Path:
    """On-disk path for locks, ipynb, and ``_record.*`` next to the notebook.

    VS Code / Jupyter on Remote SSH may pass paths like
    ``/ssh-remote+<json>/share/home/user/x.ipynb`` or ``vscode-remote://...``.
    Those are not valid ``Path.parent`` segments on the remote filesystem; strip
    the pseudo-prefix before ``resolve()`` so ``mkdir`` / lock files work.
    """
    s = str(path).strip()
    if not s:
        raise ValueError("Empty notebook path")
    # Multiple passes: e.g. file: → /ssh-remote+… → /share/…
    for _ in range(8):
        t = _strip_vscode_remote_filesystem_path(s)
        t = _fix_windows_drive_relative(t)
        if t == s:
            break
        s = t
    p = Path(s).expanduser().resolve()
    # ``resolve()`` can preserve a bogus first segment if the path was already absolute junk
    ps = str(p)
    if "/ssh-remote+" in ps or ps.startswith("ssh-remote+"):
        s2 = _strip_vscode_remote_filesystem_path(ps)
        s2 = _fix_windows_drive_relative(s2)
        if s2 != ps:
            p = Path(s2).expanduser().resolve()
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


def _shutdown_kernel_via_connection_file(connection_file: str) -> None:
    """Ask an ipykernel to shut down (best effort)."""
    try:
        from jupyter_client.blocking.client import BlockingKernelClient
    except ImportError:
        logger.debug("jupyter_client not available; skipping predecessor shutdown")
        return
    kc: Any = None
    try:
        kc = BlockingKernelClient()
        kc.load_connection_file(connection_file)
        kc.start_channels()
        try:
            kc.wait_for_ready(timeout=2.0)
        except Exception:
            pass
        kc.shutdown()
    except Exception:
        logger.debug("Predecessor shutdown failed for %s", connection_file, exc_info=True)
    finally:
        if kc is not None:
            try:
                kc.stop_channels()
            except Exception:
                pass


def register(notebook_path: str | Path, connection_file: str) -> None:
    """Register a kernel for the given notebook.

    Called by JupyLink kernel when it has a notebook path.
    At most one registered connection per notebook; if a different connection was
    registered before, the old kernel is asked to shut down when
    ``JUPYLINK_REGISTER_SHUTDOWN_PREDECESSOR`` is on (default), so stray live kernels
    do not accumulate for the same ``.ipynb``.
    """
    nb = _normalize(notebook_path)
    cf = str(Path(connection_file).resolve())
    shutdown_pred = os.environ.get("JUPYLINK_REGISTER_SHUTDOWN_PREDECESSOR", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )

    def _do():
        kernels = _read_registry()
        old_cf: str | None = None
        for k, v in kernels.items():
            if _normalize(k) == nb:
                old_cf = v
                break
        if (
            shutdown_pred
            and old_cf
            and Path(old_cf).resolve() != Path(cf).resolve()
            and Path(old_cf).is_file()
        ):
            _shutdown_kernel_via_connection_file(old_cf)
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

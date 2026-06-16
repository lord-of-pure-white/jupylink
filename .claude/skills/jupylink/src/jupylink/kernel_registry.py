"""Registry mapping notebook paths to kernel connection files.

When a JupyLink kernel starts with a notebook, it registers here.
CLI/MCP can then connect to that same kernel instead of spawning a new one.
"""

import json
import logging
import os
import re
import sys
import time
from contextlib import contextmanager

try:
    from urllib.parse import urlparse as _urlparse, unquote as _unquote
except ImportError:
    from urlparse import urlparse as _urlparse
    from urllib import unquote as _unquote

try:
    from pathlib import Path
except ImportError:
    Path = None  # Py2

logger = logging.getLogger(__name__)

# Next to foo.ipynb: foo.jupylink_kernel.json
KERNEL_SIDECAR_SUFFIX = ".jupylink_kernel.json"

# Single line: absolute .ipynb path last used by MCP/CLI (same dir as kernels.json).
_LAST_ACTIVE_NOTEBOOK_NAME = "last_active_notebook"


# ---------------------------------------------------------------------------
# Simple file lock (cross-process, compatible with Py2 and Py3)
# ---------------------------------------------------------------------------
def _acquire_lock(lock_path, timeout):
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.close(fd)
            return
        except OSError:
            if time.time() >= deadline:
                raise RuntimeError(
                    "Failed to acquire lock on {} after {}s".format(lock_path, timeout)
                )
            time.sleep(0.1)


def _release_lock(lock_path):
    try:
        os.unlink(lock_path)
    except OSError:
        pass


@contextmanager
def _locked(lock_path, timeout):
    _acquire_lock(lock_path, timeout)
    try:
        yield
    finally:
        _release_lock(lock_path)


# ---------------------------------------------------------------------------
# Path helpers (os.path, works on Py2 and Py3)
# ---------------------------------------------------------------------------
def _path_join(base, *parts):
    p = str(base)
    for part in parts:
        p = os.path.join(p, str(part))
    return p


def _path_stem(p):
    return os.path.splitext(os.path.basename(str(p)))[0]


def _path_suffix(p):
    return os.path.splitext(str(p))[1]


def _path_parent(p):
    return os.path.dirname(str(p))


def _path_exists(p):
    return os.path.exists(str(p))


def _path_isfile(p):
    return os.path.isfile(str(p))


def _mkdir_p(path, mode=0o700):
    p = str(path)
    if not os.path.isdir(p):
        try:
            os.makedirs(p, mode)
        except OSError:
            if not os.path.isdir(p):
                raise


def _read_text(p):
    with open(str(p), "r", encoding="utf-8") as fh:
        return fh.read()


def _write_text(p, s):
    with open(str(p), "w", encoding="utf-8") as fh:
        fh.write(s)


def _resolve_path(p):
    return os.path.abspath(os.path.expanduser(str(p)))


def _normcase(p):
    if os.name == "nt":
        return str(p).lower()
    return str(p)


def _expanduser(p):
    return os.path.expanduser(str(p))


# ---------------------------------------------------------------------------
def _as_path(p):
    """Return Path on Py3 (for callers that expect pathlib), str on Py2."""
    if p is None:
        return None
    if sys.version_info >= (3,) and Path is not None:
        return Path(p)
    return str(p)


def user_jupylink_dir():
    """Per-user state directory: kernels.json, last_active_notebook, locks."""
    return _as_path(_path_parent(_registry_path()))


def _registry_path():
    """Path to the registry file.

    - Windows: %APPDATA%/jupylink/
    - macOS:   ~/.jupylink/
    - Linux:   $XDG_DATA_HOME/jupylink/ (default ~/.local/share/jupylink/)
               Falls back to ~/.jupylink/ if it already exists.
    """
    if os.name == "nt":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        dir_path = os.path.join(base, "jupylink")
    elif sys.platform == "darwin":
        dir_path = os.path.join(os.path.expanduser("~"), ".jupylink")
    else:
        legacy = os.path.join(os.path.expanduser("~"), ".jupylink")
        if os.path.isdir(legacy):
            dir_path = legacy
        else:
            xdg_data = os.environ.get("XDG_DATA_HOME", "")
            base = xdg_data if xdg_data else os.path.join(os.path.expanduser("~"), ".local", "share")
            dir_path = os.path.join(base, "jupylink")
    _mkdir_p(dir_path, 0o700)
    return _as_path(os.path.join(dir_path, "kernels.json"))


# ---------------------------------------------------------------------------
# Path cleanup (vscode-remote / ssh-remote prefixes)
# ---------------------------------------------------------------------------
_SSH_REMOTE_PATH_PREFIX = re.compile(r"^/ssh-remote\+[^/]+")
_SSH_REMOTE_PATH_PREFIX_REL = re.compile(r"^ssh-remote\+[^/]+")


def _fix_windows_leading_slash_drive(p):
    if os.name != "nt" or len(p) < 4:
        return p
    if p.startswith("/") and p[2] == ":" and p[3] in "/\\":
        return p[1] + ":" + p[3:]
    return p


def _fix_windows_drive_relative(p):
    if os.name != "nt" or len(p) < 3:
        return p
    if p[1] == ":" and p[2] not in "\\/":
        return p[:2] + "\\" + p[2:]
    return p


def _strip_vscode_remote_filesystem_path(path_str):
    s = path_str.strip()
    if not s:
        return s
    if s.lower().startswith("file:"):
        try:
            parsed = _urlparse(s)
            inner = _unquote(parsed.path or "")
            if inner and inner != s:
                s = inner
        except Exception:
            pass
    if s.lower().startswith("vscode-remote:"):
        try:
            parsed = _urlparse(s)
            netloc = (parsed.netloc or "").lower()
            if netloc.startswith("ssh-remote+") or netloc.startswith("wsl+"):
                p = _unquote(parsed.path or "")
                p = _fix_windows_leading_slash_drive(p)
                return p if p else s
        except Exception:
            pass
    m = _SSH_REMOTE_PATH_PREFIX.match(s)
    if m:
        tail = s[m.end():]
        if not tail.startswith("/"):
            tail = "/{}".format(tail)
        tail = _fix_windows_leading_slash_drive(tail)
        return tail
    m2 = _SSH_REMOTE_PATH_PREFIX_REL.match(s)
    if m2:
        tail = s[m2.end():]
        if not tail.startswith("/"):
            tail = "/{}".format(tail)
        tail = _fix_windows_leading_slash_drive(tail)
        return tail
    return s


def resolve_notebook_filesystem_path(path):
    s = str(path).strip()
    if not s:
        raise ValueError("Empty notebook path")
    for _ in range(8):
        t = _strip_vscode_remote_filesystem_path(s)
        t = _fix_windows_drive_relative(t)
        if t == s:
            break
        s = t
    p = _resolve_path(s)
    ps = str(p)
    if "/ssh-remote+" in ps or ps.startswith("ssh-remote+"):
        s2 = _strip_vscode_remote_filesystem_path(ps)
        s2 = _fix_windows_drive_relative(s2)
        if s2 != ps:
            p = _resolve_path(s2)
    return _as_path(p)


def _normalize(path):
    raw = str(path).strip()
    stripped = _strip_vscode_remote_filesystem_path(raw)
    stripped = _fix_windows_drive_relative(stripped)
    return _normcase(_resolve_path(stripped))


# ---------------------------------------------------------------------------
# Registry lock paths
# ---------------------------------------------------------------------------
def _lock_path():
    reg = str(_registry_path())
    return os.path.splitext(reg)[0] + ".json.lock"


def _spawn_lock_path():
    return _path_join(_path_parent(_registry_path()), "spawn.lock")


# ---------------------------------------------------------------------------
# Active notebook hints
# ---------------------------------------------------------------------------
_MAX_ACTIVE_NOTEBOOK_WALK = 12


def read_active_notebook_hint(cwd=None):
    raw = os.environ.get("JUPYLINK_ACTIVE_NOTEBOOK", "").strip()
    if raw.endswith(".ipynb"):
        try:
            exp = resolve_notebook_filesystem_path(raw)
            if _path_isfile(exp):
                return exp
        except (OSError, ValueError):
            pass

    fp = os.environ.get("JUPYLINK_ACTIVE_NOTEBOOK_FILE", "").strip()
    if fp:
        try:
            line = _read_text(os.path.expanduser(fp)).splitlines()[0].strip()
            if line.endswith(".ipynb"):
                try:
                    exp = resolve_notebook_filesystem_path(line)
                    if _path_isfile(exp):
                        return exp
                except (OSError, ValueError):
                    pass
        except OSError:
            pass

    try:
        lastp = _path_join(user_jupylink_dir(), _LAST_ACTIVE_NOTEBOOK_NAME)
        if _path_isfile(lastp):
            line = _read_text(lastp).splitlines()[0].strip()
            if line.endswith(".ipynb"):
                try:
                    exp = resolve_notebook_filesystem_path(line)
                    if _path_isfile(exp):
                        return exp
                except (OSError, ValueError):
                    pass
    except OSError:
        pass

    cur = _resolve_path(cwd or os.getcwd())
    for _ in range(_MAX_ACTIVE_NOTEBOOK_WALK + 1):
        for cand_name in (".jupylink/active_notebook", "jupylink-active-notebook"):
            cand = _path_join(cur, cand_name)
            try:
                if not _path_isfile(cand):
                    continue
                line = _read_text(cand).splitlines()[0].strip()
                if not line.endswith(".ipynb"):
                    continue
                try:
                    exp = resolve_notebook_filesystem_path(line)
                    if _path_isfile(exp):
                        return exp
                except (OSError, ValueError):
                    pass
            except OSError:
                pass
        if cur == _path_parent(cur):
            break
        cur = _path_parent(cur)
    return None


def write_active_notebook_hint(notebook_path):
    try:
        p = resolve_notebook_filesystem_path(notebook_path)
    except (OSError, ValueError):
        return
    p_str = str(p)
    if not _path_isfile(p_str) or _path_suffix(p_str).lower() != ".ipynb":
        return
    line = p_str + "\n"
    try:
        ud = user_jupylink_dir()
        _mkdir_p(ud, 0o700)
        _write_text(_path_join(ud, _LAST_ACTIVE_NOTEBOOK_NAME), line)
    except OSError:
        logger.debug("Could not write %s in user jupylink dir", _LAST_ACTIVE_NOTEBOOK_NAME, exc_info=True)
    for base in (_path_parent(p_str), os.getcwd()):
        try:
            d = _path_join(base, ".jupylink")
            _mkdir_p(d)
            _write_text(_path_join(d, "active_notebook"), line)
        except OSError:
            logger.debug("Could not write active notebook hint under %s", base, exc_info=True)


# ---------------------------------------------------------------------------
# Spawn lock
# ---------------------------------------------------------------------------
@contextmanager
def spawn_lock(timeout=30.0):
    with _locked(_spawn_lock_path(), timeout):
        yield


# ---------------------------------------------------------------------------
# Registry read/write
# ---------------------------------------------------------------------------
def _read_registry():
    p = _registry_path()
    if not _path_exists(p):
        return {}
    try:
        data = json.loads(_read_text(p))
        return data.get("kernels", {})
    except Exception:
        return {}


def _write_registry(kernels):
    p = _registry_path()
    _write_text(p, json.dumps({"kernels": kernels}, indent=2))


def _with_registry_lock(operation):
    with _locked(_lock_path(), 10):
        return operation()


# ---------------------------------------------------------------------------
# Sidecar helpers
# ---------------------------------------------------------------------------
def _resolved_notebook_file(notebook_path):
    raw = str(notebook_path).strip()
    stripped = _strip_vscode_remote_filesystem_path(raw)
    try:
        p = _resolve_path(stripped)
    except OSError:
        return None
    if _path_suffix(p).lower() != ".ipynb" or not _path_isfile(p):
        return None
    return p


def sidecar_path_for_notebook(notebook_path):
    nb = _resolved_notebook_file(notebook_path)
    if nb is None:
        return None
    stem = _path_stem(nb)
    return _as_path(_path_join(_path_parent(nb), "{}{}".format(stem, KERNEL_SIDECAR_SUFFIX)))


def _write_kernel_sidecar(notebook_path, connection_file):
    sp = sidecar_path_for_notebook(notebook_path)
    if sp is None:
        return
    nb = _resolved_notebook_file(notebook_path)
    data = {
        "connection_file": _resolve_path(connection_file),
        "notebook_path": str(nb) if nb else str(notebook_path),
    }
    try:
        _write_text(sp, json.dumps(data, indent=2))
    except OSError:
        logger.debug("Could not write kernel sidecar %s", sp, exc_info=True)


def _sidecar_path_from_notebook_path(notebook_path):
    raw = str(notebook_path).strip()
    stripped = _strip_vscode_remote_filesystem_path(raw)
    p = str(stripped)
    if _path_suffix(p).lower() != ".ipynb":
        return None
    stem = _path_stem(p)
    return _path_join(_path_parent(p), "{}{}".format(stem, KERNEL_SIDECAR_SUFFIX))


def _remove_kernel_sidecar(notebook_path):
    sp = _sidecar_path_from_notebook_path(notebook_path)
    if sp is not None and _path_isfile(sp):
        try:
            os.unlink(str(sp))
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Registry public API
# ---------------------------------------------------------------------------
def _shutdown_kernel_via_connection_file(connection_file):
    try:
        from jupyter_client.blocking.client import BlockingKernelClient
    except ImportError:
        logger.debug("jupyter_client not available; skipping predecessor shutdown")
        return
    kc = None
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


def register(notebook_path, connection_file):
    nb = _normalize(notebook_path)
    cf = _resolve_path(connection_file)
    shutdown_pred = os.environ.get("JUPYLINK_REGISTER_SHUTDOWN_PREDECESSOR", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )

    def _do():
        kernels = _read_registry()
        old_cf = None
        for k, v in kernels.items():
            if _normalize(k) == nb:
                old_cf = v
                break
        if (shutdown_pred and old_cf
                and _resolve_path(old_cf) != _resolve_path(cf)
                and _path_isfile(old_cf)):
            _shutdown_kernel_via_connection_file(old_cf)
        for k in list(kernels):
            if k != nb and _normalize(k) == nb:
                kernels.pop(k, None)
        kernels[nb] = cf
        _write_registry(kernels)

    _with_registry_lock(_do)
    _write_kernel_sidecar(notebook_path, cf)
    logger.debug("Registered kernel for %s -> %s", nb, cf)


def unregister(notebook_path):
    nb = _normalize(notebook_path)

    def _do():
        kernels = _read_registry()
        for k in list(kernels):
            if _normalize(k) == nb:
                kernels.pop(k, None)
        _write_registry(kernels)

    _with_registry_lock(_do)
    _remove_kernel_sidecar(notebook_path)


def get_connection_file(notebook_path):
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
        if not _path_exists(cf):
            to_drop = [k for k in kernels if _normalize(k) == nb]
            for k in to_drop:
                kernels.pop(k, None)
                _remove_kernel_sidecar(k)
            _write_registry(kernels)
            return None
        return cf

    return _with_registry_lock(_do)


def probe_kernel(connection_file, timeout=1.0):
    """Check whether a kernel is alive via heartbeat. Returns True/False.

    Does NOT modify the registry. Lightweight — uses a short timeout.
    """
    try:
        from jupyter_client.blocking.client import BlockingKernelClient
    except ImportError:
        logger.debug("jupyter_client not available; skipping probe")
        return True  # assume alive if we can't check

    kc = None
    try:
        kc = BlockingKernelClient()
        kc.load_connection_file(connection_file)
        kc.start_channels()
        kc.wait_for_ready(timeout=timeout)
        return True
    except Exception:
        logger.debug("Probe failed for %s", connection_file, exc_info=True)
        return False
    finally:
        if kc is not None:
            try:
                kc.stop_channels()
            except Exception:
                pass


def list_kernels():
    def _do():
        kernels = _read_registry()
        by_canon = {}
        stale = []
        for nb, cf in kernels.items():
            if not _path_exists(cf):
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

    entries = _with_registry_lock(_do)
    for e in entries:
        e["alive"] = probe_kernel(e["connection_file"], timeout=1.0)
    return entries


def cleanup_stale():
    def _do():
        kernels = _read_registry()
        stale = [nb for nb, cf in kernels.items() if not _path_exists(cf)]
        for nb in stale:
            kernels.pop(nb, None)
            _remove_kernel_sidecar(nb)
        if stale:
            _write_registry(kernels)
        return len(stale)

    return _with_registry_lock(_do)

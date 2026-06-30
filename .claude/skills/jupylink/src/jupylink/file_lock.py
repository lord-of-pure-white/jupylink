"""File locking utilities for notebook operations.

Two-layer locking:
1. ``threading.Lock`` — same-process threads never contend on the file system.
2. PID-aware lock file — cross-process safety; stale locks from dead PIDs are broken.
"""

import logging
import os
import threading
import time
from contextlib import contextmanager

from .kernel_registry import resolve_notebook_filesystem_path

logger = logging.getLogger(__name__)

try:
    ProcessLookupError
except NameError:
    ProcessLookupError = OSError

# Per-lock-path state (guarded by _state_lock)
_state_lock = threading.Lock()
_state: dict[str, dict] = {}  # lock_path -> {"lock": threading.Lock, "refs": int}


def _get_lock_timeout():
    try:
        return float(os.environ.get("JUPYLINK_LOCK_TIMEOUT", "10"))
    except (ValueError, TypeError):
        return 10.0


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _read_lock_pid(fpath):
    try:
        with open(fpath, "r") as fh:
            return int(fh.read(128).strip())
    except (ValueError, OSError, IOError):
        return None


def _write_lock_file(fpath):
    """Create lock file with our PID. Raises OSError if it already exists."""
    fd = os.open(fpath, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o644)
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
    finally:
        os.close(fd)


def _acquire_file_lock(fpath, timeout):
    """Block until we own the cross-process lock, breaking stale locks."""
    deadline = time.time() + timeout
    our_pid = os.getpid()
    while True:
        try:
            _write_lock_file(fpath)
            return
        except OSError:
            pid = _read_lock_pid(fpath)
            if pid is not None and pid != our_pid and not _pid_alive(pid):
                logger.info("Breaking stale lock %s (PID %s dead)", fpath, pid)
                try:
                    os.unlink(fpath)
                except OSError:
                    pass
                continue
            if time.time() >= deadline:
                holder = "PID {}".format(pid) if pid else "unknown"
                raise RuntimeError(
                    "Failed to acquire lock on {} after {}s (held by {})".format(
                        fpath, timeout, holder
                    )
                )
            time.sleep(0.1)


def _release_file_lock(fpath):
    """Release the cross-process lock (only if we own it)."""
    try:
        pid = _read_lock_pid(fpath)
        if pid == os.getpid():
            os.unlink(fpath)
    except OSError:
        pass


@contextmanager
def notebook_lock(notebook_path, timeout=None):
    """Exclusive lock across threads (in-process) and processes (file lock)."""
    path = resolve_notebook_filesystem_path(notebook_path)
    stem = os.path.splitext(os.path.basename(str(path)))[0]
    fpath = os.path.join(os.path.dirname(str(path)), ".{}.lock".format(stem))
    tout = timeout if timeout is not None else _get_lock_timeout()

    with _state_lock:
        if fpath not in _state:
            _state[fpath] = {"lock": threading.Lock(), "refs": 0}
        entry = _state[fpath]

    with entry["lock"]:
        with _state_lock:
            entry["refs"] += 1
            if entry["refs"] == 1:
                # First thread: acquire the file lock
                _acquire_file_lock(fpath, tout)

        try:
            yield
        finally:
            with _state_lock:
                entry["refs"] -= 1
                if entry["refs"] == 0:
                    # Last thread: release the file lock
                    _release_file_lock(fpath)

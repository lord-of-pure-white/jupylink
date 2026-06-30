"""File locking utilities for notebook operations.

PID-aware lock files for cross-process coordination. If a process dies
without releasing its lock, subsequent waiters detect the stale PID and
automatically break the lock.
"""

import logging
import os
import time
from contextlib import contextmanager

from .kernel_registry import resolve_notebook_filesystem_path

logger = logging.getLogger(__name__)


def _get_lock_timeout():
    """Lock timeout in seconds. Configurable via JUPYLINK_LOCK_TIMEOUT."""
    try:
        return float(os.environ.get("JUPYLINK_LOCK_TIMEOUT", "10"))
    except (ValueError, TypeError):
        return 10.0


try:
    ProcessLookupError
except NameError:
    ProcessLookupError = OSError


def _pid_alive(pid):
    """Check whether a process with the given PID is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _read_lock_pid(lock_path):
    """Read the PID stored in a lock file. Returns None if unreadable."""
    try:
        with open(lock_path, "r") as fh:
            raw = fh.read(128)
        return int(raw.strip())
    except (ValueError, OSError):
        return None


def _write_lock(lock_path):
    """Create lock file with current PID. Returns True on success."""
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o644)
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
        finally:
            os.close(fd)
        return True
    except OSError:
        return False


def _simple_lock(lock_path, timeout):
    """Acquire lock. Steals it if the previous holder's PID is dead."""
    deadline = time.time() + timeout
    while True:
        if _write_lock(lock_path):
            return

        # Lock exists — check if the holder is still alive
        pid = _read_lock_pid(lock_path)
        if pid is not None and pid != os.getpid() and not _pid_alive(pid):
            # Stale lock from dead process — break it
            logger.info(
                "Breaking stale lock %s (PID %d is dead)", lock_path, pid
            )
            try:
                os.unlink(lock_path)
            except OSError:
                pass
            continue  # retry acquisition immediately

        if time.time() >= deadline:
            holder = "PID {}".format(pid) if pid else "unknown"
            raise RuntimeError(
                "Failed to acquire lock on {} after {}s (held by {})".format(
                    lock_path, timeout, holder
                )
            )
        time.sleep(0.1)


def _simple_unlock(lock_path):
    """Release a lock file (only if we hold it)."""
    try:
        pid = _read_lock_pid(lock_path)
        if pid == os.getpid():
            os.unlink(lock_path)
        # If it's not our lock, don't touch it
    except OSError:
        pass


@contextmanager
def notebook_lock(notebook_path, timeout=None):
    """Acquire an exclusive lock for operations on the given notebook.

    Lock file: .{stem}.lock next to the notebook.
    Automatically breaks stale locks from dead processes.
    """
    path = resolve_notebook_filesystem_path(notebook_path)
    stem = os.path.splitext(os.path.basename(str(path)))[0]
    lock_path = os.path.join(os.path.dirname(str(path)), ".{}.lock".format(stem))
    tout = timeout if timeout is not None else _get_lock_timeout()
    _simple_lock(lock_path, tout)
    try:
        yield
    finally:
        _simple_unlock(lock_path)

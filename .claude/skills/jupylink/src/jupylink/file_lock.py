"""File locking utilities for notebook operations.

Provides per-notebook locks to prevent concurrent read-modify-write corruption
on .ipynb and _record.json files.

Uses a simple lock-file approach (compatible with Python 2 and 3) instead of
the filelock library which dropped Py2 support in 3.x.
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


def _simple_lock(lock_path, timeout):
    """Acquire a lock file. Returns True on success, raises on timeout."""
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


def _simple_unlock(lock_path):
    """Release a lock file."""
    try:
        os.unlink(lock_path)
    except OSError:
        pass


@contextmanager
def notebook_lock(notebook_path, timeout=None):
    """Acquire an exclusive lock for operations on the given notebook.

    Lock file is placed next to the notebook as .{stem}.lock.
    Covers both .ipynb and _record.json writes.
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

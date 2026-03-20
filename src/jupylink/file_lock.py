"""File locking utilities for notebook operations.

Provides per-notebook locks to prevent concurrent read-modify-write corruption
on .ipynb and _record.json files.

On Windows, filelock uses msvcrt.locking() which has ~1s delay per failed
acquisition. Use JUPYLINK_LOCK_TIMEOUT to tune (default 10s).
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from filelock import FileLock

from .kernel_registry import resolve_notebook_filesystem_path

logger = logging.getLogger(__name__)


def _get_lock_timeout() -> float:
    """Lock timeout in seconds. Configurable via JUPYLINK_LOCK_TIMEOUT."""
    try:
        return float(os.environ.get("JUPYLINK_LOCK_TIMEOUT", "10"))
    except (ValueError, TypeError):
        return 10.0


@contextmanager
def notebook_lock(notebook_path: str | Path, timeout: float | None = None) -> Iterator[None]:
    """Acquire an exclusive lock for operations on the given notebook.

    Lock file is placed next to the notebook as .{stem}.lock.
    Covers both .ipynb and _record.json writes.
    """
    path = resolve_notebook_filesystem_path(notebook_path)
    lock_path = path.parent / f".{path.stem}.lock"
    tout = timeout if timeout is not None else _get_lock_timeout()
    lock = FileLock(lock_path, timeout=tout)
    try:
        lock.acquire()
        yield
    finally:
        lock.release()

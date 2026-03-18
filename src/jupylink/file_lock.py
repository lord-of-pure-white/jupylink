"""File locking utilities for notebook operations.

Provides per-notebook locks to prevent concurrent read-modify-write corruption
on .ipynb and _record.json files.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from filelock import FileLock

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30


@contextmanager
def notebook_lock(notebook_path: str | Path, timeout: float = _DEFAULT_TIMEOUT) -> Iterator[None]:
    """Acquire an exclusive lock for operations on the given notebook.

    Lock file is placed next to the notebook as .{stem}.lock.
    Covers both .ipynb and _record.json writes.
    """
    path = Path(notebook_path).resolve()
    lock_path = path.parent / f".{path.stem}.lock"
    lock = FileLock(lock_path, timeout=timeout)
    try:
        lock.acquire()
        yield
    finally:
        lock.release()

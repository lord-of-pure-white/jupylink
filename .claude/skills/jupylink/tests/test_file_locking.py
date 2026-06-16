"""Tests for file locking to prevent concurrent write corruption."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import nbformat
import pytest

from jupylink.file_lock import notebook_lock
from jupylink.ipynb_ops import write_cell, create_cell, delete_cell, list_cells


class TestNotebookLock:
    """Test the notebook_lock context manager."""

    def test_lock_creates_lock_file(self, tmp_notebook: Path) -> None:
        lock_path = tmp_notebook.parent / f".{tmp_notebook.stem}.lock"
        assert not lock_path.exists()
        with notebook_lock(tmp_notebook):
            assert lock_path.exists()

    def test_lock_is_exclusive(self, tmp_notebook: Path) -> None:
        """Two threads contending on the same lock should serialize."""
        results: list[str] = []

        def worker(name: str, delay: float) -> None:
            with notebook_lock(tmp_notebook):
                results.append(f"{name}_enter")
                time.sleep(delay)
                results.append(f"{name}_exit")

        t1 = threading.Thread(target=worker, args=("A", 0.3))
        t2 = threading.Thread(target=worker, args=("B", 0.1))
        t1.start()
        time.sleep(0.05)  # ensure A gets the lock first
        t2.start()
        t1.join()
        t2.join()

        assert results == ["A_enter", "A_exit", "B_enter", "B_exit"]


class TestConcurrentIpynbOps:
    """Test that ipynb operations don't corrupt the file under concurrent access."""

    def test_concurrent_writes_preserve_data(self, tmp_notebook: Path) -> None:
        """Multiple threads writing to different cells shouldn't lose data."""
        nb = nbformat.read(tmp_notebook, as_version=nbformat.NO_CONVERT)
        code_cells = [c for c in nb.cells if c.cell_type == "code"]
        cell_ids = [c.id for c in code_cells]

        errors: list[Exception] = []

        def write_to_cell(cell_id: str, content: str) -> None:
            try:
                write_cell(tmp_notebook, cell_id, content)
            except Exception as e:
                errors.append(e)

        threads = []
        for i, cid in enumerate(cell_ids):
            t = threading.Thread(target=write_to_cell, args=(cid, f"updated_{i}"))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent writes: {errors}"

        nb_after = nbformat.read(tmp_notebook, as_version=nbformat.NO_CONVERT)
        code_cells_after = [c for c in nb_after.cells if c.cell_type == "code"]
        sources = [c.source if isinstance(c.source, str) else "".join(c.source)
                   for c in code_cells_after]
        for i in range(len(cell_ids)):
            assert f"updated_{i}" in sources, f"Cell {i} content lost after concurrent writes"

    def test_concurrent_create_and_delete(self, tmp_notebook: Path) -> None:
        """Creating and deleting cells concurrently shouldn't corrupt the notebook."""
        initial_cells = list_cells(tmp_notebook)
        initial_count = len(initial_cells)

        created_ids: list[str] = []
        lock = threading.Lock()

        def create_one(idx: int) -> None:
            cid = create_cell(tmp_notebook, source=f"new_cell_{idx}")
            if cid:
                with lock:
                    created_ids.append(cid)

        threads = [threading.Thread(target=create_one, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(created_ids) == 5
        cells_after = list_cells(tmp_notebook)
        assert len(cells_after) == initial_count + 5

        # Clean up: delete the created cells
        for cid in created_ids:
            assert delete_cell(tmp_notebook, cid)

        cells_final = list_cells(tmp_notebook)
        assert len(cells_final) == initial_count

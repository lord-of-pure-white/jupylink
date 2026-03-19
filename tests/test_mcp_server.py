"""Tests for MCP server tools: get_record, sync_record, get_status, execute."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from unittest import mock

import nbformat
import pytest

from jupylink import mcp_server
from jupylink.mcp_server import (
    _resource_record_csv,
    _resource_record_json,
    jupylink_execute_cell,
    jupylink_execute_cells,
    jupylink_get_record,
    jupylink_get_status,
    jupylink_list_cells,
    jupylink_list_kernels,
    jupylink_sync_record,
)
from jupylink.record_manager import RecordManager


@pytest.fixture(autouse=True)
def _disable_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable IDE refresh during tests."""
    monkeypatch.setenv("JUPYLINK_NO_REFRESH", "1")


class TestGetRecord:
    """Fix #5: get_record should be read-only when record file already exists."""

    def test_reads_existing_record_without_writing(
        self, tmp_notebook_with_record: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(mcp_server, "_bound_notebook", tmp_notebook_with_record)

        rm = RecordManager(tmp_notebook_with_record)
        rm.load_from_record_file()
        rm.write_record()

        py_path = tmp_notebook_with_record.parent / f"{tmp_notebook_with_record.stem}_record.py"
        original_content = py_path.read_text(encoding="utf-8")
        original_mtime = py_path.stat().st_mtime

        import time
        time.sleep(0.05)

        result = jupylink_get_record()
        assert "x = 42" in result
        assert py_path.stat().st_mtime == original_mtime

    def test_generates_record_if_missing(
        self, tmp_notebook: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(mcp_server, "_bound_notebook", tmp_notebook)

        py_path = tmp_notebook.parent / f"{tmp_notebook.stem}_record.py"
        assert not py_path.exists()

        result = jupylink_get_record()
        assert "x = 42" in result
        assert py_path.exists()


class TestSyncRecord:
    """Fix #5: sync_record should explicitly re-merge and rewrite."""

    def test_sync_rewrites_record(
        self, tmp_notebook_with_record: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(mcp_server, "_bound_notebook", tmp_notebook_with_record)

        result = json.loads(jupylink_sync_record())
        assert result["status"] == "ok"

        py_path = tmp_notebook_with_record.parent / f"{tmp_notebook_with_record.stem}_record.py"
        assert py_path.exists()


class TestMCPResources:
    """MCP resources for _record.json and _record.csv."""

    def test_resource_record_json(
        self, tmp_notebook_with_record: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(mcp_server, "_bound_notebook", tmp_notebook_with_record)
        result = _resource_record_json()
        data = json.loads(result)
        assert "notebook_path" in data
        assert "execution_log" in data
        assert "cells" in data
        assert any(c.get("code") == "x = 42" for c in data["cells"])

    def test_resource_record_csv(
        self, tmp_notebook_with_record: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(mcp_server, "_bound_notebook", tmp_notebook_with_record)
        result = _resource_record_csv()
        assert "id,cell_type,status" in result
        assert "x = 42" in result

    def test_resource_record_json_no_notebook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(mcp_server, "_bound_notebook", None)
        result = _resource_record_json()
        data = json.loads(result)
        assert "error" in data
        assert "No notebook bound" in data["error"]

    def test_resource_record_csv_no_notebook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(mcp_server, "_bound_notebook", None)
        result = _resource_record_csv()
        assert "error" in result
        assert "No notebook bound" in result


class TestGetStatus:
    """Fix #10: Lightweight status query without side effects."""

    def test_status_from_record_json(
        self, tmp_notebook_with_record: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(mcp_server, "_bound_notebook", tmp_notebook_with_record)

        result = json.loads(jupylink_get_status())
        assert isinstance(result, list)
        assert len(result) > 0

        statuses = {c["status"] for c in result}
        assert "ok" in statuses

        for cell in result:
            assert "id" in cell
            assert "status" in cell
            assert "editable" in cell
            assert "source_preview" in cell

    def test_status_fallback_without_record(
        self, tmp_notebook: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no _record.json exists, falls back to listing cells from ipynb."""
        monkeypatch.setattr(mcp_server, "_bound_notebook", tmp_notebook)

        result = json.loads(jupylink_get_status())
        assert isinstance(result, list)
        assert len(result) > 0
        # Without record, all cells are pending or empty
        for cell in result:
            assert cell["status"] in ("pending", "empty")
            assert cell["editable"] is True

    def test_status_is_readonly(
        self, tmp_notebook_with_record: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_status should not modify any files."""
        monkeypatch.setattr(mcp_server, "_bound_notebook", tmp_notebook_with_record)

        json_path = tmp_notebook_with_record.parent / f"{tmp_notebook_with_record.stem}_record.json"
        mtime_before = json_path.stat().st_mtime

        import time
        time.sleep(0.05)

        jupylink_get_status()
        assert json_path.stat().st_mtime == mtime_before


class TestListKernels:
    """jupylink_list_kernels returns running kernels with notebook_path and connection_file."""

    def test_returns_valid_json(self) -> None:
        """list_kernels returns JSON array (may be empty)."""
        result = jupylink_list_kernels()
        data = json.loads(result)
        assert isinstance(data, list)
        for item in data:
            assert "notebook_path" in item
            assert "connection_file" in item


class TestMCPExecute:
    """MCP execute_cell and execute_cells via jupylink_execute_* tools."""

    def test_execute_single_cell(
        self, tmp_notebook: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Execute one cell via jupylink_execute_cell and verify output."""
        monkeypatch.setattr(mcp_server, "_bound_notebook", tmp_notebook)
        cells = json.loads(jupylink_list_cells())
        first_code = next(c for c in cells if c["cell_type"] == "code" and c["id"])
        cell_id = first_code["id"]

        result = json.loads(jupylink_execute_cell(cell_id=cell_id))
        assert "error" not in result
        assert result.get("status") == "ok"
        assert "execution_count" in result

    def test_execute_multiple_cells_same_kernel(
        self, tmp_notebook: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Execute multiple dependent cells via jupylink_execute_cells - same kernel."""
        monkeypatch.setattr(mcp_server, "_bound_notebook", tmp_notebook)
        cells = json.loads(jupylink_list_cells())
        code_cells = [c for c in cells if c["cell_type"] == "code" and c["id"]]
        cell_ids = [c["id"] for c in code_cells[:3]]

        result = json.loads(jupylink_execute_cells(cell_ids=cell_ids))
        assert "error" not in result
        assert isinstance(result, list)
        assert len(result) == 3
        for r in result:
            assert r.get("status") == "ok"

    def test_concurrent_execute_single_kernel(
        self, tmp_notebook: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Concurrent execute_cell calls should use single kernel (spawn lock)."""
        monkeypatch.setattr(mcp_server, "_bound_notebook", tmp_notebook)
        cells = json.loads(jupylink_list_cells())
        code_cells = [c for c in cells if c["cell_type"] == "code" and c["id"]]
        if len(code_cells) < 2:
            pytest.skip("Need at least 2 code cells")

        results: list[dict] = []
        errors: list[str] = []

        def run(cell_id: str) -> None:
            try:
                r = json.loads(jupylink_execute_cell(cell_id=cell_id))
                results.append(r)
                if "error" in r:
                    errors.append(r["error"])
            except Exception as e:
                errors.append(str(e))

        t1 = threading.Thread(target=run, args=(code_cells[0]["id"],))
        t2 = threading.Thread(target=run, args=(code_cells[1]["id"],))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Errors: {errors}"
        assert len(results) == 2, f"Expected 2 results, got {results}"

        kernels = json.loads(jupylink_list_kernels())
        nb_path = str(tmp_notebook.resolve())
        matching = [
            k for k in kernels
            if os.path.normcase(k["notebook_path"]) == os.path.normcase(nb_path)
        ]
        assert len(matching) <= 1, f"Expected at most 1 kernel for notebook, got {len(matching)}: {matching}"

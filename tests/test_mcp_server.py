"""Tests for MCP server tools: get_record, sync_record, get_status."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import nbformat
import pytest

from jupylink import mcp_server
from jupylink.mcp_server import (
    jupylink_get_record,
    jupylink_get_status,
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

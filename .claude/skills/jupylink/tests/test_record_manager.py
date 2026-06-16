"""Tests for RecordManager: markdown cells, history loading, record generation."""

from __future__ import annotations

import json
from pathlib import Path

import nbformat
import pytest

from jupylink.record_manager import RecordManager


class TestMarkdownCellsInRecord:
    """Fix #2: Markdown cells should be visible in the record."""

    def test_markdown_cells_appear_in_py_record(self, tmp_notebook: Path) -> None:
        rm = RecordManager(tmp_notebook)
        rm.write_record()

        py_path = tmp_notebook.parent / f"{tmp_notebook.stem}_record.py"
        content = py_path.read_text(encoding="utf-8")

        assert "# %% [markdown]" in content
        assert "# ## Section 1" in content
        assert "# Some description" in content
        assert "# ## Section 2" in content
        assert "# [markdown - editable]" in content

    def test_markdown_cells_appear_in_json_record(self, tmp_notebook: Path) -> None:
        rm = RecordManager(tmp_notebook)
        rm.write_record()

        json_path = tmp_notebook.parent / f"{tmp_notebook.stem}_record.json"
        data = json.loads(json_path.read_text(encoding="utf-8"))

        markdown_cells = [c for c in data["cells"] if c.get("cell_type") == "markdown"]
        assert len(markdown_cells) == 2
        assert "## Section 1" in markdown_cells[0]["code"]
        assert markdown_cells[0]["status"] == "markdown"
        assert markdown_cells[0]["editable"] is True

    def test_markdown_cells_preserve_notebook_order(self, tmp_notebook: Path) -> None:
        """Markdown cells should appear inline, not all grouped at the end."""
        rm = RecordManager(tmp_notebook)
        rm.write_record()

        json_path = tmp_notebook.parent / f"{tmp_notebook.stem}_record.json"
        data = json.loads(json_path.read_text(encoding="utf-8"))

        cell_types = [c.get("cell_type", "code") for c in data["cells"]]
        # With no executions: all cells are pending, order = ipynb order
        # code, markdown, code, code(empty), markdown, code
        assert cell_types == ["code", "markdown", "code", "code", "markdown", "code"]

    def test_write_record_creates_csv(self, tmp_notebook: Path) -> None:
        """write_record should create _record.csv alongside .py and .json."""
        rm = RecordManager(tmp_notebook)
        rm.write_record()

        csv_path = tmp_notebook.parent / f"{tmp_notebook.stem}_record.csv"
        assert csv_path.exists()
        content = csv_path.read_text(encoding="utf-8")
        assert "id,cell_type,status,exec_order,execution_count,code,error_ename,error_evalue" in content
        assert "x = 42" in content


class TestHistoryLoading:
    """Fix #4: RecordManager should load existing execution history."""

    def test_load_preserves_execution_records(self, tmp_notebook_with_record: Path) -> None:
        rm = RecordManager(tmp_notebook_with_record)
        loaded = rm.load_from_record_file()
        assert loaded is True
        assert len(rm._execution_records) == 2
        assert rm._execution_records[0]["exec_order"] == 1
        assert rm._execution_records[1]["exec_order"] == 2

    def test_load_preserves_execution_log(self, tmp_notebook_with_record: Path) -> None:
        rm = RecordManager(tmp_notebook_with_record)
        rm.load_from_record_file()
        assert len(rm._execution_log) == 2
        assert rm._execution_log[0]["status"] == "ok"

    def test_write_after_load_keeps_executed_cells(self, tmp_notebook_with_record: Path) -> None:
        rm = RecordManager(tmp_notebook_with_record)
        rm.load_from_record_file()
        rm.write_record()

        py_path = tmp_notebook_with_record.parent / f"{tmp_notebook_with_record.stem}_record.py"
        content = py_path.read_text(encoding="utf-8")
        assert "# [executed - do not modify]" in content
        assert "x = 42" in content
        assert "print(x)" in content

    def test_load_returns_false_when_no_record(self, tmp_notebook: Path) -> None:
        rm = RecordManager(tmp_notebook)
        loaded = rm.load_from_record_file()
        assert loaded is False

    def test_new_execution_after_load_continues_order(self, tmp_notebook_with_record: Path) -> None:
        """exec_order should continue from loaded history, not restart from 1."""
        rm = RecordManager(tmp_notebook_with_record)
        rm.load_from_record_file()
        rm.add_execution(
            cell_id="new_cell",
            code="z = 100",
            status="ok",
            execution_count=3,
        )
        assert rm._execution_records[-1]["exec_order"] == 3


class TestGetOutput:
    """Test output retrieval from record."""

    def test_get_output_returns_latest(self, tmp_notebook_with_record: Path) -> None:
        rm = RecordManager(tmp_notebook_with_record)
        rm.load_from_record_file()

        nb = nbformat.read(tmp_notebook_with_record, as_version=nbformat.NO_CONVERT)
        code_cells = [c for c in nb.cells if c.cell_type == "code"]
        cell_id = code_cells[1].id  # print(x)

        output = rm.get_output(cell_id)
        assert output is not None
        assert output[0]["msg_type"] == "stream"
        assert output[0]["text"] == "42\n"

    def test_get_output_from_record_file_static(self, tmp_notebook_with_record: Path) -> None:
        nb = nbformat.read(tmp_notebook_with_record, as_version=nbformat.NO_CONVERT)
        code_cells = [c for c in nb.cells if c.cell_type == "code"]
        cell_id = code_cells[1].id

        output = RecordManager.get_output_from_record_file(
            tmp_notebook_with_record, cell_id
        )
        assert output is not None
        assert output[0]["text"] == "42\n"

    def test_get_output_returns_none_for_unknown_cell(self, tmp_notebook_with_record: Path) -> None:
        rm = RecordManager(tmp_notebook_with_record)
        rm.load_from_record_file()
        assert rm.get_output("nonexistent_cell") is None


class TestUpdateCellOutput:
    """Test static update_cell_output with file locking."""

    def test_update_output_writes_to_json(self, tmp_notebook_with_record: Path) -> None:
        nb = nbformat.read(tmp_notebook_with_record, as_version=nbformat.NO_CONVERT)
        code_cells = [c for c in nb.cells if c.cell_type == "code"]
        cell_id = code_cells[0].id  # x = 42

        new_output = [{"msg_type": "stream", "name": "stdout", "text": "updated\n"}]
        result = RecordManager.update_cell_output(
            tmp_notebook_with_record, cell_id, new_output
        )
        assert result is True

        json_path = tmp_notebook_with_record.parent / f"{tmp_notebook_with_record.stem}_record.json"
        data = json.loads(json_path.read_text(encoding="utf-8"))
        cell = next(c for c in data["cells"] if c["id"] == cell_id)
        assert cell["output"][0]["text"] == "updated\n"

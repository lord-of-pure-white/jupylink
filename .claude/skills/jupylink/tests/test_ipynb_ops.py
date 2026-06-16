"""Tests for ipynb_ops: source format preservation, basic operations."""

from __future__ import annotations

from pathlib import Path

import nbformat
import pytest

from jupylink.ipynb_ops import (
    _to_source,
    create_cell,
    delete_cell,
    get_cell_source,
    list_cells,
    write_cell,
)


class TestToSource:
    """Fix #9: _to_source should preserve existing cell format."""

    def test_preserves_string_format(self) -> None:
        result = _to_source("print('hello')", existing_source="old content")
        assert isinstance(result, str)
        assert result == "print('hello')"

    def test_preserves_list_format(self) -> None:
        result = _to_source("line1\nline2\n", existing_source=["old\n"])
        assert isinstance(result, list)
        assert result == ["line1\n", "line2\n"]

    def test_defaults_to_list_for_new_cells(self) -> None:
        result = _to_source("print('hello')", existing_source=None)
        assert isinstance(result, list)

    def test_empty_content_with_string_format(self) -> None:
        result = _to_source("", existing_source="old")
        assert isinstance(result, str)
        assert result == ""

    def test_empty_content_with_list_format(self) -> None:
        result = _to_source("", existing_source=["old\n"])
        assert isinstance(result, list)
        assert result == []


class TestWriteCell:
    def test_write_and_read_back(self, tmp_notebook: Path) -> None:
        cells = list_cells(tmp_notebook)
        code_cells = [c for c in cells if c["cell_type"] == "code"]
        cell_id = code_cells[0]["id"]

        assert write_cell(tmp_notebook, cell_id, "x = 99")
        source = get_cell_source(tmp_notebook, cell_id)
        assert source == "x = 99"

    def test_write_preserves_existing_source_format(self, tmp_notebook: Path) -> None:
        """After write_cell, the source format (string vs list) should match the original."""
        nb = nbformat.read(tmp_notebook, as_version=nbformat.NO_CONVERT)
        code_cell = next(c for c in nb.cells if c.cell_type == "code")
        original_type = type(code_cell.source)
        cell_id = code_cell.id

        write_cell(tmp_notebook, cell_id, "new_content = True")

        nb_after = nbformat.read(tmp_notebook, as_version=nbformat.NO_CONVERT)
        cell_after = next(c for c in nb_after.cells if c.id == cell_id)
        assert isinstance(cell_after.source, original_type)

    def test_write_nonexistent_cell_returns_false(self, tmp_notebook: Path) -> None:
        assert write_cell(tmp_notebook, "nonexistent_id", "content") is False

    def test_write_nonexistent_file_returns_false(self, tmp_path: Path) -> None:
        assert write_cell(tmp_path / "no.ipynb", "cell", "content") is False


class TestCreateCell:
    def test_create_appends_by_default(self, tmp_notebook: Path) -> None:
        initial = list_cells(tmp_notebook)
        new_id = create_cell(tmp_notebook, source="new_cell_content")
        assert new_id is not None

        after = list_cells(tmp_notebook)
        assert len(after) == len(initial) + 1
        assert after[-1]["id"] == new_id
        assert "new_cell_content" in after[-1]["source"]

    def test_create_at_index(self, tmp_notebook: Path) -> None:
        new_id = create_cell(tmp_notebook, index=0, source="first_cell")
        assert new_id is not None
        cells = list_cells(tmp_notebook)
        assert cells[0]["id"] == new_id

    def test_create_markdown_cell(self, tmp_notebook: Path) -> None:
        new_id = create_cell(tmp_notebook, cell_type="markdown", source="# Title")
        assert new_id is not None
        cells = list_cells(tmp_notebook)
        md = next(c for c in cells if c["id"] == new_id)
        assert md["cell_type"] == "markdown"

    def test_create_invalid_type_returns_none(self, tmp_notebook: Path) -> None:
        assert create_cell(tmp_notebook, cell_type="invalid") is None


class TestDeleteCell:
    def test_delete_existing_cell(self, tmp_notebook: Path) -> None:
        cells = list_cells(tmp_notebook)
        cell_id = cells[0]["id"]
        count_before = len(cells)

        assert delete_cell(tmp_notebook, cell_id)
        cells_after = list_cells(tmp_notebook)
        assert len(cells_after) == count_before - 1
        assert all(c["id"] != cell_id for c in cells_after)

    def test_delete_nonexistent_returns_false(self, tmp_notebook: Path) -> None:
        assert delete_cell(tmp_notebook, "nonexistent") is False


class TestListCells:
    def test_lists_all_cell_types(self, tmp_notebook: Path) -> None:
        cells = list_cells(tmp_notebook)
        types = {c["cell_type"] for c in cells}
        assert "code" in types
        assert "markdown" in types

    def test_empty_flag_on_empty_cells(self, tmp_notebook: Path) -> None:
        cells = list_cells(tmp_notebook)
        empty_cells = [c for c in cells if c["empty"]]
        assert len(empty_cells) >= 1

    def test_source_preview_truncation(self, tmp_path: Path) -> None:
        nb = nbformat.v4.new_notebook()
        nb.cells = [nbformat.v4.new_code_cell(source="x" * 200)]
        path = tmp_path / "long.ipynb"
        nbformat.write(nb, path)

        cells = list_cells(path)
        assert len(cells[0]["source_preview"]) < 200
        assert cells[0]["source_preview"].endswith("...")

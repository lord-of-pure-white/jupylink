"""Shared test fixtures for JupyLink tests."""

from __future__ import annotations

import json
from pathlib import Path

import nbformat
import pytest

from jupylink import kernel_registry as _kr


def _make_registry_path_fn(reg: Path):
    def _fn() -> Path:
        return reg

    return _fn


@pytest.fixture
def isolated_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``kernel_registry._registry_path`` at a temp ``kernels.json``."""
    reg = tmp_path / "kernels.json"
    monkeypatch.setattr(_kr, "_registry_path", _make_registry_path_fn(reg))
    return reg
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook


@pytest.fixture
def tmp_notebook(tmp_path: Path) -> Path:
    """Create a minimal test notebook with code and markdown cells."""
    nb = new_notebook()
    nb.cells = [
        new_code_cell(source="x = 42"),
        new_markdown_cell(source="## Section 1\nSome description"),
        new_code_cell(source="print(x)"),
        new_code_cell(source=""),  # empty cell
        new_markdown_cell(source="## Section 2"),
        new_code_cell(source="y = x + 1"),
    ]
    path = tmp_path / "test.ipynb"
    nbformat.write(nb, path)
    return path


@pytest.fixture
def tmp_notebook_with_record(tmp_notebook: Path) -> Path:
    """Create a notebook with a pre-existing _record.json that has execution history."""
    nb = nbformat.read(tmp_notebook, as_version=nbformat.NO_CONVERT)
    code_cells = [c for c in nb.cells if c.cell_type == "code"]

    record = {
        "notebook_path": str(tmp_notebook.resolve()),
        "execution_log": [
            {"cell_id": code_cells[0].id, "status": "ok"},
            {"cell_id": code_cells[1].id, "status": "ok"},
        ],
        "cells": [
            {
                "id": code_cells[0].id,
                "code": "x = 42",
                "status": "ok",
                "editable": False,
                "exec_order": 1,
                "execution_count": 1,
                "output": [],
            },
            {
                "id": code_cells[1].id,
                "code": "print(x)",
                "status": "ok",
                "editable": False,
                "exec_order": 2,
                "execution_count": 2,
                "output": [
                    {"msg_type": "stream", "name": "stdout", "text": "42\n"}
                ],
            },
            {
                "id": code_cells[2].id,
                "code": "",
                "status": "empty",
                "editable": True,
            },
            {
                "id": code_cells[3].id,
                "code": "y = x + 1",
                "status": "pending",
                "editable": True,
            },
        ],
    }
    json_path = tmp_notebook.parent / f"{tmp_notebook.stem}_record.json"
    json_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return tmp_notebook

"""Tests for executor: timeout configuration, helper functions."""

from __future__ import annotations

import os

import pytest

from jupylink.executor import _get_exec_timeout


class TestExecTimeout:
    """Fix #6: Execution timeout should be configurable via environment variable."""

    def test_default_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("JUPYLINK_EXEC_TIMEOUT", raising=False)
        assert _get_exec_timeout() == 60

    def test_custom_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JUPYLINK_EXEC_TIMEOUT", "120")
        assert _get_exec_timeout() == 120

    def test_invalid_env_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JUPYLINK_EXEC_TIMEOUT", "not_a_number")
        assert _get_exec_timeout() == 60

    def test_empty_env_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JUPYLINK_EXEC_TIMEOUT", "")
        assert _get_exec_timeout() == 60

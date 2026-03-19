"""Tests for cross-platform compatibility (Linux, macOS, Windows)."""

from __future__ import annotations

import os
import sys
import webbrowser
from pathlib import Path
from unittest import mock

import pytest


class TestRegistryPathPlatform:
    """kernel_registry._registry_path should use platform-appropriate locations."""

    def test_windows_uses_appdata(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", str(tmp_path))

        from jupylink.kernel_registry import _registry_path
        result = _registry_path()
        assert "jupylink" in str(result)
        assert result.name == "kernels.json"
        assert tmp_path in result.parents or result.parent.parent == tmp_path

    @pytest.mark.skipif(os.name == "nt", reason="PosixPath cannot be created on Windows")
    def test_linux_xdg_data_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()

        with mock.patch("jupylink.kernel_registry.Path.home", return_value=fake_home):
            from jupylink.kernel_registry import _registry_path
            result = _registry_path()
            assert result.name == "kernels.json"
            assert str(tmp_path) in str(result)

    @pytest.mark.skipif(os.name == "nt", reason="PosixPath cannot be created on Windows")
    def test_linux_legacy_fallback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """If ~/.jupylink/ already exists, use it for backward compatibility."""
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setattr(sys, "platform", "linux")

        fake_home = tmp_path / "home"
        legacy_dir = fake_home / ".jupylink"
        legacy_dir.mkdir(parents=True)

        with mock.patch("jupylink.kernel_registry.Path.home", return_value=fake_home):
            from jupylink.kernel_registry import _registry_path
            result = _registry_path()
            assert ".jupylink" in str(result)

    @pytest.mark.skipif(os.name == "nt", reason="PosixPath cannot be created on Windows")
    def test_macos_uses_dot_jupylink(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setattr(sys, "platform", "darwin")

        with mock.patch("jupylink.kernel_registry.Path.home", return_value=tmp_path):
            from jupylink.kernel_registry import _registry_path
            result = _registry_path()
            assert ".jupylink" in str(result)
            assert result.name == "kernels.json"

    def test_registry_path_creates_directory(self, tmp_path: Path) -> None:
        """Registry path should auto-create its parent directory."""
        from jupylink.kernel_registry import _registry_path
        result = _registry_path()
        assert result.parent.exists()
        assert result.name == "kernels.json"


class TestFindEditorCmd:
    """notify_ide._find_editor_cmd should search platform-appropriate candidates."""

    def test_finds_cursor_in_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import shutil
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/cursor" if name == "cursor" else None)

        from jupylink.notify_ide import _find_editor_cmd
        result = _find_editor_cmd()
        assert result == "/usr/bin/cursor"

    def test_prefers_cursor_in_cursor_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import shutil
        found = {"cursor": "/usr/bin/cursor", "code": "/usr/bin/code"}
        monkeypatch.setattr(shutil, "which", lambda name: found.get(name))
        monkeypatch.setenv("CURSOR_SESSION", "1")

        from jupylink.notify_ide import _find_editor_cmd
        result = _find_editor_cmd()
        assert "cursor" in result

    def test_no_cmd_suffix_on_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On Linux, should not search for .cmd variants."""
        monkeypatch.setattr(sys, "platform", "linux")
        searched_names: list[str] = []
        import shutil
        original_which = shutil.which
        def track_which(name: str) -> str | None:
            searched_names.append(name)
            return None
        monkeypatch.setattr(shutil, "which", track_which)

        from jupylink.notify_ide import _find_editor_cmd
        _find_editor_cmd()
        assert not any(n.endswith(".cmd") for n in searched_names), \
            f"Should not search .cmd on Linux, but searched: {searched_names}"

    def test_linux_fallback_paths(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """On Linux, should check common non-PATH locations as fallback."""
        monkeypatch.setattr(sys, "platform", "linux")
        import shutil
        monkeypatch.setattr(shutil, "which", lambda name: None)

        cursor_bin = tmp_path / ".local" / "bin" / "cursor"
        cursor_bin.parent.mkdir(parents=True)
        cursor_bin.touch()

        with mock.patch("jupylink.notify_ide.Path.home", return_value=tmp_path):
            from jupylink.notify_ide import _find_editor_cmd
            result = _find_editor_cmd()
            assert result is not None
            assert "cursor" in result

    def test_returns_none_when_nothing_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import shutil
        monkeypatch.setattr(shutil, "which", lambda name: None)
        monkeypatch.setattr(sys, "platform", "linux")

        from jupylink.notify_ide import _find_editor_cmd
        with mock.patch("jupylink.notify_ide.Path.home", return_value=Path("/nonexistent")):
            result = _find_editor_cmd()
            assert result is None


class TestRequestRefreshPosix:
    """Popen on POSIX should use start_new_session=True."""

    def test_posix_detaches_child(self, monkeypatch: pytest.MonkeyPatch, tmp_notebook: Path) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("JUPYLINK_NO_REFRESH", "0")

        import shutil
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/cursor" if name == "cursor" else None)

        popen_kwargs: dict = {}
        original_popen = __builtins__  # dummy

        import subprocess
        original_popen_cls = subprocess.Popen
        def fake_popen(cmd, **kwargs):
            popen_kwargs.update(kwargs)
            m = mock.MagicMock()
            return m
        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        from jupylink.notify_ide import request_notebook_refresh, _refresh_disabled
        monkeypatch.setattr("jupylink.notify_ide._refresh_disabled", False)
        monkeypatch.setattr("jupylink.notify_ide._get_refresh_delay", lambda: 0)
        monkeypatch.setattr("jupylink.notify_ide._is_temp_path", lambda _: False)
        monkeypatch.setattr(webbrowser, "open", lambda _: None)  # avoid opening URL in tests

        import time
        result = request_notebook_refresh(tmp_notebook)
        assert result is True
        time.sleep(0.1)
        assert popen_kwargs.get("start_new_session") is True
        assert "creationflags" not in popen_kwargs

    def test_windows_uses_creation_flags(self, monkeypatch: pytest.MonkeyPatch, tmp_notebook: Path) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("JUPYLINK_NO_REFRESH", "0")

        import shutil
        monkeypatch.setattr(shutil, "which", lambda name: "C:\\cursor.exe" if name == "cursor" else None)

        popen_kwargs: dict = {}
        import subprocess
        def fake_popen(cmd, **kwargs):
            popen_kwargs.update(kwargs)
            return mock.MagicMock()
        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr("os.startfile", lambda _: None)  # avoid opening URL in tests
        monkeypatch.setattr("jupylink.notify_ide._refresh_disabled", False)
        monkeypatch.setattr("jupylink.notify_ide._get_refresh_delay", lambda: 0)
        monkeypatch.setattr("jupylink.notify_ide._is_temp_path", lambda _: False)

        import time
        from jupylink.notify_ide import request_notebook_refresh
        result = request_notebook_refresh(tmp_notebook)
        assert result is True
        time.sleep(0.1)
        assert "creationflags" in popen_kwargs
        assert "start_new_session" not in popen_kwargs

    def test_skips_refresh_for_temp_path(self, monkeypatch: pytest.MonkeyPatch, tmp_notebook: Path) -> None:
        """Refresh is skipped for paths under temp (e.g. pytest artifacts)."""
        monkeypatch.setattr("jupylink.notify_ide._refresh_disabled", False)
        from jupylink.notify_ide import request_notebook_refresh
        result = request_notebook_refresh(tmp_notebook)
        assert result is False

    def test_skips_refresh_in_remote_ssh_context(self, monkeypatch: pytest.MonkeyPatch, tmp_notebook: Path) -> None:
        """Refresh is skipped when JUPYLINK_REFRESH_SKIP_REMOTE=1."""
        monkeypatch.setattr("jupylink.notify_ide._refresh_disabled", False)
        monkeypatch.setattr("jupylink.notify_ide._is_temp_path", lambda _: False)
        monkeypatch.setenv("JUPYLINK_NO_REFRESH", "0")
        monkeypatch.setenv("JUPYLINK_REFRESH_SKIP_REMOTE", "1")
        monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 12345 5.6.7.8 22")
        from jupylink.notify_ide import request_notebook_refresh
        result = request_notebook_refresh(tmp_notebook)
        assert result is False

    def test_uses_remote_refresh_when_ssh_connection_set(
        self, monkeypatch: pytest.MonkeyPatch, tmp_notebook: Path
    ) -> None:
        """When SSH_CONNECTION is set and host is derivable, use vscode-remote URI for refresh."""
        monkeypatch.setattr("jupylink.notify_ide._refresh_disabled", False)
        monkeypatch.setattr("jupylink.notify_ide._is_temp_path", lambda _: False)
        monkeypatch.setenv("JUPYLINK_NO_REFRESH", "0")
        monkeypatch.delenv("JUPYLINK_REFRESH_SKIP_REMOTE", raising=False)
        monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 12345 5.6.7.8 22")
        opened_uris: list[str] = []
        import webbrowser

        def capture_open(uri: str) -> None:
            opened_uris.append(uri)

        monkeypatch.setattr(webbrowser, "open", capture_open)
        from jupylink.notify_ide import request_notebook_refresh
        result = request_notebook_refresh(tmp_notebook)
        assert result is True
        import time
        time.sleep(0.25)
        assert len(opened_uris) == 1
        assert opened_uris[0].startswith("cursor://")
        assert "vscode-remote" in opened_uris[0]
        assert "ssh-remote+5.6.7.8" in opened_uris[0]
        assert tmp_notebook.name in opened_uris[0]

    def test_uses_jupylink_remote_ssh_host_when_set(
        self, monkeypatch: pytest.MonkeyPatch, tmp_notebook: Path
    ) -> None:
        """JUPYLINK_REMOTE_SSH_HOST overrides host derived from SSH_CONNECTION."""
        monkeypatch.setattr("jupylink.notify_ide._refresh_disabled", False)
        monkeypatch.setattr("jupylink.notify_ide._is_temp_path", lambda _: False)
        monkeypatch.setenv("JUPYLINK_NO_REFRESH", "0")
        monkeypatch.delenv("JUPYLINK_REFRESH_SKIP_REMOTE", raising=False)
        monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 12345 5.6.7.8 22")
        monkeypatch.setenv("JUPYLINK_REMOTE_SSH_HOST", "my-server.example.com")
        opened_uris: list[str] = []
        import webbrowser
        monkeypatch.setattr(webbrowser, "open", lambda uri: opened_uris.append(uri))
        from jupylink.notify_ide import request_notebook_refresh
        result = request_notebook_refresh(tmp_notebook)
        assert result is True
        import time
        time.sleep(0.25)
        assert opened_uris[0].startswith("cursor://")
        assert "ssh-remote+my-server.example.com" in opened_uris[0]

    def test_uses_vscode_scheme_when_env_set(
        self, monkeypatch: pytest.MonkeyPatch, tmp_notebook: Path
    ) -> None:
        """JUPYLINK_REFRESH_USE_VSCODE=1 uses vscode:// instead of cursor://."""
        monkeypatch.setattr("jupylink.notify_ide._refresh_disabled", False)
        monkeypatch.setattr("jupylink.notify_ide._is_temp_path", lambda _: False)
        monkeypatch.setenv("JUPYLINK_NO_REFRESH", "0")
        monkeypatch.delenv("JUPYLINK_REFRESH_SKIP_REMOTE", raising=False)
        monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 12345 5.6.7.8 22")
        monkeypatch.setenv("JUPYLINK_REFRESH_USE_VSCODE", "1")
        opened_uris: list[str] = []
        import webbrowser
        monkeypatch.setattr(webbrowser, "open", lambda uri: opened_uris.append(uri))
        from jupylink.notify_ide import request_notebook_refresh
        result = request_notebook_refresh(tmp_notebook)
        assert result is True
        import time
        time.sleep(0.25)
        assert opened_uris[0].startswith("vscode://")


class TestUriToPathCrossPlatform:
    """kernel._uri_to_path should handle both Windows and Linux URIs.

    _uri_to_path is defined in kernel.py which imports ipykernel.
    We import it once (at current platform) and test with os.name monkeypatching.
    Tests that need a different os.name than the current platform are skipped
    because ipykernel's import chain creates platform-specific Path objects.
    """

    def test_file_uri_current_platform(self) -> None:
        from jupylink.kernel import _uri_to_path
        if os.name == "nt":
            result = _uri_to_path("file:///e%3A/projects/test.ipynb")
            assert result is not None
            assert "projects" in result
            assert result.endswith("test.ipynb")
        else:
            result = _uri_to_path("file:///home/user/notebooks/test.ipynb")
            assert result == "/home/user/notebooks/test.ipynb"

    def test_vscode_cell_uri_current_platform(self) -> None:
        from jupylink.kernel import _uri_to_path
        if os.name == "nt":
            result = _uri_to_path("/e%3A/projects/jupytest/test.ipynb")
            assert result is not None
            assert result.endswith(".ipynb")
        else:
            result = _uri_to_path("file:///home/user/notebooks/test.ipynb")
            assert result == "/home/user/notebooks/test.ipynb"

    def test_plain_path_current_platform(self) -> None:
        from jupylink.kernel import _uri_to_path
        if os.name == "nt":
            # On Windows, a plain path without drive letter won't match
            result = _uri_to_path("E:\\projects\\test.ipynb")
            assert result is not None
        else:
            result = _uri_to_path("/home/user/test.ipynb")
            assert result == "/home/user/test.ipynb"

    def test_none_for_non_notebook(self) -> None:
        from jupylink.kernel import _uri_to_path
        assert _uri_to_path("not_a_notebook.txt") is None
        assert _uri_to_path("") is None
        assert _uri_to_path(None) is None

    @pytest.mark.skipif(os.name != "nt", reason="Windows-specific drive letter stripping")
    def test_windows_strips_leading_slash(self) -> None:
        from jupylink.kernel import _uri_to_path
        result = _uri_to_path("file:///e:/projects/test.ipynb")
        assert result is not None
        assert not result.startswith("/")

    @pytest.mark.skipif(os.name == "nt", reason="Linux-specific path preservation")
    def test_linux_preserves_leading_slash(self) -> None:
        from jupylink.kernel import _uri_to_path
        result = _uri_to_path("file:///home/user/test.ipynb")
        assert result is not None
        assert result.startswith("/")

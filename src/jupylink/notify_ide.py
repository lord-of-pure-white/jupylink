"""Request IDE (VS Code/Cursor) to refresh notebook file after external modification."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

def _get_refresh_delay() -> float:
    """Delay before requesting refresh (seconds). Configurable via JUPYLINK_REFRESH_DELAY."""
    try:
        return float(os.environ.get("JUPYLINK_REFRESH_DELAY", "0.2"))
    except ValueError:
        return 0.2


_refresh_disabled: bool = False

# Debounce: one pending refresh per path; rapid successive ops coalesce into a single refresh
_pending_refresh: dict[Path, threading.Timer] = {}
_pending_lock = threading.Lock()


def set_refresh_disabled(disabled: bool = True) -> None:
    """Disable refresh for current process (e.g. when --no-refresh is passed)."""
    global _refresh_disabled
    _refresh_disabled = disabled


def _should_refresh() -> bool:
    """Return False if refresh is disabled via env or set_refresh_disabled()."""
    if _refresh_disabled:
        return False
    return os.environ.get("JUPYLINK_NO_REFRESH", "").lower() not in ("1", "true", "yes")


def _find_editor_cmd() -> str | None:
    """Find cursor or code CLI. Returns full path for reliable subprocess."""
    import shutil

    def try_cmd(name: str) -> str | None:
        return shutil.which(name)

    is_win = sys.platform == "win32"
    is_mac = sys.platform == "darwin"

    # Build candidate list per platform
    if is_win:
        candidates = ("cursor", "cursor.cmd", "code", "code.cmd", "code-insiders", "code-insiders.cmd")
    else:
        candidates = ("cursor", "code", "code-insiders")

    # Prefer Cursor when in Cursor environment
    if os.environ.get("CURSOR") or os.environ.get("CURSOR_SESSION"):
        cursor_names = ("cursor", "cursor.cmd") if is_win else ("cursor",)
        for name in cursor_names:
            if path := try_cmd(name):
                return path

    for name in candidates:
        if path := try_cmd(name):
            return path

    # Platform-specific fallback: MCP may have minimal PATH
    if is_win:
        fallback_bases = [
            (os.environ.get("LOCALAPPDATA", ""), "Programs", "Cursor"),
            (os.environ.get("LOCALAPPDATA", ""), "cursor"),
            (os.environ.get("PROGRAMFILES", "C:\\Program Files"), "cursor"),
        ]
        for base, *rest in fallback_bases:
            if base:
                for name in ("cursor.cmd", "cursor"):
                    cand = Path(base, *rest, "resources", "app", "bin", name)
                    if cand.exists():
                        return str(cand)
    elif is_mac:
        mac_paths = [
            Path("/Applications/Cursor.app/Contents/Resources/app/bin/cursor"),
            Path.home() / "Applications" / "Cursor.app" / "Contents" / "Resources" / "app" / "bin" / "cursor",
            Path("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),
        ]
        for cand in mac_paths:
            if cand.exists():
                return str(cand)
    else:
        # Linux: check common non-PATH locations
        linux_paths = [
            Path.home() / ".local" / "bin" / "cursor",
            Path("/usr/local/bin/cursor"),
            Path("/usr/bin/cursor"),
            Path.home() / ".local" / "bin" / "code",
            Path("/usr/local/bin/code"),
            Path("/usr/bin/code"),
            Path("/snap/bin/code"),
            Path("/usr/share/code/bin/code"),
        ]
        for cand in linux_paths:
            if cand.exists():
                return str(cand)

    return None


def _path_to_vscode_uri(path: Path) -> str:
    """Convert file path to vscode://file/ URI. Cursor registers as handler."""
    p = path.resolve()
    if sys.platform == "win32":
        # E:\projects\x.ipynb -> vscode://file/E:/projects/x.ipynb
        drive = p.drive
        rest = str(p)[len(drive) :].replace("\\", "/")
        return f"vscode://file/{drive}{rest}"
    return f"vscode://file{p}"


def _run_refresh(path: Path, cmd: str) -> None:
    """Invoke IDE CLI and/or URL scheme to focus/reload the file. Called after debounce delay."""
    # 1. CLI: cursor path -r (reuse window)
    try:
        kwargs: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        use_no_window = os.environ.get("JUPYLINK_REFRESH_NO_WINDOW", "1").lower() in ("1", "true", "yes")
        if sys.platform == "win32" and use_no_window:
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([cmd, str(path), "-r"], **kwargs)
    except Exception:
        logger.debug("Failed to request IDE refresh for %s", path, exc_info=True)

    # 2. URL scheme: vscode://file/path - often triggers reload when file already open.
    # Set JUPYLINK_REFRESH_USE_URL=0 to disable.
    if os.environ.get("JUPYLINK_REFRESH_USE_URL", "1").lower() not in ("0", "false", "no"):
        try:
            uri = _path_to_vscode_uri(path)
            if sys.platform == "win32":
                os.startfile(uri)
            else:
                import webbrowser
                webbrowser.open(uri)
        except Exception:
            logger.debug("Failed to open vscode:// URI for %s", path, exc_info=True)


def _is_temp_path(path: Path) -> bool:
    """Return True if path is under a temp directory (skip refresh for test artifacts)."""
    path_str = str(path).lower()
    temp_markers = (
        "\\temp\\",
        "/temp/",
        "\\tmp\\",
        "/tmp/",
        "pytest-of-",
        "\\appdata\\local\\temp\\",
        "/.cache/",
    )
    return any(m in path_str for m in temp_markers)


def _on_refresh_timer(path: Path, cmd: str) -> None:
    """Called when debounce timer fires: run refresh and clear pending."""
    with _pending_lock:
        _pending_refresh.pop(path, None)
    _run_refresh(path, cmd)


def request_notebook_refresh(notebook_path: str | Path) -> bool:
    """Ask IDE to refresh the notebook file (reopen from disk).

    Tries cursor/code CLI with -r (reuse window) to focus and reload the file.
    Uses debouncing: rapid successive requests for the same path coalesce into
    one refresh, scheduled delay seconds after the last request.
    Skips refresh for paths under temp directories (e.g. pytest artifacts).
    Returns True if refresh was requested.
    """
    if not _should_refresh():
        return False
    path = Path(notebook_path).resolve()
    if not path.exists() or path.suffix != ".ipynb":
        return False
    if _is_temp_path(path):
        logger.debug("Skip refresh for temp path: %s", path)
        return False
    cmd = _find_editor_cmd()
    if not cmd:
        logger.debug("IDE CLI (cursor/code) not found in PATH; skip refresh for %s", path)
        return False

    delay = _get_refresh_delay()
    with _pending_lock:
        old = _pending_refresh.pop(path, None)
        if old is not None:
            old.cancel()
        timer = threading.Timer(delay, _on_refresh_timer, args=(path, cmd))
        timer.daemon = True
        _pending_refresh[path] = timer
        timer.start()
    return True

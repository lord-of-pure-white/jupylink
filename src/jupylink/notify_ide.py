"""Request IDE (VS Code/Cursor) to refresh notebook file after external modification."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


_refresh_disabled: bool = False


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
        for base in (
            os.environ.get("LOCALAPPDATA", ""),
            os.environ.get("PROGRAMFILES", "C:\\Program Files"),
        ):
            if base:
                for name in ("cursor.cmd", "cursor"):
                    cand = Path(base) / "cursor" / "resources" / "app" / "bin" / name
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


def request_notebook_refresh(notebook_path: str | Path) -> bool:
    """Ask IDE to refresh the notebook file (reopen from disk).

    Tries cursor/code CLI with -r (reuse window) to focus and reload the file.
    Runs in background; does not block. Returns True if refresh was requested.
    """
    if not _should_refresh():
        return False
    path = Path(notebook_path).resolve()
    if not path.exists() or path.suffix != ".ipynb":
        return False
    cmd = _find_editor_cmd()
    if not cmd:
        return False
    try:
        kwargs: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            # POSIX: detach child so it survives if MCP server process exits
            kwargs["start_new_session"] = True
        subprocess.Popen([cmd, str(path), "-r"], **kwargs)
        return True
    except Exception:
        logger.debug("Failed to request IDE refresh for %s", path, exc_info=True)
        return False

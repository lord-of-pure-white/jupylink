"""Tests for python -m jupylink entry (help vs ipykernel)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_module_help_without_f_shows_jupylink_message() -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    src = str(root / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "").strip(os.pathsep)
    r = subprocess.run(
        [sys.executable, "-m", "jupylink", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(root),
        env=env,
    )
    assert r.returncode == 0
    out = r.stdout + r.stderr
    assert "jupylink --help" in out or "console script" in out.lower()
    assert "install-kernelspec" in out


def test_cli_install_kernelspec_help_registered() -> None:
    from typer.testing import CliRunner

    from jupylink.cli import app

    r = CliRunner().invoke(app, ["install-kernelspec", "--help"])
    assert r.exit_code == 0
    assert "kernelspec" in r.stdout.lower()

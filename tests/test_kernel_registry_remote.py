"""Kernel registry: same notebook must share one key across Remote-SSH path forms."""

from __future__ import annotations

import json
import os
from pathlib import Path

from jupylink import kernel_registry as kr


def _ssh_remote_style_path(nb: Path) -> str:
    """Match VS Code path form: /ssh-remote+<token>/<fs path>."""
    return f"/ssh-remote+legacytoken/{nb.resolve().as_posix()}"


def test_strip_embedded_ssh_remote_prefix():
    from jupylink.kernel_registry import _strip_vscode_remote_filesystem_path

    hex_cfg = "7b22686f73744e616d65223a223137322e31362e31392e35227d"
    plain = "/share/home/kangxinyu/test/test_jupylink.ipynb"
    embedded = f"/ssh-remote+{hex_cfg}{plain}"
    assert _strip_vscode_remote_filesystem_path(embedded) == plain


def test_strip_vscode_remote_ssh_uri():
    from jupylink.kernel_registry import _strip_vscode_remote_filesystem_path

    hex_cfg = "7b22686f73744e616d65223a223137322e31362e31392e35227d"
    plain = "/share/home/kangxinyu/test/test_jupylink.ipynb"
    uri = f"vscode-remote://ssh-remote+{hex_cfg}{plain}"
    assert _strip_vscode_remote_filesystem_path(uri) == plain


def test_resolve_notebook_filesystem_path_strips_ssh_prefix(tmp_path):
    """Strip + resolve must point at the same file as the real notebook."""
    from jupylink.kernel_registry import resolve_notebook_filesystem_path

    nb = tmp_path / "n.ipynb"
    nb.write_text("{}", encoding="utf-8")
    hex_cfg = "7b22686f73744e616d65223a223137322e31362e31392e35227d"
    posix = nb.resolve().as_posix()
    # Linux Remote-SSH path shape; on Windows use vscode-remote URI (same strip logic).
    if os.name == "nt":
        uri = f"vscode-remote://ssh-remote+{hex_cfg}/{posix.lstrip('/')}"
        got = resolve_notebook_filesystem_path(uri)
    else:
        embedded = f"/ssh-remote+{hex_cfg}{posix}"
        got = resolve_notebook_filesystem_path(embedded)
    assert got.exists()
    assert os.path.samefile(got, nb)


def test_register_ssh_embedded_lookup_plain(isolated_registry, tmp_path):
    nb = tmp_path / "n.ipynb"
    nb.write_text("{}", encoding="utf-8")
    cf = tmp_path / "kernel.json"
    cf.write_text("{}", encoding="utf-8")

    kr.register(_ssh_remote_style_path(nb), str(cf))
    assert kr.get_connection_file(nb) == str(cf.resolve())


def test_register_plain_lookup_legacy_ssh_key(isolated_registry, tmp_path):
    """Old registry entries used full /ssh-remote+.../path keys without stripping."""
    nb = tmp_path / "n.ipynb"
    nb.write_text("{}", encoding="utf-8")
    cf = tmp_path / "kernel.json"
    cf.write_text("{}", encoding="utf-8")

    full = _ssh_remote_style_path(nb)
    legacy_key = os.path.normcase(str(Path(full).resolve()))
    reg = {
        "kernels": {legacy_key: str(cf.resolve())},
    }
    isolated_registry.write_text(json.dumps(reg), encoding="utf-8")

    assert kr.get_connection_file(nb) == str(cf.resolve())


def test_unregister_removes_sidecar(isolated_registry, tmp_path):
    nb = tmp_path / "n.ipynb"
    nb.write_text("{}", encoding="utf-8")
    cf = tmp_path / "k.json"
    cf.write_text("{}", encoding="utf-8")
    kr.register(nb, str(cf))
    assert (tmp_path / "n.jupylink_kernel.json").is_file()
    kr.unregister(nb)
    assert not (tmp_path / "n.jupylink_kernel.json").exists()


def test_register_writes_sidecar_next_to_notebook(isolated_registry, tmp_path):
    nb = tmp_path / "n.ipynb"
    nb.write_text("{}", encoding="utf-8")
    cf = tmp_path / "k.json"
    cf.write_text("{}", encoding="utf-8")
    kr.register(nb, str(cf))
    sidecar = nb.with_name("n.jupylink_kernel.json")
    assert sidecar.is_file()
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["connection_file"] == str(cf.resolve())


def test_register_dedupes_alias_keys(isolated_registry, tmp_path):
    nb = tmp_path / "n.ipynb"
    nb.write_text("{}", encoding="utf-8")
    cf1 = tmp_path / "k1.json"
    cf2 = tmp_path / "k2.json"
    cf1.write_text("{}", encoding="utf-8")
    cf2.write_text("{}", encoding="utf-8")

    ssh_key = _ssh_remote_style_path(nb)
    kr.register(ssh_key, str(cf1))
    kr.register(nb, str(cf2))
    data = json.loads(isolated_registry.read_text(encoding="utf-8"))
    assert len(data["kernels"]) == 1
    assert kr.get_connection_file(nb) == str(cf2.resolve())

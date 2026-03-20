"""Tests for IDE kernel bridge (MCP / existing kernel reuse)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from jupylink import kernel_ide_proxy as kip
from jupylink import kernel_registry as kr


@pytest.fixture(autouse=True)
def _ide_bridge_probe_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests use fake connection JSON; skip real ZMQ heartbeat probes."""
    monkeypatch.setattr(kip, "probe_kernel_connection_file", lambda *args, **kwargs: True)


def test_parse_connection_file_from_argv() -> None:
    assert kip.parse_connection_file_from_argv(["-m", "jupylink", "-f", "/tmp/x.json"]) == "/tmp/x.json"
    assert kip.parse_connection_file_from_argv(["--f=/a b.json"]) == "/a b.json"
    assert kip.parse_connection_file_from_argv(['--f="C:/a.json"']) == "C:/a.json"


def test_resolve_explicit_connection_file(tmp_path, monkeypatch) -> None:
    fe = tmp_path / "front.json"
    ex = tmp_path / "exist.json"
    fe.write_text("{}", encoding="utf-8")
    ex.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("JUPYLINK_IDE_CONNECTION_FILE", str(ex))
    assert kip.resolve_existing_connection_for_ide(str(fe)) == str(ex.resolve())


def test_discover_via_single_workspace_sidecar(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("JUPYLINK_IDE_CONNECTION_FILE", raising=False)
    monkeypatch.setenv("JUPYLINK_IDE_REGISTRY_SINGLE", "0")
    monkeypatch.chdir(tmp_path)
    nb = tmp_path / "book.ipynb"
    nb.write_text("{}", encoding="utf-8")
    cf = tmp_path / "real-kernel.json"
    cf.write_text("{}", encoding="utf-8")
    fe = tmp_path / "ide.json"
    fe.write_text("{}", encoding="utf-8")
    sidecar = tmp_path / "book.jupylink_kernel.json"
    sidecar.write_text(
        json.dumps({"connection_file": str(cf.resolve()), "notebook_path": str(nb.resolve())}),
        encoding="utf-8",
    )
    got = kip.discover_connection_via_workspace_sidecars(fe.resolve())
    assert got == str(cf.resolve())


def test_sidecar_via_explicit_notebook_env_not_cwd(tmp_path, monkeypatch) -> None:
    """Hinted notebook picks sidecar next to ipynb even when cwd is elsewhere."""
    monkeypatch.delenv("JUPYLINK_IDE_CONNECTION_FILE", raising=False)
    monkeypatch.setenv("JUPYLINK_IDE_REGISTRY_SINGLE", "0")
    sub = tmp_path / "nested" / "deep"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    nb = tmp_path / "book.ipynb"
    nb.write_text("{}", encoding="utf-8")
    cf = tmp_path / "real-kernel.json"
    cf.write_text("{}", encoding="utf-8")
    fe = tmp_path / "ide.json"
    fe.write_text("{}", encoding="utf-8")
    (tmp_path / "book.jupylink_kernel.json").write_text(
        json.dumps({"connection_file": str(cf.resolve()), "notebook_path": str(nb.resolve())}),
        encoding="utf-8",
    )
    monkeypatch.setenv("JUPYTER_NOTEBOOK_PATH", str(nb.resolve()))
    assert kip.discover_connection_via_workspace_sidecars(fe.resolve()) == str(cf.resolve())
    monkeypatch.delenv("JUPYTER_NOTEBOOK_PATH", raising=False)


def test_discover_ambiguous_two_sidecars(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JUPYLINK_IDE_REGISTRY_SINGLE", "0")
    monkeypatch.chdir(tmp_path)
    for name in ("a", "b"):
        (tmp_path / f"{name}.ipynb").write_text("{}", encoding="utf-8")
        (tmp_path / f"{name}.jupylink_kernel.json").write_text(
            json.dumps({"connection_file": str((tmp_path / f"{name}.json").resolve())}),
            encoding="utf-8",
        )
        (tmp_path / f"{name}.json").write_text("{}", encoding="utf-8")
    fe = tmp_path / "ide.json"
    fe.write_text("{}", encoding="utf-8")
    assert kip.discover_connection_via_workspace_sidecars(fe.resolve()) is None


def test_resolve_via_registry(isolated_registry, tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("JUPYLINK_IDE_CONNECTION_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    nb = tmp_path / "n.ipynb"
    nb.write_text("{}", encoding="utf-8")
    cf = tmp_path / "k.json"
    cf.write_text("{}", encoding="utf-8")
    fe = tmp_path / "front.json"
    fe.write_text("{}", encoding="utf-8")

    kr.register(nb, str(cf))
    got = kip.resolve_existing_connection_for_ide(str(fe))
    assert got == str(cf.resolve())


def test_registry_single_require_notebook_hint_blocks_unhinted(
    isolated_registry, tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JUPYLINK_IDE_CONNECTION_FILE", raising=False)
    monkeypatch.setenv("JUPYLINK_IDE_REGISTRY_SINGLE", "1")
    monkeypatch.setenv("JUPYLINK_IDE_REGISTRY_SINGLE_REQUIRE_NOTEBOOK_HINT", "1")
    nb = tmp_path / "n.ipynb"
    nb.write_text("{}", encoding="utf-8")
    cf = tmp_path / "k.json"
    cf.write_text("{}", encoding="utf-8")
    fe = tmp_path / "front.json"
    fe.write_text("{}", encoding="utf-8")
    kr.register(nb, str(cf))
    assert kip.discover_connection_via_registry_single(fe.resolve()) is None
    monkeypatch.delenv("JUPYLINK_IDE_REGISTRY_SINGLE_REQUIRE_NOTEBOOK_HINT", raising=False)


def test_registry_single_skips_when_env_notebook_differs(
    isolated_registry, tmp_path, monkeypatch
) -> None:
    """Single registry entry must not bridge if env points at a different notebook."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JUPYLINK_IDE_CONNECTION_FILE", raising=False)
    nb_a = tmp_path / "a.ipynb"
    nb_b = tmp_path / "b.ipynb"
    nb_a.write_text("{}", encoding="utf-8")
    nb_b.write_text("{}", encoding="utf-8")
    cf = tmp_path / "k.json"
    cf.write_text("{}", encoding="utf-8")
    fe = tmp_path / "front.json"
    fe.write_text("{}", encoding="utf-8")
    kr.register(nb_a, str(cf))
    monkeypatch.setenv("JUPYTER_NOTEBOOK_PATH", str(nb_b.resolve()))
    assert kip.discover_connection_via_registry_single(fe.resolve()) is None
    monkeypatch.delenv("JUPYTER_NOTEBOOK_PATH", raising=False)


def test_resolve_via_registry_single_prefers_user_registry(isolated_registry, tmp_path, monkeypatch) -> None:
    """Single entry in kernels.json suffices; no notebook env or cwd sidecar."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JUPYLINK_IDE_CONNECTION_FILE", raising=False)
    monkeypatch.setenv("JUPYLINK_IDE_REGISTRY_SINGLE", "1")
    nb = tmp_path / "n.ipynb"
    nb.write_text("{}", encoding="utf-8")
    cf = tmp_path / "k.json"
    cf.write_text("{}", encoding="utf-8")
    fe = tmp_path / "front.json"
    fe.write_text("{}", encoding="utf-8")
    kr.register(nb, str(cf))
    assert kip.discover_connection_via_registry_single(fe.resolve()) == str(cf.resolve())


def test_proxy_roundtrip_with_kernel_manager(tmp_path, monkeypatch) -> None:
    pytest.importorskip("jupyter_client.manager")
    from jupyter_client.blocking.client import BlockingKernelClient
    from jupyter_client.connect import write_connection_file
    from jupyter_client.manager import KernelManager

    monkeypatch.setenv("JUPYLINK_IDE_REUSE", "0")

    km = KernelManager(kernel_name="python3")
    km.start_kernel()
    try:
        cf_a = km.connection_file
        cf_b, _cfg_b = write_connection_file(str(tmp_path / "bridge.json"))

        stop = threading.Event()

        def run_proxy() -> None:
            try:
                kip.run_ide_proxy(cf_b, cf_a)
            finally:
                stop.set()

        t = threading.Thread(target=run_proxy, daemon=True)
        t.start()
        deadline = time.time() + 15
        while time.time() < deadline:
            if Path(cf_b).exists() and json.loads(Path(cf_b).read_text(encoding="utf-8")).get("shell_port"):
                break
            time.sleep(0.05)
        time.sleep(0.3)

        kc = BlockingKernelClient()
        kc.load_connection_file(cf_b)
        kc.start_channels()
        kc.wait_for_ready(timeout=15)
        reply = kc.execute("40 + 2", reply=True, timeout=15)
        assert reply is not None
        assert reply["content"]["status"] == "ok"
        kc.stop_channels()
    finally:
        try:
            km.shutdown_kernel(now=True)
        except Exception:
            pass


def test_active_notebook_hint_unblocks_registry_single_require_hint(
    isolated_registry, tmp_path, monkeypatch
) -> None:
    """MCP-style ``write_active_notebook_hint`` lets IDE bridge when strict hint is required."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JUPYLINK_IDE_CONNECTION_FILE", raising=False)
    monkeypatch.setenv("JUPYLINK_IDE_REGISTRY_SINGLE", "1")
    monkeypatch.setenv("JUPYLINK_IDE_REGISTRY_SINGLE_REQUIRE_NOTEBOOK_HINT", "1")
    nb = tmp_path / "n.ipynb"
    nb.write_text("{}", encoding="utf-8")
    cf = tmp_path / "k.json"
    cf.write_text("{}", encoding="utf-8")
    fe = tmp_path / "front.json"
    fe.write_text("{}", encoding="utf-8")
    kr.register(nb, str(cf))
    kr.write_active_notebook_hint(nb)
    assert kip.resolve_existing_connection_for_ide(str(fe)) == str(cf.resolve())


def test_resolve_unique_live_picks_only_heartbeat_ok(
    isolated_registry, tmp_path, monkeypatch
) -> None:
    """Several registry rows but only one live kernel → bridge to that connection file."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JUPYLINK_IDE_CONNECTION_FILE", raising=False)
    monkeypatch.setenv("JUPYLINK_IDE_REGISTRY_SINGLE", "0")
    for name in ("a", "b"):
        (tmp_path / f"{name}.ipynb").write_text("{}", encoding="utf-8")
    cf1 = tmp_path / "k1.json"
    cf2 = tmp_path / "k2.json"
    cf1.write_text("{}", encoding="utf-8")
    cf2.write_text("{}", encoding="utf-8")
    fe = tmp_path / "front.json"
    fe.write_text("{}", encoding="utf-8")
    kr.register(tmp_path / "a.ipynb", str(cf1))
    kr.register(tmp_path / "b.ipynb", str(cf2))

    def probe(cf: str, *a, **k) -> bool:
        return Path(cf).resolve() == cf2.resolve()

    monkeypatch.setattr(kip, "probe_kernel_connection_file", probe)
    assert kip.resolve_existing_connection_for_ide(str(fe)) == str(cf2.resolve())

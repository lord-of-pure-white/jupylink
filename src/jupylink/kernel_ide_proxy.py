"""Bridge IDE-provisioned ZMQ ports to an existing Jupyter kernel (e.g. MCP-spawned).

The parent (VS Code / Jupyter) creates connection file *B* with key Kb and starts this
process. An MCP-spawned kernel uses connection file *A* with Ka. Raw frame forwarding is
not enough (HMAC differs); we deserialize with one Session and re-send with another.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections import deque
from pathlib import Path
from typing import Any

import zmq
from jupyter_client.session import Session

logger = logging.getLogger(__name__)


def parse_connection_file_from_argv(argv: list[str]) -> str | None:
    """Extract ``-f`` / ``--f=`` path from argv (ipykernel / VS Code style)."""
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-f", "--f") and i + 1 < len(argv):
            return argv[i + 1].strip().strip('"')
        if a.startswith("--f=") or a.startswith("-f="):
            return a.split("=", 1)[1].strip().strip('"')
        i += 1
    return None


def _session_from_cfg(cfg: dict[str, Any]) -> Session:
    s = Session()
    key = cfg.get("key", "")
    if isinstance(key, str):
        s.key = key.encode("utf-8")
    else:
        s.key = key
    s.signature_scheme = cfg.get("signature_scheme", "hmac-sha256")
    s.check_pid = False
    return s


def _url(cfg: dict[str, Any], port_key: str) -> str:
    transport = cfg.get("transport", "tcp")
    ip = cfg["ip"]
    port = cfg[port_key]
    if transport == "tcp":
        return f"tcp://{ip}:{port}"
    return f"{transport}://{ip}-{port}"


def _ide_notebook_path_for_reuse() -> Path | None:
    for key in ("JUPYLINK_IDE_NOTEBOOK_PATH", "JUPYTER_NOTEBOOK_PATH"):
        v = os.environ.get(key, "").strip()
        if v.endswith(".ipynb"):
            p = Path(v).expanduser()
            if p.is_file():
                return p.resolve()
    sn = os.environ.get("JPY_SESSION_NAME", "").strip()
    if sn.endswith(".ipynb"):
        p = Path(sn).expanduser()
        if p.is_file():
            return p.resolve()
    if os.environ.get("JUPYLINK_IDE_REUSE_UNIQUE", "").lower() in ("1", "true", "yes"):
        from .kernel_registry import list_kernels

        kernels = list_kernels()
        if len(kernels) == 1:
            p = Path(kernels[0]["notebook_path"])
            if p.is_file():
                return p.resolve()
    return None


_SKIP_WALK_DIRS = frozenset(
    {
        "node_modules",
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".tox",
        "dist",
        "build",
        ".mypy_cache",
        ".ruff_cache",
        ".cursor",
    }
)


def _iter_jupylink_sidecar_files(root: Path, max_depth: int):
    from .kernel_registry import KERNEL_SIDECAR_SUFFIX

    root = root.resolve()
    if not root.is_dir():
        return
    suffix = KERNEL_SIDECAR_SUFFIX
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _SKIP_WALK_DIRS
            and not d.startswith(".pytest")
            and not d.startswith("pytest-of-")
        ]
        dp = Path(dirpath)
        try:
            rel_parts = dp.relative_to(root).parts
        except ValueError:
            continue
        if len(rel_parts) > max_depth:
            dirnames[:] = []
            continue
        for fn in filenames:
            if fn.endswith(suffix):
                yield dp / fn


def discover_connection_via_workspace_sidecars(frontend_cf: Path) -> str | None:
    """If cwd tree has exactly one valid ``*.jupylink_kernel.json``, return its connection file.

    Sidecars are written next to notebooks when a kernel registers (MCP or IDE).
    """
    if os.environ.get("JUPYLINK_IDE_SIDECAR", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        max_depth = int(os.environ.get("JUPYLINK_IDE_SIDECAR_DEPTH", "12"))
    except (ValueError, TypeError):
        max_depth = 12

    root = Path.cwd()
    candidates: set[str] = set()
    for sp in _iter_jupylink_sidecar_files(root, max_depth):
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
            cf = data.get("connection_file")
            if not cf:
                continue
            p = Path(cf).resolve()
            if not p.is_file():
                continue
            if p == frontend_cf.resolve():
                continue
            candidates.add(str(p))
        except Exception:
            logger.debug("Bad or unreadable sidecar %s", sp, exc_info=True)
            continue
    if len(candidates) == 1:
        return candidates.pop()
    return None


def discover_connection_via_registry_single(frontend_cf: Path) -> str | None:
    """If the global registry has exactly one live kernel, use it (no cwd, no /tmp).

    Data lives under the same user directory as ``kernels.json`` (e.g. ``~/.jupylink/`` or
    ``%APPDATA%/jupylink/``), so it survives reboots and does not depend on IDE cwd.
    """
    if os.environ.get("JUPYLINK_IDE_REGISTRY_SINGLE", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return None

    from .kernel_registry import list_kernels

    kernels = list_kernels()
    if len(kernels) != 1:
        return None
    cf = kernels[0].get("connection_file")
    if not cf:
        return None
    p = Path(cf).resolve()
    if not p.is_file() or p == frontend_cf.resolve():
        return None
    return str(p)


def resolve_existing_connection_for_ide(frontend_cf: str) -> str | None:
    """Return path to an existing kernel connection JSON to bridge to, or None."""
    v = os.environ.get("JUPYLINK_IDE_REUSE", "1").strip().lower()
    if v in ("0", "false", "no", "off"):
        return None

    fe = Path(frontend_cf).resolve()

    explicit = os.environ.get("JUPYLINK_IDE_CONNECTION_FILE", "").strip()
    if explicit:
        ep = Path(explicit).resolve()
        if ep.is_file() and ep != fe:
            return str(ep)

    via_registry = discover_connection_via_registry_single(fe)
    if via_registry:
        return via_registry

    via_sidecar = discover_connection_via_workspace_sidecars(fe)
    if via_sidecar:
        return via_sidecar

    nb = _ide_notebook_path_for_reuse()
    if nb is None:
        return None

    from .kernel_registry import get_connection_file

    existing = get_connection_file(nb)
    if not existing:
        return None
    ex = Path(existing).resolve()
    if ex == fe or not ex.is_file():
        return None
    return str(ex)


def run_ide_proxy(frontend_cf: str, existing_cf: str) -> None:
    """Bind *frontend* ports and forward to *existing* kernel (blocking)."""
    with open(frontend_cf, encoding="utf-8") as f:
        cfg_b = json.load(f)
    with open(existing_cf, encoding="utf-8") as f:
        cfg_a = json.load(f)

    session_b = _session_from_cfg(cfg_b)
    session_a = _session_from_cfg(cfg_a)

    ctx = zmq.Context()

    # Frontend (we act as kernel): ROUTER shell/stdin/control, PUB iopub, REP hb
    b_shell = ctx.socket(zmq.ROUTER)
    b_shell.bind(_url(cfg_b, "shell_port"))

    b_control = ctx.socket(zmq.ROUTER)
    b_control.bind(_url(cfg_b, "control_port"))

    b_stdin = ctx.socket(zmq.ROUTER)
    b_stdin.bind(_url(cfg_b, "stdin_port"))

    b_iopub = ctx.socket(zmq.PUB)
    b_iopub.bind(_url(cfg_b, "iopub_port"))

    b_hb = ctx.socket(zmq.REP)
    b_hb.bind(_url(cfg_b, "hb_port"))

    # Upstream (we act as client to real kernel)
    a_shell = ctx.socket(zmq.DEALER)
    a_shell.connect(_url(cfg_a, "shell_port"))

    a_control = ctx.socket(zmq.DEALER)
    a_control.connect(_url(cfg_a, "control_port"))

    a_stdin = ctx.socket(zmq.DEALER)
    a_stdin.connect(_url(cfg_a, "stdin_port"))

    a_iopub = ctx.socket(zmq.SUB)
    a_iopub.setsockopt(zmq.SUBSCRIBE, b"")
    a_iopub.connect(_url(cfg_a, "iopub_port"))

    a_hb = ctx.socket(zmq.REQ)
    a_hb.connect(_url(cfg_a, "hb_port"))

    poller = zmq.Poller()
    for s in (b_shell, a_shell, b_control, a_control, b_stdin, a_stdin, a_iopub, b_hb):
        poller.register(s, zmq.POLLIN)

    shell_pending: deque[list[bytes]] = deque()
    control_pending: deque[list[bytes]] = deque()
    last_routing_idents: list[bytes] = []

    logger.info("IDE bridge: listening on %s; upstream %s", frontend_cf, existing_cf)

    def drain_iopub() -> None:
        while True:
            try:
                raw = a_iopub.recv_multipart(zmq.NOBLOCK)
            except zmq.Again:
                break
            try:
                idents, rest = session_a.feed_identities(raw)
                msg = session_a.deserialize(rest)
            except Exception:
                logger.exception("iopub deserialize failed")
                continue
            try:
                topic = idents if idents else [b""]
                session_b.send(b_iopub, msg, ident=topic)
            except Exception:
                logger.exception("iopub forward failed")

    try:
        while True:
            drain_iopub()
            events = dict(poller.poll(500))
            if not events:
                continue

            if b_hb in events:
                try:
                    ping = b_hb.recv(zmq.NOBLOCK)
                    a_hb.send(ping)
                    pong = a_hb.recv()
                    b_hb.send(pong)
                except Exception:
                    logger.exception("heartbeat bridge failed")

            drain_iopub()

            if b_shell in events:
                try:
                    idents, msg = session_b.recv(b_shell, zmq.NOBLOCK)
                    if msg is not None:
                        last_routing_idents.clear()
                        last_routing_idents.extend(idents)
                        shell_pending.append(idents)
                        session_a.send(a_shell, msg)
                except zmq.Again:
                    pass
                except Exception:
                    logger.exception("shell recv from frontend failed")

            if a_shell in events:
                try:
                    _, msg = session_a.recv(a_shell, zmq.NOBLOCK)
                    if msg is not None:
                        if not shell_pending:
                            logger.warning("shell reply without pending request; dropping")
                            continue
                        idents_f = shell_pending.popleft()
                        session_b.send(b_shell, msg, ident=idents_f)
                except zmq.Again:
                    pass
                except Exception:
                    logger.exception("shell recv from upstream failed")

            if b_control in events:
                try:
                    idents, msg = session_b.recv(b_control, zmq.NOBLOCK)
                    if msg is not None:
                        control_pending.append(idents)
                        session_a.send(a_control, msg)
                except zmq.Again:
                    pass
                except Exception:
                    logger.exception("control recv from frontend failed")

            if a_control in events:
                try:
                    _, msg = session_a.recv(a_control, zmq.NOBLOCK)
                    if msg is not None:
                        if not control_pending:
                            logger.warning("control reply without pending; dropping")
                            continue
                        idents_f = control_pending.popleft()
                        session_b.send(b_control, msg, ident=idents_f)
                except zmq.Again:
                    pass
                except Exception:
                    logger.exception("control recv from upstream failed")

            if b_stdin in events:
                try:
                    idents, msg = session_b.recv(b_stdin, zmq.NOBLOCK)
                    if msg is not None:
                        session_a.send(a_stdin, msg)
                except zmq.Again:
                    pass
                except Exception:
                    logger.exception("stdin recv from frontend failed")

            if a_stdin in events:
                try:
                    _, msg = session_a.recv(a_stdin, zmq.NOBLOCK)
                    if msg is not None:
                        route = list(shell_pending[0]) if shell_pending else list(last_routing_idents)
                        if not route:
                            logger.warning("stdin from kernel without shell routing context")
                            continue
                        session_b.send(b_stdin, msg, ident=route)
                except zmq.Again:
                    pass
                except Exception:
                    logger.exception("stdin recv from upstream failed")

    finally:
        for s in (
            b_shell,
            b_control,
            b_stdin,
            b_iopub,
            b_hb,
            a_shell,
            a_control,
            a_stdin,
            a_iopub,
            a_hb,
        ):
            try:
                s.close(linger=0)
            except Exception:
                pass
        ctx.term()


def maybe_run_ide_proxy_from_argv(argv: list[str] | None = None) -> bool:
    """If reuse applies, run proxy and return True; else return False."""
    argv = argv if argv is not None else sys.argv
    frontend_cf = parse_connection_file_from_argv(argv)
    if not frontend_cf or not Path(frontend_cf).is_file():
        return False

    existing = resolve_existing_connection_for_ide(frontend_cf)
    if not existing:
        return False

    if os.environ.get("JUPYLINK_IDE_PROXY_LOG", "").strip() in ("1", "debug", "true", "yes"):
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)
    else:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    logger.info(
        "JupyLink: bridging IDE connection file to existing kernel (set JUPYLINK_IDE_REUSE=0 to disable)"
    )
    run_ide_proxy(frontend_cf, existing)
    return True

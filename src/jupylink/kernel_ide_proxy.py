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


def probe_kernel_connection_file(connection_file: str, timeout: float | None = None) -> bool:
    """Return True if a kernel appears to answer on the heartbeat channel.

    Connection JSON may still exist on disk after the process exits; this weeds out
    most dead kernels without blocking the IDE proxy loop (used only at bridge resolve).

    Timeout defaults to ``JUPYLINK_IDE_PROBE_TIMEOUT`` (seconds, default ``0.6``).
    """
    if timeout is None:
        try:
            timeout = float(os.environ.get("JUPYLINK_IDE_PROBE_TIMEOUT", "0.6").strip())
        except (ValueError, TypeError):
            timeout = 0.6

    try:
        from jupyter_client.blocking.client import BlockingKernelClient
    except ImportError:
        logger.debug("jupyter_client not available; skipping connection probe")
        return True

    kc: Any = None
    try:
        kc = BlockingKernelClient()
        kc.load_connection_file(connection_file)
        kc.start_channels()
        kc.wait_for_ready(timeout=timeout)
        return True
    except Exception:
        logger.debug("Connection probe failed for %s", connection_file, exc_info=True)
        return False
    finally:
        if kc is not None:
            try:
                kc.stop_channels()
            except Exception:
                pass


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


def _explicit_ide_notebook_file() -> Path | None:
    """Notebook path from IDE/Jupyter env (not ``JUPYLINK_IDE_REUSE_UNIQUE``)."""
    from .kernel_registry import resolve_notebook_filesystem_path

    for key in (
        "JUPYLINK_IDE_NOTEBOOK_PATH",
        "JUPYTER_NOTEBOOK_PATH",
        "JPY_SESSION_NAME",
        "JUPYLINK_NOTEBOOK_PATH",
    ):
        v = os.environ.get(key, "").strip()
        if v.endswith(".ipynb"):
            try:
                p = resolve_notebook_filesystem_path(v)
                if p.is_file():
                    return p
            except (OSError, ValueError):
                pass
    return None


def _ide_notebook_path_for_reuse() -> Path | None:
    from .kernel_registry import resolve_notebook_filesystem_path

    for key in ("JUPYLINK_IDE_NOTEBOOK_PATH", "JUPYTER_NOTEBOOK_PATH"):
        v = os.environ.get(key, "").strip()
        if v.endswith(".ipynb"):
            try:
                p = resolve_notebook_filesystem_path(v)
                if p.is_file():
                    return p
            except (OSError, ValueError):
                pass
    sn = os.environ.get("JPY_SESSION_NAME", "").strip()
    if sn.endswith(".ipynb"):
        try:
            p = resolve_notebook_filesystem_path(sn)
            if p.is_file():
                return p
        except (OSError, ValueError):
            pass
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


def _sidecar_scan_roots() -> list[Path]:
    """Directories to scan for ``*.jupylink_kernel.json`` (cwd alone is often wrong in IDEs)."""
    roots: list[Path] = []
    extra = os.environ.get("JUPYLINK_IDE_SIDECAR_ROOT", "").strip()
    if extra:
        ep = Path(extra).expanduser().resolve()
        if ep.is_dir():
            roots.append(ep)
    roots.append(Path.cwd().resolve())
    nb = _explicit_ide_notebook_file()
    if nb is not None:
        roots.append(nb.parent.resolve())
    seen: set[Path] = set()
    out: list[Path] = []
    for r in roots:
        if r in seen or not r.is_dir():
            continue
        seen.add(r)
        out.append(r)
    return out


def discover_connection_via_workspace_sidecars(frontend_cf: Path) -> str | None:
    """Resolve upstream connection via sidecar next to the notebook or under scan roots.

    Prefer an explicit notebook env (``JUPYTER_NOTEBOOK_PATH``, etc.): read that
    notebook's sidecar directly so cwd and multi-notebook trees do not block matching.
    """
    if os.environ.get("JUPYLINK_IDE_SIDECAR", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        max_depth = int(os.environ.get("JUPYLINK_IDE_SIDECAR_DEPTH", "12"))
    except (ValueError, TypeError):
        max_depth = 12

    fe = frontend_cf.resolve()

    nb = _explicit_ide_notebook_file()
    if nb is not None:
        from .kernel_registry import sidecar_path_for_notebook

        sp = sidecar_path_for_notebook(nb)
        if sp is not None and sp.is_file():
            try:
                data = json.loads(sp.read_text(encoding="utf-8"))
                cf = data.get("connection_file")
                if cf:
                    p = Path(cf).resolve()
                    if p.is_file() and p != fe:
                        return str(p)
            except Exception:
                logger.debug("Bad sidecar for hinted notebook %s", sp, exc_info=True)

    candidates: set[str] = set()
    for root in _sidecar_scan_roots():
        for sp in _iter_jupylink_sidecar_files(root, max_depth):
            try:
                data = json.loads(sp.read_text(encoding="utf-8"))
                cf = data.get("connection_file")
                if not cf:
                    continue
                p = Path(cf).resolve()
                if not p.is_file():
                    continue
                if p == fe:
                    continue
                candidates.add(str(p))
            except Exception:
                logger.debug("Bad or unreadable sidecar %s", sp, exc_info=True)
                continue
    if len(candidates) == 1:
        return candidates.pop()
    return None


def discover_connection_via_registry_single(frontend_cf: Path) -> str | None:
    """If the global registry has exactly one live kernel, use it when it matches hints.

    When ``JUPYTER_NOTEBOOK_PATH`` / ``JUPYLINK_IDE_NOTEBOOK_PATH`` / ``JPY_SESSION_NAME``
    points at a real notebook, the sole registry entry must be for that notebook (same
    canonical path); otherwise we avoid bridging the IDE to the wrong kernel.

    Set ``JUPYLINK_IDE_REGISTRY_SINGLE_REQUIRE_NOTEBOOK_HINT=1`` to refuse sole-registry
    bridging when none of those env vars resolve to a file (avoids wrong-kernel reuse
    when the IDE did not export a notebook path).
    """
    if os.environ.get("JUPYLINK_IDE_REGISTRY_SINGLE", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return None

    from .kernel_registry import _normalize, list_kernels

    kernels = list_kernels()
    if len(kernels) != 1:
        return None
    hint = _explicit_ide_notebook_file()
    nb_reg = kernels[0]["notebook_path"]
    if hint is not None:
        if _normalize(nb_reg) != _normalize(hint):
            return None
    elif os.environ.get("JUPYLINK_IDE_REGISTRY_SINGLE_REQUIRE_NOTEBOOK_HINT", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
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
    probe_on = os.environ.get("JUPYLINK_IDE_CONNECTION_PROBE", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )

    ordered: list[str] = []

    explicit = os.environ.get("JUPYLINK_IDE_CONNECTION_FILE", "").strip()
    if explicit:
        ep = Path(explicit).resolve()
        if ep.is_file() and ep != fe:
            ordered.append(str(ep))

    via_registry = discover_connection_via_registry_single(fe)
    if via_registry:
        ordered.append(via_registry)

    via_sidecar = discover_connection_via_workspace_sidecars(fe)
    if via_sidecar:
        ordered.append(via_sidecar)

    nb = _ide_notebook_path_for_reuse()
    if nb is not None:
        from .kernel_registry import get_connection_file

        existing = get_connection_file(nb)
        if existing:
            ordered.append(existing)

    seen: set[str] = set()
    for cand in ordered:
        cr = str(Path(cand).resolve())
        if cr in seen or cr == str(fe):
            continue
        seen.add(cr)
        if not Path(cr).is_file():
            continue
        if not probe_on or probe_kernel_connection_file(cr):
            return cr
    return None


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
    hb_front_queue: deque[bytes] = deque()
    hb_upstream_pending = False

    logger.info("IDE bridge: listening on %s; upstream %s", frontend_cf, existing_cf)

    # Real ipykernel exits after forwarding shutdown_reply; we must too or Cursor restart hangs.
    exit_after_shutdown_reply = False

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

    def _hb_start_upstream_if_idle() -> None:
        nonlocal hb_upstream_pending
        if hb_upstream_pending or not hb_front_queue:
            return
        ping = hb_front_queue.popleft()
        try:
            a_hb.send(ping)
            hb_upstream_pending = True
            poller.register(a_hb, zmq.POLLIN)
        except Exception:
            logger.exception("heartbeat forward to upstream failed")
            hb_front_queue.appendleft(ping)

    try:
        while True:
            drain_iopub()
            events = dict(poller.poll(500))
            if not events:
                continue

            if b_hb in events:
                try:
                    while True:
                        hb_front_queue.append(b_hb.recv(zmq.NOBLOCK))
                except zmq.Again:
                    pass
                except Exception:
                    logger.exception("heartbeat recv from frontend failed")
                _hb_start_upstream_if_idle()

            if a_hb in events and hb_upstream_pending:
                try:
                    pong = a_hb.recv(zmq.NOBLOCK)
                    b_hb.send(pong)
                except zmq.Again:
                    pass
                except Exception:
                    logger.exception("heartbeat bridge failed")
                else:
                    try:
                        poller.unregister(a_hb)
                    except Exception:
                        pass
                    hb_upstream_pending = False
                    _hb_start_upstream_if_idle()

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
                        if msg.get("header", {}).get("msg_type") == "shutdown_reply":
                            exit_after_shutdown_reply = True
                            break
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
        if hb_upstream_pending:
            try:
                poller.unregister(a_hb)
            except Exception:
                pass
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

    if exit_after_shutdown_reply:
        logger.info("IDE bridge exiting after shutdown_reply (kernel restart/shutdown).")
        sys.exit(0)


def maybe_run_ide_proxy_from_argv(argv: list[str] | None = None) -> bool:
    """If reuse applies, run proxy and return True; else return False.

    When bridging, this process is only a ZMQ proxy; the real JupyLinkKernel runs in the
    upstream MCP/CLI kernel process (records, registry, execute hooks live there).
    """
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
    try:
        run_ide_proxy(frontend_cf, existing)
    except Exception:
        logger.exception(
            "IDE bridge failed (bind/connect/upstream); falling back to in-process kernel. "
            "Disable reuse with JUPYLINK_IDE_REUSE=0 if this persists."
        )
        return False
    return True

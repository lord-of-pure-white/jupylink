"""Execute notebook cells via jupyter_client."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Optional, Union

import nbformat
from jupyter_client.blocking import BlockingKernelClient
from jupyter_client.manager import start_new_kernel

from .ipynb_ops import get_cell_source, update_cell_output as update_ipynb_output
from .kernel_registry import (
    cleanup_stale,
    get_connection_file,
    register,
    resolve_notebook_filesystem_path,
    spawn_lock,
    unregister,
    write_active_notebook_hint,
)
from .record_manager import RecordManager, extract_rich_output

logger = logging.getLogger(__name__)

_DEFAULT_EXEC_TIMEOUT = 60
_KERNEL_READY_TIMEOUT = 5

# Keep kernel managers alive so they don't get GC'd and kill the kernel.
# Keyed by normalized notebook path.
_active_kms: dict[str, Any] = {}


def _shutdown_old_kernel(path_str):
    """Shut down and unregister the previously-cached kernel for *path_str*."""
    old = _active_kms.pop(path_str, None)
    if old is None:
        return
    try:
        old.shutdown_kernel(now=True)
    except Exception:
        logger.debug("Failed to shut down old kernel for %s", path_str, exc_info=True)


def _get_exec_timeout():
    """Read execution timeout from env, defaulting to 60s."""
    try:
        return int(os.environ.get("JUPYLINK_EXEC_TIMEOUT", str(_DEFAULT_EXEC_TIMEOUT)))
    except (ValueError, TypeError):
        return _DEFAULT_EXEC_TIMEOUT


def _output_hook_impl(msg, captured):
    """Extract output from IOPub message into captured list."""
    msg_type = msg.get("header", {}).get("msg_type", "")
    content = msg.get("content", {})
    if msg_type in ("stream", "error", "execute_result", "display_data"):
        out = {"msg_type": msg_type}
        if msg_type == "stream":
            out["name"] = content.get("name", "stdout")
            out["text"] = content.get("text", "")
        elif msg_type == "error":
            out["ename"] = content.get("ename", "")
            out["evalue"] = content.get("evalue", "")
            out["traceback"] = content.get("traceback", [])
        elif msg_type in ("execute_result", "display_data"):
            out["data"] = content.get("data", {})
            out["metadata"] = content.get("metadata", {})
            if "execution_count" in content:
                out["execution_count"] = content["execution_count"]
        captured.append(out)


def _execute_with_metadata(kc, code, cell_id, captured):
    """Send execute_request with cellId in metadata, collect output, return reply."""
    timeout = _get_exec_timeout()
    content = {
        "code": code,
        "silent": False,
        "store_history": True,
        "user_expressions": {},
        "allow_stdin": False,
        "stop_on_error": True,
    }
    msg = kc.session.msg("execute_request", content, metadata={"cellId": cell_id})
    msg_id = msg["header"]["msg_id"]
    kc.shell_channel.send(msg)

    deadline = time.monotonic() + timeout
    got_idle = False
    while time.monotonic() < deadline:
        try:
            iopub_msg = kc.iopub_channel.get_msg(timeout=1.0)
        except Exception:
            continue
        if iopub_msg.get("parent_header", {}).get("msg_id") != msg_id:
            continue
        _output_hook_impl(iopub_msg, captured)
        if (
            iopub_msg.get("header", {}).get("msg_type") == "status"
            and iopub_msg.get("content", {}).get("execution_state") == "idle"
        ):
            got_idle = True
            break

    if not got_idle:
        logger.warning("IOPub did not reach idle within %ds for cell %s", timeout, cell_id)

    reply_timeout = min(max(5, deadline - time.monotonic()), 10)
    try:
        return kc._recv_reply(msg_id, timeout=reply_timeout)
    except Exception:
        logger.warning("Shell reply timed out for cell %s", cell_id)
        return None


def _execute_with_client(kc, code, cell_id=None, notebook_path=None):
    """Execute code with a kernel client and return result."""
    captured: list = []

    def output_hook(msg):
        _output_hook_impl(msg, captured)

    try:
        if cell_id:
            reply = _execute_with_metadata(kc, code, cell_id, captured)
        else:
            reply = kc.execute_interactive(
                code,
                output_hook=output_hook,
                allow_stdin=False,
                timeout=_get_exec_timeout(),
            )
    except Exception:
        logger.exception("Execution failed for cell %s", cell_id)
        return None

    if reply is None:
        return None
    if cell_id and notebook_path:
        extract_rich_output(captured, notebook_path, cell_id)
    content = reply.get("content", {})
    result = {
        "status": content.get("status", "ok"),
        "execution_count": content.get("execution_count"),
        "output": captured,
        "ename": content.get("ename"),
        "evalue": content.get("evalue"),
        "traceback": content.get("traceback", []),
    }
    if cell_id and notebook_path:
        update_ipynb_output(
            notebook_path, cell_id, captured, result.get("execution_count")
        )
        RecordManager.sync_record(notebook_path)
    return result


def _connect_existing_kernel(path):
    """Try to connect to an existing JupyLink kernel for this notebook."""
    cf = get_connection_file(path)
    if not cf:
        return None
    try:
        kc = BlockingKernelClient()
        kc.load_connection_file(cf)
        kc.start_channels()
        kc.wait_for_ready(timeout=_KERNEL_READY_TIMEOUT)
        return kc
    except Exception:
        logger.debug("Could not connect to existing kernel for %s", path, exc_info=True)
        try:
            kc.stop_channels()
        except Exception:
            pass
        unregister(path)
        _active_kms.pop(str(path), None)
        return None


def _read_notebook_kernel_name(path):
    """Read the kernel name from a notebook's kernelspec metadata."""
    try:
        nb = nbformat.read(str(path), as_version=nbformat.NO_CONVERT)
    except Exception:
        return None
    return (nb.metadata.get("kernelspec", {}) or {}).get("name")


def _find_kernel_name(path):
    """Find the best kernel name for this notebook."""
    from jupyter_client.kernelspec import get_kernel_spec

    try:
        get_kernel_spec("jupylink")
        return "jupylink"
    except Exception:
        pass

    nb_name = _read_notebook_kernel_name(path)
    if nb_name and "python2" in nb_name.lower():
        try:
            get_kernel_spec("jupylink2")
            logger.info("Using jupylink2 kernel for python2 notebook")
            return "jupylink2"
        except Exception:
            pass

    return nb_name or "python3"


def _spawn_kernel(path):
    """Spawn a new kernel. Saves the kernel manager to prevent GC from killing it."""
    env = os.environ.copy()
    p = str(path)
    env["JUPYTER_NOTEBOOK_PATH"] = p
    env["JUPYLINK_NOTEBOOK_PATH"] = p

    kernel_name = _find_kernel_name(path)
    logger.info("Starting kernel '%s' for %s", kernel_name, p)

    try:
        km, kc = start_new_kernel(kernel_name=kernel_name, env=env, independent=True)
        # Keep km alive so the kernel process is not killed by GC
        path_key = str(resolve_notebook_filesystem_path(path))
        _shutdown_old_kernel(path_key)
        _active_kms[path_key] = km
        return km, kc
    except Exception:
        logger.exception("Failed to spawn kernel for %s", path)
        return None


def execute_cell(notebook_path, cell_id):
    """Execute a cell by cell_id and return status, output, execution_count.

    Connects to the existing JupyLink kernel when available (even across
    separate CLI invocations). Spawns a new kernel only on first use.
    """
    cleanup_stale()
    path = resolve_notebook_filesystem_path(notebook_path)
    if not os.path.exists(str(path)):
        return None
    write_active_notebook_hint(path)

    code = get_cell_source(path, cell_id)
    if code is None:
        return None

    kc = _connect_existing_kernel(path)
    if kc:
        try:
            return _execute_with_client(kc, code, cell_id=cell_id, notebook_path=path)
        finally:
            kc.stop_channels()

    with spawn_lock():
        kc = _connect_existing_kernel(path)
        if kc:
            try:
                return _execute_with_client(kc, code, cell_id=cell_id, notebook_path=path)
            finally:
                kc.stop_channels()

        pair = _spawn_kernel(path)
        if not pair:
            return None
        km, kc = pair
        cf = getattr(km, "connection_file", None)
        if cf:
            register(path, cf)
        try:
            result = _execute_with_client(kc, code, cell_id=cell_id, notebook_path=path)
            return result
        finally:
            kc.stop_channels()


def execute_cells(notebook_path, cell_ids):
    """Execute multiple cells in sequence, sharing the same kernel."""
    cleanup_stale()
    path = resolve_notebook_filesystem_path(notebook_path)
    if not os.path.exists(str(path)):
        return []
    write_active_notebook_hint(path)

    codes = []
    for cid in cell_ids:
        code = get_cell_source(path, cid)
        if code is None:
            logger.error("Cell not found: %s", cid)
            return []
        codes.append((cid, code))

    def _run(kc):
        results = []
        for cid, code in codes:
            r = _execute_with_client(kc, code, cell_id=cid, notebook_path=path)
            if r is None:
                results.append({"status": "error", "cell_id": cid, "error": "execution failed"})
            else:
                results.append(r)
        return results

    kc = _connect_existing_kernel(path)
    if kc:
        try:
            return _run(kc)
        finally:
            kc.stop_channels()

    with spawn_lock():
        kc = _connect_existing_kernel(path)
        if kc:
            try:
                return _run(kc)
            finally:
                kc.stop_channels()

        pair = _spawn_kernel(path)
        if not pair:
            return []
        km, kc = pair
        cf = getattr(km, "connection_file", None)
        if cf:
            register(path, cf)
        try:
            return _run(kc)
        finally:
            kc.stop_channels()

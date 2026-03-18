"""Execute notebook cells via jupyter_client."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from jupyter_client.blocking import BlockingKernelClient
from jupyter_client.manager import start_new_kernel

from .ipynb_ops import get_cell_source, update_cell_output as update_ipynb_output
from .kernel_registry import get_connection_file
from .notify_ide import request_notebook_refresh
from .record_manager import RecordManager

logger = logging.getLogger(__name__)

_DEFAULT_EXEC_TIMEOUT = 60
_KERNEL_READY_TIMEOUT = 5
_KERNEL_REGISTRATION_POLL_INTERVAL = 0.3
_KERNEL_REGISTRATION_MAX_WAIT = 3.0


def _get_exec_timeout() -> int:
    """Read execution timeout from env, defaulting to 60s."""
    try:
        return int(os.environ.get("JUPYLINK_EXEC_TIMEOUT", _DEFAULT_EXEC_TIMEOUT))
    except (ValueError, TypeError):
        return _DEFAULT_EXEC_TIMEOUT


def _output_hook_impl(msg: dict[str, Any], captured: list[dict[str, Any]]) -> None:
    """Extract output from IOPub message into captured list."""
    msg_type = msg.get("header", {}).get("msg_type", "")
    content = msg.get("content", {})
    if msg_type in ("stream", "error", "execute_result", "display_data"):
        out: dict[str, Any] = {"msg_type": msg_type}
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


def _execute_with_metadata(
    kc: BlockingKernelClient, code: str, cell_id: str, captured: list[dict[str, Any]]
) -> dict[str, Any] | None:
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

    # Poll IOPub until status=idle for our request
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

    # Reserve dedicated time for the shell reply instead of using leftover deadline
    reply_timeout = min(max(5, deadline - time.monotonic()), 10)
    try:
        return kc._recv_reply(msg_id, timeout=reply_timeout)
    except Exception:
        logger.warning("Shell reply timed out for cell %s", cell_id)
        return None


def _execute_with_client(
    kc: BlockingKernelClient,
    code: str,
    cell_id: str | None = None,
    notebook_path: Path | None = None,
) -> dict[str, Any] | None:
    """Execute code with a kernel client and return result."""
    captured: list[dict[str, Any]] = []

    def output_hook(msg: dict[str, Any]) -> None:
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
        RecordManager.update_cell_output(
            notebook_path, cell_id, captured, result.get("execution_count")
        )
        update_ipynb_output(
            notebook_path, cell_id, captured, result.get("execution_count")
        )
    if notebook_path:
        request_notebook_refresh(notebook_path)
    return result


def _connect_existing_kernel(path: Path) -> BlockingKernelClient | None:
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
        return None


def _spawn_kernel(path: Path) -> tuple[Any, BlockingKernelClient] | None:
    """Spawn a new kernel for this notebook. Returns (km, kc) or None."""
    env = os.environ.copy()
    env["JUPYTER_NOTEBOOK_PATH"] = str(path)

    kernel_name = "jupylink"
    try:
        from jupyter_client.kernelspec import get_kernel_spec
        get_kernel_spec(kernel_name)
    except Exception:
        kernel_name = "python3"
        logger.info("jupylink kernelspec not found, falling back to python3")

    try:
        km, kc = start_new_kernel(kernel_name=kernel_name, env=env, independent=True)
        return km, kc
    except Exception:
        logger.exception("Failed to spawn kernel for %s", path)
        return None


def _wait_for_kernel_registration(path: Path) -> BlockingKernelClient | None:
    """Poll for the newly spawned kernel to register in the registry.

    Spawned kernels register themselves asynchronously; this avoids the race
    where the next execute_cell call can't find the kernel yet.
    """
    waited = 0.0
    while waited < _KERNEL_REGISTRATION_MAX_WAIT:
        time.sleep(_KERNEL_REGISTRATION_POLL_INTERVAL)
        waited += _KERNEL_REGISTRATION_POLL_INTERVAL
        cf = get_connection_file(path)
        if cf:
            logger.debug("Kernel registered after %.1fs", waited)
            return None
    return None


def execute_cell(notebook_path: str | Path, cell_id: str) -> dict[str, Any] | None:
    """Execute a cell by cell_id and return status, output, execution_count.

    First tries to connect to the existing JupyLink kernel for this notebook.
    If none is registered, spawns a new kernel and keeps it alive for reuse.
    """
    path = Path(notebook_path).resolve()
    if not path.exists():
        return None

    code = get_cell_source(path, cell_id)
    if code is None:
        return None

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
    try:
        result = _execute_with_client(kc, code, cell_id=cell_id, notebook_path=path)
        return result
    finally:
        kc.stop_channels()
        _wait_for_kernel_registration(path)


def execute_cells(
    notebook_path: str | Path, cell_ids: list[str]
) -> list[dict[str, Any]]:
    """Execute multiple cells in sequence, reusing the same kernel.

    Returns a list of results (one per cell). Use this when cells depend on each other.
    """
    path = Path(notebook_path).resolve()
    if not path.exists():
        return []

    codes: list[tuple[str, str]] = []
    for cid in cell_ids:
        code = get_cell_source(path, cid)
        if code is None:
            logger.error("Cell not found: %s", cid)
            return []
        codes.append((cid, code))

    def _run_with_client(kc: BlockingKernelClient) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
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
            return _run_with_client(kc)
        finally:
            kc.stop_channels()

    pair = _spawn_kernel(path)
    if not pair:
        return []
    km, kc = pair
    try:
        return _run_with_client(kc)
    finally:
        kc.stop_channels()
        _wait_for_kernel_registration(path)

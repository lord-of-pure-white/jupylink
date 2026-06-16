"""JupyLink Wrapper Kernel - intercepts execution and generates agent-friendly records."""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import json
import logging
import os
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional, Union

from ipykernel.ipkernel import IPythonKernel

from .kernel_registry import register, resolve_notebook_filesystem_path, unregister
from .magics import JupyLinkMagics
from .record_manager import RecordManager, _is_ide_injected_code

logger = logging.getLogger(__name__)


def _notebook_path_from_env_or_argv() -> Optional[str]:
    """Resolve ``.ipynb`` path from Jupyter/JupyLink env or argv (early registration)."""
    for key in (
        "JUPYTER_NOTEBOOK_PATH",
        "JPY_SESSION_NAME",
        "JUPYLINK_NOTEBOOK_PATH",
        "JUPYLINK_IDE_NOTEBOOK_PATH",
    ):
        raw = os.environ.get(key, "").strip()
        if raw.endswith(".ipynb"):
            try:
                p = resolve_notebook_filesystem_path(raw)
                if p.is_file():
                    return str(p)
            except (OSError, ValueError):
                pass
    for arg in sys.argv:
        if isinstance(arg, str) and arg.lower().endswith(".ipynb"):
            try:
                p = resolve_notebook_filesystem_path(arg)
                if p.is_file():
                    return str(p)
            except (OSError, ValueError):
                pass
    return None


def _discover_notebook_via_jupyter_api() -> Optional[str]:
    """Discover notebook path via Jupyter Server REST API.

    When running under Jupyter Lab / Notebook, the kernel can ask the server
    which notebook it belongs to.  The connection file name encodes the kernel
    id (``kernel-<id>.json``); we match that against the server's ``/api/sessions``
    response.  No IDE-specific metadata needed.
    """
    try:
        from ipykernel.connect import get_connection_file

        cf = get_connection_file()
    except Exception:
        return None
    if not cf:
        return None
    cf_stem = Path(cf).stem  # e.g. "kernel-<uuid>"

    # Extract both the full stem and uuid-only variant
    if cf_stem.startswith("kernel-"):
        kernel_id = cf_stem[len("kernel-"):]
    else:
        kernel_id = cf_stem

    # Locate the runtime directory
    try:
        from jupyter_core.paths import jupyter_runtime_dir

        runtime_dir = jupyter_runtime_dir()
    except ImportError:
        runtime_dir = os.path.join(os.path.expanduser("~"), ".local", "share", "jupyter", "runtime")
    if not os.path.isdir(runtime_dir):
        return None

    # Collect server connection files, newest first
    server_files: list[str] = []
    try:
        for fn in os.listdir(runtime_dir):
            if (fn.startswith("jpserver-") or fn.startswith("nbserver-")) and fn.endswith(".json"):
                server_files.append(os.path.join(runtime_dir, fn))
    except OSError:
        return None
    server_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)

    for sf in server_files:
        try:
            with open(sf, encoding="utf-8") as fh:
                srv = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        url = (srv.get("url") or "").rstrip("/")
        token = srv.get("token", "")
        root_dir = srv.get("root_dir", "")
        if not url:
            continue

        try:
            api_url = f"{url}/api/sessions?token={token}"
            req = urllib.request.Request(api_url)
            with urllib.request.urlopen(req, timeout=2) as resp:
                sessions = json.loads(resp.read())
        except Exception:
            continue

        for session in sessions:
            k = session.get("kernel", {})
            sid = k.get("id", "")
            # Match full stem or uuid-only
            if sid != cf_stem and sid != kernel_id:
                continue
            nb = session.get("notebook", {})
            rel_path = nb.get("path") or session.get("path", "")
            if not rel_path or not rel_path.endswith(".ipynb"):
                continue
            nb_path = rel_path
            if root_dir and not os.path.isabs(nb_path):
                nb_path = os.path.join(root_dir, nb_path)
            nb_path = os.path.abspath(nb_path)
            if os.path.isfile(nb_path):
                logger.info("Discovered notebook via Jupyter API: %s", nb_path)
                return nb_path

    return None


class _CapturingStreamWrapper:
    """Wraps stdout/stderr to capture output during execution.

    ipykernel sends stream output via session.send() from OutStream, not via
    send_response, so we must intercept at the stream level.
    """

    def __init__(self, real: Any, kernel: Any, name: str) -> None:
        self._real = real
        self._kernel = kernel
        self._name = name

    def write(self, s: str) -> Optional[int]:
        if self._kernel._capturing and s:
            self._kernel._captured_output.append({
                "msg_type": "stream",
                "content": {"name": self._name, "text": s},
            })
        return self._real.write(s)

    def set_parent(self, parent: dict) -> None:
        if hasattr(self._real, "set_parent"):
            self._real.set_parent(parent)

    def flush(self) -> None:
        self._real.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _uri_to_path(uri: str) -> Optional[str]:
    """Convert file URI to local path. Handles file:// and vscode-notebook-cell: style."""
    if not uri or not isinstance(uri, str):
        return None
    uri = uri.strip()
    # file:///e:/projects/... or file:///e%3A/projects/...
    if uri.startswith("file://"):
        try:
            parsed = urllib.parse.urlparse(uri)
            path = urllib.parse.unquote(parsed.path)
            if os.name == "nt" and path.startswith("/"):
                path = path[1:]
            return path
        except Exception:
            pass
    # vscode-notebook-cell:/e%3A/projects/jupylink/test.ipynb -> e:/projects/...
    if "/" in uri or "%" in uri:
        try:
            path = urllib.parse.unquote(uri)
            if os.name == "nt" and path.startswith("/") and len(path) > 2 and path[2] == ":":
                path = path[1] + ":" + path[3:]
            if path.endswith(".ipynb"):
                return path
        except Exception:
            pass
    if uri.endswith(".ipynb") and (os.path.exists(uri) or "/" in uri or "\\" in uri):
        return uri
    return None


class JupyLinkKernel(IPythonKernel):
    """Wrapper kernel that records execution for agent-friendly format."""

    implementation = "JupyLink"
    implementation_version = "0.1.0"
    banner = "JupyLink - Kernel proxy for agent-friendly execution records"

    _WATCHER_INTERVAL = 1.5  # seconds between ipynb mtime checks

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._record_manager = RecordManager()
        self.shell.register_magics(JupyLinkMagics)
        self._capturing = False
        self._captured_output: list[dict[str, Any]] = []
        self._registered_for_cli = False
        self._record_pipeline_lock = threading.Lock()
        self._watcher_started = False
        self._setup_notebook_path()
        self._wrap_streams_for_capture()

    def _setup_notebook_path(self) -> None:
        """Resolve notebook path from env vars / argv, then Jupyter API fallback.

        Do NOT load execution history — after kernel restart the process has no
        in-memory state. Reset record to all-pending so it reflects fresh kernel.
        """
        path = _notebook_path_from_env_or_argv()
        if not path:
            path = _discover_notebook_via_jupyter_api()
        if path:
            self._record_manager.set_notebook_path(path)
            self._record_manager.write_record()  # reset: all cells pending (no prior execution)
            self._register_for_cli()
            self._start_notebook_watcher()

    def _start_notebook_watcher(self) -> None:
        """Start a lightweight background poller that detects ipynb saves in Jupyter Lab.

        The kernel has no way to know when the user presses Ctrl+S.  This timer
        checks the file's mtime every ~1.5 s and re-syncs the record when it changes.
        """
        if self._watcher_started:
            return
        self._watcher_started = True

        def _poll() -> None:
            t = threading.Timer(self._WATCHER_INTERVAL, _poll)
            t.daemon = True
            t.start()
            try:
                with self._record_pipeline_lock:
                    self._record_manager.sync_if_ipynb_changed()
            except Exception:
                pass

        t = threading.Timer(self._WATCHER_INTERVAL, _poll)
        t.daemon = True
        t.start()

    def _wrap_streams_for_capture(self) -> None:
        """Wrap stdout/stderr to capture stream output (ipykernel sends via session, not send_response)."""
        if sys.stdout is not None and not isinstance(sys.stdout, _CapturingStreamWrapper):
            sys.stdout = _CapturingStreamWrapper(sys.stdout, self, "stdout")
        if sys.stderr is not None and not isinstance(sys.stderr, _CapturingStreamWrapper):
            sys.stderr = _CapturingStreamWrapper(sys.stderr, self, "stderr")

    def _try_set_notebook_from_request(self) -> None:
        """If notebook path not set, try to resolve it from the execute_request.

        VS Code sends cellId metadata; Jupyter Lab does not.  For Jupyter Lab,
        fall back to the server REST API so the kernel can register itself.
        """
        if self._record_manager.notebook_path:
            return
        try:
            parent = self.get_parent("shell")
            if not parent:
                return
            path = None
            # 1. Extract from cellId (VS Code format: vscode-notebook-cell:URI#fragment)
            meta = parent.get("metadata") or {}
            cell_id_meta = meta.get("cellId") or meta.get("cell_id")
            if cell_id_meta and isinstance(cell_id_meta, str) and "vscode-notebook-cell:" in cell_id_meta:
                uri_part = cell_id_meta.split("#")[0].replace("vscode-notebook-cell:", "").strip()
                path = _uri_to_path("file://" + uri_part) if uri_part.startswith("/") else _uri_to_path(uri_part)
            # 2. Fallback: metadata keys
            if not path:
                for key in ("vscode_notebook_uri", "vscode_notebook_path", "notebook", "notebookPath", "uri"):
                    val = meta.get(key)
                    if isinstance(val, dict):
                        val = val.get("path") or val.get("uri")
                    if val:
                        path = _uri_to_path(str(val)) or (str(val) if str(val).endswith(".ipynb") else None)
                        break
            # 3. Jupyter Lab fallback: query the server REST API
            if not path:
                path = _discover_notebook_via_jupyter_api()
            if path and Path(path).suffix == ".ipynb":
                self._record_manager.set_notebook_path(path)
                self._record_manager.write_record()  # reset: all cells pending
                self._register_for_cli()
                self._start_notebook_watcher()
                logger.info("Notebook path resolved from request: %s", path)
        except Exception:
            logger.debug("Failed to extract notebook path from request", exc_info=True)

    def _register_for_cli(self) -> None:
        """Register this kernel in the registry so CLI/MCP can connect to it."""
        if self._registered_for_cli:
            return
        nb_path = self._record_manager.notebook_path
        if not nb_path:
            return
        try:
            from ipykernel.connect import get_connection_file

            cf = get_connection_file()
            if cf and Path(cf).exists():
                register(nb_path, cf)
                self._registered_for_cli = True
                atexit.register(self._unregister_for_cli)
        except Exception:
            pass

    def _unregister_for_cli(self) -> None:
        """Unregister this kernel from the registry (lifecycle: shutdown)."""
        if not self._registered_for_cli:
            return
        nb_path = getattr(self._record_manager, "notebook_path", None)
        if nb_path:
            unregister(nb_path)
            self._registered_for_cli = False

    def _start_capture(self) -> None:
        self._capturing = True
        self._captured_output = []

    def _stop_capture(self) -> list[dict[str, Any]]:
        self._capturing = False
        return self._captured_output

    def send_response(
        self,
        stream: Any,
        msg_or_type: Union[str, dict],
        content: Optional[dict[str, Any]] = None,
        ident: Any = None,
        buffers: Any = None,
        **kwargs: Any,
    ) -> None:
        """Override to capture IOPub messages during execution.

        Stream output is captured via _CapturingStreamWrapper on stdout/stderr,
        since ipykernel sends stream via session.send from OutStream, not here.
        """
        if self._capturing and stream is self.iopub_socket:
            msg_type = msg_or_type if isinstance(msg_or_type, str) else (msg_or_type or {}).get("msg_type", "")
            if msg_type in ("stream", "error", "execute_result", "display_data"):
                self._captured_output.append({
                    "msg_type": msg_type,
                    "content": content or {},
                })
        super().send_response(stream, msg_or_type, content, ident, buffers, **kwargs)

    async def do_execute(
        self,
        code: str,
        silent: bool,
        store_history: bool = True,
        user_expressions: Optional[dict] = None,
        allow_stdin: bool = False,
        *,
        cell_meta: Optional[dict] = None,
        cell_id: Optional[str] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute code and record result."""
        self._try_set_notebook_from_request()
        # IDE (Remote SSH) may supply paths with /ssh-remote+…; normalize before record I/O.
        self._record_manager._sync_notebook_path_for_fs()
        self._start_capture()
        try:
            reply = await super().do_execute(
                code,
                silent,
                store_history=store_history,
                user_expressions=user_expressions or {},
                allow_stdin=allow_stdin,
                cell_id=cell_id,
                **kwargs,
            )
        finally:
            captured = self._stop_capture()
        if reply is not None:
            self._register_for_cli()  # ensure registered when we have notebook_path
            await asyncio.to_thread(
                self._record_execution_locked,
                code,
                reply,
                cell_id,
                cell_meta,
                captured,
            )
        return reply

    def _record_execution_locked(
        self,
        code: str,
        reply: dict[str, Any],
        cell_id: Optional[str],
        cell_meta: Optional[dict],
        captured_output: Optional[list[dict[str, Any]]],
    ) -> None:
        try:
            with self._record_pipeline_lock:
                self._record_execution(code, reply, cell_id, cell_meta, captured_output)
        except Exception:
            logger.exception("Record write failed")

    def _record_execution(
        self,
        code: str,
        reply: dict[str, Any],
        cell_id: Optional[str],
        cell_meta: Optional[dict] = None,
        captured_output: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        """Record execution result to RecordManager."""
        if _is_ide_injected_code(code, cell_id):
            return
        if not cell_id and cell_meta:
            cell_id = cell_meta.get("cellId") or cell_meta.get("cell_id")
        if not cell_id:
            # Deterministic fallback: same code => same id across runs (hashlib is stable)
            h = hashlib.sha256(code.encode("utf-8")).hexdigest()[:8]
            cell_id = f"cell_{h}"
        status = reply.get("status", "ok")
        error_info: Optional[dict[str, Any]] = None
        output: Optional[list[dict[str, Any]]] = None

        if status == "error":
            error_info = {
                "ename": reply.get("ename", "Error"),
                "evalue": reply.get("evalue", ""),
                "traceback": reply.get("traceback", []),
            }
        # Serialize captured IOPub output (stream, execute_result, display_data, error)
        if captured_output:
            output = self._serialize_output(captured_output)

        self._record_manager.add_execution(
            cell_id=cell_id,
            code=code,
            status=status,
            error_info=error_info,
            output=output,
            execution_count=reply.get("execution_count"),
        )
        self._record_manager.write_record()

    def do_shutdown(self, restart: bool) -> dict[str, Any]:
        """Handle shutdown: unregister from CLI before exit."""
        self._unregister_for_cli()
        return super().do_shutdown(restart)

    def _serialize_output(self, captured: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Serialize captured IOPub messages for storage.

        Merges consecutive stream messages with same name for cleaner output.
        """
        result: list[dict[str, Any]] = []
        stream_buf: dict[str, list[str]] = {}  # name -> text chunks

        def flush_stream() -> None:
            for name, chunks in stream_buf.items():
                if chunks:
                    result.append({
                        "msg_type": "stream",
                        "name": name,
                        "text": "".join(chunks),
                    })
            stream_buf.clear()

        for item in captured:
            msg_type = item.get("msg_type", "")
            content = item.get("content", {})
            if msg_type == "stream":
                name = content.get("name", "stdout")
                text = content.get("text", "")
                if isinstance(text, list):
                    text = "".join(text)
                stream_buf.setdefault(name, []).append(text)
            else:
                flush_stream()
                out: dict[str, Any] = {"msg_type": msg_type}
                if msg_type == "error":
                    out["ename"] = content.get("ename", "")
                    out["evalue"] = content.get("evalue", "")
                    out["traceback"] = content.get("traceback", [])
                elif msg_type in ("execute_result", "display_data"):
                    out["data"] = content.get("data", {})
                    out["metadata"] = content.get("metadata", {})
                    if "execution_count" in content:
                        out["execution_count"] = content["execution_count"]
                result.append(out)
        flush_stream()
        return result

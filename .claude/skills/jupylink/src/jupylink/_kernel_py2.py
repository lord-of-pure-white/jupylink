"""JupyLink Kernel — Python 2 compatible (synchronous do_execute, ipykernel 5.x)."""

import atexit
import hashlib
import logging
import os
import sys
import threading

from ipykernel.connect import get_connection_file
from ipykernel.ipkernel import IPythonKernel

from .kernel_registry import register, resolve_notebook_filesystem_path, unregister
from .magics import JupyLinkMagics
from .record_manager import RecordManager, _is_ide_injected_code

logger = logging.getLogger(__name__)


def _get_parent_header(kernel):
    """ipykernel 5.x uses parent_header attribute, 6.x uses get_parent()."""
    try:
        return kernel.get_parent("shell")
    except (AttributeError, TypeError):
        return getattr(kernel, "parent_header", None) or getattr(kernel, "_parent_header", None)


def _notebook_path_from_env_or_argv():
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
                if os.path.isfile(str(p)):
                    return str(p)
            except (OSError, ValueError):
                pass
    for arg in sys.argv:
        if isinstance(arg, basestring) and arg.lower().endswith(".ipynb"):
            try:
                p = resolve_notebook_filesystem_path(arg)
                if os.path.isfile(str(p)):
                    return str(p)
            except (OSError, ValueError):
                pass
    return None


class _CapturingStreamWrapper(object):
    """Wraps stdout/stderr to capture output during execution."""

    def __init__(self, real, kernel, name):
        self._real = real
        self._kernel = kernel
        self._name = name

    def write(self, s):
        if self._kernel._capturing and s:
            self._kernel._captured_output.append({
                "msg_type": "stream",
                "content": {"name": self._name, "text": s},
            })
        return self._real.write(s)

    def set_parent(self, parent):
        if hasattr(self._real, "set_parent"):
            self._real.set_parent(parent)

    def flush(self):
        self._real.flush()

    def __getattr__(self, name):
        return getattr(self._real, name)


class JupyLinkKernel(IPythonKernel):
    """Wrapper kernel that records execution for agent-friendly format."""

    implementation = "JupyLink"
    implementation_version = "0.1.0"
    banner = "JupyLink - Kernel proxy for agent-friendly execution records"

    _WATCHER_INTERVAL = 1.5

    def __init__(self, **kwargs):
        super(JupyLinkKernel, self).__init__(**kwargs)
        self._record_manager = RecordManager()
        self.shell.register_magics(JupyLinkMagics)
        self._capturing = False
        self._captured_output = []
        self._registered_for_cli = False
        self._record_pipeline_lock = threading.Lock()
        self._watcher_started = False
        self._setup_notebook_path()
        self._wrap_streams_for_capture()

    def _setup_notebook_path(self):
        path = _notebook_path_from_env_or_argv()
        if path:
            self._record_manager.set_notebook_path(path)
            self._record_manager.write_record()
            self._register_for_cli()
            self._start_notebook_watcher()

    def _start_notebook_watcher(self):
        if self._watcher_started:
            return
        self._watcher_started = True

        def _poll():
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

    def _wrap_streams_for_capture(self):
        if sys.stdout is not None and not isinstance(sys.stdout, _CapturingStreamWrapper):
            sys.stdout = _CapturingStreamWrapper(sys.stdout, self, "stdout")
        if sys.stderr is not None and not isinstance(sys.stderr, _CapturingStreamWrapper):
            sys.stderr = _CapturingStreamWrapper(sys.stderr, self, "stderr")

    def _try_set_notebook_from_request(self):
        if self._record_manager.notebook_path:
            return
        try:
            parent = _get_parent_header(self)
            if not parent:
                return
            meta = parent.get("metadata") or {}
            cell_id_meta = meta.get("cellId") or meta.get("cell_id")
            if cell_id_meta and isinstance(cell_id_meta, basestring) and "vscode-notebook-cell:" in cell_id_meta:
                uri_part = cell_id_meta.split("#")[0].replace("vscode-notebook-cell:", "").strip()
                path = self._uri_to_path(uri_part)
            else:
                path = None
            if not path:
                for key in ("vscode_notebook_uri", "vscode_notebook_path", "notebook", "notebookPath", "uri"):
                    val = meta.get(key)
                    if isinstance(val, dict):
                        val = val.get("path") or val.get("uri")
                    if val:
                        path = self._uri_to_path(str(val))
                        if not path and str(val).endswith(".ipynb"):
                            path = str(val)
                        break
            if path and path.endswith(".ipynb"):
                self._record_manager.set_notebook_path(path)
                self._record_manager.write_record()
                self._register_for_cli()
                self._start_notebook_watcher()
                logger.info("Notebook path resolved from request: %s", path)
        except Exception:
            logger.debug("Failed to extract notebook path from request", exc_info=True)

    @staticmethod
    def _uri_to_path(uri):
        if not uri:
            return None
        uri = uri.strip()
        if uri.startswith("file://"):
            uri = uri[7:]
        try:
            from urllib import unquote as _unquote
        except ImportError:
            from urllib.parse import unquote as _unquote
        try:
            path = _unquote(uri)
        except Exception:
            path = uri
        if os.name == "nt" and path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]
        if (path.endswith(".ipynb") and
                (os.path.exists(path) or "/" in path or "\\" in path)):
            return path
        return None

    def _register_for_cli(self):
        if self._registered_for_cli:
            return
        nb_path = self._record_manager.notebook_path
        if not nb_path:
            return
        try:
            cf = get_connection_file()
            if cf and os.path.exists(cf):
                register(nb_path, cf)
                self._registered_for_cli = True
                atexit.register(self._unregister_for_cli)
        except Exception:
            pass

    def _unregister_for_cli(self):
        if not self._registered_for_cli:
            return
        nb_path = getattr(self._record_manager, "notebook_path", None)
        if nb_path:
            unregister(nb_path)
            self._registered_for_cli = False

    def _start_capture(self):
        self._capturing = True
        self._captured_output = []

    def _stop_capture(self):
        self._capturing = False
        return self._captured_output

    def send_response(self, stream, msg_or_type, content=None, ident=None, buffers=None, track=False):
        """Override to capture IOPub messages during execution."""
        if self._capturing and stream is getattr(self, "iopub_socket", None):
            if isinstance(msg_or_type, dict):
                msg_type = (msg_or_type or {}).get("msg_type", "")
            else:
                msg_type = msg_or_type
            if msg_type in ("stream", "error", "execute_result", "display_data"):
                self._captured_output.append({
                    "msg_type": msg_type,
                    "content": content or {},
                })
        return super(JupyLinkKernel, self).send_response(
            stream, msg_or_type, content=content, ident=ident, buffers=buffers, track=track
        )

    def do_execute(self, code, silent, store_history=True, user_expressions=None, allow_stdin=False):
        """Execute code and record result (synchronous, ipykernel 5.x compatible)."""
        self._try_set_notebook_from_request()
        self._record_manager._sync_notebook_path_for_fs()
        self._start_capture()
        try:
            reply = super(JupyLinkKernel, self).do_execute(
                code, silent, store_history=store_history,
                user_expressions=user_expressions or {},
                allow_stdin=allow_stdin,
            )
        finally:
            captured = self._stop_capture()
        if reply is not None:
            self._register_for_cli()
            thread = threading.Thread(
                target=self._record_execution_locked,
                args=(code, reply, captured),
            )
            thread.daemon = True
            thread.start()
        return reply

    def _record_execution_locked(self, code, reply, captured_output):
        try:
            with self._record_pipeline_lock:
                self._record_execution(code, reply, captured_output)
        except Exception:
            logger.exception("Record write failed")

    def _extract_cell_id(self):
        """Try to get the ipynb cell id from the execute_request metadata.

        Jupyter Lab sends ``cellId`` (nbformat UUID) in the message metadata.
        VS Code sends ``vscode-notebook-cell:URI#fragment`` — we skip those.
        Falls back to None (caller should then hash the code).
        """
        try:
            parent = _get_parent_header(self)
            if not parent:
                return None
            meta = parent.get("metadata") or {}
            cid = meta.get("cellId") or meta.get("cell_id")
            if not cid or not isinstance(cid, basestring):
                return None
            # Skip VS Code URIs — those aren't real cell ids
            if "vscode-notebook-cell:" in cid or cid.startswith("file://"):
                return None
            return cid
        except Exception:
            return None

    def _record_execution(self, code, reply, captured_output=None):
        if _is_ide_injected_code(code):
            return
        cell_id = self._extract_cell_id()
        if not cell_id:
            h = hashlib.sha256(code.encode("utf-8")).hexdigest()[:8]
            cell_id = "cell_{}".format(h)
        status = reply.get("status", "ok")
        error_info = None
        output = None

        if status == "error":
            error_info = {
                "ename": reply.get("ename", "Error"),
                "evalue": reply.get("evalue", ""),
                "traceback": reply.get("traceback", []),
            }
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

    def do_shutdown(self, restart):
        self._unregister_for_cli()
        return super(JupyLinkKernel, self).do_shutdown(restart)

    def _serialize_output(self, captured):
        result = []
        stream_buf = {}

        def flush_stream():
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
                out = {"msg_type": msg_type}
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

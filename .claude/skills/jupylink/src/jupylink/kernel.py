"""JupyLink Wrapper Kernel — version-aware bridge.

On Python 3 (ipykernel >= 6): async do_execute, Jupyter API discovery, full capture.
On Python 2 (ipykernel 5.x): synchronous do_execute, no API discovery, compatible capture.
"""
import sys

if sys.version_info >= (3,):
    from ._kernel_py3 import (
        JupyLinkKernel,
        _CapturingStreamWrapper,
        _discover_notebook_via_jupyter_api,
        _notebook_path_from_env_or_argv,
        _uri_to_path,
    )
else:
    from ._kernel_py2 import (
        JupyLinkKernel,
        _CapturingStreamWrapper,
        _notebook_path_from_env_or_argv,
    )
    _uri_to_path = None  # unused in Py2 kernel, _kernel_py2 has its own

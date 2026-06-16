"""Entry point for JupyLink kernel."""

import sys

from ipykernel.kernelapp import IPKernelApp

from .kernel import JupyLinkKernel
from .kernel_ide_proxy import maybe_run_ide_proxy_from_argv, parse_connection_file_from_argv

_KERNEL_HELP = """\
JupyLink — Jupyter starts this module with: python -m jupylink -f <connection_file>

For CLI (list-cells, execute, serve, …), use the console script (not this entry):
  jupylink --help

Install a kernelspec that uses *this* interpreter (recommended inside a venv):
  jupylink install-kernelspec

Docs: README / project SKILL for JUPYLINK_* variables and IDE bridge behavior.
"""


if __name__ == "__main__":
    argv_rest = sys.argv[1:]
    if parse_connection_file_from_argv(sys.argv) is None and (
        "-h" in argv_rest or "--help" in argv_rest
    ):
        print(_KERNEL_HELP, end="")
        sys.exit(0)
    if not maybe_run_ide_proxy_from_argv():
        IPKernelApp.launch_instance(kernel_class=JupyLinkKernel)

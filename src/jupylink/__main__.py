"""Entry point for JupyLink kernel."""

from ipykernel.kernelapp import IPKernelApp

from .kernel import JupyLinkKernel
from .kernel_ide_proxy import maybe_run_ide_proxy_from_argv

if __name__ == "__main__":
    if not maybe_run_ide_proxy_from_argv():
        IPKernelApp.launch_instance(kernel_class=JupyLinkKernel)

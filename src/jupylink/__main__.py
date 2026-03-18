"""Entry point for JupyLink kernel."""

from ipykernel.kernelapp import IPKernelApp

from .kernel import JupyLinkKernel

if __name__ == "__main__":
    IPKernelApp.launch_instance(kernel_class=JupyLinkKernel)

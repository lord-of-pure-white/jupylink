"""Magics for JupyLink kernel."""

from __future__ import annotations

from IPython.core.magic import Magics, line_magic, magics_class


@magics_class
class JupyLinkMagics(Magics):
    """Magics for JupyLink kernel."""

    @line_magic
    def notebook_path(self, line: str) -> None:
        """Set the notebook path for record generation.

        Usage: %notebook_path /path/to/notebook.ipynb
        """
        path = line.strip()
        if not path:
            kernel = getattr(self.shell, "kernel", None)
            if kernel and hasattr(kernel, "_record_manager"):
                nb_path = getattr(kernel._record_manager, "notebook_path", None)
                if nb_path:
                    print(f"Current: {nb_path}")
                else:
                    print("Notebook path not set. Use: %notebook_path /path/to/notebook.ipynb")
            return
        kernel = getattr(self.shell, "kernel", None)
        if kernel and hasattr(kernel, "_record_manager"):
            kernel._record_manager.set_notebook_path(path)
            if hasattr(kernel, "_register_for_cli"):
                kernel._register_for_cli()
            print(f"Notebook path set to: {path}")
        else:
            print("JupyLink kernel not active.")

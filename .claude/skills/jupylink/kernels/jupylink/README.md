# Bundled kernelspec

`kernel.json` uses `"python"` in `argv`. If that is not the interpreter where JupyLink is installed, run:

```bash
jupylink install-kernelspec
```

That registers a spec with `sys.executable` (your current venv).

"""
The desktop shell (workstream H).

Two modules, and the split is load-bearing:

  `launcher.py`  lock, socket, environment, server, shutdown wiring.
                 **Imports no GUI toolkit**, so the backend test suite can
                 cover it on a headless box.
  `gui.py`       the ONLY module that imports `webview`.

Nothing here may be imported by `main` — the hosted deployment must not
acquire a dependency on the desktop shell.
"""

"""
The desktop shell (workstream H).

Three modules today, and the split is load-bearing:

  `single_instance.py`  the one-window lock. The only module here with a
                        platform-conditional import (`fcntl` / `msvcrt`).
  `launcher.py`         socket, environment, server, shutdown wiring.
                        **Imports no GUI toolkit**, so the backend test suite
                        can cover it on a headless box.
  `gui.py`              the ONLY module that will import `webview`.
                        NOT WRITTEN YET — Task 5.

Nothing here may be imported by `main` — the hosted deployment must not
acquire a dependency on the desktop shell.
"""

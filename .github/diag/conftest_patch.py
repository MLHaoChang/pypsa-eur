

# TEMP diagnostic (claude/diag-blender-hang, not for merge): on the first
# solve-queue reset that times out, write every thread's stack to a file and exit.
import os as _diag_os
import sys as _diag_sys
import threading as _diag_threading
import traceback as _diag_tb

from services import solve_queue as _diag_sq

_diag_orig_reset = _diag_sq.SolveQueue.reset_for_tests


def _diag_reset(self):
    import time as _t
    t0 = _t.monotonic()
    _diag_orig_reset(self)
    if _t.monotonic() - t0 > 20:
        names = {t.ident: t.name for t in _diag_threading.enumerate()}
        with open(_diag_os.environ.get("DIAG_STACKS", "/tmp/diag-stacks.txt"), "w") as f:
            f.write(f"unfinished_tasks={getattr(self._q, 'unfinished_tasks', None)} running={self._running_ids}\n")
            for ident, frame in _diag_sys._current_frames().items():
                f.write(f"\n--- thread {names.get(ident)} ({ident})\n")
                f.write("".join(_diag_tb.format_stack(frame)))
            import subprocess as _sp
            f.write("\n--- ps\n" + _sp.run(["ps", "-ef", "--forest"], capture_output=True, text=True).stdout)
        _diag_os._exit(3)


_diag_sq.SolveQueue.reset_for_tests = _diag_reset

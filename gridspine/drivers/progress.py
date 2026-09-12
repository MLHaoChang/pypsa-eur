"""Progress and abort for a long driver run — the two things a job runner needs.

The backend's solve queue gives a job a `stop_event` and a log queue and runs
it on one thread (`services/solve_queue.py`). `Progress` is the whole of what
`drivers/` needs to live in that queue: a callback the runner turns into log
lines, and a stop it polls where the time actually goes — inside the unit
commitment's window loop, the AC year pass and the per-hour screening, not
only at stage boundaries. A year is ~2 h of UC plus ~20 min of screening; a
stop checked once per stage is not a stop.

`tick` EMITS BEFORE IT CHECKS. The event that was just completed is real work
and the runner should see it; the stop then takes effect before the next unit
of work begins. Reversing the two would throw away a window that had already
been solved.

Emission is throttled to ~100 events per stage (always the first and the
last) so a 8760-hour pass does not write 8760 log lines; the stop is checked
on EVERY tick, throttled or not. No imports beyond the standard library: this
module is on the boundary the backend calls.
"""


class StudyAborted(RuntimeError):
    """The run stopped because its `stop_event` was set.

    A control-flow signal, not a failure of the model: the driver still writes
    its `StageError` artifact so the UI renders where it stopped, and a run
    aborted after the dispatch stage resumes with `from_dispatch`.
    """


class Progress:
    """Wraps a caller's `progress(stage, done, total)` and its stop event.

    Both are optional; the no-callback, no-stop instance is what a CLI run
    uses and costs one attribute lookup per tick.
    """

    #: at most this many events per stage, first and last always emitted
    MAX_EVENTS_PER_STAGE = 100

    def __init__(self, callback=None, stop_event=None):
        self._callback = callback
        self._stop_event = stop_event
        self._stage = None

    def tick(self, stage: str, done: int, total: int) -> None:
        """Report one completed unit of `stage`, then honour a pending stop."""
        self._stage = stage
        if self._callback is not None and self._emits(done, total):
            self._callback(stage, int(done), int(total))
        self.check(stage)

    def check(self, stage: str = None) -> None:
        """Raise `StudyAborted` if the stop is set. Cheap enough to call in a loop."""
        if self._stop_event is not None and self._stop_event.is_set():
            raise StudyAborted(f"aborted during {stage or self._stage or 'the run'}")

    def _emits(self, done: int, total: int) -> bool:
        if done <= 1 or done >= total:
            return True
        step = max(1, int(total) // self.MAX_EVENTS_PER_STAGE)
        return done % step == 0


#: The instance every driver parameter defaults to: no callback, no stop.
NULL_PROGRESS = Progress()

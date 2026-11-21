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

With a `record` path, every emitted tick is also written there (`progress.json`
in the run directory): the stage, its done/total and when the stage began.
`drivers.status` reads it for the running stage, whose own artifacts say
nothing until the stage ends — the unit commitment writes `dispatch.csv` only
once every window is solved. The record is a live view, never an artifact a
later stage reads: a new run removes it first, and a failed write of it never
fails the run.
"""

import json
import os
import time
from pathlib import Path

#: The file name of the live record in a run directory.
PROGRESS_RECORD = "progress.json"


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

    def __init__(self, callback=None, stop_event=None, record=None):
        self._callback = callback
        self._stop_event = stop_event
        self._stage = None
        self._record = Path(record) if record is not None else None
        self._stage_started_at = None
        if self._record is not None:
            # A record left by an earlier run describes that run, not this one.
            try:
                self._record.unlink(missing_ok=True)
            except OSError:
                pass

    def tick(self, stage: str, done: int, total: int) -> None:
        """Report one completed unit of `stage`, then honour a pending stop."""
        if stage != self._stage:
            self._stage_started_at = time.time()
        self._stage = stage
        if self._emits(done, total):
            if self._callback is not None:
                self._callback(stage, int(done), int(total))
            self._write_record(stage, int(done), int(total))
        self.check(stage)

    def begin(self, stage: str, total: int) -> None:
        """Say `stage` has started, with `total` units ahead and none done yet.

        Only the live record hears it: the callback's events are completed
        work, and nothing is completed yet. It still honours a pending stop.
        """
        if stage != self._stage:
            self._stage_started_at = time.time()
        self._stage = stage
        self._write_record(stage, 0, int(total))
        self.check(stage)

    def check(self, stage: str = None) -> None:
        """Raise `StudyAborted` if the stop is set. Cheap enough to call in a loop."""
        if self._stop_event is not None and self._stop_event.is_set():
            raise StudyAborted(f"aborted during {stage or self._stage or 'the run'}")

    def _write_record(self, stage: str, done: int, total: int) -> None:
        if self._record is None:
            return
        body = {
            "stage": stage, "done": done, "total": total,
            "stage_started_at": self._stage_started_at, "updated_at": time.time(),
        }
        tmp = self._record.with_name(self._record.name + ".tmp")
        try:
            self._record.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(body))
            os.replace(tmp, self._record)   # a reader never sees half a file
        except OSError:
            pass   # a live view: losing one write must not fail the run

    def _emits(self, done: int, total: int) -> bool:
        if done <= 1 or done >= total:
            return True
        step = max(1, int(total) // self.MAX_EVENTS_PER_STAGE)
        return done % step == 0


#: The instance every driver parameter defaults to: no callback, no stop.
NULL_PROGRESS = Progress()

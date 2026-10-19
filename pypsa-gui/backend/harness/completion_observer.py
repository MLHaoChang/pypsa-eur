"""Bounded best-effort status observation; never execution or result authority."""
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import threading
import time
import weakref

MAX_PENDING = 512
MAX_SECONDS = 24 * 3600
POLL_SECONDS = 0.25
_CONDITION = threading.Condition()
_PENDING = OrderedDict()
_INCOMPLETE = OrderedDict()
_RUNNER = None
_GENERATION = 0


@dataclass(frozen=True)
class Watch:
    context: weakref.ReferenceType
    owner: str
    project: str
    directory: Path
    row: dict
    expires: float


def _incomplete(watch):
    note_incomplete(watch.owner, watch.project)


def note_incomplete(owner, project):
    with _CONDITION:
        key = (str(owner), str(project))
        _INCOMPLETE[key] = None
        _INCOMPLETE.move_to_end(key)
        while len(_INCOMPLETE) > 32:
            _INCOMPLETE.popitem(last=False)


def warnings(owner, project):
    with _CONDITION:
        return ['activity_completion_observation_incomplete'] if (owner, project) in _INCOMPLETE else []


def watch(handle, ctx, worker):
    """Freeze owned metadata only; legacy/unestablished workers stay unwatched."""
    global _RUNNER
    if not isinstance(worker, threading.Thread) or worker.ident is None:
        return
    from harness import activity_history
    try:
        owner, project = str(handle.owner_user_id), str(handle.project_uuid)
        pending = Watch(weakref.ref(ctx), owner, project, activity_history._directory(owner, project),
            dict(source_id=handle.job_id, family=handle.family, tool_name=handle.tool_name,
                 registered_at=handle.registered_at), time.monotonic() + MAX_SECONDS)
    except (ValueError, TypeError):
        return  # No owned or weakly bound origin can be established.
    with _CONDITION:
        _PENDING[handle.job_id] = pending
        while len(_PENDING) > MAX_PENDING:
            _incomplete(_PENDING.popitem(last=False)[1])
        if _RUNNER is None or not _RUNNER.is_alive():
            _RUNNER = threading.Thread(target=_run, args=(_GENERATION,),
                                       name='native-completion-observer', daemon=True)
            try:
                _RUNNER.start()
            except RuntimeError:
                _RUNNER = None
                for missed in _PENDING.values():
                    _incomplete(missed)
                _PENDING.clear()
        _CONDITION.notify_all()


def _run(generation):
    global _RUNNER
    from harness import activity_history, job_adapters

    while True:
        with _CONDITION:
            if generation != _GENERATION:
                return
            if not _PENDING:
                _RUNNER = None
                _CONDITION.notify_all()
                return
            batch = list(_PENDING.items())
        for job, pending in batch:
            with _CONDITION:
                if generation != _GENERATION:
                    return
                if _PENDING.get(job) is not pending:
                    continue
            retire = incomplete = False
            try:
                ctx = pending.context()
                if ctx is None or str(ctx.project_uuid) != pending.project or time.monotonic() >= pending.expires:
                    retire = incomplete = True
                else:
                    status = job_adapters._completion_status(job, ctx, pending.project)
                    if status in activity_history._TERMINAL[pending.row['family']]:
                        # No lifecycle/observer lock is held during owned I/O.
                        with _CONDITION:
                            if generation != _GENERATION or _PENDING.get(job) is not pending:
                                continue
                        incomplete = not activity_history.remember(pending.owner, pending.project,
                            dict(pending.row, native_status=status), directory=pending.directory)
                        retire = True
                    elif status == 'unknown':
                        retire = incomplete = True
                    # None means a busy lock, not missing authority. Retry a read.
            except Exception:  # noqa: BLE001 -- best-effort observation cannot affect native work.
                retire = incomplete = True
            finally:
                ctx = None  # Never pin a project/network while sleeping.
            if retire:
                with _CONDITION:
                    if generation == _GENERATION and _PENDING.get(job) is pending:
                        _PENDING.pop(job)
                        if incomplete:
                            _incomplete(pending)
                        _CONDITION.notify_all()
        with _CONDITION:
            if generation == _GENERATION and _PENDING:
                _CONDITION.wait(POLL_SECONDS)


def _reset_for_tests():
    global _RUNNER, _GENERATION
    with _CONDITION:
        _GENERATION += 1
        runner, _RUNNER = _RUNNER, None
        _PENDING.clear()
        _INCOMPLETE.clear()
        _CONDITION.notify_all()
    if runner is not None and runner is not threading.current_thread():
        runner.join(10)
        if runner.is_alive():
            raise RuntimeError('completion observer did not stop')

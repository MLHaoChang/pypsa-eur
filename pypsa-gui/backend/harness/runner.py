"""The server-side harness runner (study harness issue 02, design §6.6).

Advances harness runs (task file version 3, `services/assistant_tasks.py`)
with no chat panel open: one worker thread, a lease per run, one action per
tick, woken by a job settling, a user resume or Continue, and a 60 s timer.

Explicit context (GK B, B1 option b). Each tick resolves the run's project as
its owner (ACL re-checked every tick), takes the project's own context
through `services.study.runner.base_context` (`hydrate_or_adopt`) and binds it
in a copied contextvars context: the four request bindings
(`PyPSAService.bind_request_scope`), the owner as acting user, the
runner-owned session `run:<task_id>` as the chat session,
`assistant_tasks.RUN_CONTEXT` and the harness-lock marker. The foreground's
active context is never read or moved.

Steps go through the ordinary dispatch path (`loop.dispatch_tool_call`:
admit, the confirmation gate, begin_step, the dispatcher, finish_step) on a
per-run helper thread. Before a carded call nobody has decided, the runner
issues nothing: it records `run.waiting`, goes `needs_user` and notifies.
A card is issued only when a decision is there: the user present
(`present`, its TTL starting then) or, in tests, a decision source passed to
the `Runner` (production passes none). The helper takes the project's
`mutation_lock` then `solver_state_lock` after the decision and before
begin_step, re-checks the project, owner and locks under them, and lets go
on `tool_running`: the handler runs on the tool executor and takes its own
locks, and no lock is held across a card wait or a job.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from pathlib import Path

from fastapi import HTTPException

logger = logging.getLogger(__name__)

TICK_SECONDS = 60.0
HEARTBEAT_SECONDS = 30.0
LEASE_SECONDS = 90.0  # three heartbeats
RESTART_WINDOW_SECONDS = 3600.0
MAX_RESTARTS = 3  # within the window, then the run is paused (crash loop)
MAX_ERRORS = 2  # consecutive runner errors, then the run is paused
MAX_EMAILS = 10  # per run
SCAN_LIMIT = 200  # task files read per scan
LOCK_TIMEOUT_SECONDS = 5.0
SESSION_PREFIX = "run:"
NOTICE_STATES = ("needs_user", "completed", "failed", "paused")
_DRIVEN = ("running", "waiting_job", "needs_user")

# Model cost (design §6.5, GK B): EUR per million tokens, metered from the
# provider's own usage. Owner values; `PYPSA_GUI_MODEL_RATES_EUR` (JSON, the
# same shape) replaces the table, and an invalid one meters nothing.
DEFAULT_COST_EUR = 5.0
USAGE_KEYS = ("input_tokens", "cache_read_tokens", "cache_create_tokens", "output_tokens")
DEFAULT_RATES_EUR = {
    "claude-opus": {"input_tokens": 14.0, "cache_read_tokens": 1.4, "cache_create_tokens": 17.5, "output_tokens": 70.0},
    "claude-sonnet": {"input_tokens": 2.8, "cache_read_tokens": 0.28, "cache_create_tokens": 3.5, "output_tokens": 14.0},
    "claude-haiku": {"input_tokens": 0.93, "cache_read_tokens": 0.093, "cache_create_tokens": 1.16, "output_tokens": 4.6},
}
_STATE_WORDS = {"needs_user": "needs you", "completed": "completed", "failed": "failed", "paused": "is paused"}


class _Stop(Exception):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status, self.reason = status, reason


def _tasks():
    from services import assistant_tasks
    return assistant_tasks


def _read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def _task_path(path):
    """`path` resolved, refused (ValueError) unless it is a task file directly
    inside an `assistant_tasks` folder: the only files the runner drives or
    locks. Spelled as realpath plus a string prefix check, the form CodeQL
    reads as a py/path-injection sanitiser (see `site_service`)."""
    full = os.path.realpath(path)
    root = os.path.join(os.path.dirname(os.path.dirname(full)), "assistant_tasks")
    if not full.startswith(root + os.sep):
        raise ValueError(f"not a task file: {path}")
    if not full.endswith(".json"):
        raise ValueError(f"not a task file: {path}")
    return Path(full)


@contextlib.contextmanager
def _file_lock(path):
    """The lease's cross-process guard: an exclusive lock on a sidecar file."""
    from harness import durable
    path = _task_path(path)
    with durable.file_lock(path.with_name(path.name + ".lease"), timeout=LEASE_SECONDS):
        yield


def _update(path, change):
    """Read, change and save the task under the task lock; `change` returns
    False to leave the file as it is."""
    tasks = _tasks()
    with tasks._LOCK:
        task = _read(path)
        if task is None or not task.get("run"):
            return None
        if change(task) is False:
            return task
        tasks._save(Path(path), task)
        return task


def _args_hash(args):
    return hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()


# ── Model cost ───────────────────────────────────────────────────────────────


def rates():
    """The EUR rate table; raises ValueError for an invalid override."""
    raw = os.environ.get("PYPSA_GUI_MODEL_RATES_EUR")
    if not raw:
        return DEFAULT_RATES_EUR
    table = json.loads(raw)
    if not isinstance(table, dict) or not table:
        raise ValueError("the model rate table is not an object")
    for prefix, rate in table.items():
        if not isinstance(prefix, str) or not prefix or not isinstance(rate, dict) or set(rate) != set(USAGE_KEYS):
            raise ValueError("a model rate names every usage kind")
        for value in rate.values():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("a model rate is a finite number of at least 0")
    return table


def _rate(model):
    table = rates()
    match = max((p for p in table if isinstance(model, str) and model.startswith(p)), key=len, default=None)
    if match is None:
        raise ValueError(f"no rate for model {model!r}")
    return table[match]


def _budget(task):
    from services import run_profiles
    run = task["run"]
    if "budget" not in run:
        try:
            limit = float(run_profiles.resolve(run["profile"]).get("budget", {}).get("cost_eur", DEFAULT_COST_EUR))
        except Exception:  # noqa: BLE001 — an unreadable profile limit is the default
            limit = DEFAULT_COST_EUR
        run["budget"] = {"limit": {"cost_eur": limit},
                         "used": {**{k: 0 for k in USAGE_KEYS}, "cost_eur": 0.0, "unmetered": False}}
    return run["budget"]


def budget_spent(task):
    budget = _budget(task)
    return budget["used"]["unmetered"] or budget["used"]["cost_eur"] >= budget["limit"]["cost_eur"]


def charge(path, usage, model):
    """Meter one provider call on the run's model-cost budget, from the
    provider's usage only. An unknown model, a missing or invalid usage count
    or an invalid rate table fails closed: the budget counts as spent. A
    spent budget ends the run `needs_user` and nothing more is dispatched."""
    def change(task):
        budget = _budget(task)
        used = budget["used"]
        try:
            rate = _rate(model)
            counts = {}
            for key in USAGE_KEYS:
                value = (usage or {}).get(key)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError(f"usage {key} is missing or invalid")
                counts[key] = value
        except ValueError:
            used["unmetered"] = True
        else:
            for key, value in counts.items():
                used[key] += value
            used["cost_eur"] = round(used["cost_eur"] + sum(counts[k] * rate[k] for k in USAGE_KEYS) / 1e6, 6)
        if budget_spent(task) and task["run"]["status"] in _DRIVEN:
            _stop_in(task, "needs_user", "budget")
    task = _update(path, change)
    return copy_budget(task)


def copy_budget(task):
    return json.loads(json.dumps(task["run"]["budget"])) if task and task["run"].get("budget") else None


def _stop_in(task, status, reason):
    run = task["run"]
    run["status"], run["reason"] = status, reason
    if status == "paused" and task["status"] not in ("cancelled", "completed"):
        task["status"] = "needs_review"


# ── Notices ──────────────────────────────────────────────────────────────────


def _plain(text, size):
    """Run titles in an email: one plain line, bounded, no markup."""
    text = re.sub(r"[\x00-\x1f\x7f<>]", " ", str(text or ""))
    return " ".join(text.split())[:size]


def _email_opted_in(path, task_id):
    """Q1 (GK B): only the envelope's own `notify.email` (issue 03's file)."""
    envelope = _read(Path(path).with_name(task_id + ".envelope.json"))
    return isinstance(envelope, dict) and (envelope.get("notify") or {}).get("email") is True


def _notify(path):
    """One notice per run state change into needs_user, completed, failed or
    paused: an attention record, and an email when the deployment sends
    them and the user opted in (≤ 1 per change, ≤ 10 per run). The text is
    built here from fixed fields, never from model text or values."""
    pending = {}

    def change(task):
        run = task["run"]
        status = run["status"]
        if status not in NOTICE_STATES:
            if run.get("noticed") is None:
                return False
            run["noticed"] = None
            return
        if run.get("noticed") == status:
            return False
        run["noticed"] = status
        notices = run.setdefault("notices", [])
        notices.append({"kind": status, "at": time.time(), "channel": "app", "delivered": False})
        pending.update(seq=len(notices), status=status, task=json.loads(json.dumps(task)))
    _update(path, change)
    if not pending:
        return None
    task, status, seq = pending["task"], pending["status"], pending["seq"]
    if task["run"].get("reason") == "access":
        return status  # the owner no longer reaches the project: nothing names it to them
    from harness import attention
    delivered = attention.run_notice(task["owner_id"], task["project_id"], task["task_id"], status, seq)
    emailed = _email(path, task, status)

    def mark(task):
        notices = task["run"].get("notices") or []
        if len(notices) < seq:
            return False
        notices[seq - 1]["delivered"] = delivered
        if emailed:
            notices.append({"kind": status, "at": time.time(), "channel": "email", "delivered": True})
    _update(path, mark)
    return status


def _email(path, task, status):
    from services import email_service
    sent = sum(1 for n in task["run"].get("notices") or [] if n["channel"] == "email")
    if sent >= MAX_EMAILS or not email_service.get_status()["configured"] \
            or not _email_opted_in(path, task["task_id"]):
        return False
    from db.models import Project, User
    from db.session import SessionLocal
    from settings import get_settings
    with SessionLocal() as db:
        user = db.get(User, uuid.UUID(task["owner_id"]))
        project = db.get(Project, uuid.UUID(task["project_id"]))
        if user is None or project is None or not getattr(user, "email", None):
            return False
        address, name = user.email, project.name
    link = f"{get_settings().public_base_url.rstrip('/')}/?project={task['project_id']}&run={task['task_id']}"
    words = _STATE_WORDS[status]
    # No run title: the model wrote it (criterion 11, no researched value in a mail).
    body = (f"Your harness run {words}.\n\nProject: {_plain(name, 120)}\n"
            f"State: {status}\n\nOpen it: {link}\n")
    try:
        email_service.send_email(to=address, subject=f"PyPSA Studio: a harness run {words}", body_text=body,
                                 metadata={"kind": "harness_run", "task_id": task["task_id"], "state": status})
        return True
    except Exception:  # noqa: BLE001 — the in-app record stands; mail is best effort
        logger.warning("harness run %s: the notice email could not be sent", task["task_id"], exc_info=True)
        return False


# ── The runner ───────────────────────────────────────────────────────────────


class Runner:
    """One worker thread driving every harness run it holds the lease of.

    `decisions` is a test seam: a callable `(task_id, ref, args_hash)` that
    returns a decision ("approve", "deny") already made for a card the run
    would otherwise wait on, used once. The application's runner has none.
    """

    def __init__(self, instance=None, *, clock=time.time, decisions=None):
        self.instance = instance or f"{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.clock = clock
        self._decisions = decisions
        self._wakeup = threading.Event()
        self._stopping = threading.Event()
        self._due = set()
        self._due_lock = threading.Lock()
        self._inflight = {}
        self._errors = {}
        self._present = {}
        self._thread = None
        self._last_scan = 0.0

    # lifecycle

    def start(self):
        self._stopping.clear()
        _tasks()._WAKE_LISTENERS.append(self.wake)
        self._thread = threading.Thread(target=self._loop, name="harness-runner", daemon=True)
        self._thread.start()

    def stop(self, timeout=5.0):
        self._stopping.set()
        self._wakeup.set()
        with contextlib.suppress(ValueError):
            _tasks()._WAKE_LISTENERS.remove(self.wake)
        if self._thread is not None:
            self._thread.join(timeout)

    def wake(self, path=None):
        if path is not None:
            try:
                _task_path(path)
            except ValueError:
                logger.warning("harness runner: wake for a path outside assistant_tasks refused")
                return
            with self._due_lock:
                self._due.add(Path(path))
        self._wakeup.set()

    def present(self, task_id):
        """The user opened the run: a card it waits on is issued now, its TTL
        starting at display, and decided on the run's own session."""
        from harness import session as harness_session
        self._present[task_id] = self.clock() + harness_session.CONFIRMATION_TTL_SECONDS
        self.wake()

    def _loop(self):
        while not self._stopping.is_set():
            self._wakeup.wait(HEARTBEAT_SECONDS)
            self._wakeup.clear()
            if self._stopping.is_set():
                return
            with self._due_lock:
                paths, self._due = self._due, set()
            if time.monotonic() - self._last_scan >= TICK_SECONDS:
                self._last_scan = time.monotonic()
                paths |= set(self.scan())
            paths |= {p for p, _ in self._inflight.values()}
            for path in sorted(paths):
                try:
                    self.tick(path)
                except Exception:  # noqa: BLE001 — one run's fault never stops the others
                    logger.exception("harness runner: tick on %s failed", path)

    def scan(self):
        """Every run the runner may drive: reads task files only, never a
        network, at most SCAN_LIMIT of them."""
        from db.models import Project
        from db.session import SessionLocal
        from services import project_registry
        found = []
        try:
            with SessionLocal() as db:
                folders = [project_registry.project_dir(p) / "assistant_tasks" for p in db.query(Project).all()]
        except Exception:  # noqa: BLE001 — no database, no runs to find this round
            logger.exception("harness runner: the project scan failed")
            return found
        read = 0
        for folder in folders:
            for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
                if path.name.endswith(".envelope.json"):
                    continue
                read += 1
                if read > SCAN_LIMIT:
                    return found
                task = _read(path)
                if task and task.get("version") == 3 and (task.get("run") or {}).get("status") in _DRIVEN:
                    found.append(path)
        return found

    # one tick

    def tick(self, path):
        """At most one action on one run; returns what it did."""
        path = Path(path)
        task = _read(path)
        run = (task or {}).get("run")
        if not task or task.get("version") != 3 or not run:
            return "not_a_run"
        task_id = task["task_id"]
        flight = self._inflight.get(task_id)
        if flight is not None and flight[1].is_alive():
            self._heartbeat(path)
            return "dispatching"
        self._inflight.pop(task_id, None)
        if run["status"] == "needs_user" and not run.get("waiting"):
            self._sync_hold(task)
            _notify(path)
            return "needs_user"  # a failed gate or a stop: only the user's resume goes on
        if run["status"] not in _DRIVEN:
            self._let_go(task)
            _notify(path)
            return "idle"
        lease = self._lease(path)
        if lease is None:
            return "leased_elsewhere"
        try:
            did = self._act(path, task, lease == "takeover")
            self._errors.pop(task_id, None)
        except _Stop as stop:
            _update(path, lambda t, stop=stop: _stop_in(t, stop.status, stop.reason))
            did = stop.reason
        except Exception:  # noqa: BLE001
            logger.exception("harness runner: run %s errored", task_id)
            self._errors[task_id] = self._errors.get(task_id, 0) + 1
            if self._errors[task_id] >= MAX_ERRORS:
                _update(path, lambda t: _stop_in(t, "paused", "runner_errors"))
            did = "error"
        self._sync_hold(_read(path))
        _notify(path)
        return did

    def _lease(self, path):
        """Take or renew the run's lease: None when another instance holds a
        live one, "takeover" when this instance takes it from a lapsed holder.
        Atomic across processes (the sidecar file lock) and threads (the task
        lock); `lease_until` is wall time in the file, one clock for all."""
        result = {}

        def change(task):
            runner = task["run"].setdefault("runner", {"restarts": 0, "restart_times": []})
            now = self.clock()
            holder = runner.get("lease_owner")
            if holder not in (None, self.instance) and runner.get("lease_until", 0) > now:
                return False
            takeover = holder not in (None, self.instance)
            runner.update(lease_owner=self.instance, lease_until=now + LEASE_SECONDS, heartbeat_at=now, last_tick=now)
            if takeover:
                runner["restarts"] = runner.get("restarts", 0) + 1
                runner["restart_times"] = [t for t in runner.get("restart_times", [])
                                           if now - t < RESTART_WINDOW_SECONDS] + [now]
            result["lease"] = "takeover" if takeover else "held"
        with _file_lock(path):
            _update(path, change)
        return result.get("lease")

    def _heartbeat(self, path):
        def change(task):
            runner = task["run"].get("runner") or {}
            if runner.get("lease_owner") != self.instance:
                return False
            now = self.clock()
            runner.update(lease_until=now + LEASE_SECONDS, heartbeat_at=now)
        with _file_lock(path):
            _update(path, change)

    def _let_go(self, task):
        from services import harness_locks
        if task:
            harness_locks.release(task["project_id"], task["task_id"])

    def _sync_hold(self, task):
        """The project stays held while an effectful step of the run is
        dispatching, running or waiting on its job; otherwise it is let go."""
        if not task:
            return
        flight = self._inflight.get(task["task_id"])
        busy = (flight is not None and flight[1].is_alive()) or (
            task["run"]["status"] in _DRIVEN and any(
                s["status"] in ("running", "waiting") and _tasks()._effectful(s["tool"]) for s in task["steps"]))
        if not busy:
            self._let_go(task)

    # context

    def _context(self, task):
        """The run's project as its owner sees it, and its own context.
        A lost owner or ACL stops the run: `access`, no effect."""
        from db.models import User
        from db.session import SessionLocal
        from services import project_registry
        from services.study.runner import base_context
        with SessionLocal() as db:
            user = db.get(User, uuid.UUID(task["owner_id"]))
            if user is None or user.status != "active":
                raise _Stop("needs_user", "access")
            try:
                row = project_registry.resolve_project(db, user, task["project_id"])
            except HTTPException:
                raise _Stop("needs_user", "access") from None
            ctx = base_context(row)
            return ctx, project_registry.registry_key(row)

    @contextlib.contextmanager
    def _bound(self, task, ctx, slot):
        """Bind the run's context, owner and session in the current
        (copied) contextvars context."""
        from harness import eventlog
        from harness import session as harness_session
        from services import chat_tools, harness_locks
        from services.pypsa_service import PyPSAService
        session, created = harness_session.get_or_create_session_reporting(
            SESSION_PREFIX + task["task_id"], owner_user_id=task["owner_id"])
        if session.owner_user_id != task["owner_id"]:
            raise _Stop("needs_user", "access")  # a session of that name that is not the owner's
        if created:
            eventlog.resume(session)  # the run's conversation, rebuilt from its log
        session.task_id = task["task_id"]
        scope = PyPSAService.bind_request_scope(ctx, slot, f"run-scratch:{task['task_id']}")
        chat_tools.set_acting_user(task["owner_id"])
        chat_tools.set_acting_session(None)
        chat_tools.set_chat_session(session)
        run_token = _tasks().RUN_CONTEXT.set(ctx)
        lock_token = harness_locks.enter_run(task["task_id"])
        try:
            yield session
        finally:
            harness_locks.leave_run(lock_token)
            _tasks().RUN_CONTEXT.reset(run_token)
            PyPSAService.reset_request_scope(scope)

    def recheck(self, task_id, path, ctx):
        """Under the project's locks, before begin_step (GK B condition 3):
        the context is still the run's project, the owner still reaches it,
        no other user's edit lock and no other run's hold."""
        from db.models import User
        from db.session import SessionLocal
        from services import harness_locks, project_registry
        task = _read(path)
        if not task or task["task_id"] != task_id or str(ctx.project_uuid) != task["project_id"]:
            return False
        try:
            with SessionLocal() as db:
                user = db.get(User, uuid.UUID(task["owner_id"]))
                if user is None or user.status != "active":
                    return False
                project_registry.resolve_project(db, user, task["project_id"])
        except HTTPException:
            return False
        if harness_locks.holder(ctx.project_uuid) not in (None, task_id):
            return False
        return _tasks()._may_write(task)

    def _act(self, path, task, takeover):
        ctx, slot = self._context(task)

        def bound():
            with self._bound(task, ctx, slot) as session:
                if takeover:
                    self._recover(path)
                return self._step(path, ctx, session)
        return contextvars.copy_context().run(bound)

    def _decision(self, task, ref, args):
        """A decision for the card `ref` waits on: "live" while the user is
        present, else one the test seam already holds, else None."""
        if self._present.get(task["task_id"], 0) > self.clock():
            return "live"
        if self._decisions is not None:
            return self._decisions(task["task_id"], ref, None if args is None else _args_hash(args))
        return None

    def _wait_for_user(self, path, kind, ref):
        def change(task):
            run = task["run"]
            if (run.get("waiting") or {}).get("ref") == ref and run["status"] == "needs_user":
                return False
            run["waiting"] = {"kind": kind, "ref": ref, "since": self.clock()}
            run["status"], run["reason"] = "needs_user", "waiting_" + kind
        _update(path, change)

    def _go_on(self, path):
        def change(task):
            run = task["run"]
            if run["status"] != "needs_user" or not run.get("waiting"):
                return False
            run["status"], run["waiting"] = "running", None
            run.pop("reason", None)
        _update(path, change)

    def _step(self, path, ctx, session):
        from services import run_profiles
        tasks = _tasks()
        taken = {}

        def decide(task, ref, args):
            if ref not in taken:
                taken[ref] = self._decision(task, ref, args)
            return taken[ref]
        task = _read(path)
        run = task["run"]
        if task["status"] == "cancelled" or run["status"] not in _DRIVEN:
            return "idle"
        if budget_spent(task):
            raise _Stop("needs_user", "budget")
        if any(s["status"] == "uncertain" for s in task["steps"]):
            raise _Stop("needs_user", "uncertain")
        if not tasks._may_write(task):
            raise _Stop("needs_user", "locked")
        if run["status"] == "needs_user":
            waiting = run["waiting"]
            step = tasks._next(task)
            args = tasks._resolve(step["args"], task["steps"]) if step and step["id"] == waiting["ref"] else None
            decision = decide(task, waiting["ref"], args)
            if decision is None or (decision == "live" and waiting["ref"] in run_profiles.STAGES):
                return "waiting"  # a stage's Continue comes only from the plan card (COP-1)
            self._go_on(path)
        if run["status"] == "waiting_job":
            _update(path, lambda t: t["run"].update(status="running"))
        tasks._advance_run(path, ctx)
        task = _read(path)
        run = task["run"]
        if run["status"] != "running":
            return run["status"]
        if task["status"] == "paused_at_card":
            card = task["pause"]["card"]
            decision = decide(task, card, None)
            if decision == "approve":
                tasks.continue_at_card(session, task["task_id"])
                self.wake(path)
                return "continued"
            self._wait_for_user(path, "card", card)
            return "waiting"
        step = tasks._next(task)
        if step is None:
            return "idle"
        if step["status"] == "waiting":
            self._hold_if_effectful(task, step)
            _update(path, lambda t: t["run"].update(status="waiting_job") if t["run"]["status"] == "running" else False)
            return "waiting_job"
        if step["status"] == "running":
            return "running"  # the chat runs it
        if step["status"] != "pending":
            raise _Stop("needs_user", "step_" + step["status"])
        args = tasks._resolve(step["args"], task["steps"])
        decision = None
        if self._policy(session, step["tool"]) != "none":
            decision = decide(task, step["id"], args)
            if decision is None:
                self._wait_for_user(path, "card", step["id"])
                return "waiting"
        if not tasks.claim_step(path, step["id"], session.session_id):
            return "claimed_elsewhere"
        context = contextvars.copy_context()
        thread = threading.Thread(target=context.run, args=(self.dispatch, path, ctx, session, step, args, decision),
                                  name=f"harness-run-{task['task_id'][:8]}", daemon=True)
        self._inflight[task["task_id"]] = (path, thread)
        thread.start()
        return "dispatched"

    def _hold_if_effectful(self, task, step):
        from services import harness_locks
        if _tasks()._effectful(step["tool"]):
            harness_locks.hold(task["project_id"], task["task_id"])

    def _policy(self, session, tool):
        """The card policy the dispatch path will apply (Guided: every write
        and run is carded, COP-1)."""
        from harness import confirm, edit_card, loop, restrictions
        tier = loop._tool_tier(session, tool)
        guided = tool not in confirm.GUIDED_UNCARDED or (tier != "read" and edit_card.is_edit(tool))
        try:
            mode = (restrictions.capture(session) or {}).get("mode", "standard")
            policy = confirm.confirmation_policy(tier, guided=guided, restriction_mode=mode)
        except Exception:  # noqa: BLE001 — an unreadable rule asks the user
            return "user"
        return "none" if policy == "policy" else policy

    def dispatch(self, path, ctx, session, step, args, decision):
        """One step through the ordinary dispatch path, on this helper thread
        (its contextvars copied from the bound tick)."""
        from harness import eventlog, loop
        task_id = session.task_id
        tool_use_id = f"run-{step['id']}-{uuid.uuid4().hex[:8]}"
        locks = contextlib.ExitStack()
        refused = []

        def take_locks(*_):
            if not ctx.mutation_lock.acquire(timeout=LOCK_TIMEOUT_SECONDS):
                refused.append("busy")
                return False
            locks.callback(ctx.mutation_lock.release)
            locks.enter_context(ctx.solver_state_lock)
            if not self.recheck(task_id, path, ctx):
                refused.append("recheck")
                locks.close()
                return False
            self._hold_if_effectful(_read(path), step)  # decided: the project is the run's until the step ends
            return True

        results = []
        session._undo_snapshotted = []  # each runner step is its own undo step, as a chat turn is
        session._turn_in_flight = True
        try:
            if decision is None and not take_locks():
                return
            session.decision_guard = None if decision is None else take_locks
            eventlog.message(session, {"role": "assistant", "content": [
                {"type": "tool_use", "id": tool_use_id, "name": step["tool"], "input": args}]})
            frames = loop.dispatch_tool_call(session, {"id": tool_use_id, "name": step["tool"], "input": args},
                                             results, turn_ctx=ctx, guided=True)
            for kind, payload in frames:
                eventlog.record(session, "runner/frame", frame=kind, step=step["id"], tool_use_id=tool_use_id,
                                **({"error_kind": payload["error_kind"]} if payload.get("error_kind") else {}))
                if kind == "tool_pending_confirmation":
                    if decision in ("approve", "deny"):
                        session.record_decision(payload["confirmation_token"], decision)
                    elif decision != "live":
                        session.withdraw_confirmation(payload["confirmation_token"])  # nobody is there
                elif kind == "tool_running":
                    locks.close()  # the handler takes its own locks on the tool executor
        finally:
            locks.close()
            session.decision_guard = None
            session._turn_in_flight = False
            if results:
                eventlog.message(session, {"role": "user", "content": [results[-1]]})
            _tasks().release_claim(path, step["id"], session.session_id)
            if refused:
                _update(path, lambda t: _stop_in(t, "needs_user", "locked" if refused == ["busy"] else "recheck"))
            self.wake(path)

    # restart

    def _recover(self, path):
        """A run taken over from a lapsed lease (design §6.6 restart): settle
        waiting queue steps from their rows, follow live ones again, lost
        ones are uncertain (SC-09: nothing is requeued); any uncertain step
        is `needs_user`; 3 restarts in an hour pause the run."""
        from harness import continuation
        tasks = _tasks()
        task = _read(path)
        for step in task["steps"]:
            if step["status"] != "waiting":
                continue
            ticket = (task["task_id"], step["id"], step.get("attempt_id"))
            if step["job"].get("kind") != "queue":
                continue  # a foreground job: `_reconcile_lost` below decides
            states = [continuation._queue_job_status(job) for job in step["job"]["jobs"]]
            if states and all(s in ("queued", "running") for s in states):
                continuation.follow_queue_job(path, ticket)
            elif states and all(s not in ("queued", "running", "unknown") for s in states):
                continuation.settle_queue_from_record(path, ticket)
        with tasks._LOCK:
            task = _read(path)
            tasks._reconcile_lost(path, task)
        task = _read(path)
        runner = task["run"].get("runner") or {}
        now = self.clock()
        if len([t for t in runner.get("restart_times", []) if now - t < RESTART_WINDOW_SECONDS]) >= MAX_RESTARTS:
            raise _Stop("paused", "restarts")
        if any(s["status"] == "uncertain" for s in task["steps"]):
            raise _Stop("needs_user", "uncertain")


_DEFAULT = None


def enabled():
    """On in the app; off under pytest unless a test starts its own Runner."""
    default = "0" if "PYTEST_CURRENT_TEST" in os.environ else "1"
    return os.environ.get("PYPSA_GUI_HARNESS_RUNNER", default) == "1"


def start_default():
    global _DEFAULT
    if _DEFAULT is None and enabled():
        _DEFAULT = Runner()
        _DEFAULT.start()
    return _DEFAULT


def stop_default():
    global _DEFAULT
    if _DEFAULT is not None:
        _DEFAULT.stop()
        _DEFAULT = None


def present(task_id):
    """The user opened the run (issue 08 wires the companion to this)."""
    if _DEFAULT is not None:
        _DEFAULT.present(task_id)

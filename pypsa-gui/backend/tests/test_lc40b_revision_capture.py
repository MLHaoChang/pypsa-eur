# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-40b: one helper captures the network revision for every study runner.

`dirty_state.capture` stores the edit counter on a study record (under the
mutation lock when a worker passes it); `dirty_state.hold` is a loop's own
`restore="final"` hold: bump once, re-capture. The guard keeps every runner on
them: a bare `record["network_revision"] =` write in `services/` fails it.
The per-reader stale banners are in `test_lc40b_reader_stale.py`.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import dirty_state, study_state
from tests.test_study_runner import _setup, fake, studies_on  # noqa: F401
from tests.study_s4_support import wait_run

SERVICES = Path(__file__).resolve().parents[1] / "services"


class RecordingLock:
    """A mutation lock stand-in that says whether it is held."""

    def __init__(self):
        self.held = False

    def __enter__(self):
        assert not self.held, "the mutation lock is not re-entrant"
        self.held = True
        return self

    def __exit__(self, *exc):
        self.held = False


class Ctx:
    """A context whose counter read records whether `lock` was held."""

    def __init__(self, revision: int, lock: RecordingLock | None = None):
        self._revision, self._lock, self.reads = revision, lock, []

    @property
    def network_revision(self):
        self.reads.append(self._lock.held if self._lock else None)
        return self._revision

    @network_revision.setter
    def network_revision(self, value):
        self._revision = value


# ── capture ──────────────────────────────────────────────────────────────

def test_capture_stores_the_counter_on_the_record_and_returns_it():
    record = {"status": "running", "network_revision": None}
    assert study_state.edited_since(record, revision=7) is None    # running: Unavailable
    assert dirty_state.capture(record, SimpleNamespace(network_revision=7)) == 7
    assert record["network_revision"] == 7


def test_a_record_reads_none_until_it_is_captured():
    record = {"status": "done", "network_revision": None}
    assert study_state.edited_since(record, revision=3) is None
    dirty_state.capture(record, SimpleNamespace(network_revision=3))
    assert study_state.edited_since(record, revision=3) is False


def test_capture_with_a_lock_reads_the_counter_while_the_lock_is_held():
    lock = RecordingLock()
    ctx = Ctx(4, lock)
    record = {"network_revision": None}
    assert dirty_state.capture(record, ctx, lock=lock) == 4
    assert ctx.reads == [True]
    assert lock.held is False                                     # released after


def test_capture_waits_for_a_held_lock():
    lock = threading.Lock()
    ctx = SimpleNamespace(network_revision=1)
    record = {"network_revision": None}
    lock.acquire()
    worker = threading.Thread(target=dirty_state.capture, args=(record, ctx),
                              kwargs={"lock": lock}, daemon=True)
    worker.start()
    worker.join(0.3)
    assert worker.is_alive() and record["network_revision"] is None
    ctx.network_revision = 2                                      # an edit under the lock
    lock.release()
    worker.join(5.0)
    assert record["network_revision"] == 2


# ── hold ─────────────────────────────────────────────────────────────────

def test_hold_bumps_once_and_re_captures_so_only_other_studies_read_stale():
    ctx = SimpleNamespace(network_revision=5)
    sibling = {"status": "done", "network_revision": None}
    own = {"status": "done", "network_revision": None}
    dirty_state.capture(sibling, ctx)
    dirty_state.capture(own, ctx)
    assert dirty_state.hold(own, ctx) == 6
    assert ctx.network_revision == 6
    assert own["network_revision"] == 6
    now = dirty_state.revision(ctx)
    assert study_state.edited_since(own, revision=now) is False
    assert study_state.edited_since(sibling, revision=now) is True


def test_hold_takes_no_lock():
    ctx = Ctx(0, RecordingLock())
    dirty_state.hold({"network_revision": None}, ctx)
    assert ctx.reads and not any(ctx.reads)                       # read, lock untouched


# ── study/runner.py: captured before the thread starts ──────────────────

def test_the_decision_study_record_holds_its_revision_before_the_thread_starts(
        client, api_project, studies_on, fake, monkeypatch):
    seen = []
    real_start = threading.Thread.start

    def start(self):
        if self.name.startswith("decision-study-"):
            cells = dict(zip(self._target.__code__.co_freevars,
                             (c.cell_contents for c in self._target.__closure__)))
            seen.append(cells["worker_args"]["record"]["network_revision"])
        return real_start(self)

    monkeypatch.setattr(threading.Thread, "start", start)
    sid = _setup(client, api_project, "lc40b-start")
    r = client.post(f"/api/projects/lc40b-start/studies/{sid}/run",
                    json={"fidelity": "quick_screen"})
    assert r.status_code == 202, r.text
    assert len(seen) == 1 and isinstance(seen[0], int), seen
    assert wait_run(client, "lc40b-start", sid)["status"] == "done"


# ── the source guard ─────────────────────────────────────────────────────

# A record capture outside the helper: a subscript write, a literal that reads
# the counter, or a local named for the record field that reads it.
CAPTURE_RE = re.compile(
    r'\["network_revision"\]\s*=(?!=)'
    r'|"network_revision":\s*dirty_state\.revision'
    r'|\bnetwork_revision\s*=\s*dirty_state\.revision\(')

# (path under services/, stripped line) -> why it stays.
ALLOWLIST = {
    ("adequacy/mc_loop_runner.py", "network_revision = dirty_state.revision()"):
        "no worker: it reads the active ctx inside PyPSAService.get_lock() while "
        "snapshotting, into a local stored later, and calls revision() with no ctx",
    ("chat_tools.py", "revision = dirty_state.revision()"):
        "the chat entry's campaign read before start() (site 10, option C): out of scope",
    ("network_undo.py", '"network_revision": dirty_state.revision(),'):
        "the undo-status payload the hub polls, not a study record",
}


def bare_captures(root: Path) -> list[tuple[str, int, str]]:
    hits = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel == "dirty_state.py":
            continue
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if CAPTURE_RE.search(line) and (rel, line.strip()) not in ALLOWLIST:
                hits.append((rel, no, line.strip()))
    return hits


def test_no_runner_captures_the_revision_outside_the_helper():
    assert bare_captures(SERVICES) == []


@pytest.mark.parametrize(("rel", "line"), list(ALLOWLIST))
def test_each_allowlisted_read_is_still_there(rel, line):
    source = (SERVICES / rel).read_text(encoding="utf-8").splitlines()
    assert line in [s.strip() for s in source], f"{rel}: drop or update its allowlist entry"


@pytest.mark.parametrize("planted", [
    'record["network_revision"] = dirty_state.revision(study_ctx)',
    'record["network_revision"] = dirty_state.bump_revision(study_ctx)',
    '"network_revision": dirty_state.revision(ctx),',
    'network_revision = dirty_state.revision(ctx)',
])
def test_a_planted_bare_capture_fails_the_guard(tmp_path, planted):
    runner = tmp_path / "adequacy" / "planted_runner.py"
    runner.parent.mkdir()
    runner.write_text(f"def worker(record):\n    {planted}\n", encoding="utf-8")
    assert bare_captures(tmp_path) == [("adequacy/planted_runner.py", 2, planted)]

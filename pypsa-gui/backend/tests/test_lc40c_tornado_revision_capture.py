# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-40c: the tornado study captures its network revision.

`start_tornado` stores the edit counter on its record through
`dirty_state.capture` before its thread starts, so each variant's campaign
entry carries it and reads stale after an edit, as the decision study's do.
"""
from __future__ import annotations

import threading

from services.adequacy import campaign
from services.study import runner as R
from tests.test_eh_study_edited_since import _edit_bus
from tests.test_study_tornado_routes import _run, fake, studies_on, wait_tornado  # noqa: F401


def _tornado(client, base, sid):
    r = client.post(f"/api/projects/{base}/studies/{sid}/findings/tornado", json={})
    assert r.status_code == 202, r.text
    rec = wait_tornado(client, base, sid)
    assert rec["status"] == "done", rec
    return rec


def test_tornado_campaign_entries_read_stale_after_an_http_edit_and_fresh_after_a_re_run(
        client, api_project, studies_on, fake, session_ctx):
    sid = _run(client, api_project, "lc40c-tor")
    r = client.post("/api/projects/lc40c-tor/activate")
    assert r.status_code == 200, r.text
    ctx = session_ctx(client)
    # Opened after the decision run, so every entry in it is a tornado variant's.
    R._on_ctx(ctx, campaign.start, "LC-40c tornado", 20)

    def flags():
        return [e["edited_since_study"] for e in R._on_ctx(ctx, campaign.status)["entries"]
                if e["study"] == R.STUDY_KEY]

    _tornado(client, "lc40c-tor", sid)
    first = flags()
    assert first and first == [False] * len(first), first
    _edit_bus(client)
    assert flags() == [True] * len(first)
    _tornado(client, "lc40c-tor", sid)
    assert flags() == [True] * len(first) + [False] * len(first)


def test_the_tornado_record_holds_its_revision_before_the_thread_starts(
        client, api_project, studies_on, fake, monkeypatch):
    sid = _run(client, api_project, "lc40c-start")
    seen = []
    real_start = threading.Thread.start

    def start(self):
        if self.name.startswith("decision-tornado-"):
            # The record reaches the worker through the target lambda's closure.
            cells = dict(zip(self._target.__code__.co_freevars,
                             (c.cell_contents for c in self._target.__closure__ or ())))
            assert "record" in cells, f"the tornado thread's target changed: {cells}"
            seen.append(cells["record"]["network_revision"])
        return real_start(self)

    monkeypatch.setattr(threading.Thread, "start", start)
    _tornado(client, "lc40c-start", sid)
    assert len(seen) == 1 and isinstance(seen[0], int), seen

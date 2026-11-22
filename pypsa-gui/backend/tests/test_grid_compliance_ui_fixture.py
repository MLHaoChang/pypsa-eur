"""GC-09: the frontend's copy of the grid-compliance API shapes (U1).

``GridComplianceSection.test.tsx`` renders from
``frontend/src/test/fixtures/gridCompliance.json``. This test drives the real
API to a representative state, captures the real refusal bodies, replaces
volatile values (timestamps, hashes, digests) with fixed placeholders, and
fails when the committed file differs. Regenerate it after a shape change with
``GC_UI_FIXTURE_WRITE=1 python -m pytest tests/test_grid_compliance_ui_fixture.py``
run from pypsa-gui/backend.
"""

import json
import os
import pathlib

from services.grid_compliance import sources
from tests.test_grid_compliance import (  # noqa: F401
    BASE,
    NAME,
    _campus,
    _freeze,
    _judge,
    _network,
    _no_pdf,
    _set,
    _tracker_file,
    hub,
)
from tests.test_worksheet_foreign_lock import same_org_other_user  # noqa: F401

FIXTURE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "test"
    / "fixtures"
    / "gridCompliance.json"
)

#: Timestamps differ run to run (and may share a second), so each key gets
#: one fixed ISO value; tokens are numbered by distinct value, so equal values
#: keep equal placeholders (a submission record is the freeze it went out on).
_TIMESTAMPS = {
    "frozen_at": "2026-11-10T09:00:00+00:00",
    "status_at": "2026-11-11T09:00:00+00:00",
    "created_at": "2026-11-12T09:00:00+00:00",
    "updated_at": "2026-11-13T09:00:00+00:00",
    "generated_at": "2026-11-14T09:00:00+00:00",
}
_TOKENS = {"model_set_id": "MODELSET", "data_hash": "DATAHASH", "digest": "DIGEST"}


class _Scrub:
    def __init__(self):
        self.seen: dict[tuple[str, str], str] = {}

    def _token(self, key: str, value: str) -> str:
        family = _TOKENS[key]
        got = self.seen.get((family, value))
        if got is None:
            n = sum(1 for f, _ in self.seen if f == family) + 1
            got = self.seen[(family, value)] = f"{family}-PLACEHOLDER-{n}"
        return got

    def __call__(self, obj, key: str = ""):
        if isinstance(obj, dict):
            return {k: self(v, k) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self(v, key) for v in obj]
        if isinstance(obj, str) and key in _TIMESTAMPS:
            return _TIMESTAMPS[key]
        if isinstance(obj, str) and key in _TOKENS:
            return self._token(key, obj)
        if key == "holder_email" and isinstance(obj, str):
            return "holder@example.com"
        return obj


def _refusal(resp, status: int, kind: str) -> dict:
    assert resp.status_code == status, f"{kind}: {resp.status_code} {resp.text[:300]}"
    detail = resp.json()["detail"]
    assert detail["error_kind"] == kind, detail
    return {"status": status, "detail": detail}


def build_fixture(client, hub, other, monkeypatch) -> dict:  # noqa: F811
    def broken(project):
        raise RuntimeError("provider bug")

    monkeypatch.setitem(sources.SOURCES, "GC-03", broken)
    _campus(hub)
    errors: dict[str, dict] = {}

    # 14 submitted on a fresh freeze; 12 frozen; then the inputs change
    _judge(client, "13")
    _freeze(client, hub, "14")
    assert _set(client, "14", "submitted").status_code == 200
    _freeze(client, hub, "12")
    _campus(hub, "campus: {name: edited}\n")
    _network(hub).add("Bus", "gc09-ui-fixture", v_nom=33.0)
    errors["gc_freeze_after_submission"] = _refusal(
        client.post(f"{BASE}/deliverables/14/freeze"), 409, "gc_freeze_after_submission"
    )
    errors["gc_freeze_not_fresh"] = _refusal(
        _set(client, "12", "submitted"), 409, "gc_freeze_not_fresh"
    )
    errors["gc_prerequisite_not_judged"] = _refusal(
        _set(client, "08", "submitted"), 409, "gc_prerequisite_not_judged"
    )

    # 11: a pack, a comment answered with it, and an open comment
    _set(client, "11", "results_in")
    packed = client.post(f"{BASE}/deliverables/11/pack")
    assert packed.status_code == 200, packed.text
    for text in ("Add the transformer datasheet.", "Show the recovery time."):
        resp = client.post(
            f"{BASE}/deliverables/11/comments",
            json={"date": "2026-11-02", "clause": "4.2", "comment": text},
        )
        assert resp.status_code == 201, resp.text
    resp = client.patch(
        f"{BASE}/deliverables/11/comments/C-001",
        json={
            "response": "Added in section 3.",
            "revision": "B",
            "status": "responded",
            "pack_version": 1,
        },
    )
    assert resp.status_code == 200, resp.text

    # 15: an edited data list (its GC-03 feed raises: unavailable)
    items = client.get(BASE).json()["deliverables"][14]["data"]
    items[0] |= {"supplier": "TSO", "due": "2026-12-01", "status": "received"}
    resp = client.put(f"{BASE}/deliverables/15/data", json={"items": items})
    assert resp.status_code == 200, resp.text
    _set(client, "15", "data")

    errors["gc_invalid"] = _refusal(
        client.put(
            f"{BASE}/deliverables/15/data",
            json={"items": [{"item_id": "x", "label": "y", "due": "next week"}]},
        ),
        422,
        "gc_invalid",
    )
    errors["gc_unknown_status"] = _refusal(
        _set(client, "01", "finished"), 422, "gc_unknown_status"
    )
    errors["gc_unknown_deliverable"] = _refusal(
        _set(client, "26", "data"), 404, "gc_unknown_deliverable"
    )

    state = client.get(BASE)
    assert state.status_code == 200, state.text

    assert client.post(f"/api/projects/{NAME}/lock").status_code == 200
    errors["project_locked"] = _refusal(
        other.put(f"{BASE}/deliverables/01/status", json={"status": "data"}),
        409,
        "project_locked",
    )

    _tracker_file(hub).write_bytes(b"{not json")
    errors["gc_tracker_unreadable"] = _refusal(
        client.get(BASE), 409, "gc_tracker_unreadable"
    )

    return _Scrub()(
        {
            "_about": "Generated by pypsa-gui/backend/tests/"
            "test_grid_compliance_ui_fixture.py; do not edit by hand.",
            "state": state.json(),
            "pack": {"content_disposition": packed.headers["content-disposition"]},
            "errors": errors,
        }
    )


def test_the_frontend_fixture_matches_the_api(
    client,
    hub,  # noqa: F811
    same_org_other_user,  # noqa: F811
    monkeypatch,
):
    built = build_fixture(client, hub, same_org_other_user, monkeypatch)
    text = json.dumps(built, indent=1, ensure_ascii=False) + "\n"
    if os.environ.get("GC_UI_FIXTURE_WRITE") == "1":
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.is_file(), "run with GC_UI_FIXTURE_WRITE=1 to create the fixture"
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == built, (
        "gridCompliance.json is out of date: rerun with GC_UI_FIXTURE_WRITE=1"
    )

"""
DC 03 slice 6: the user's own grid-readiness limits.

A user sets the connection ramp and oscillation limits (and the source of the
fault level) in a bundled sidecar, ``grid_readiness_limits.json``. The
template's ``eh_template.json`` is never written: it stays byte-identical
through a set and a clear. The scorecard reads the user's value first, then
the template's ledger row, then the generic placeholder, and says which; the
flow's to-check item for a limit clears once its source is not ``assumed``.
"""
from __future__ import annotations

import json
import uuid as _uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import main
from services.adequacy import grid_limits as L
from services.adequacy import grid_readiness as G
from tests.conftest import attach_session

BASE = "/api/campus-electrical"


@pytest.fixture
def hub(client, tmp_projects_dir, project_storage_dir):
    """A data-centre project made by the create-from-template route."""
    r = client.post("/api/projects/from_template/eh_datacenter", params={"name": "limits_hub"})
    assert r.status_code == 200, r.text
    name = r.json().get("name", "limits_hub")
    return name, project_storage_dir(name)


def _put(client, name, item, body):
    return client.put(f"{BASE}/{name}/grid-readiness/limits/{item}", json=body)


def _row(client, name, rid):
    r = client.get(f"{BASE}/{name}/grid-readiness")
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["rows"] if x["id"] == rid)


def test_a_set_limit_survives_a_snapshot_round_trip(client, hub):
    name, d = hub
    r = _put(client, name, "connection_ramp",
             {"value": 7.5, "source": "datasheet", "note": "TSO offer letter, table 2"})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/projects/{name}/snapshots", json={"label": "v1"})
    assert r.status_code in (200, 201), r.text
    snap_id = r.json()["id"]
    assert client.delete(f"{BASE}/{name}/grid-readiness/limits/connection_ramp").status_code == 200
    assert _row(client, name, "ramp_rate")["limit"] != 7.5
    r = client.post(f"/api/projects/{name}/snapshots/{snap_id}/restore")
    assert r.status_code == 200, r.text
    row = _row(client, name, "ramp_rate")
    assert row["limit"] == 7.5
    assert row["source"].startswith("Your value (datasheet)")


def test_a_snapshot_taken_before_any_limit_restores_the_templates_limit(client, hub):
    name, d = hub
    template_limit = _row(client, name, "ramp_rate")["limit"]
    r = client.post(f"/api/projects/{name}/snapshots", json={"label": "before"})
    assert r.status_code in (200, 201), r.text
    snap_id = r.json()["id"]
    assert _put(client, name, "connection_ramp", {"value": 7.5, "source": "code"}).status_code == 200
    r = client.post(f"/api/projects/{name}/snapshots/{snap_id}/restore")
    assert r.status_code == 200, r.text
    assert not (d / L.SIDECAR_NAME).exists()
    assert _row(client, name, "ramp_rate")["limit"] == template_limit


def test_the_template_file_stays_byte_identical_through_a_set_and_a_clear(client, hub):
    name, d = hub
    before = (d / "eh_template.json").read_bytes()
    assert _put(client, name, "oscillation", {"value": 0.5, "source": "measured"}).status_code == 200
    assert _put(client, name, "fault_level", {"source": "code", "note": "TSO letter"}).status_code == 200
    assert (d / "eh_template.json").read_bytes() == before
    assert (d / L.SIDECAR_NAME).is_file()
    for item in ("oscillation", "fault_level"):
        assert client.delete(f"{BASE}/{name}/grid-readiness/limits/{item}").status_code == 200
    assert (d / "eh_template.json").read_bytes() == before
    assert not (d / L.SIDECAR_NAME).exists()


def test_the_scorecard_shows_the_users_limit_live_and_says_whose_it_is(client, hub):
    name, _d = hub
    row = _row(client, name, "ramp_rate")
    assert row["source"].startswith("Template assumption") and row["assumed"] is True
    assert row["setting"]["user"] is None
    default = row["setting"]["default"]
    assert default == {"value": row["limit"], "source": "assumed", "origin": "template"}

    r = _put(client, name, "connection_ramp", {"value": 12.0, "source": "code", "note": "Grid code §4"})
    assert r.status_code == 200, r.text
    entry = r.json()
    assert entry["value"] == 12.0 and entry["source"] == "code" and entry["set_at"]
    assert entry["default"] == {"value": default["value"], "source": "assumed"}
    row = _row(client, name, "ramp_rate")
    assert row["limit"] == 12.0 and row["source"] == "Your value (code): Grid code §4"
    assert row["setting"]["user"]["value"] == 12.0
    assert "set_by" not in row["setting"]["user"]
    assert row["setting"]["default"] == default

    # A value the user still only assumes stays to check.
    _put(client, name, "connection_ramp", {"value": 9.0, "source": "assumed"})
    row = _row(client, name, "ramp_rate")
    assert row["limit"] == 9.0 and row["source"] == "Your assumption" and row["assumed"] is True

    assert client.delete(f"{BASE}/{name}/grid-readiness/limits/connection_ramp").json() == {
        "item": "connection_ramp", "cleared": True}
    row = _row(client, name, "ramp_rate")
    assert row["limit"] == default["value"] and row["source"].startswith("Template assumption")


def test_the_fault_levels_source_reaches_the_scr_row(client, hub):
    name, _d = hub
    assert "fault level assumed" in _row(client, name, "scr")["note"]
    assert _put(client, name, "fault_level", {"source": "measured"}).status_code == 200
    row = _row(client, name, "scr")
    assert "fault level as entered" in row["note"]
    assert row["setting"]["user"]["source"] == "measured"


def _flow_items(client, name) -> dict:
    r = client.get(f"/api/projects/{name}/flow")
    assert r.status_code == 200, r.text
    return {i["code"]: i for s in r.json()["sections"] for i in s["items"]}


def test_an_assumed_value_over_a_code_row_stays_to_check(client, hub):
    name, d = hub
    meta = json.loads((d / "eh_template.json").read_text())
    for row in meta["ledger"]:
        if row["item"] == "oscillation":
            row["source"] = "code"
    (d / "eh_template.json").write_text(json.dumps(meta))
    assert "assumed_oscillation" not in _flow_items(client, name)
    _put(client, name, "oscillation", {"value": 3.0, "source": "assumed"})
    assert "assumed_oscillation" in _flow_items(client, name)
    assert _row(client, name, "oscillation")["source"] == "Your assumption"


def test_a_confirmed_limit_clears_its_to_check_item_in_the_flow(client, hub):
    name, _d = hub
    items = _flow_items(client, name)
    assert "assumed_connection_ramp" in items and "assumed_oscillation" in items
    _put(client, name, "connection_ramp", {"value": 6.0, "source": "datasheet"})
    _put(client, name, "oscillation", {"value": 2.0, "source": "assumed"})
    items = _flow_items(client, name)
    assert "assumed_connection_ramp" not in items
    assert items["assumed_oscillation"]["message"].split(":")[1].strip().startswith("2 ")


@pytest.mark.parametrize("item, body, kind", [
    ("fault_level", {"value": 300.0, "source": "code"}, "value_lives_on_the_bus"),
    ("connection_ramp", {"value": 0.0, "source": "code"}, "invalid_limit_value"),
    ("connection_ramp", {"value": -1.0, "source": "code"}, "invalid_limit_value"),
    ("connection_ramp", {"source": "code"}, "invalid_limit_value"),
    ("connection_ramp", {"value": 1.0}, "invalid_limit_source"),
    ("oscillation", {"value": 100.5, "source": "code"}, "invalid_limit_value"),
    ("oscillation", {"value": 1.0, "source": "guess"}, "invalid_limit_source"),
    ("oscillation", {"value": 1.0, "source": "code", "note": "x" * 501}, "note_too_long"),
    ("band_of_concern", {"value": 1.0, "source": "code"}, "unknown_limit_item"),
])
def test_a_refused_limit_is_a_422_and_changes_nothing(client, hub, item, body, kind):
    name, d = hub
    r = _put(client, name, item, body)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == kind
    assert not (d / L.SIDECAR_NAME).exists()


def test_the_oscillation_limit_may_be_a_hundred_percent(client, hub):
    name, _d = hub
    assert _put(client, name, "oscillation", {"value": 100, "source": "code"}).status_code == 200


def test_a_non_finite_value_is_refused_by_the_service(tmp_path):
    for v in (float("inf"), float("nan")):
        with pytest.raises(L.LimitError) as exc:
            L.set_limit(tmp_path, "connection_ramp", value=v, source="code")
        assert exc.value.kind == "invalid_limit_value"


def test_a_user_built_project_takes_the_users_limit_over_the_placeholder(client, api_project):
    name = api_project("limits_plain")
    row = _row(client, name, "oscillation")
    assert row["source"].startswith("assumed (generic placeholder)")
    assert row["setting"]["default"] == {"value": G.GENERIC_OSCILLATION_PCT, "source": "assumed",
                                         "origin": "placeholder"}
    r = _put(client, name, "oscillation", {"value": 0.4, "source": "datasheet"})
    assert r.status_code == 200, r.text
    assert r.json()["default"] is None
    row = _row(client, name, "oscillation")
    assert row["limit"] == 0.4 and row["source"] == "Your value (datasheet)" and row["assumed"] is False


@pytest.fixture
def same_org_other_user(_auth_db, seeded_identity):
    """A second user in the same org (a cross-org caller would stop at the ACL)."""
    from db.models import OrgMembership, User
    from services.auth_service import hash_password

    _engine, session_local = _auth_db
    with session_local() as db:
        u = User(id=_uuid.uuid4(), email=f"colleague-{_uuid.uuid4().hex[:6]}@example.com",
                 password_hash=hash_password("irrelevant"), status="active",
                 is_super_admin=False, created_at=datetime.now(tz=timezone.utc))
        db.add(u)
        db.flush()
        db.add(OrgMembership(id=_uuid.uuid4(), user_id=u.id,
                             org_id=seeded_identity["org_id"], role="admin"))
        db.commit()
        uid = u.id
    with TestClient(main.app) as c:
        yield attach_session(c, session_local, uid)


def test_a_non_holder_cannot_set_or_reset_a_limit(client, api_project, project_storage_dir,
                                                  same_org_other_user):
    name = api_project("limits_locked")
    assert client.post(f"/api/projects/{name}/lock").status_code == 200
    assert _put(client, name, "oscillation", {"value": 0.7, "source": "code"}).status_code == 200
    before = (project_storage_dir(name) / L.SIDECAR_NAME).read_bytes()
    for r in (_put(same_org_other_user, name, "oscillation", {"value": 9.0, "source": "code"}),
              same_org_other_user.delete(f"{BASE}/{name}/grid-readiness/limits/oscillation")):
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["error_kind"] == "project_locked"
    assert (project_storage_dir(name) / L.SIDECAR_NAME).read_bytes() == before

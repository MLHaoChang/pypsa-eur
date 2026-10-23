"""grid-readiness/1 (checked-results S9): scorecard rows checked by their declared rule."""

# ruff: noqa: F811 -- imported shared fixtures.
import json

import pytest

from harness import grid_readiness_calculations as R
from services.adequacy import grid_readiness as G
from tests.assurance_acceptance.support import approval, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_grid_readiness import PROFILE, T, _harmonic_site, _project, _rows
from tests.test_workflow_tools import baseline  # noqa: F401


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", R.PROFILE)


def raw_for(rows):
    return {"version": 1, "profile": R.PROFILE, "entity_id": "network", "index": ["saved scorecard"],
            "grid_code": {"id": PROFILE, "title": "EU RfG"}, "rows": rows, "unsaved_edits": False}


def card(tmp_path, tid="eh_datacenter", dc_load=None):
    d, n = _project(tmp_path, tid, dc_load=dc_load)
    return G.grid_readiness(d, n, PROFILE)["rows"]


def counts(rows):
    scope = {"version": 1, "profile": R.PROFILE, "entity_id": "network"}
    facts, _ = R.calculate(raw_for(rows), scope)
    return {f["metric"]: f["value"] for f in facts}


@pytest.mark.parametrize("tid, dc_load", [("eh_datacenter", {}), ("eh_datacenter", None), ("eh_h2_hub", None),
                                          ("eh_microgrid_staged", None)])
def test_every_row_has_one_disposition_and_the_counts_add_up(tmp_path, tid, dc_load):
    rows = card(tmp_path, tid, dc_load)
    c = counts(rows)
    assert c["checked_pass"] + c["checked_warn"] + c["checked_fail"] + c["not_checked"] == len(rows)
    lines = R.notes(raw_for(rows), str)
    assert len(lines) == 1 + len(rows)  # the grid-code line, then one per row


def test_each_declared_rule_checks_its_row(tmp_path):
    rows = {r["id"]: r for r in card(tmp_path, "eh_datacenter", {})}
    for rid in ("scr", "ramp_rate", "rapid_voltage_change"):
        kind, verdict = R.disposition(rows[rid])
        assert kind == "checked" and verdict == rows[rid]["verdict"], rid
    reactive = dict(rows["reactive_range"], project_value=5.0, limit=4.0, verdict="pass")
    assert R.disposition(reactive) == ("checked", "pass")
    line = next(x for x in R.notes(raw_for([rows["rapid_voltage_change"]]), str) if x.startswith("Rapid"))
    assert "before mitigation; a screening upper bound" in line


def test_a_rounded_boundary_is_withheld_not_re_verdicted(tmp_path):
    d, n = _project(tmp_path, "eh_datacenter")
    n.buses.at["grid", "eh_sk_mva"] = 89.988  # SCR 2.9996: stored 3.0, the engine says warn
    row = _rows(G.grid_readiness(d, n, PROFILE))["scr"]
    assert R.disposition(row) == ("boundary_disagreement", "pass")
    assert counts([row]) == {"checked_pass": 0, "checked_warn": 0, "checked_fail": 0, "not_checked": 1}


def test_unknown_not_scalar_and_categorical_are_named(tmp_path):
    rows = card(tmp_path, "eh_datacenter", None)
    kinds = {r["id"]: R.disposition(r)[0] for r in rows}
    assert kinds["ramp_rate"] == "unknown"  # no AI-training profile: no value
    assert kinds["lole"] == "not_scalar" and kinds["oscillation"] == "not_scalar"
    assert any(k.startswith("energization_peak:") and v == "not_scalar" for k, v in kinds.items())
    assert kinds["grid_former"] == "categorical"
    text = "\n".join(R.notes(raw_for(rows), str))
    assert "not checked; its verdict rests on inputs the scorecard does not store" in text
    assert "not checked; a yes-or-no requirement" in text and "not checked; an input is missing" in text


@pytest.mark.parametrize("source", ["6-pulse", "12-pulse"])
def test_the_harmonic_row_is_not_scalar_since_its_verdict_reads_each_order(tmp_path, source):
    row = _rows(G.grid_readiness(tmp_path, _harmonic_site(source), PROFILE))["harmonic_distortion"]
    assert row["project_value"] is not None
    assert R.disposition(row)[0] == "not_scalar"


def test_a_rule_that_is_not_the_codes_rule_is_withheld(tmp_path):
    row = _rows({"rows": card(tmp_path, "eh_datacenter", {})})["scr"]
    loose = dict(row, rule={**row["rule"], "comparator": "<="})
    assert R.disposition(loose)[0] == "rule_mismatch"
    stray = dict(row, id="made_up_row")
    assert R.disposition(stray)[0] == "rule_mismatch"


# ── end to end ────────────────────────────────────────────────────────────


@pytest.fixture
def hub(baseline):
    ctx, _project_id, path = baseline
    T.BUILDERS["eh_datacenter"]().export_to_netcdf(str(path / "network.nc"))
    T.write_sidecars(path, "eh_datacenter")
    return ctx, path


def ask(checked, ctx, values):
    scope = {"version": 1, "profile": R.PROFILE, "project_id": ctx.project_uuid, "entity_id": "network",
             "retain": False}
    return checked.run(NumericalProvider(values), message="Check the grid readiness.", checked_scope=scope)


def test_a_checked_answer_states_counts_and_a_line_per_row(hub, checked):
    from harness import calculations

    ctx, _ = hub
    scope = {"version": 1, "profile": R.PROFILE, "entity_id": "network"}
    raw = calculations.scoped_source(ctx, scope)
    facts, _ = calculations.scoped_calculate(raw, scope)
    values = {f["metric"]: f["value"] for f in facts}
    frames = ask(checked, ctx, values)
    record = approval(frames)
    answer = checked.client.get("/api/chat/answers/" + record["answer_id"]).json()
    assert {b["metric"]: b["value"] for b in answer["blocks"]} == values
    text = public_text(frames)
    assert "from the project's saved network" in text and "Short-circuit ratio at the PoC" in text


def test_unsaved_edits_are_said(hub, checked):
    from harness import calculations
    from services import dirty_state

    ctx, _ = hub
    dirty_state.mark_dirty(ctx)
    raw = calculations.scoped_source(ctx, {"version": 1, "profile": R.PROFILE, "entity_id": "network"})
    assert R.notes(raw, str)[0].endswith("unsaved edits are not included.")


def test_no_saved_network_and_an_unknown_grid_code_are_refused(hub, checked):
    from harness import calculations
    from services import campus_electrical_service as ce

    ctx, path = hub
    scope = {"version": 1, "profile": R.PROFILE, "entity_id": "network"}
    (path / ce.SUBDIR).mkdir(exist_ok=True)
    (path / ce.SUBDIR / ce.SETTINGS_FILE).write_text(json.dumps({"profile": "nope"}))
    with pytest.raises(ValueError, match="unknown_grid_code_profile"):
        calculations.scoped_source(ctx, scope)
    (path / ce.NETWORK_FILE).unlink()
    with pytest.raises(ValueError, match="no_saved_project"):
        calculations.scoped_source(ctx, scope)


def test_an_edited_limit_makes_the_checked_evidence_stale(hub, checked):
    from fastapi import HTTPException

    from harness import calculations
    from services import assurance_records
    from services.adequacy import grid_limits

    ctx, path = hub
    scope = {"version": 1, "profile": R.PROFILE, "project_id": ctx.project_uuid, "entity_id": "network",
             "retain": False}
    grid_limits.set_limit(path, "fault_level", source="code", note="TSO letter")
    record = assurance_records.capture_scoped(assurance_records.actor_id(), scope)
    assert assurance_records.evidence(assurance_records.actor_id(), record["evidence_id"])
    rows = calculations.scoped_source(ctx, scope)["rows"]
    # A note-only edit leaves every row as it was; the sidecar's bytes still differ.
    grid_limits.set_limit(path, "fault_level", source="code", note="TSO letter, revised")
    assert calculations.scoped_source(ctx, scope)["rows"] == rows
    with pytest.raises(HTTPException):
        assurance_records.evidence(assurance_records.actor_id(), record["evidence_id"])


def test_a_limits_file_appearing_makes_the_checked_evidence_stale(hub, checked):
    from fastapi import HTTPException

    from harness import calculations
    from services import assurance_records
    from services.adequacy import grid_limits

    ctx, path = hub
    scope = {"version": 1, "profile": R.PROFILE, "project_id": ctx.project_uuid, "entity_id": "network",
             "retain": False}
    assert not (path / grid_limits.SIDECAR_NAME).exists()
    assert calculations.scoped_source(ctx, scope)["limits_sha256"] is None
    record = assurance_records.capture_scoped(assurance_records.actor_id(), scope)
    assert assurance_records.evidence(assurance_records.actor_id(), record["evidence_id"])
    grid_limits.set_limit(path, "fault_level", source="code", note="TSO letter")
    assert calculations.scoped_source(ctx, scope)["limits_sha256"] is not None
    with pytest.raises(HTTPException):
        assurance_records.evidence(assurance_records.actor_id(), record["evidence_id"])


def test_a_limits_edit_during_the_read_is_refused_by_name(hub, checked, monkeypatch):
    from harness import calculations
    from services.adequacy import grid_limits

    ctx, path = hub
    read = G.grid_readiness_for
    edits = iter(range(1000))

    def edited_mid_read(project):
        card = read(project)
        grid_limits.set_limit(path, "fault_level", source="code", note=f"edit {next(edits)}")
        return card

    monkeypatch.setattr(G, "grid_readiness_for", edited_mid_read)
    with pytest.raises(ValueError, match="limits_changed_during_read"):
        calculations.scoped_source(ctx, {"version": 1, "profile": R.PROFILE, "entity_id": "network"})
    frames = ask(checked, ctx, {})
    reasons = {s["reason"] for s in approval(frames)["receipt"]["result_slots"] if s["disposition"] != "answered"}
    assert reasons == {"limits_changed_during_read"}

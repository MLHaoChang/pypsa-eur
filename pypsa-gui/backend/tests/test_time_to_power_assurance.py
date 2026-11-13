"""DC 27: the time-to-power fact read through `harness.calculations` from a
saved project (GK A plan G7, G9, G10)."""

import json

import pytest

from harness import time_to_power_calculations as R
from project_templates import eh_templates as T
from tests.assurance_acceptance.support import approval, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_workflow_tools import baseline  # noqa: F401

SCOPE = {"version": 1, "profile": R.PROFILE, "entity_id": "network"}


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", R.PROFILE)


def save(path, tid, body=None):
    """The project saved as a data-centre template; ``body`` None saves it
    without a dc_load spec (a native create)."""
    spec = None if body is None else T.dc_load_spec(tid, body)
    T.BUILDERS[tid](load_spec=spec).export_to_netcdf(str(path / "network.nc"))
    T.write_sidecars(path, tid, load_spec=spec)
    meta = json.loads((path / T.META_FILE).read_text())
    if spec is not None:
        meta["dc_load"] = T.dc_load_meta(tid, spec)
    (path / T.META_FILE).write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")


def edit_spec(path, **dates):
    meta = json.loads((path / T.META_FILE).read_text())
    meta["dc_load"]["spec"].update(dates)
    (path / T.META_FILE).write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")


@pytest.fixture
def staged_dc(baseline):
    ctx, _project_id, path = baseline
    save(path, "eh_datacenter_staged", {})
    return ctx, path


def facts(ctx):
    from harness import calculations

    raw = calculations.scoped_source(ctx, SCOPE)
    found, slots = calculations.scoped_calculate(raw, SCOPE)
    return raw, {f["metric"]: f for f in found}, {s["metric"]: s for s in slots}


def test_the_staged_example_reads_12_months_to_the_2027_stage(staged_dc):
    raw, found, _ = facts(staged_dc[0])
    fact = found["time_to_power_months"]
    assert (fact["value"], fact["unit"], fact["formula_id"]) == (12.0, "months", "dc-time-to-power/1")
    lines = R.notes(raw, str)
    assert "First power on 2027-01-01" in lines[0] and "36-month period" in lines[0]
    assert "2026-01-01" in lines[0]


def test_a_checked_answer_states_the_fact(staged_dc, checked):
    ctx, _ = staged_dc
    _raw, found, _ = facts(ctx)
    values = {m: f["value"] for m, f in found.items()}
    scope = {**SCOPE, "project_id": ctx.project_uuid, "retain": False}
    frames = checked.run(NumericalProvider(values), message="What is the time to power?", checked_scope=scope)
    record = approval(frames)
    answer = checked.client.get("/api/chat/answers/" + record["answer_id"]).json()
    assert {b["metric"]: b["value"] for b in answer["blocks"]} == values
    assert "First power on 2027-01-01" in public_text(frames)


def test_a_connection_date_wins_and_is_exact(staged_dc):
    ctx, path = staged_dc
    edit_spec(path, connection_date="2028-07-15")
    raw, found, _ = facts(ctx)
    assert found["time_to_power_months"]["value"] == 31.0
    assert "exact to the day" in R.notes(raw, str)[0]


def test_a_single_stage_project_reads_its_modelled_year(baseline):
    ctx, _project_id, path = baseline
    save(path, "eh_datacenter", {"project_start": "2028-10-01"})
    raw, found, _ = facts(ctx)
    # One stage, the modelled 2030 year at the fixed 40 MW import; the phase is in from 2030-01-01.
    assert found["time_to_power_months"]["value"] == 15.0
    assert "12-month period" in R.notes(raw, str)[0]


@pytest.mark.parametrize("body, reason", [
    (None, "no_dc_load_spec"),
    ({}, "no_project_start"),
])
def test_a_missing_input_is_withheld_with_its_reason_never_zero(baseline, body, reason):
    ctx, _project_id, path = baseline
    save(path, "eh_datacenter", body)
    raw, found, slots = facts(ctx)
    assert found == {}
    assert slots["time_to_power_months"]["disposition"] == "unavailable"
    assert slots["time_to_power_months"]["reason"] == reason
    assert R.notes(raw, str)[0].startswith("Time to power is not available")


def test_a_stage_refusal_is_withheld_by_name(staged_dc):
    import pypsa

    ctx, path = staged_dc
    n = pypsa.Network(str(path / "network.nc"))
    n.links.loc["grid_import", "eh_role"] = ""
    n.export_to_netcdf(str(path / "network.nc"))
    _raw, found, slots = facts(ctx)
    assert found == {} and slots["time_to_power_months"]["reason"] == "import_link_missing"


def test_no_saved_network_is_refused(staged_dc):
    from harness import calculations

    ctx, path = staged_dc
    (path / "network.nc").unlink()
    with pytest.raises(ValueError, match="no_saved_project"):
        calculations.scoped_source(ctx, SCOPE)
    with pytest.raises(ValueError, match="network_scope_required"):
        calculations.scoped_source(ctx, {**SCOPE, "entity_id": "G1"})


@pytest.mark.parametrize("change", ["project_start", "connection_date", "schedule"])
def test_an_edited_input_makes_the_checked_evidence_stale(staged_dc, change):
    import pypsa
    from fastapi import HTTPException

    from services import assurance_records

    ctx, path = staged_dc
    scope = {**SCOPE, "project_id": ctx.project_uuid, "retain": False}
    record = assurance_records.capture_scoped(assurance_records.actor_id(), scope)
    assert assurance_records.evidence(assurance_records.actor_id(), record["evidence_id"])
    if change == "schedule":
        n = pypsa.Network(str(path / "network.nc"))
        n.links_t["eh_energization_mw"]["grid_import"] = n.links_t["eh_energization_mw"]["grid_import"] + 1.0
        n.export_to_netcdf(str(path / "network.nc"))
    else:
        edit_spec(path, **{change: "2025-05-05"})
    with pytest.raises(HTTPException):
        assurance_records.evidence(assurance_records.actor_id(), record["evidence_id"])


def test_the_profile_is_opt_in_and_moves_no_other_profile(monkeypatch):
    """G12: off unless listed; the default-on profiles are the ones they were."""
    from harness import calculations, rollout

    monkeypatch.delenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", raising=False)
    assert R.PROFILE in calculations.OPT_IN_PROFILES
    assert not rollout.profile_enabled(R.PROFILE)
    caps = rollout.capabilities()["profiles"]
    assert not caps[R.PROFILE]["enabled"]
    on = {p for p, c in caps.items() if c["enabled"]}
    assert on == set(calculations.PROFILES) - calculations.OPT_IN_PROFILES
    assert R.PROFILE not in on
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", "grid-readiness/1")
    assert not rollout.profile_enabled(R.PROFILE)


def test_a_disabled_profile_emits_no_fact(staged_dc, checked, monkeypatch):
    ctx, _ = staged_dc
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", "grid-readiness/1")
    scope = {**SCOPE, "project_id": ctx.project_uuid, "retain": False}
    frames = checked.run(NumericalProvider({"time_to_power_months": 12.0}),
                         message="What is the time to power?", checked_scope=scope)
    assert "profile_disabled" in json.dumps(frames)
    assert "dc-time-to-power/1" not in json.dumps(frames)


@pytest.mark.parametrize("profile, index, expected", [
    ("grid-readiness/1", ["saved scorecard"],
     {"start": "saved scorecard", "end": "saved scorecard", "snapshots": None,
      "weight_basis": {"checked_pass": "none", "checked_warn": "none", "checked_fail": "none",
                       "not_checked": "none"}}),
    ("time-to-power/1", ["saved project"],
     {"start": "saved project", "end": "saved project", "snapshots": None,
      "weight_basis": {"time_to_power_months": "none"}}),
])
def test_fact_identity_windows_are_pinned(profile, index, expected):
    """G11: an existing network-scoped profile's window reads as before the new token."""
    from harness import fact_identity

    assert fact_identity.window(index, {"profile": profile, "entity_id": "network"}) == expected


def test_the_disclosure_says_grid_power_only_and_the_period_resolution():
    """G4, G3: on-site supply is not counted; a staged date is as fine as its period."""
    from harness import calculations

    words = calculations.disclosure(SCOPE)
    assert "Grid-delivered power only" in words and "on-site generation" in words
    assert "not counted" in words and "up to a period earlier" in words

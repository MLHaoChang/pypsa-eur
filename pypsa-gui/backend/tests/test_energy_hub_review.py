"""
P22 — assistant support: review_eh_study findings, their actions, and the
guide / template / registry tools (plan 2026-09-26).

Every action a finding offers must be something the named tool accepts
verbatim — validated here against the tool schema AND, for run_eh_study,
the study request's own validation — because the assistant is told to run
an accepted action unchanged.
"""
from __future__ import annotations

import copy
import pathlib
import sys
import time

import pytest
from fastapi import HTTPException

from services import chat_service
from services import chat_tools as T
from services.adequacy.eh_review import review_report
from services.chat_tools_schema import TOOLS

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def _report(**over) -> dict:
    base = {
        "archetype": "weak_flexible", "pack_hash": "p", "assumptions_hash": "a",
        "ens_cap_permyriad": 10.0, "achieved_ens_permyriad": 0.0,
        "mc_lole_h": 12.4, "certified": False, "cost_at_target_eur": 3.7e7,
        "completeness": {"target": "ok", "certification": "ok"},
        "sections": {"certification": {"status": "ok", "note": None, "payload": {
            "verdict": "fail", "target_lole_h": 3.0, "lole_ci": [0.2, 0.3],
            "draws": 500, "converged": True}}},
        "pipeline": {"solves_consumed": 25, "budget_solves": 60, "stages": []},
        "notes": [],
    }
    base.update(over)
    return base


RECORD = {"archetype": "weak_flexible", "budget_solves": 60,
          "stages": None, "pack_overrides": {"import_p_nom_mw": 40.0},
          "dtc_attribution": "per_load"}


def _by_id(out, fid):
    return next(f for f in out["findings"] if f["id"] == fid)


def _validate_action(action: dict) -> None:
    """The named tool exists and accepts these args unchanged."""
    tool = next((t for t in TOOLS if t["name"] == action["tool"]), None)
    assert tool is not None, action
    schema = tool["input_schema"]
    assert set(action["args"]) <= set(schema["properties"]), action
    assert set(schema.get("required", [])) <= set(action["args"]), action
    if action["tool"] == "run_eh_study":
        from services.adequacy.eh_study import validate_stages
        from services.adequacy.eh_study_runner import (
            _PACK_FACTORY,
            EhStudyRequest,
            McOptions,
            apply_pack_overrides,
            resolve_dtc_attribution,
        )
        body = EhStudyRequest.model_validate(action["args"])
        apply_pack_overrides(_PACK_FACTORY[body.archetype](), body.pack_overrides,
                             raise_http=True)
        McOptions.model_validate(body.mc or {})
        resolve_dtc_attribution(body.dtc_config, body.dtc_attribution)
        if body.stages is not None:
            validate_stages(body.stages)


# ── rules ───────────────────────────────────────────────────────────────────


def test_a_failed_certification_is_high_with_a_tighter_replan():
    out = review_report(_report(), RECORD)
    f = _by_id(out, "certification_fail")
    assert f["severity"] == "high" and out["findings"][0] is f
    assert f["evidence"]["lole_h_per_year"] == 12.4
    assert f["evidence"]["target_lole_h"] == 3.0
    act = f["actions"][0]
    assert act["tool"] == "run_eh_study"
    po = act["args"]["pack_overrides"]
    assert po["ens_cap_permyriad"] == 2.5 and po["import_p_nom_mw"] == 40.0
    assert po["levers"] == {"redundancy": True, "storage_duration": True}
    assert act["args"]["dtc_attribution"] == "per_load"     # request preserved
    _validate_action(act)


def test_inconclusive_offers_more_draws_and_no_target_offers_one():
    cert = {"verdict": "inconclusive", "target_lole_h": 3.0, "draws": 500}
    out = review_report(_report(sections={"certification": {
        "status": "ok", "payload": cert}}), RECORD)
    act = _by_id(out, "certification_inconclusive")["actions"][0]
    assert act["args"]["mc"] == {"draws": 1000}
    _validate_action(act)
    out = review_report(_report(certified=None, mc_lole_h=4.2, sections={
        "certification": {"status": "ok", "payload": {"verdict": None,
                                                      "target_lole_h": None}}}),
        {"archetype": "strong_grid"})
    act = _by_id(out, "certification_no_target")["actions"][0]
    assert act["args"]["pack_overrides"] == {"target_lole_h": 3.0,
                                             "certification_metric": "mc_lole"}
    _validate_action(act)


@pytest.mark.parametrize("note,expect_tool", [
    ("budget_solves exhausted before every contingency was solved", "run_eh_study"),
    ("fmea_top needs VOLL > 0 (the session VOLL is 0)", "update_solver_config"),
    ("tag at least one bus eh_critical — or pass dtc_config", None),
    ("eh_sk_mva (and IBR capacity) required on ALL eh_poc buses", None),
])
def test_not_established_sections_get_a_reason_specific_recommendation(
        note, expect_tool):
    rep = _report(completeness={"target": "ok", "dtc": "not_established",
                                "certification": "ok"})
    rep["sections"]["dtc"] = {"status": "not_established", "note": note}
    f = _by_id(review_report(rep, RECORD), "not_established_dtc")
    assert f["evidence"]["note"] == note
    if expect_tool is None:
        assert f["actions"] == [] and f["recommendation"]
    else:
        assert f["actions"][0]["tool"] == expect_tool
        _validate_action(f["actions"][0])


def test_dominant_mode_dtc_unserved_levers_and_caveats():
    rep = _report()
    rep["sections"]["fmea_top"] = {"status": "ok", "payload": {"rows": [
        {"name": "site_transformer", "criticality_eur_per_year": 7.9e6},
        {"name": "grid_import", "criticality_eur_per_year": 5.9e5}]}}
    rep["sections"]["dtc"] = {"status": "ok", "payload": {
        "mode": "stress_fixed_plan", "priority_exact": False,
        "priority_caveat_links": ["feeder"], "contingencies": [
            {"contingency": "grid_import", "status": "ok",
             "critical_unserved_mwh": 60.0, "noncritical_unserved_mwh": 20.0}]}}
    rep["sections"]["levers"] = {"status": "ok", "payload": {"options": [
        {"kind": "storage_duration", "value": 4, "unit": "h", "status": "ok",
         "cost_at_target_eur": 900.0, "meets_target": True},
        {"kind": "storage_duration", "value": 8, "unit": "h", "status": "ok",
         "cost_at_target_eur": 800.0, "meets_target": True}]}}
    rep["notes"] = ["weak_flexible pack has dsr_opt_in=True but no dsr_buses "
                    "were supplied — DSR stays OFF"]
    out = review_report(rep, RECORD)
    dom = _by_id(out, "fmea_dominant_mode")
    assert dom["evidence"]["mode"] == "site_transformer"
    assert dom["evidence"]["share"] == pytest.approx(0.93, abs=0.01)
    dtc = _by_id(out, "dtc_critical_unserved")
    assert dtc["severity"] == "high" and dtc["evidence"]["critical_unserved_mwh"] == 60.0
    assert _by_id(out, "levers_best_option")["evidence"]["value"] == 8
    assert _by_id(out, "dtc_priority_caveat")["evidence"]["lossy_links"] == ["feeder"]
    assert _by_id(out, "dsr_off")["severity"] == "low"
    for f in out["findings"]:
        for act in f["actions"]:
            _validate_action(act)
    sev = [f["severity"] for f in out["findings"]]
    assert sev == sorted(sev, key=lambda s: ["high", "medium", "low", "info"].index(s))


# ── tools ───────────────────────────────────────────────────────────────────


def test_tool_tiers_and_no_data(install_network):
    assert chat_service._safety_tier_for("review_eh_study") == "read"
    assert chat_service._safety_tier_for("get_feature_guide") == "read"
    assert chat_service._safety_tier_for("get_eh_template") == "read"
    assert chat_service._safety_tier_for("put_stress_scenarios") == "write"
    from tests.test_energy_hub_frontier_fmea import _feeder_hub
    install_network(_feeder_hub())
    assert T.review_eh_study()["status"] == "no_data"


def test_feature_guide_index_tour_field_and_404():
    idx = T.get_feature_guide()
    assert {"eh_study", "fmea", "eh_tagging"} <= set(idx["tours"])
    assert "eh_poc" in idx["fields"]
    assert T.get_feature_guide(tour="fmea")["steps"][0]["target"] == "fmea-sweep"
    assert "GRID-side" in T.get_feature_guide(field="eh_poc")["help"]
    with pytest.raises(HTTPException):
        T.get_feature_guide(field="nope")


def test_prompt_carries_the_eh_workflow_both_modes():
    assert "review_eh_study" in chat_service._EH_GUIDE
    assert "OFFER" in chat_service._EH_GUIDE
    assert "review_eh_study" not in chat_service._EH_GUIDE_FACTS   # tools-off


# ── the loop, live: study → review → apply the action → review again ───────


def _poll(client, url, timeout=900.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(url)
        if r.status_code == 200 and r.json().get("status") != "running":
            return r.json()
        time.sleep(0.2)
    raise AssertionError("never finished")


def _chat_poll(timeout=900.0):
    """Poll the way the assistant does (get_adequacy_results), so the whole
    loop stays on the chat path's context."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = T.get_adequacy_results("eh_study")
        if isinstance(st, dict) and st.get("status") not in ("running", None):
            return st
        time.sleep(0.2)
    raise AssertionError("never finished")


@pytest.mark.live_solve
def test_review_then_apply_the_recommended_action_on_the_datacenter(
        client, install_network):
    sys.path.insert(0, str(BACKEND / "project_templates"))
    import eh_templates as TPL
    install_network(TPL.build_eh_datacenter())
    meta = TPL.TEMPLATE_META["eh_datacenter"]
    stages = ["apply_pack", "ens_solve", "mc_certify", "assemble"]
    args = {"archetype": meta["recommended_archetype"], "stages": stages,
            "pack_overrides": meta["pack_overrides"],
            "dtc_attribution": meta["dtc_attribution"]}
    # the chat path end to end: run_eh_study → poll → review_eh_study
    T.run_eh_study(**args)
    assert _chat_poll()["status"] == "done"

    first = T.review_eh_study()
    fail = _by_id(first, "certification_fail")
    assert fail["evidence"]["lole_h_per_year"] > fail["evidence"]["target_lole_h"]
    action = copy.deepcopy(fail["actions"][0])
    _validate_action(action)
    assert action["args"]["stages"] == stages          # the user's request kept

    # "apply it": the exact tool + args the finding named
    T.run_eh_study(**action["args"])
    assert _chat_poll()["status"] == "done"
    second = T.review_eh_study()
    assert second["summary"]["ens_cap_permyriad"] == \
        action["args"]["pack_overrides"]["ens_cap_permyriad"]
    assert second["summary"]["mc_lole_h_per_year"] <= \
        first["summary"]["mc_lole_h_per_year"] + 1e-9


def test_get_eh_template_and_put_stress_scenarios_via_chat(tmp_path, monkeypatch):
    import types
    sys.path.insert(0, str(BACKEND / "project_templates"))
    import eh_templates as TPL
    proj = types.SimpleNamespace(directory=tmp_path, name="p")
    monkeypatch.setattr(T, "_authorized_project", lambda name: proj)
    assert T.get_eh_template("p")["status"] == "no_data"
    TPL.write_sidecars(tmp_path, "eh_microgrid")
    assert T.get_eh_template("p")["recommended_archetype"] == "off_grid"
    reg = T.get_stress_scenarios("p")["scenarios"]
    reg.append({"id": "storm", "kind": "parametric", "frequency_per_year": 0.1,
                "renewable_availability_multiplier": 0.0})
    assert [s["id"] for s in T.put_stress_scenarios("p", reg)["scenarios"]] == \
        ["dunkelflaute", "heatwave", "storm"]
    with pytest.raises(HTTPException) as exc:
        T.put_stress_scenarios("p", reg + [{"id": "bad", "kind": "parametric",
                                            "frequency_per_year": 0}])
    assert exc.value.status_code == 422

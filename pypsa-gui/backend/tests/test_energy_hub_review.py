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
from tests._tool_actions import _validate_action  # shared with the setup tests (§6.3)

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


# ── rules ───────────────────────────────────────────────────────────────────


def test_a_failed_energy_limited_plan_is_high_with_a_tighter_replan():
    out = review_report(_report(achieved_ens_permyriad=9.5), RECORD)
    f = _by_id(out, "certification_fail")
    assert f["severity"] == "high" and out["findings"][0] is f
    assert f["evidence"]["lole_h_per_year"] == 12.4
    assert f["evidence"]["target_lole_h"] == 3.0
    act = f["actions"][0]
    assert act["tool"] == "run_eh_study"
    po = act["args"]["pack_overrides"]
    assert po["ens_cap_permyriad"] == 2.5 and po["import_p_nom_mw"] == 40.0
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
    # P25 (guided-mode spec §6.3): the setup tool is offered in both modes,
    # tools-on only — a tools-off prompt names no tool.
    assert "suggest_eh_setup" in chat_service._EH_GUIDE
    assert "suggest_eh_setup" not in chat_service._EH_GUIDE_FACTS


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
    # The template's own VOLL: the study start refuses none (template-e2e 06).
    from services.pypsa_service import PyPSAService
    PyPSAService.get_solver_state()["solver_config"].voll = \
        TPL.solver_config("eh_datacenter")["voll"]
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
    assert "outage" in fail["title"]                    # ENS already met
    action = copy.deepcopy(fail["actions"][0])
    _validate_action(action)
    assert set(stages) <= set(action["args"]["stages"])  # request kept
    assert action["args"]["pack_overrides"] == meta["pack_overrides"]
    assert action["args"]["dtc_attribution"] == "per_load"

    # "apply it": the exact tool + args the finding named — and it must
    # produce NEW evidence (the islanded sizing), not re-run a no-op
    T.run_eh_study(**action["args"])
    assert _chat_poll()["status"] == "done"
    second = T.review_eh_study()
    assert second["summary"]["completeness"]["dtc"] == "ok"
    assert "dtc" not in first["summary"]["completeness"] or \
        first["summary"]["completeness"]["dtc"] != "ok"


def test_get_eh_template_and_put_stress_scenarios_via_chat(tmp_path, monkeypatch):
    import types
    sys.path.insert(0, str(BACKEND / "project_templates"))
    import eh_templates as TPL
    # An AuthorizedProject carries a uuid; master's sidecar lock gate
    # (68e5f62c3) reads it. "u-1" is not a real uuid → no lock row, exactly
    # as master's own worksheet/stress handler tests construct it. This is
    # the no-lock-row CONTROL only: the real-uuid variants (holder writes,
    # non-holder gets 409 project_locked) are in
    # tests/test_chat_tools_handler_dependencies.py (merge review B1).
    proj = types.SimpleNamespace(directory=tmp_path, name="p", uuid="u-1")
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


# ── P19–P22 gate ────────────────────────────────────────────────────────────


def test_an_outage_driven_fail_does_not_offer_a_no_op_ens_tightening():
    """BINDING 1: with the ENS target already met (achieved 0 ‱), tightening
    ENS changes nothing — the LOLE comes from outages the deterministic plan
    never sees. Offer the islanded sizing step instead, and say why."""
    out = review_report(_report(achieved_ens_permyriad=0.0), RECORD)
    f = _by_id(out, "certification_fail")
    assert "outage" in f["recommendation"].lower()
    assert not any("ens_cap_permyriad" in (a["args"].get("pack_overrides") or {})
                   for a in f["actions"])
    assert f["actions"] and "dtc_planning" in f["actions"][0]["args"]["stages"]
    for a in f["actions"]:
        _validate_action(a)


def test_an_energy_limited_fail_still_offers_a_tighter_target():
    out = review_report(_report(achieved_ens_permyriad=9.0), RECORD)
    act = _by_id(out, "certification_fail")["actions"][0]
    assert act["args"]["pack_overrides"]["ens_cap_permyriad"] == 2.5
    _validate_action(act)


def test_rerun_actions_keep_mc_dsr_and_dtc_config():
    """BINDING 2: the record now carries mc / dsr_buses / dtc_config and
    every re-run action keeps them."""
    record = {**RECORD, "mc": {"draws": 300, "seed": 3},
              "dsr_buses": ["dc_mv"],
              "dtc_config": {"critical_bus_ids": ["it_bus"],
                             "islanding_contingencies": ["grid_import"]}}
    rep = _report(sections={"certification": {"status": "ok", "payload": {
        "verdict": "inconclusive", "target_lole_h": 3.0, "draws": 300}}})
    act = _by_id(review_report(rep, record), "certification_inconclusive")["actions"][0]
    assert act["args"]["mc"] == {"draws": 600, "seed": 3}
    assert act["args"]["dsr_buses"] == ["dc_mv"]
    assert act["args"]["dtc_config"]["critical_bus_ids"] == ["it_bus"]
    _validate_action(act)


def test_the_study_record_stores_the_whole_request(client, install_network,
                                                   monkeypatch):
    import threading
    reached = threading.Event()

    def fake_run(network, pack, cfg, **kw):
        reached.set()
        raise RuntimeError("stop")

    monkeypatch.setattr("services.adequacy.eh_study.run_eh_study", fake_run)
    from tests.test_energy_hub_frontier_fmea import _feeder_hub
    install_network(_feeder_hub())
    # The study start refuses a missing VOLL (template-e2e 06).
    cfg = client.get("/api/simulation/solver_config").json()
    assert client.put("/api/simulation/solver_config",
                      json={**cfg, "voll": 5000.0}).status_code == 200
    body = {"archetype": "weak_flexible", "mc": {"draws": 300, "seed": 3},
            "dsr_buses": ["hub"],
            "dtc_config": {"critical_bus_ids": ["hub"],
                           "islanding_contingencies": ["import"]}}
    assert client.post("/api/results/eh_study", json=body).status_code == 200
    assert reached.wait(10)
    rec = client.get("/api/results/eh_study").json()
    assert rec["mc"] == {"draws": 300, "seed": 3}
    assert rec["dsr_buses"] == ["hub"]
    assert rec["dtc_config"]["islanding_contingencies"] == ["import"]


# ── owner's Q3 rule (2026-09-29): no knee finding below three points ───────


def _frontier_report(n_ok: int, knee: int = 0) -> dict:
    pts = [{"target_permyriad": t, "status": "ok",
            "point": {"total_system_cost_eur": c, "achieved_ens_mwh": e}}
           for t, c, e in [(40.0, 1.0, 9.0), (20.0, 2.0, 5.0),
                           (10.0, 3.0, 2.0)][:n_ok]]
    return {
        "archetype": "strong_grid", "ens_cap_permyriad": 5.0,
        "completeness": {"frontier": "ok"},
        "sections": {"frontier": {"status": "ok", "note": None, "payload": {
            "knee_index": knee, "points": pts}}},
    }


def test_review_never_reports_a_knee_from_two_points():
    """A report stored before the fix may carry knee_index from 2 points."""
    out = review_report(_frontier_report(2))
    ids = [f.get("id") for f in (out.get("findings") or [])]
    assert "frontier_knee" not in ids, ids


def test_review_reports_a_knee_from_three_points():
    out = review_report(_frontier_report(3))
    ids = [f.get("id") for f in (out.get("findings") or [])]
    assert "frontier_knee" in ids, ids


# ── asset-investment walkthrough 2026-11-17, issue 04 ───────────────────────
# Island Microgrid: the hospital's only feeder is the one ranked Link, beside
# three diesels in the class-A ranking. Hand check: 657,000 / (657,000 +
# 3 × 302,114) = 42 % of all ranked risk, though 100 % of the Link risk.
def _island_fmea_report(*, critical_feeder: bool = True,
                        feeder_model: str = "composed"):
    rep = _report()
    rep["sections"]["fmea_top"] = {"status": "ok", "payload": {
        "rows": [{"name": "hospital_feeder",
                  "criticality_eur_per_year": 657_000.0}],
        "class_a": {"status": "ok", "rows": [
            {"name": f"diesel_{i}", "criticality_eur_per_year": 302_114.0}
            for i in (1, 2, 3)]}}}
    cert = rep["sections"].setdefault("certification", {
        "status": "ok", "payload": {}})
    cert.setdefault("payload", {})["fleet_scope"] = {"critical_feeders": [
        {"name": "hospital_feeder", "buses": ["hospital"],
         "model": feeder_model}]
        if critical_feeder else []}
    return rep


def test_dominant_link_states_its_share_of_all_ranked_risk():
    dom = _by_id(review_report(_island_fmea_report(), RECORD),
                 "fmea_dominant_mode")
    assert dom["title"] == ("hospital_feeder carries 100% of ranked Link risk "
                            "(42% of all ranked risk)")
    assert dom["evidence"]["share"] == pytest.approx(1.0)
    assert dom["evidence"]["share_of_all_ranked"] == pytest.approx(0.42, abs=0.005)


def test_a_critical_feeder_gets_a_feeder_remedy_not_generic_spare_capacity():
    # A spare unit or storage on the island cannot serve the hospital while
    # its only feeder is out: the remedy is a second feeder or local backup.
    dom = _by_id(review_report(_island_fmea_report(), RECORD),
                 "fmea_dominant_mode")
    rec = dom["recommendation"]
    assert "second feeder" in rec and "backup supply at hospital," in rec
    assert dom["evidence"]["feeds_critical_buses"] == ["hospital"]
    assert "N+1" not in rec
    assert dom["actions"] == []


def test_a_dominant_link_that_feeds_no_critical_bus_keeps_the_redundancy_rerun():
    dom = _by_id(review_report(_island_fmea_report(critical_feeder=False),
                               RECORD), "fmea_dominant_mode")
    assert [a["tool"] for a in dom["actions"]] == ["run_eh_study"]


@pytest.mark.parametrize("class_a", [
    None,                                               # no class-A block
    {"status": "not_established", "rows": []},          # ranking failed
    {"status": "ok", "rows": [{"name": "diesel_1",
                               "criticality_eur_per_year": float("nan")}]},
])
def test_share_of_all_ranked_is_none_when_it_cannot_be_stated(class_a):
    rep = _island_fmea_report()
    rep["sections"]["fmea_top"]["payload"]["class_a"] = class_a
    dom = _by_id(review_report(rep, RECORD), "fmea_dominant_mode")
    assert dom["evidence"]["share_of_all_ranked"] is None
    assert dom["title"] == "hospital_feeder carries 100% of ranked Link risk"


def test_a_feeder_without_outage_data_keeps_the_generic_advice():
    # F9: the feeder remedy needs a computed pocket, not a missing input.
    rep = _island_fmea_report(feeder_model="no_outage_data")
    dom = _by_id(review_report(rep, RECORD), "fmea_dominant_mode")
    assert [a["tool"] for a in dom["actions"]] == ["run_eh_study"]
    assert "second feeder" not in dom["recommendation"]


def test_a_composed_feeder_that_is_not_the_dominant_link_keeps_the_generic_advice():
    rep = _island_fmea_report()
    rep["sections"]["certification"]["payload"]["fleet_scope"][
        "critical_feeders"][0]["name"] = "other_feeder"
    dom = _by_id(review_report(rep, RECORD), "fmea_dominant_mode")
    assert [a["tool"] for a in dom["actions"]] == ["run_eh_study"]
    assert "second feeder" not in dom["recommendation"]


@pytest.mark.parametrize("cert", [None, {"status": "ok", "payload": None}])
def test_no_certification_payload_keeps_the_generic_advice(cert):
    # A report stored before the certification ran, or one that has none.
    rep = _island_fmea_report()
    rep["sections"]["certification"] = cert
    dom = _by_id(review_report(rep, RECORD), "fmea_dominant_mode")
    assert [a["tool"] for a in dom["actions"]] == ["run_eh_study"]


def test_a_feeder_row_without_buses_still_reads():
    rep = _island_fmea_report()
    rep["sections"]["certification"]["payload"]["fleet_scope"][
        "critical_feeders"][0]["buses"] = None
    dom = _by_id(review_report(rep, RECORD), "fmea_dominant_mode")
    assert "second feeder" in dom["recommendation"]
    assert dom["evidence"]["feeds_critical_buses"] == []


def test_zero_total_risk_has_no_share_of_all_ranked():
    # Not reachable through review_report (no risk, no dominant mode), so
    # the helper is pinned directly: 0 / 0 is no share, not a crash.
    from services.adequacy.eh_review import _share_of_all_ranked

    rows = [{"criticality_eur_per_year": 0.0}]
    assert _share_of_all_ranked(0.0, rows, {"status": "ok", "rows": rows}) is None


def test_class_a_without_rows_counts_the_links_alone():
    rep = _island_fmea_report()
    rep["sections"]["fmea_top"]["payload"]["class_a"] = {"status": "ok",
                                                         "rows": None}
    dom = _by_id(review_report(rep, RECORD), "fmea_dominant_mode")
    assert dom["evidence"]["share_of_all_ranked"] == pytest.approx(1.0)


# GK A F6': a pass withheld by a pocket's upper bound is not a sampling
# question — the interval can sit under the goal — so no "more draws".
_BOUND = {"pockets": [{"buses": ["it_bus"], "feeders": ["site_transformer"],
                       "bound_h_per_year": 43.8}],
          "chained": [], "target_h_per_year": 25.0}


def test_an_inconclusive_held_by_a_feeder_bound_names_the_feeder_not_draws():
    cert = {"verdict": "inconclusive", "target_lole_h": 25.0, "draws": 200,
            "feeder_bound": _BOUND}
    out = review_report(_report(sections={"certification": {
        "status": "ok", "payload": cert}}), RECORD)
    f = _by_id(out, "certification_inconclusive")
    assert f["actions"] == []
    assert "it_bus" in f["title"] and "site_transformer" in f["title"]
    assert "43.8" in f["title"] and "straddles" not in f["title"]
    assert "second feeder" in f["recommendation"]
    assert "issue 09" in f["recommendation"]  # GK R6: the two-area treatment
    assert "Outage data" not in f["recommendation"]
    assert out["summary"]["feeder_bound"] == _BOUND


def test_a_bound_held_by_a_feeder_without_data_asks_for_its_data():
    # GK R2: the unread rate is taken as certain; its data settles it.
    fb = {"pockets": [{"buses": ["crit"], "feeders": ["crit_feeder"],
                       "bound_h_per_year": 8760.0, "no_data": ["crit_feeder"]}],
          "chained": [], "target_h_per_year": 3.0}
    cert = {"verdict": "inconclusive", "target_lole_h": 3.0, "draws": 200,
            "feeder_bound": fb}
    out = review_report(_report(sections={"certification": {
        "status": "ok", "payload": cert}}), RECORD)
    f = _by_id(out, "certification_inconclusive")
    assert "Outage data for crit_feeder" in f["recommendation"]
    assert "not known" in f["recommendation"]
    assert "own units" not in f["recommendation"]


def test_a_bounded_dominant_feeder_gets_the_feeder_remedy():
    rep = _island_fmea_report(feeder_model="unsampled")
    rep["sections"]["certification"]["payload"]["fleet_scope"][
        "critical_feeders"][0]["upper_bound"] = True
    dom = _by_id(review_report(rep, RECORD), "fmea_dominant_mode")
    assert "second feeder" in dom["recommendation"] and dom["actions"] == []


def _bound_review(verdict, fb):
    cert = {"verdict": verdict, "target_lole_h": 25.0, "draws": 200,
            "feeder_bound": fb}
    return review_report(_report(sections={"certification": {
        "status": "ok", "payload": cert}}), RECORD)


def test_a_chained_feeder_alone_cannot_be_bounded():
    f = _by_id(_bound_review("inconclusive", {"chained": ["mid_feeder"]}),
               "certification_inconclusive")
    assert f["title"] == "Certification not decided: mid_feeder cannot be bounded"
    assert f["actions"] == []


def test_a_bound_with_missing_fields_still_reads():
    f = _by_id(_bound_review("inconclusive", {"pockets": [{}]}),
               "certification_inconclusive")
    assert f["title"].startswith("Certification not decided:  could be cut off up to 0")


def test_a_feeder_bound_on_a_pass_adds_no_inconclusive_finding():
    out = _bound_review("pass", _BOUND)
    assert all(f["id"] != "certification_inconclusive" for f in out["findings"])


# template-e2e 21: below the resolution floor, doubling the draws cannot
# settle it; the finding names the floor and Improve offers the fewest draws
# that resolve the goal.
def _floor_cert(**over):
    return {"verdict": "inconclusive", "target_lole_h": 3.0, "draws": 1,
            "n_samples": 1, "horizon_years": 1.0, "lole_ci": [0.0, 0.0],
            "resolution_floor_h": 52.1, "below_floor": True, "min_draws": 18,
            **over}


def test_below_the_floor_offers_the_minimum_draws_not_double():
    out = review_report(_report(sections={"certification": {
        "status": "ok", "payload": _floor_cert()}}), RECORD)
    f = _by_id(out, "certification_inconclusive")
    assert f["title"] == ("Certification inconclusive: with 1 draw the "
                          "smallest shortfall it can show is 52.1 h/yr, more "
                          "than the 3 h/yr goal")
    assert "at least 18 draws" in f["recommendation"]
    assert "straddles" not in f["title"]
    act = f["actions"][0]
    assert act["args"]["mc"] == {"draws": 18}
    assert act["effect"] == "set simulation runs per check to 18 (was 1)"
    _validate_action(act)


def test_below_the_floor_keeps_doubling_when_that_is_more():
    out = review_report(_report(sections={"certification": {
        "status": "ok", "payload": _floor_cert(draws=300, n_samples=300,
                                               min_draws=320)}}), RECORD)
    act = _by_id(out, "certification_inconclusive")["actions"][0]
    assert act["args"]["mc"] == {"draws": 600}


@pytest.mark.parametrize("min_draws", [2001, None])
def test_below_the_floor_beyond_the_draw_cap_offers_no_rerun(min_draws):
    out = review_report(_report(sections={"certification": {
        "status": "ok", "payload": _floor_cert(min_draws=min_draws,
                                               target_lole_h=0.0 if min_draws is None else 3.0)}}),
        RECORD)
    f = _by_id(out, "certification_inconclusive")
    assert f["actions"] == []
    assert "more draws do not" in f["recommendation"]


def test_the_straddle_finding_is_unchanged_when_not_below_the_floor():
    """GK A V4: a straddle (below_floor False or absent) keeps its words."""
    for extra in ({}, {"below_floor": False, "min_draws": None}):
        cert = {"verdict": "inconclusive", "target_lole_h": 3.0, "draws": 500,
                **extra}
        f = _by_id(review_report(_report(sections={"certification": {
            "status": "ok", "payload": cert}}), RECORD),
            "certification_inconclusive")
        assert f["title"] == ("Certification inconclusive: the LOLE interval "
                              "straddles 3 h/yr")
        assert f["recommendation"] == (
            "Not a failure: the sample cannot yet tell pass from fail. More MC "
            "draws narrow the interval; a slightly tighter plan moves it clear.")
        assert f["actions"][0]["effect"] == (
            "re-run the MC with 1000 draws (was 500) to narrow the confidence "
            "interval")


def test_below_the_floor_states_the_floor_per_year_on_a_longer_horizon():
    # 104.2 h over a 2-year horizon is 52.1 h/yr; no horizon reads as 1 year.
    for extra, floor in (({"horizon_years": 2.0, "resolution_floor_h": 104.2}, "52.1"),
                         ({"horizon_years": None}, "52.1")):
        f = _by_id(review_report(_report(sections={"certification": {
            "status": "ok", "payload": _floor_cert(**extra)}}), RECORD),
            "certification_inconclusive")
        assert f"can show is {floor} h/yr" in f["title"], f["title"]


def test_below_floor_flags_only_an_inconclusive_verdict():
    out = review_report(_report(sections={"certification": {
        "status": "ok", "payload": _floor_cert(verdict="fail")}}), RECORD)
    assert all(f["id"] != "certification_inconclusive" for f in out["findings"])


def test_a_boolean_min_draws_is_not_a_draw_count():
    f = _by_id(review_report(_report(sections={"certification": {
        "status": "ok", "payload": _floor_cert(min_draws=True)}}), RECORD),
        "certification_inconclusive")
    assert f["actions"] == []

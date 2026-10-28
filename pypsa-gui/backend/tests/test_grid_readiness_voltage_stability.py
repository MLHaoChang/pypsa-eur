"""The P–V and Q–V rows of the grid-readiness scorecard (study harness issue 18): the margin at the PoC from the
campus run's screen (``campus_study.voltage_stability_campus``), worst over periods, against the least margin
the profile or the user sets, else a planning-practice placeholder. Screening: below warns, never fails; a
lower bound below the limit is unknown. Hand-written run files with generic demo figures."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from services.adequacy import grid_readiness as G
from tests.test_grid_readiness import PROFILE, _rows
from tests.test_grid_readiness_motor_start import _site

IDS = ("voltage_stability_pv", "voltage_stability_qv")


def _period(period=2030, margin_pct=40.0, q=120.0, nose_bound=False, q_bound=False, verdict="solved",
            note="planned load 50 MW: the largest converged PCC import (hour 3) binds"):
    return {"period": period, "verdict": verdict, "planned_mw": 50.0, "hour": 3, "pf": 0.97, "e_pu": 1.0,
            "nose_mw": 50.0 * (1 + margin_pct / 100), "nose_is_lower_bound": nose_bound,
            "margin_mw": 0.5 * margin_pct, "margin_pct": margin_pct, "nose_below_planned": margin_pct < 0,
            "q_margin_mvar": q, "qmin_at_sweep_edge": q_bound, "v_poc_pu": 0.99, "sk_min_mva": 600.0,
            "rx_min": 0.1, "c": 1.0, "vn_kv": 110.0, "basis": "screening; binding answer is the RMS study",
            "note": note, "engine_note": ""}


def _write(d, periods=None, error=None):
    from gridspine.drivers.campus_study import VOLTAGE_STABILITY_CSV

    run = d / "campus_electrical" / "run"
    run.mkdir(parents=True, exist_ok=True)
    if error is not None:
        (run / "voltage_stability_error.json").write_text(json.dumps({"error": error}))
        return
    pd.DataFrame(periods if periods is not None else [_period()]).to_csv(run / VOLTAGE_STABILITY_CSV, index=False)
    (run / "campus_voltage_stability.json").write_text(json.dumps({"ledger": [], "notes": []}))


def _vs(d, profile=PROFILE, dirs=()):
    out = G.grid_readiness(d, _site(), profile, profile_dirs=dirs)
    rows = _rows(out)
    return rows.get(IDS[0]), rows.get(IDS[1]), out


def _profile_with(tmp_path, **limits):
    from gridspine.templates.grid_codes import load_grid_code

    p = load_grid_code(PROFILE, raw=True)
    for k, v in limits.items():
        p[k] = {"value": v, "clause": "demo clause 1.2", "source": "code"}
    codes = tmp_path / "codes"
    codes.mkdir(exist_ok=True)
    (codes / "with_margins.yaml").write_text(json.dumps(p))
    return "with_margins", (codes,)


# ── pass, warn and the wording ──────────────────────────────────────────────

def test_both_rows_show_a_margin_a_limit_and_its_source(tmp_path):
    _write(tmp_path)
    pv, qv, _ = _vs(tmp_path)
    assert (pv["verdict"], pv["project_value"], pv["limit"]) == ("pass", 40.0, 5.0)
    assert (qv["verdict"], qv["project_value"], qv["limit"]) == ("pass", 120.0, 0.0)
    for r in (pv, qv):
        assert r["section"] == "connection" and r["assumed"] is True
        assert "A screening estimate: an RMS study gives the binding answer." in r["note"]
        assert "within the screening margin" in r["note"] and "Worst period 2030" in r["note"]
        assert "planned load 50 MW" in r["note"] and "binds" in r["note"]


def test_the_worst_period_is_shown(tmp_path):
    _write(tmp_path, [_period(2030, 40.0, 120.0), _period(2035, 12.0, 90.0), _period(2040, 30.0, 60.0)])
    pv, qv, _ = _vs(tmp_path)
    assert (pv["project_value"], pv["period"]) == (12.0, 2035)
    assert (qv["project_value"], qv["period"]) == (60.0, 2040)


def test_below_the_limit_warns_never_fails_and_never_claims_compliance(tmp_path):
    _write(tmp_path, [_period(margin_pct=3.0, q=-4.0)])
    pv, qv, _ = _vs(tmp_path)
    assert pv["verdict"] == qv["verdict"] == "warn"
    assert pv["rule"] == G.RULE_PV and qv["rule"] == G.RULE_QV
    assert G.RULE_PV["otherwise"] == G.RULE_QV["otherwise"] == "warn"
    for r in (pv, qv):
        assert "below the screening margin" in r["note"]
        text = " ".join(str(r[k]) for k in ("requirement", "source", "note")).lower()
        assert "compliant" not in text and "meets" not in text


# ── the limits ──────────────────────────────────────────────────────────────

def test_with_no_user_limit_the_placeholders_show_with_their_wording(tmp_path):
    _write(tmp_path)
    pv, qv, _ = _vs(tmp_path)
    assert (pv["limit"], qv["limit"]) == (G.GENERIC_PV_MARGIN_PCT, G.GENERIC_QV_MARGIN_MVAR) == (5.0, 0.0)
    for r in (pv, qv):
        assert r["source"].startswith("assumed (generic placeholder)")
        assert "planning practice placeholder, not a requirement of this project's grid code" in r["source"]
    assert "zero injection at the operating voltage" in qv["source"]


def test_a_limit_in_the_users_profile_replaces_the_placeholder_and_is_cited(tmp_path):
    name, dirs = _profile_with(tmp_path, pv_margin_min_pct=50.0, qv_margin_min_mvar=100.0)
    _write(tmp_path)
    pv, qv, _ = _vs(tmp_path, name, dirs)
    assert (pv["limit"], pv["verdict"]) == (50.0, "warn")             # 40 % below 50 %
    assert (qv["limit"], qv["verdict"]) == (100.0, "pass")
    for r in (pv, qv):
        assert "demo clause 1.2" in r["source"] and "placeholder" not in r["source"]


# ── lower bounds (one handling for both rows) ──────────────────────────────

@pytest.mark.parametrize("rid, over, value", [
    ("voltage_stability_pv", {"nose_bound": True}, "margin_pct"),
    ("voltage_stability_qv", {"q_bound": True}, "q"),
])
@pytest.mark.parametrize("v, verdict", [(4.9, "unknown"), (5.0, "pass"), (50.0, "pass")],
                         ids=["below", "at", "above"])
def test_a_lower_bound_passes_at_or_above_the_limit_and_is_unknown_below(tmp_path, rid, over, value, v, verdict):
    name, dirs = _profile_with(tmp_path, pv_margin_min_pct=5.0, qv_margin_min_mvar=5.0)
    _write(tmp_path, [_period(**{"margin_pct": 40.0, "q": 120.0, value: v, **over})])
    rows = _rows(G.grid_readiness(tmp_path, _site(), name, profile_dirs=dirs))
    r = rows[rid]
    assert r["verdict"] == verdict and r["project_value_is_lower_bound"] is True
    if verdict == "pass":
        assert f"≥ {v:.4g}" in r["note"]
    else:
        reach = "the nose" if rid.endswith("pv") else "the Q–V minimum"
        assert f"the sweep did not reach {reach}" in r["note"]


def test_a_found_value_below_the_limit_beside_a_bound_still_warns(tmp_path):
    _write(tmp_path, [_period(2030, 2.0, 120.0), _period(2035, 1.0, 120.0, nose_bound=True)])
    pv, _, _ = _vs(tmp_path)
    assert pv["verdict"] == "warn" and pv["period"] == 2030


def test_judge_bounded_is_one_rule_for_both_rows():
    for rule in (G.RULE_PV, G.RULE_QV):
        assert G.judge_bounded([(1, 4.0, True)], 5.0, rule) == ("unknown", (1, 4.0, True))
        assert G.judge_bounded([(1, 4.0, False)], 5.0, rule) == ("warn", (1, 4.0, False))
        assert G.judge_bounded([(1, 6.0, True), (2, 8.0, False)], 5.0, rule) == ("pass", (1, 6.0, True))


# ── unknown, each with its reason ──────────────────────────────────────────

def test_no_run_is_unknown(tmp_path):
    pv, qv, _ = _vs(tmp_path)
    for r in (pv, qv):
        assert r["verdict"] == "unknown" and r["project_value"] is None
        assert r["note"].startswith("No grid study with the voltage stability check yet: run the grid study")


def test_no_sk_min_entered_is_unknown_with_the_reason(tmp_path):
    from gridspine.drivers.campus_study import NO_SK_MIN

    _write(tmp_path, error=NO_SK_MIN)
    for r in _vs(tmp_path)[:2]:
        assert r["verdict"] == "unknown" and r["note"].startswith("No minimum short-circuit power entered at the PCC")


def test_a_stage_error_is_unknown_with_the_error(tmp_path):
    _write(tmp_path, error="demo failure")
    for r in _vs(tmp_path)[:2]:
        assert r["verdict"] == "unknown"
        assert r["note"].startswith("The P–V/Q–V screen could not run: demo failure")


def test_a_not_solved_intact_case_is_unknown(tmp_path):
    _write(tmp_path, [_period(2030), _period(2035, verdict="not_solved",
                                             note="does not converge with no load added")])
    for r in _vs(tmp_path)[:2]:
        assert r["verdict"] == "unknown"
        assert "Period 2035: the intact case does not solve" in r["note"]


def test_a_blank_bound_flag_is_no_answer_never_a_bound(tmp_path):
    p = _period(2030, margin_pct=1.0)
    p["nose_is_lower_bound"] = None
    _write(tmp_path, [p])
    pv, qv, _ = _vs(tmp_path)
    assert pv["verdict"] == "unknown" and "Period 2030: the check gave no P–V margin" in pv["note"]
    assert qv["verdict"] == "pass"


@pytest.mark.parametrize("fields, cause", [
    ({"margin_pct": -5.0}, "Period 2030: no Q–V margin, because the grid connection can't carry the planned load (its maximum power is below it)"),
    ({"note": "planned-load point did not converge: no Q–V"},
     "Period 2030: no Q–V margin, because the load flow at the planned load did not solve"),
    ({"note": ""}, "Period 2030: no Q–V margin, because no Q–V point could be solved"),
], ids=["nose_below_planned", "planned_point_not_converged", "no_qv_point_solves"])
def test_a_blank_qv_margin_names_its_own_cause(tmp_path, fields, cause):
    p = {**_period(2030, margin_pct=fields.pop("margin_pct", 40.0))}
    # the driver's note: the operating-point notes first, the engine note last, and the engine note alone beside it
    engine = fields.get("note", "nose below the planned load" if p["nose_below_planned"] else "")
    p["engine_note"] = engine
    p["note"] = "; ".join(x for x in (p["note"], "probe power factor 0.97 lagging, from hour 3", "E = 1 pu", engine) if x)
    p["q_margin_mvar"] = float("nan")
    _write(tmp_path, [p])
    _, qv, _ = _vs(tmp_path)
    assert qv["verdict"] == "unknown" and cause in qv["note"]


def test_a_period_with_no_operating_point_is_unknown(tmp_path):
    from gridspine.drivers.campus_study import VS_NO_OPERATING_POINT

    p = {**_period(2030), "verdict": VS_NO_OPERATING_POINT,
         "note": "no converged hour and no rated import (the connection capacity)"}
    _write(tmp_path, [p])
    for r in _vs(tmp_path)[:2]:
        assert r["verdict"] == "unknown" and "Period 2030: no operating point" in r["note"]


# ── off-grid: excluded with the reason ──────────────────────────────────────

def _template(d, archetype):
    (d / "eh_template.json").write_text(json.dumps({"id": "demo", "recommended_archetype": archetype}))


def _stored(d, archetype):
    (d / "solver_config.json").write_text(json.dumps({"site_archetype": archetype}))


@pytest.mark.parametrize("setup", [
    lambda d: _template(d, "off_grid"),
    lambda d: (_template(d, "strong_grid"), _stored(d, "off_grid")),
], ids=["template_off_grid", "stored_off_grid_on_a_grid_template"])
def test_off_grid_excludes_both_rows_with_the_reason(tmp_path, setup):
    setup(tmp_path)
    _write(tmp_path)
    pv, qv, out = _vs(tmp_path)
    assert pv is None and qv is None
    assert [e["id"] for e in out["excluded"]] == list(IDS)
    assert all(e["reason"] == G.VS_OFF_GRID for e in out["excluded"])
    assert sum(out["summary"][v] for v in G.VERDICTS) == len(out["rows"])


def test_a_stored_grid_site_on_an_off_grid_template_keeps_both_rows(tmp_path):
    _template(tmp_path, "off_grid")
    _stored(tmp_path, "strong_grid")
    _write(tmp_path)
    pv, qv, out = _vs(tmp_path)
    assert pv["verdict"] == qv["verdict"] == "pass" and out["excluded"] == []


def test_a_grid_project_excludes_nothing(tmp_path):
    _write(tmp_path)
    assert _vs(tmp_path)[2]["excluded"] == []


# ── the gate fact (study harness 10's dc.readiness.<id>) ───────────────────

def _gate(monkeypatch, card, row_id):
    """``card``'s row through the gate as issue 10's ``dc.readiness.<id>`` reader will read it."""
    from services import run_gates

    monkeypatch.setitem(run_gates.FACTS, "demo.readiness", run_gates.Fact(
        "services.adequacy.grid_readiness.readiness_fact", "verdict", None,
        lambda env: G.readiness_fact(card, row_id)))
    rule = run_gates.check_rule({"id": f"dc.readiness.{row_id}", "fact": "demo.readiness", "op": "eq",
                                 "value": "pass", "severity": "warn", "basis": "issue 18"})
    return run_gates.evaluate([rule], run_gates.Env(task={}, ctx=None, project=None, project_dir=None))[0]


def test_an_excluded_row_reads_no_evidence_with_the_reason_never_pass(tmp_path, monkeypatch):
    _template(tmp_path, "off_grid")
    _write(tmp_path)
    card = _vs(tmp_path)[2]
    for rid in IDS:
        fact = G.readiness_fact(card, rid)
        assert fact.value == "excluded" and fact.reason == f"excluded: {G.VS_OFF_GRID}"
        result = _gate(monkeypatch, card, rid)
        assert result["verdict"] == "not_evaluated" and result["reason"] == f"excluded: {G.VS_OFF_GRID}"


def test_a_present_row_is_its_verdict(tmp_path, monkeypatch):
    _write(tmp_path)
    card = _vs(tmp_path)[2]
    assert G.readiness_fact(card, IDS[0]) == "pass"
    assert _gate(monkeypatch, card, IDS[0])["verdict"] == "pass"
    assert G.readiness_fact(card, "no_such_row").value == "missing"


def test_a_present_unknown_row_still_reads_its_verdict(tmp_path, monkeypatch):
    """An unknown row is a verdict, not an exclusion: the existing unknown semantics stay as they were."""
    card = _vs(tmp_path)[2]                      # no run: both rows unknown
    assert G.readiness_fact(card, IDS[0]) == "unknown"
    assert _gate(monkeypatch, card, IDS[0])["verdict"] == "fail"           # eq pass on "unknown"


# ── the harness's checked-results reader ───────────────────────────────────

def test_the_checker_leaves_the_rows_unchecked_rather_than_misjudging_a_bound(tmp_path):
    from harness import grid_readiness_calculations as R

    _write(tmp_path, [_period(margin_pct=1.0, nose_bound=True)])
    pv, _, _ = _vs(tmp_path)
    assert pv["verdict"] == "unknown" and R.disposition(pv)[0] == "unknown"

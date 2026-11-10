# ruff: noqa: F811 - pytest fixtures (hub, user_and_db) are imported and re-bound as arguments
"""Study harness 10b: eh_datacenter_staged@1, the planning-contingency fact
and L2's DC13-SCOPE restriction (GK B (36) Q3-Q4, D1-D8; GK A (20) CT-1..CT-6,
ST-1..ST-3, L2-1..L2-2).

- The staged profile is the single-year one per period: the energization
  family has one row per investment period of the network at gate time
  (ST-1), LOLE goes on the network's horizon (ST-2), and the investment
  case's counterfactual and returns only warn (ST-3).
- `dc.campus.contingency` reads the run's own campus study after its taps
  and compensation (`status_with_measures` of the invested table), bound to
  the frozen profile, the published run the step settled on and the network
  revision the job read; an assumed voltage band never passes it (D3).
- L2 has no lever on a contingency failure; taps, shunts and compensation
  are never tunable; redispatch is never a campus setting.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from services import campus_electrical_service as ce
from services import ic_counterfactual as ic
from services import run_actions, run_gates, run_profiles
from services.adequacy import grid_readiness as G
from tests.test_campus_electrical_service import hub, user_and_db  # noqa: F401 - fixtures
from tests.test_campus_scan_and_grid_code_since_run import _publish

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

REFS = ("eh_datacenter@1", "eh_datacenter_staged@1")
STAGED = "eh_datacenter_staged@1"


def _rules(ref, stage="result_gate"):
    return {r["id"]: r for r in run_profiles.resolve(ref)["stages"][stage]["rules"]}


# ── the staged profile ───────────────────────────────────────────────────────


def test_the_staged_profile_is_the_single_years_with_the_investment_case():
    single, staged = (run_profiles.resolve(r) for r in REFS)
    assert staged["templates"] == ["eh_datacenter_staged"] and single["templates"] == ["eh_datacenter"]
    assert _rules(STAGED, "setup")["set.project.template"]["value"] == "eh_datacenter_staged"
    assert [a["tool"] for a in staged["stages"]["solve"]["actions"]] == \
        [a["tool"] for a in single["stages"]["solve"]["actions"]]
    extra = set(_rules(STAGED)) - set(_rules(REFS[0]))
    assert extra == {"dc.ic.counterfactual", "dc.ic.returns"}
    for rule in extra:  # ST-3: reported, never a block, never a hurdle
        assert _rules(STAGED)[rule]["severity"] == "warn"
    assert _rules(STAGED)["dc.ic.returns"]["op"] == "nonempty"
    assert "investment_case" in staged["chapters"] and "investment_case" not in single["chapters"]
    assert staged["tunable"] == [] and "envelope" not in staged
    assert not any("*" in r["fact"] for r in staged["stages"]["result_gate"]["rules"])


@pytest.mark.parametrize("ref", REFS)
def test_both_profiles_block_on_the_contingency(ref):
    rule = _rules(ref)["dc.campus.contingency"]
    assert (rule["fact"], rule["op"], rule["severity"]) == ("dc.campus.contingency", "empty", "block")
    assert "redispatch" in rule["basis"] and "Input:" in rule["basis"]


def _staged_card(tmp_path, network=None):
    d = tmp_path / "staged"
    d.mkdir(exist_ok=True)
    n = network if network is not None else T.BUILDERS["eh_datacenter_staged"]()
    T.write_sidecars(d, "eh_datacenter_staged")
    return G.grid_readiness(d, n, "generic_assumed"), n


@pytest.mark.parametrize("profile, dc_load", [("generic_assumed", None), ("eu_rfg_dcc_ce", {})])
def test_every_row_the_staged_template_emits_is_gated_or_listed(tmp_path, profile, dc_load):
    """D8 on the staged template, and on a staged project with sidecars (E5):
    one energization row per period, every row classified."""
    d = tmp_path / profile
    d.mkdir()
    spec = T.dc_load_spec("eh_datacenter_staged", dc_load) if dc_load is not None else None
    n = T.BUILDERS["eh_datacenter_staged"](load_spec=spec) if spec is not None else T.BUILDERS["eh_datacenter_staged"]()
    T.write_sidecars(d, "eh_datacenter_staged")
    if spec is not None:
        meta = json.loads((d / "eh_template.json").read_text())
        meta["dc_load"] = T.dc_load_meta("eh_datacenter_staged", spec)
        (d / "eh_template.json").write_text(json.dumps(meta))
    card = G.grid_readiness(d, n, profile)
    ids = [r["id"] for r in card["rows"]] + [r["id"] for r in card["excluded"]]
    rows = {row for row, *_ in run_profiles.DC_SCORECARD}

    def classified(i):
        family = next((f for f in run_gates.FAMILIES if i.startswith(f)), None)
        return i in rows or family in rows or i in run_profiles.DC_NOT_GATED
    assert ids and all(classified(i) for i in ids)
    periods = sorted(i for i in ids if i.startswith("energization_peak:"))
    assert periods == [f"energization_peak:{p}" for p in n.investment_periods]
    if dc_load is not None:  # the sidecar rows are classified too
        assert any(i.startswith("ride_through:") for i in ids) and any(i.startswith("dynamic:") for i in ids)


def test_until_the_intake_opens_the_staged_run_stops_for_the_grid_frequency(tmp_path, monkeypatch):
    """GK B (41): the chat intake for grid.frequency_hz is slice 10c; until then
    the staged run stops at its input gate, never reading the network's."""
    import services.solver.island as island

    monkeypatch.setattr(island, "campus_f_hz", lambda project_dir: 60.0)
    d = tmp_path / "p"
    (d / ce.SUBDIR).mkdir(parents=True)
    (d / ce.SUBDIR / ce.CAMPUS_FILE).write_text("campus:\n  f_hz: 60\n")
    env = run_gates.Env(task={"steps": [], "run": {"intake": {"rows": []}}}, ctx=None, project=object(),
                        project_dir=d)
    [result] = run_gates.evaluate([_rules(STAGED, "input_gate")["dc.gridcode.frequency"]], env)
    assert result["verdict"] == "not_evaluated" and result["reason"] == "state the grid frequency (Hz)"


CODE = {"id": "sagc_dc", "label": "SAGC (project)", "digest": "a" * 64}


def _read_step(card):
    return {"id": "solve.4", "tool": run_actions.READINESS, "native": run_actions.KIND,
            "status": "completed", "args": dict(run_actions.FROZEN), "frozen": {"grid_code": CODE},
            "result": {"profile": CODE["id"], "digest": CODE["digest"], "rows_digest": run_actions.rows_digest(card)}}


def _confirmed(card):
    """The card as if every energization input were confirmed (no D3 hold),
    so the period handling alone decides."""
    return {**card, "rows": [{**r, "assumed": False} if r["id"].startswith("energization_peak:") else r
                             for r in card["rows"]]}


def _energization(monkeypatch, card, network):
    monkeypatch.setattr(G, "grid_readiness_for", lambda project, profile=None: card)
    monkeypatch.setattr(run_actions, "unchanged", lambda project, code: True)
    rule = _rules(STAGED)["dc.energization_peak"]
    env = run_gates.Env(task={"steps": [_read_step(card)]}, ctx=SimpleNamespace(network=network),
                        project=object(), project_dir=None)
    return run_gates.evaluate([rule], env)[0]


def test_staged_energization_is_judged_on_every_period_of_the_network(tmp_path, monkeypatch):
    """ST-1: one row per period in the record; one failing period fails the
    family. On the shipped (assumed) inputs a pass is held by D3."""
    card, n = _staged_card(tmp_path)
    shipped = _energization(monkeypatch, card, n)
    rows = {r["id"]: r["verdict"] for r in shipped["facts"][0]["rows"]}
    assert set(rows) == {f"energization_peak:{p}" for p in (2027, 2030, 2035)}
    assert shipped["verdict"] == "not_evaluated" and "assumed" in shipped["reason"]
    confirmed = _confirmed(card)
    assert _energization(monkeypatch, confirmed, n)["verdict"] == "pass"
    one_fails = {**confirmed, "rows": [{**r, "verdict": "fail"} if r["id"] == "energization_peak:2030" else r
                                       for r in confirmed["rows"]]}
    assert _energization(monkeypatch, one_fails, n)["verdict"] == "fail"


def test_a_period_the_rows_do_not_cover_is_no_evidence(tmp_path, monkeypatch):
    """ST-1: the periods are the network's at gate time, never the template's
    planning: a network with 2028 added and no row for it is not evaluated."""
    card, n = _staged_card(tmp_path)
    card = _confirmed(card)
    assert _energization(monkeypatch, card, n)["verdict"] == "pass"
    edited = SimpleNamespace(investment_periods=pd.Index([2027, 2028, 2030, 2035]))
    result = _energization(monkeypatch, card, edited)
    assert result["verdict"] == "not_evaluated" and "2028" in result["reason"]
    fewer = SimpleNamespace(investment_periods=pd.Index([2027, 2030]))
    assert _energization(monkeypatch, card, fewer)["verdict"] == "not_evaluated"


def test_rows_from_the_edited_network_cover_its_new_period(tmp_path, monkeypatch):
    """ST-1: the scorecard reads the network, so a staged network re-planned on
    2027/2028/2035 has rows for exactly those periods, and they are judged."""
    n = T.BUILDERS["eh_datacenter_staged"](planning=T.Planning((2027, 2028, 2035), 2045))
    card, _ = _staged_card(tmp_path, n)
    card = _confirmed(card)
    assert sorted(r["id"] for r in card["rows"] if r["id"].startswith("energization_peak:")) == \
        [f"energization_peak:{p}" for p in (2027, 2028, 2035)]
    assert _energization(monkeypatch, card, n)["verdict"] == "pass"


def test_staged_lole_goal_is_on_the_networks_horizon_with_its_period_weights(monkeypatch):
    """ST-2 (Q5, SH-05): the h/yr goal goes through `loop_goal(horizon_years(n))`;
    the staged network's period weights make the horizon more than its snapshots."""
    from harness import run_loops
    from services import reliability_goal
    from services.adequacy.metrics import horizon_years

    n = T.BUILDERS["eh_datacenter_staged"]()
    n.snapshot_weightings.loc[:, "generators"] = 2.0  # non-unit weights
    goal = {"target_lole_h": {"value": 3.0, "source": "user"}, "mc_draws": {"value": 100, "source": "user"}}
    monkeypatch.setattr(reliability_goal, "active_goal", lambda state: goal)
    years = float(horizon_years(n))
    assert years != pytest.approx(len(n.snapshots) / 8760)
    target = run_loops._card_target(SimpleNamespace(solver_state={}, network=n))
    assert target == pytest.approx(3.0 * years)
    lole = next(r for r in run_profiles.resolve(STAGED)["stages"]["result_gate"]["rules"] if r.get("engine"))
    assert lole["engine"]["tool"] == "run_coupling_loop" and run_profiles.needs_goal(run_profiles.resolve(STAGED))


# ── ic.returns (ST-3) ────────────────────────────────────────────────────────

DONE = {"status": "done", "flags": [], "report": {"present": True, "stale": False}}


def _report(npv=1.5e6, irr=0.081):
    return {"sections": {"project": {"payload": {
        "project_post_tax_npv": npv, "project_post_tax_irr": irr, "currency": "EUR"}}}}


def _returns(monkeypatch, body, report, current=True):
    from routers import results
    from services import workflow_tools
    monkeypatch.setattr(results, "get_investment_case", lambda: body)
    monkeypatch.setattr(results, "get_investment_case_report", lambda detail="export": report)
    monkeypatch.setattr(workflow_tools, "solve_freshness", lambda ctx: {"results_current": current})
    return ic.read_returns(None, lambda fn: fn())


def test_returns_are_read_from_a_current_established_case(monkeypatch):
    fact = _returns(monkeypatch, DONE, _report())
    assert fact["state"] == ic.ESTABLISHED
    assert fact["returns"] == {"npv_at_wacc": 1.5e6, "irr": 0.081, "currency": "EUR"}


@pytest.mark.parametrize("body, current, state", [
    (None, True, ic.NOT_RUN),
    ({**DONE, "report": {"present": True, "stale": True, "changed": ["finance"]}}, True, ic.NOT_RUN),
    (DONE, False, ic.NOT_RUN),  # results no longer describe the network
    ({**DONE, "flags": ["counterfactual_not_established:no_tariff"]}, True, ic.NOT_ESTABLISHED),
])
def test_a_stale_or_unestablished_case_has_no_returns(monkeypatch, body, current, state):
    fact = _returns(monkeypatch, body, _report(), current)
    assert fact["state"] == state and fact["returns"] is None


def test_an_irr_the_case_could_not_establish_is_never_a_zero(monkeypatch):
    fact = _returns(monkeypatch, DONE, _report(irr=None))
    assert fact["state"] == ic.NOT_ESTABLISHED and fact["reasons"] == ["irr_not_established"]
    assert fact["returns"] is None


def test_the_returns_rule_warns_and_never_blocks(monkeypatch):
    env = run_gates.Env(task={"steps": []}, ctx=None, project=object(), project_dir=None)
    rule = _rules(STAGED)["dc.ic.returns"]
    monkeypatch.setattr(ic, "read_returns", lambda ctx, on: {"state": ic.NOT_RUN, "reasons": ["no_case"],
                                                             "returns": None})
    result = run_gates.evaluate([rule], env)[0]
    assert result["verdict"] == "not_evaluated" and run_gates.passed([result])
    monkeypatch.setattr(ic, "read_returns", lambda ctx, on: {"state": ic.ESTABLISHED, "reasons": [],
                                                             "returns": {"npv_at_wacc": -2.0e6, "irr": -0.01,
                                                                         "currency": "EUR"}})
    result = run_gates.evaluate([rule], env)[0]
    assert result["verdict"] == "pass"  # a negative NPV is reported, never judged against a hurdle


# ── dc.campus.contingency (CT-1..CT-6) ───────────────────────────────────────

ROWS = {
    "transformer_loading": ("pass", "pass", "assumed", "N-1:T1", "worst T2 (N-1:T1)"),
    "pcc_voltage": ("pass", "pass", "code", "intact", "worst at GRID (intact); band 0.9-1.1 pu"),
    "campus_voltage": ("pass", "pass", "code", "intact", "worst at IT_BUS (intact); band 0.95-1.05 pu"),
    "pcc_reactive": ("fail", "pass", "assumed", None, ""),
}


EQUIPMENT = "equipment rating (not a grid-code clause)"
CASES = [("intact", True), ("N-1:T1", True), ("N-1:T2", True)]


def _table(rows):
    return pd.DataFrame([{"check": c, "status_as_is": a, "status_with_measures": w, "value": 0.5, "limit": 1.0,
                          "unit": "pu", "worst_period": 2030, "worst_hour": 3,
                          "clause": EQUIPMENT if c == "transformer_loading" else "code §1", "source": s,
                          "detail": d, "worst_case": case, "limit_min": None, "limit_max": None}
                         for c, (a, w, s, case, d) in rows.items()])


def _cases(run, cases=CASES):
    pd.DataFrame([{"period": 2030, "hour": 3, "case": c, "converged": ok, "p_mw": 1.0, "q_mvar": 0.0,
                   "losses_mw": 0.0} for c, ok in cases]).to_csv(run / ce.cs.LF_PCC_CSV, index=False)


def _settle(run, step):
    step["result"]["run_files"] = run_actions.run_files(run)


def _campus(hub, rows=ROWS, as_is=None, revision=7, cases=CASES):
    """A published campus run held to the published profile, its step settled on it."""
    _publish(hub, "sagc_dc")
    code = {"id": "sagc_dc", "label": "SAGC", "digest": ce.profile_fingerprint(hub, "sagc_dc")}
    run = run_actions._project_dir(hub) / ce.SUBDIR / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / ce.PROFILE_USED_FILE).write_text(json.dumps({"profile": code["id"], "sha256": code["digest"]}))
    if rows is not None:
        _table(rows).to_csv(run / ce.cs.COMPLIANCE_INVESTED_CSV, index=False)
    _table(as_is or ROWS).to_csv(run / ce.cs.COMPLIANCE_CSV, index=False)
    _cases(run, cases)
    step = {"id": "solve.3", "tool": run_actions.CAMPUS, "native": run_actions.KIND, "status": "completed",
            "args": dict(run_actions.FROZEN), "frozen": {"grid_code": code},
            "result": {"job_id": "j", "solves": 1, "grid_code": code,
                       "run_files": run_actions.run_files(run), "network_revision": revision}}
    return run, step


def _contingency(hub, step, revision=7):
    rule = _rules(REFS[0])["dc.campus.contingency"]
    env = run_gates.Env(task={"steps": [step]}, ctx=SimpleNamespace(network_revision=revision),
                        project=hub, project_dir=None)
    return run_gates.evaluate([rule], env)[0]


def test_every_check_passing_on_stated_bands_passes(hub):
    _, step = _campus(hub)
    result = _contingency(hub, step)
    assert result["verdict"] == "pass" and result["facts"][0]["value"] == []
    assert result["facts"][0]["measures"] == [] and "note" not in result  # a plain pass: no measure


def test_a_pass_resting_on_the_studys_measures_names_them(hub):
    """CT-5d (#1172): a pass that needs a resize or a fix the study bought says so."""
    resize = "GRID_IMPORT: 1 x 63 MVA recommended (now 50 MVA)"
    rows = {**ROWS, "transformer_loading": ("pass", "pass", "assumed", "N-1:T1", resize),
            "pcc_voltage": ("fail", "pass", "code", "N-1:T2", "worst at GRID (N-1:T2)")}
    _, step = _campus(hub, rows)
    result = _contingency(hub, step)
    assert result["verdict"] == "pass"
    assert result["note"] == run_gates.MEASURED_PASS + resize + "; worst at GRID (N-1:T2)"
    assert [m["check"] for m in result["facts"][0]["measures"]] == ["transformer_loading", "pcc_voltage"]


def test_a_failing_n_minus_1_check_blocks_with_its_case_period_value_and_limit(hub):
    """CT-6, and CT-1: the invested table's `status_with_measures`, never `status_as_is`."""
    rows = {**ROWS, "transformer_loading": ("pass", "fail", "assumed", "N-1:T1", "T2 at 112 %")}
    _, step = _campus(hub, rows)
    result = _contingency(hub, step)
    assert result["verdict"] == "fail"
    assert result["facts"][0]["value"] == [{"check": "transformer_loading", "worst_case": "N-1:T1",
                                            "worst_period": 2030, "value": 0.5, "limit": 1.0, "unit": "pu"}]


def test_a_pass_as_is_never_hides_a_fail_with_measures_and_the_reverse(hub):
    """CT-1: the as-is status is never read."""
    _, step = _campus(hub, {**ROWS, "pcc_voltage": ("fail", "pass", "code", "N-1:T1", "")})
    assert _contingency(hub, step)["verdict"] == "pass"


@pytest.mark.parametrize("status", ["not_rechecked", "unknown", "not_rated", None, "odd"])
def test_a_check_not_read_after_the_measures_is_no_evidence(hub, status):
    _, step = _campus(hub, {**ROWS, "campus_voltage": ("pass", status, "code", "intact", "")})
    result = _contingency(hub, step)
    assert result["verdict"] == "not_evaluated"


def test_a_missing_invested_table_is_no_evidence_and_the_as_is_table_is_never_read(hub):
    """CT-2: `invest` is frozen on, so the job writes the invested table; without it there is no evidence."""
    run, step = _campus(hub)
    (run / ce.cs.COMPLIANCE_INVESTED_CSV).unlink()
    assert _contingency(hub, step)["verdict"] == "not_evaluated"
    assert run_actions.run_files(run) is None  # a settle with no invested table records no run


@pytest.mark.parametrize("check", run_actions.CONTINGENCY_CHECKS)
def test_a_missing_row_is_no_evidence(hub, check):
    """CT-4."""
    _, step = _campus(hub, {k: v for k, v in ROWS.items() if k != check})
    result = _contingency(hub, step)
    assert result["verdict"] == "not_evaluated" and check in result["reason"]


def test_no_transformer_row_is_not_applicable_only_when_the_voltage_row_says_so(hub):
    """CT-4: the engine's NO_TRANSFORMER note on the voltage row."""
    from gridspine.static.campus_compliance import NO_TRANSFORMER

    rows = {k: v for k, v in ROWS.items() if k != "transformer_loading"}
    rows["campus_voltage"] = ("pass", "pass", "code", "intact", NO_TRANSFORMER.format(kv="20"))
    _, step = _campus(hub, rows)
    assert _contingency(hub, step)["verdict"] == "pass"
    rows["campus_voltage"] = ("pass", "pass", "code", "intact", "worst at IT_BUS (intact)")
    _, step = _campus(hub, rows)
    assert _contingency(hub, step)["verdict"] == "not_evaluated"


@pytest.mark.parametrize("check", ["pcc_voltage", "campus_voltage"])
@pytest.mark.parametrize("source", ["assumed", "extracted"])
def test_a_voltage_pass_on_an_assumed_or_unconfirmed_band_never_passes(hub, check, source):
    """CT-5 (D3, #1079)."""
    status, _, _, case, detail = ROWS[check]
    _, step = _campus(hub, {**ROWS, check: (status, "pass", source, case, detail)})
    result = _contingency(hub, step)
    assert result["verdict"] == "not_evaluated" and check in result["reason"]


def test_the_transformer_ratings_own_limit_counts_only_with_its_clause(hub):
    """CT-5, CT-5a: 100 % of each unit's own rating is an equipment limit the
    engine tags `assumed` with its clause; another clause fails closed."""
    from gridspine.static import campus_compliance

    assert run_actions.EQUIPMENT_LIMIT == {"transformer_loading"}
    assert '"equipment rating (not a grid-code clause)"' in Path(campus_compliance.__file__).read_text()
    run, step = _campus(hub)
    assert ROWS["transformer_loading"][2] == "assumed" and _contingency(hub, step)["verdict"] == "pass"
    table = pd.read_csv(run / ce.cs.COMPLIANCE_INVESTED_CSV)
    table.loc[table["check"] == "transformer_loading", "clause"] = "Grid code §7 loading"
    table.to_csv(run / ce.cs.COMPLIANCE_INVESTED_CSV, index=False)
    _settle(run, step)
    result = _contingency(hub, step)
    assert result["verdict"] == "not_evaluated" and "own rating" in result["reason"]


@pytest.mark.parametrize("check, words", [("campus_voltage", "state the campus voltage band"),
                                          ("pcc_voltage", "state the PCC voltage band")])
def test_an_assumed_band_asks_the_user_to_state_it(hub, check, words):
    """CT-5b."""
    status, _, _, case, detail = ROWS[check]
    _, step = _campus(hub, {**ROWS, check: (status, "pass", "assumed", case, detail)})
    assert words in _contingency(hub, step)["reason"]


@pytest.mark.parametrize("cases, why", [
    ([("intact", True)], "no transformer N-1 case"),
    ([("intact", True), ("N-1:T1", False)], "did not converge"),
    ([("N-1:T1", True)], "no intact case"),
    ([], "cannot be read"),
])
def test_the_cases_must_cover_intact_and_transformer_n_minus_1(hub, cases, why):
    """GK B E3: a pass on a study that solved no N-1 case, or a case that did
    not converge, is no evidence (#1079)."""
    _, step = _campus(hub, cases=cases)
    result = _contingency(hub, step)
    assert result["verdict"] == "not_evaluated" and why in result["reason"]


def test_a_campus_with_no_transformer_needs_only_its_intact_case(hub):
    from gridspine.static.campus_compliance import NO_TRANSFORMER

    rows = {k: v for k, v in ROWS.items() if k != "transformer_loading"}
    rows["campus_voltage"] = ("pass", "pass", "code", "intact", NO_TRANSFORMER.format(kv="20"))
    _, step = _campus(hub, rows, cases=[("intact", True)])
    assert _contingency(hub, step)["verdict"] == "pass"


@pytest.mark.parametrize("used", [{"profile": "other"}, {"sha256": "b" * 64}, None])
def test_a_run_held_to_another_profile_or_content_is_no_evidence(hub, used):
    """CT-3 (a): the published run's `campus_profile_used.json` against the
    frozen profile id and fingerprint, like with like (a republish of the live
    profile is `dc.gridcode.unchanged`'s, D1)."""
    run, step = _campus(hub)
    code = step["frozen"]["grid_code"]
    if used is None:
        (run / ce.PROFILE_USED_FILE).unlink()
    else:
        (run / ce.PROFILE_USED_FILE).write_text(json.dumps({"profile": code["id"], "sha256": code["digest"], **used}))
    assert _contingency(hub, step)["verdict"] == "not_evaluated"


@pytest.mark.parametrize("table", ["invested", "as_is", "cases"])
def test_a_later_campus_run_is_never_read_as_the_runs_own(hub, table):
    """CT-3 (b): a user's campus run republishes `run/` with other tables."""
    run, step = _campus(hub)
    if table == "cases":
        _cases(run, CASES[:2])
    else:
        name = ce.cs.COMPLIANCE_INVESTED_CSV if table == "invested" else ce.cs.COMPLIANCE_CSV
        _table({**ROWS, "pcc_reactive": ("pass", "pass", "assumed", None, "")}).to_csv(run / name, index=False)
    result = _contingency(hub, step)
    assert result["verdict"] == "not_evaluated" and "not the run's own" in result["reason"]


def test_an_edit_after_the_campus_job_reads_stale(hub):
    """CT-3 (c): the revision the step recorded before the job read the network."""
    _, step = _campus(hub, revision=7)
    assert _contingency(hub, step, revision=8)["verdict"] == "not_evaluated"
    step["result"]["network_revision"] = None
    assert _contingency(hub, step, revision=7)["verdict"] == "not_evaluated"


def test_a_campus_step_not_completed_is_no_evidence(hub):
    _, step = _campus(hub)
    step["status"] = "failed"
    assert _contingency(hub, step)["verdict"] == "not_evaluated"


def test_the_campus_step_records_the_revision_and_settles_on_its_run(hub, monkeypatch):
    """CT-3 (b, c) at dispatch and settle: the job record keeps the revision the
    job will read; the settled result keeps it and the published tables' digests."""
    from services import solve_queue as sq

    run, step = _campus(hub)
    ce.draft(hub)
    task = {"task_id": "t", "status": "running", "run": {"status": "running", "stages": {"solve": {}}},
            "steps": [{**step, "status": "running", "attempt_id": "a1", "result": None}]}
    path = run_actions._project_dir(hub) / "assistant_tasks" / "t.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(task))
    monkeypatch.setattr(run_actions, "_reserve", lambda path, attempt: True)
    monkeypatch.setattr(ce, "enqueue_run", lambda project, settings, user: {"id": "00000000-0000-0000-0000-000000000001"})
    run_actions._campus(path, task["steps"][0], hub, None, step["frozen"] | {"settings": {}}, "a1",
                        SimpleNamespace(network_revision=11))
    waiting = json.loads(path.read_text())["steps"][0]
    assert waiting["status"] == "waiting" and waiting["job"]["network_revision"] == 11
    monkeypatch.setattr(sq.solve_queue, "get_job", lambda job_id: {"status": "completed"})
    monkeypatch.setattr(run_actions, "release", lambda *a, **k: None)
    monkeypatch.setattr(run_actions, "_interrupted", lambda path, task: False)
    assert run_actions.settle(path) == "done"
    settled = json.loads(path.read_text())["steps"][0]
    assert settled["result"]["network_revision"] == 11
    assert settled["result"]["run_files"] == run_actions.run_files(run)


# ── L2 and DC13-SCOPE (L2-1, L2-2) ───────────────────────────────────────────


@pytest.mark.parametrize("ref", REFS)
def test_l2_has_no_lever_on_a_contingency_failure(ref):
    from harness import run_loops

    profile = run_profiles.resolve(ref)
    assert run_loops._l2_tunable(profile, ["dc.campus.contingency"], _rules(ref)) == frozenset()
    assert not any("redispatch" in t for t in profile["stages"]["result_gate"]["agent"]["tools"])


@pytest.mark.parametrize("component, attr", [
    ("Transformer", "tap_position"), ("Transformer", "tap_ratio"), ("Transformer", "tap_side"),
    ("Transformer", "tap_step"), ("ShuntImpedance", "b"), ("ShuntImpedance", "g"), ("Line", "b"),
    ("Bus", "compensation_mvar"), ("Bus", "q_comp_mvar"),
])
def test_taps_shunts_and_compensation_are_never_tunable(component, attr):
    profile = {**run_profiles.EH_DATACENTER, "tunable": [{"component": component, "attr": attr}]}
    with pytest.raises(run_profiles.ProfileError, match="never tunable"):
        run_profiles.load(profile)


def test_redispatch_is_never_a_campus_setting(hub):
    """L2-2: the frozen settings carry no redispatch, and a study that asks for one is refused."""
    _publish(hub, "sagc_dc")
    frozen = run_actions.freeze(hub)
    assert not [k for k in frozen["settings"] if "dispatch" in k]
    assert "redispatch" not in run_actions.CAMPUS_STUDY
    with pytest.raises(run_actions.FreezeError, match="unknown setting"):
        run_actions.freeze(hub, {**run_actions.CAMPUS_STUDY, "redispatch": True})
    engine = (REPO / "gridspine" / "drivers" / "campus_study.py").read_text()
    assert not re.search("redispatch", engine, re.IGNORECASE)


def test_a_network_edited_after_the_case_has_no_returns(monkeypatch):
    """GK B E1: `classify` takes the case as current only while the solve's
    results still describe the network (`workflow_tools.solve_freshness`) and
    the stored report is not stale against the run's inputs (dispatch digest,
    campus study: `routers.results.get_investment_case`); an edit after the
    case reads no evidence, never the old numbers."""
    fact = _returns(monkeypatch, DONE, _report(), current=False)
    assert fact["returns"] is None and "results_not_current" in fact["reasons"]

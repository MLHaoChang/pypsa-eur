# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 14 (design §9.3 "eh_ipp_hybrid"; GK B (52) GO, I1-I9):
eh_ipp_hybrid@1. The solve, then the single-owner investment case; the result
gate blocks on an export over the export link's rating and warns on the cover
and the pre-tax IRR; no EH study, no LOLE loop and no grid, reliability or
control stage."""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pypsa
import pytest

from harness import run_ic
from services import flow_scope, run_envelope, run_gates, run_profiles
from services import ic_counterfactual as ic

REF = "eh_ipp_hybrid@1"
TEMPLATE = "eh_ipp_hybrid"


def _profile():
    return run_profiles.resolve(REF)


def _rules(stage="result_gate"):
    return {r["id"]: r for r in _profile()["stages"][stage]["rules"]}


# ── the profile ───────────────────────────────────────────


def test_the_profile_serves_the_ipp_template_without_an_eh_study_or_a_lole_loop():
    profile = _profile()
    assert (
        profile["templates"] == [TEMPLATE]
        and profile["scope"] == "flow_scope.preset_for"
    )
    assert "eh_study" not in profile and not run_profiles.needs_goal(profile)
    assert not any(r.get("engine") for r in profile["stages"]["result_gate"]["rules"])


def test_the_result_gate_is_exactly_the_core_rules_the_export_limit_and_the_two_warnings():
    """I9: the rule set is asserted whole, so a rule added or dropped fails here."""
    rules = _rules()
    assert set(rules) == {
        "res.solve.status",
        "res.fresh",
        "ipp.export.limit",
        "ipp.ic.min_dscr",
        "ipp.ic.pre_tax_irr",
    }
    limit = rules["ipp.export.limit"]
    assert (limit["fact"], limit["op"], limit["value"], limit["severity"]) == (
        "ipp.export.over_limit_mw",
        "le",
        0.0,
        "block",
    )
    cover = rules["ipp.ic.min_dscr"]
    assert (cover["fact"], cover["op"], cover["value"], cover["severity"]) == (
        "ic.min_dscr",
        "ge",
        run_profiles.IPP_DSCR_MIN,
        "warn",
    )
    irr = rules["ipp.ic.pre_tax_irr"]
    assert (irr["fact"], irr["op"], irr["severity"]) == (
        "ic.project_pre_tax_irr",
        "nonempty",
        "warn",
    )
    assert not any(
        "unevaluated" in r for r in rules.values()
    )  # a warn rule never ends the stage


def test_the_setup_holds_the_run_to_its_template():
    rule = _rules("setup")["set.project.template"]
    assert (rule["fact"], rule["op"], rule["value"]) == (
        "project.template",
        "eq",
        TEMPLATE,
    )


def test_the_solve_runs_the_investment_case_after_the_queued_solve_and_explore_is_skipped_with_its_reason():
    stages = _profile()["stages"]
    assert [a["tool"] for a in stages["solve"]["actions"]] == [
        "solve_queue_enqueue",
        "run_investment_case",
    ]
    assert stages["solve"]["actions"][1]["args"] == {}  # a model never writes its args
    assert stages["explore"]["skip"] and "explore" in stages["explore"]["skip"].lower()


def test_the_report_names_the_chapters_the_preset_keeps():
    profile = _profile()
    assert profile["chapters"] == ["sizing", "investment_case"]
    assert any("PPA share" in m for m in profile["missing_tools"])


def test_the_scope_names_each_card_the_preset_turns_off_with_its_reason():
    """I9: the skips are read from the preset, not copied."""
    scope = run_profiles.scope_for({"id": TEMPLATE})
    _, preset = flow_scope.preset_for({"id": TEMPLATE})
    off = {card.id for card in flow_scope.CARDS if not preset.get(card.id)}
    assert off == {"grid", "control", "reliability"} and set(scope["skipped"]) == off
    assert all(
        reason and TEMPLATE not in reason for reason in scope["skipped"].values()
    )
    assert scope["cards"] == {card: list(options) for card, options in preset.items()}
    assert run_profiles.scope_for({"id": "eh_ammonia_staged"})["skipped"].keys() == {
        "grid",
        "control",
    }


def test_the_envelope_names_the_battery_only_within_twice_its_template_caps():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "project_templates"))
    import eh_ipp

    envelope = _profile()["envelope"]
    edits = {
        (e["target"]["name"], e["target"]["attr"]): e["bounds"]
        for e in envelope["edits"]
    }
    assert edits == {
        ("bess", "p_nom_max"): {"min": 0.0, "max": 2 * eh_ipp.BESS_MAX_MW},
        ("bess", "max_hours"): {"min": 1.0, "max": 2 * eh_ipp.BESS_HOURS},
    }
    assert {e["target"]["component"] for e in envelope["edits"]} == {"StorageUnit"}
    for name in (
        "wind_farm",
        "pv_plant",
        "grid_import",
        "grid_export",
        "grid",
    ):  # forced-size and the POI: never levers
        assert not any(e["target"]["name"] == name for e in envelope["edits"])
    assert [r["tool"] for r in envelope["runs"]] == [
        "solve_queue_enqueue",
        "run_investment_case",
        "generate_report",
    ]
    assert envelope["budget"]["solves"] == 3


def test_the_template_builds_what_the_envelope_names():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "project_templates"))
    import eh_templates as T

    n = T.BUILDERS[TEMPLATE]()
    assert (
        "bess" in n.storage_units.index
        and float(n.storage_units.at["bess", "p_nom_max"]) * 2 == 100.0
    )
    assert (
        float(n.storage_units.at["bess", "max_hours"]) * 2
        == run_profiles.IPP_BESS_MAX_HOURS
    )


def test_the_two_case_tools_are_bound_to_the_run_context():
    assert {
        "run_investment_case",
        "solve_ppa_price",
    } <= run_profiles.BOUND_CONTEXT_TOOLS
    assert (
        "run_investment_case" not in run_envelope.NEVER_LISTED
    )  # the grant may list it


def test_every_fact_the_profile_names_is_registered_with_its_source():
    for rule in _rules().values():
        assert rule["fact"] in run_gates.FACTS
    assert run_gates.FACTS["ipp.export.over_limit_mw"].unit == "MW"


# ── the envelope: the case is a run that spends no solve (I2) ─────────────


def test_a_grant_may_list_the_investment_case_and_it_counts_no_solve():
    entry = run_envelope._check_run({"tool": "run_investment_case"})
    assert entry == {"tool": "run_investment_case"}
    envelope = {
        "audit": [
            {"event": "used", "call_id": "a", "tool": "run_investment_case"},
            {"event": "used", "call_id": "b", "tool": "solve_queue_enqueue"},
        ]
    }
    assert run_envelope.solves_used(envelope) == 1  # the queued solve only
    assert "run_investment_case" not in run_envelope.RUN_TOOLS
    with pytest.raises(run_envelope.EnvelopeError):
        run_envelope._check_run({"tool": "run_investment_case", "restore": "final"})


def _case_refusal(
    monkeypatch,
    *,
    plan=None,
    call="c1",
    step="c1",
    raises=False,
    tool="run_investment_case",
):
    """`_refusal` for a case call: the run's own step is `step`, this call is `call`;
    `plan` is what the baseline planning says (`raises`: it cannot be read)."""
    from services.results import finance_case

    def baseline_plan(n, cfg, owner=None):
        if raises:
            raise ValueError("unreadable")
        return plan

    monkeypatch.setattr(finance_case, "baseline_plan", baseline_plan)
    monkeypatch.setattr(run_envelope, "_standing", lambda *a, **k: None)
    monkeypatch.setattr(run_envelope, "_names_another_project", lambda *a: False)
    envelope = {"runs": [{"tool": tool}], "edits": []}
    session = SimpleNamespace(run_step_call=step)
    ctx = SimpleNamespace(network=object(), solver_state={"solver_config": object()})
    note = {"call_id": call}
    return run_envelope._refusal(
        session, envelope, {}, tool, {}, "write", False, None, ctx, note
    )


def test_the_case_runs_no_solve_when_nothing_is_kept(monkeypatch):
    from services.results import baseline

    plan = baseline.BaselinePlan(removed=(), kept=(), flags=())
    assert _case_refusal(monkeypatch, plan=plan) is None
    assert run_ic.SOLVES == 0


def test_a_case_with_a_kept_asset_is_refused_since_its_baseline_solves_are_uncounted(
    monkeypatch,
):
    from services.results import baseline

    plan = baseline.BaselinePlan(
        removed=(), kept=(("StorageUnit", "old_bess"),), flags=()
    )
    assert _case_refusal(monkeypatch, plan=plan) == "baseline_solve"


def test_a_case_whose_baseline_plan_cannot_be_read_is_refused(monkeypatch):
    assert _case_refusal(monkeypatch, raises=True) == "baseline_solve"
    assert (
        _case_refusal(monkeypatch, plan=None) is None
    )  # no value flows: refused later, no solve


@pytest.mark.parametrize(
    "call,step", [("c1", None), ("c1", "c2"), (None, None), (None, "c1")]
)
def test_a_model_turn_never_starts_the_case_under_the_grant(monkeypatch, call, step):
    """#1123: the `runs` entry covers the harness's own step only."""
    from services.results import baseline

    plan = baseline.BaselinePlan(removed=(), kept=(), flags=())
    assert (
        _case_refusal(monkeypatch, plan=plan, call=call, step=step) == "own_step_only"
    )


# ── the export reader (I3) ───────────────────────────────────────────────


@pytest.fixture
def direct(monkeypatch):
    """The readers on a context of the test's own, the run's solve taken as its own."""
    import harness.calculations as calculations

    monkeypatch.setattr(
        run_gates, "_own_solve", lambda env, read, typed=False: read(env.ctx)
    )
    monkeypatch.setattr(run_gates, "_on", lambda ctx, fn, *args: fn(*args))
    monkeypatch.setattr(calculations, "solve_matches_inputs", lambda ctx: True)

    def gate(rule_id, **ctx):
        env = run_gates.Env(
            task={}, ctx=SimpleNamespace(**ctx), project=None, project_dir=None
        )
        return run_gates.evaluate([_rules()[rule_id]], env)[0]

    return gate


def _site(
    export=(0.0, 10.0, 70.0),
    imp=(0.0, 0.0, 20.0),
    rating=70.0,
    periods=False,
    poc="grid",
):
    n = pypsa.Network()
    index = (
        pd.MultiIndex.from_product([[2030, 2035], range(len(export))])
        if periods
        else pd.RangeIndex(len(export))
    )
    n.set_snapshots(index)
    for bus in ("poi", "grid"):
        n.add("Bus", bus)
    n.buses["eh_poc"] = False
    n.buses.at[poc, "eh_poc"] = True
    n.add("Link", "grid_import", bus0="grid", bus1="poi", p_nom=20.0)
    n.add("Link", "grid_export", bus0="poi", bus1="grid", p_nom=rating)
    reps = 2 if periods else 1
    n.links_t.p0["grid_export"] = np.tile(export, reps)
    n.links_t.p0["grid_import"] = np.tile(imp, reps)
    return n


def _config(link="grid_export"):
    return {"commercial": {"export_link": link}}


def _export(direct, n, config=None, **kw):
    state = {"solver_config": _config() if config is None else config}
    return direct("ipp.export.limit", network=n, solver_state=state, **kw)


def test_an_export_at_the_rating_passes_and_one_over_it_fails_with_its_peak(direct):
    ok = _export(direct, _site(export=(0.0, 30.0, 70.0)))
    assert ok["verdict"] == "pass" and ok["facts"][0]["value"] == 0.0
    over = _export(direct, _site(export=(0.0, 30.0, 70.0), rating=60.0))
    assert over["verdict"] == "fail" and over["facts"][0]["value"] == pytest.approx(
        10.0
    )
    assert (
        "70" in over["facts"][0]["detail"][0] and "60" in over["facts"][0]["detail"][0]
    )


def test_an_import_peak_never_counts_as_export(direct):
    """I3: the import link's 20 MW and a reverse flow on the export link are not export."""
    n = _site(export=(-50.0, -10.0, 0.0), imp=(20.0, 20.0, 20.0), rating=5.0)
    result = _export(direct, n)
    assert result["verdict"] == "pass" and result["facts"][0]["value"] == pytest.approx(
        -5.0
    )


def test_the_flow_is_read_in_both_directions_by_the_point_of_connection(direct):
    """The sign is explicit: with the point of connection on bus1 the export
    is `p0`; with it on bus0 the export is `-p0`, and `p0` leaving it is an import."""
    on_bus1 = _site(export=(0.0, 30.0, 80.0), rating=70.0, poc="grid")
    result = _export(direct, on_bus1)
    assert result["verdict"] == "fail" and result["facts"][0]["value"] == pytest.approx(
        10.0
    )
    on_bus0 = _site(export=(0.0, -30.0, -80.0), rating=70.0, poc="poi")
    result = _export(direct, on_bus0)
    assert result["verdict"] == "fail" and result["facts"][0]["value"] == pytest.approx(
        10.0
    )
    leaving = _site(export=(0.0, 30.0, 80.0), rating=70.0, poc="poi")
    result = _export(direct, leaving)
    assert result["verdict"] == "pass" and result["facts"][0]["value"] == pytest.approx(
        -70.0
    )


def test_the_peak_is_taken_over_every_investment_period(direct):
    n = _site(export=(0.0, 10.0, 90.0), rating=70.0, periods=True)
    n.links_t.p0.loc[(2030, 2), "grid_export"] = 5.0  # only the second period peaks
    result = _export(direct, n)
    assert result["verdict"] == "fail" and result["facts"][0]["value"] == pytest.approx(
        20.0
    )


@pytest.mark.parametrize(
    "config,code",
    [
        ({}, "no_export_link"),
        ({"commercial": {}}, "no_export_link"),
        ({"commercial": {"export_link": ""}}, "no_export_link"),
        ({"commercial": {"export_link": 7}}, "no_export_link"),
        ({"commercial": {"export_link": "nowhere"}}, "export_link_missing"),
    ],
)
def test_an_unreadable_export_link_is_not_evaluated_never_a_pass(direct, config, code):
    result = _export(direct, _site(), config=config)
    assert result["verdict"] == "not_evaluated" and code in str(result)


def test_a_context_with_no_solver_state_names_no_export_link(direct):
    result = direct("ipp.export.limit", network=_site(), solver_state=None)
    assert result["verdict"] == "not_evaluated" and "no_export_link" in str(result)


@pytest.mark.parametrize("rating", [0.0, -5.0, float("nan")])
def test_a_link_with_no_rating_has_no_limit_to_hold_the_export_to(direct, rating):
    result = _export(direct, _site(rating=rating))
    assert result["verdict"] == "not_evaluated" and "export_link_unrated" in str(result)


def test_a_non_finite_or_missing_flow_is_not_evaluated(direct):
    n = _site()
    n.links_t.p0.loc[1, "grid_export"] = float("nan")
    result = _export(direct, n)
    assert result["verdict"] == "not_evaluated" and "unreadable_dispatch" in str(result)
    n = _site()
    n.links_t.p0.drop(columns="grid_export", inplace=True)
    result = _export(direct, n)
    assert result["verdict"] == "not_evaluated" and "no_dispatch" in str(result)
    n = _site()
    n.links_t.p0 = n.links_t.p0.iloc[0:0]  # a flow frame with no row: nothing to judge
    result = _export(direct, n)
    assert result["verdict"] == "not_evaluated" and "unreadable_dispatch" in str(result)
    n = _site(export=(0.0, 5.0), imp=(0.0, 0.0))
    n.links_t.p0.loc[0, "grid_export"] = float(
        "inf"
    )  # an infinite peak is not a number of MW
    assert "unreadable_dispatch" in str(_export(direct, n))


def test_an_untagged_or_two_sided_point_of_connection_is_not_evaluated(direct):
    n = _site()
    n.buses.drop(columns="eh_poc", inplace=True)
    assert "no_poc_tag" in str(_export(direct, n))
    n = _site()
    n.buses["eh_poc"] = True  # both sides: the direction is unknown
    assert "poc_unreadable" in str(_export(direct, n))
    n = _site()
    n.buses["eh_poc"] = False
    assert "poc_unreadable" in str(_export(direct, n))


@pytest.mark.parametrize("off", ["False", "0", "", float("nan"), None])
def test_a_tag_that_does_not_read_as_true_is_not_a_tag(direct, off):
    """The shared flag rule: the grid side tagged, the other side written as text or
    NaN, still reads the one-sided tag (`bool("False")` would read two sides)."""
    n = _site()
    n.buses["eh_poc"] = n.buses["eh_poc"].astype(object)
    n.buses.at["poi", "eh_poc"] = off
    result = _export(direct, n)
    assert result["verdict"] == "pass" and "poc_unreadable" not in str(result)


def test_a_stale_solve_is_not_evaluated(direct, monkeypatch):
    import harness.calculations as calculations

    monkeypatch.setattr(calculations, "solve_matches_inputs", lambda ctx: False)
    assert _export(direct, _site())["verdict"] == "not_evaluated"


def test_the_limit_is_the_links_own_rating_never_a_constant(direct):
    """I3: 70 is nowhere in the reader; a 55 MW link holds the export to 55."""
    result = _export(direct, _site(export=(0.0, 56.0, 56.0), rating=55.0))
    assert result["facts"][0]["value"] == pytest.approx(1.0)


# ── the investment-case readers (I4, I5) ─────────────────────────────────────


DONE = {
    "status": "done",
    "flags": [],
    "report": {"present": True, "stale": False, "assumptions_hash": "h1"},
}


def _report(
    irr=0.081,
    service=(0.0, 10.0, 10.0),
    dscr=(None, 1.5, 1.3),
    amount=100.0,
    hash_="h1",
    with_debt=True,
):
    sections = {
        "project": {
            "payload": {
                "project_pre_tax_irr": irr,
                "project_post_tax_irr": 0.05,
                "project_post_tax_npv": 1.0,
                "currency": "EUR",
            }
        }
    }
    if with_debt:
        sections["debt"] = {
            "payload": {"amount": amount, "service": list(service), "dscr": list(dscr)}
        }
    return {"assumptions_hash": hash_, "sections": sections}


def _read(monkeypatch, fn, body=DONE, report=None, current=True):
    from routers import results
    from services import workflow_tools

    monkeypatch.setattr(results, "get_investment_case", lambda: body)
    monkeypatch.setattr(
        results, "get_investment_case_report", lambda detail="export": report
    )
    monkeypatch.setattr(
        workflow_tools, "solve_freshness", lambda ctx: {"results_current": current}
    )
    return fn(None, lambda f: f())


def test_the_irr_is_read_from_a_current_established_case(monkeypatch):
    fact = _read(monkeypatch, ic.read_pre_tax_irr, report=_report(irr=0.0734))
    assert fact["state"] == ic.ESTABLISHED and fact["irr"] == pytest.approx(0.0734)


@pytest.mark.parametrize(
    "irr", [None, float("nan"), float("inf"), True, "not established"]
)
def test_an_irr_the_case_could_not_establish_is_not_established_never_zero(
    monkeypatch, irr
):
    fact = _read(monkeypatch, ic.read_pre_tax_irr, report=_report(irr=irr))
    assert (
        fact["irr"] is None
        and fact["state"] == ic.NOT_ESTABLISHED
        and fact["reasons"] == ["irr_not_established"]
    )


def test_an_irr_of_zero_is_a_number_not_a_missing_one(monkeypatch):
    assert (
        _read(monkeypatch, ic.read_pre_tax_irr, report=_report(irr=0.0))["irr"] == 0.0
    )


@pytest.mark.parametrize(
    "reader,key", [(ic.read_pre_tax_irr, "irr"), (ic.read_min_dscr, "min_dscr")]
)
def test_a_report_stored_after_the_status_is_not_the_one_judged_current(
    monkeypatch, reader, key
):
    """I5: the status call and the report call are bound by the assumptions hash."""
    for stored in (None, "h2"):
        fact = _read(monkeypatch, reader, report=_report(hash_=stored))
        assert fact[key] is None and fact["reasons"] == ["report_changed"]


@pytest.mark.parametrize(
    "reader,key", [(ic.read_pre_tax_irr, "irr"), (ic.read_min_dscr, "min_dscr")]
)
def test_a_stale_report_and_results_that_no_longer_describe_the_network_give_no_figure(
    monkeypatch, reader, key
):
    stale = {
        **DONE,
        "report": {**DONE["report"], "stale": True, "changed": ["finance"]},
    }
    fact = _read(monkeypatch, reader, body=stale, report=_report())
    assert (
        fact[key] is None
        and fact["state"] == ic.NOT_RUN
        and "case_stale" in fact["reasons"]
    )
    fact = _read(monkeypatch, reader, report=_report(), current=False)
    assert fact[key] is None and "results_not_current" in fact["reasons"]
    assert _read(monkeypatch, reader, body=None, report=None)[key] is None


def _written(debt):
    """The report the finance writer itself assembles for a case with these tranches
    (#1427: the section shape is the real writer's, not a hand-built one)."""
    from services.finance.engine import run_case
    from services.finance.report import assemble_finance_sections
    from tests.test_finance_engine import LAYER, _case

    case = _case(debt=debt)
    rep = assemble_finance_sections(
        run_case(case, layers=LAYER), case, assumptions_hash="a" * 16, packs={}
    )
    return {
        **rep.model_dump(mode="json"),
        "assumptions_hash": "h1",
    }  # the status's own hash


def test_the_cover_of_a_case_the_real_writer_assembled_with_a_debt_tranche(monkeypatch):
    from models.finance import DebtTranche

    loan = DebtTranche(
        kind="term_loan",
        amount=400.0,
        rate=0.05,
        tenor_years=5,
        upfront_fee=0.01,
        dsra_months=0,
        grace_years=0,
        commitment_fee=0.0,
    )
    report = _written([loan])
    debt = report["sections"]["debt"]["payload"]
    expect = min(r for p, r in zip(debt["service"], debt["dscr"]) if p > 0)
    fact = _read(monkeypatch, ic.read_min_dscr, report=report)
    assert fact["state"] == ic.ESTABLISHED
    assert fact["min_dscr"] == pytest.approx(expect) and fact["min_dscr"] > 1.0
    assert fact["years"] == sum(1 for p in debt["service"] if p > 0) >= 5


def test_a_case_the_real_writer_assembled_with_no_debt_has_no_cover_to_judge(
    monkeypatch,
):
    """The shipped template has no debt: `no_debt_service`, which the gate leaves not evaluated."""
    fact = _read(monkeypatch, ic.read_min_dscr, report=_written([]))
    assert fact["min_dscr"] is None and fact["reasons"] == ["no_debt_service"]


def test_the_cover_is_the_minimum_over_the_years_with_debt_service_only(monkeypatch):
    fact = _read(
        monkeypatch,
        ic.read_min_dscr,
        report=_report(service=(0.0, 10.0, 10.0, 0.0), dscr=(0.1, 1.5, 1.3, 0.0)),
    )
    assert (
        fact["min_dscr"] == pytest.approx(1.3) and fact["years"] == 2
    )  # the year-0 and year-3 zeros are no service


def test_a_year_without_service_never_lowers_the_cover(monkeypatch):
    fact = _read(
        monkeypatch,
        ic.read_min_dscr,
        report=_report(service=(0.0, 10.0), dscr=(0.01, 2.0)),
    )
    assert fact["min_dscr"] == 2.0


@pytest.mark.parametrize(
    "report,reason",
    [
        (_report(with_debt=False), "dscr_unreadable"),
        (_report(amount=None), "no_debt_service"),
        (_report(service=(), dscr=()), "no_debt_service"),
        (_report(service=(0.0, 0.0), dscr=(None, None)), "no_debt_service"),
        (_report(dscr=(None, None, 1.3)), "dscr_not_finite"),
        (_report(dscr=(None, float("inf"), 1.3)), "dscr_not_finite"),
        (_report(dscr=(None, 1.5)), "dscr_unreadable"),
        (_report(service=(0.0, "x", 10.0)), "dscr_unreadable"),
    ],
)
def test_no_debt_or_a_non_finite_cover_is_not_established_never_zero_or_a_pass(
    monkeypatch, report, reason
):
    fact = _read(monkeypatch, ic.read_min_dscr, report=report)
    assert (
        fact["min_dscr"] is None
        and fact["state"] == ic.NOT_ESTABLISHED
        and fact["reasons"] == [reason]
    )


def test_a_debt_section_with_no_service_or_cover_list_is_not_established(monkeypatch):
    for key, reason in (("service", "no_debt_service"), ("dscr", "dscr_unreadable")):
        report = _report()
        report["sections"]["debt"]["payload"][key] = None
        fact = _read(monkeypatch, ic.read_min_dscr, report=report)
        assert fact["min_dscr"] is None and fact["reasons"] == [reason], key
    report = _report()
    report["sections"]["debt"]["payload"]["service"] = "10"  # not a list
    assert _read(monkeypatch, ic.read_min_dscr, report=report)["reasons"] == [
        "no_debt_service"
    ]
    report = _report()
    report["sections"]["debt"]["payload"]["dscr"] = "1.3"
    assert _read(monkeypatch, ic.read_min_dscr, report=report)["reasons"] == [
        "dscr_unreadable"
    ]


def _ic_gate(monkeypatch, rule_id, report, body=DONE):
    from routers import results
    from services import workflow_tools

    monkeypatch.setattr(results, "get_investment_case", lambda: body)
    monkeypatch.setattr(
        results, "get_investment_case_report", lambda detail="export": report
    )
    monkeypatch.setattr(
        workflow_tools, "solve_freshness", lambda ctx: {"results_current": True}
    )
    monkeypatch.setattr(run_gates, "_on", lambda ctx, fn, *args: fn(*args))
    env = run_gates.Env(task={}, ctx=SimpleNamespace(), project=None, project_dir=None)
    return run_gates.evaluate([_rules()[rule_id]], env)[0]


def test_the_cover_rule_passes_at_the_floor_fails_below_it_and_a_warning_stays_covered(
    monkeypatch,
):
    floor = run_profiles.IPP_DSCR_MIN
    at = _ic_gate(monkeypatch, "ipp.ic.min_dscr", _report(dscr=(None, 2.0, floor)))
    assert at["verdict"] == "pass"
    under = _ic_gate(
        monkeypatch, "ipp.ic.min_dscr", _report(dscr=(None, 2.0, floor - 0.01))
    )
    assert (
        under["verdict"] == "fail" and under["severity"] == "warn"
    )  # #1409: it is listed, never silently dropped


def test_the_cover_and_the_irr_rules_are_not_evaluated_on_the_shipped_no_debt_case(
    monkeypatch,
):
    assert (
        _ic_gate(monkeypatch, "ipp.ic.min_dscr", _report(with_debt=False))["verdict"]
        == "not_evaluated"
    )
    ok = _ic_gate(monkeypatch, "ipp.ic.pre_tax_irr", _report())
    assert ok["verdict"] == "pass"
    gone = _ic_gate(monkeypatch, "ipp.ic.pre_tax_irr", _report(irr=None))
    assert gone["verdict"] == "not_evaluated"


def test_the_reason_words_name_the_new_states_in_plain_words():
    expected = {
        "irr_not_established": "pre-tax IRR",
        "no_debt_service": "no debt service",
        "dscr_not_finite": "no finite DSCR",
        "dscr_unreadable": "debt section cannot be read",
    }
    for key, phrase in expected.items():
        words = ic.reason_words({"reasons": [key]})
        assert phrase in words and "_" not in words and words[0].isupper()


# ── the run's investment-case step (I2) ──────────────────────────────────────


def test_the_step_settles_from_its_own_record_only():
    record = {"status": "done", "started_at": 5.0}
    assert run_ic.outcome(record, 5.0) == ("done", "completed", None)
    for other in (6.0, None):  # a replaced record is unverifiable
        assert run_ic.outcome(record, other)[:2] == ("unknown", "uncertain")
    assert run_ic.outcome(None, 5.0)[:2] == ("unknown", "uncertain")
    assert run_ic.outcome({"status": "running", "started_at": 5.0}, 5.0)[:2] == (
        "unknown",
        "uncertain",
    )


@pytest.mark.parametrize("status", ["refused", "failed", "aborted"])
def test_a_refused_failed_or_aborted_case_fails_the_step_with_its_code(status):
    native, step_status, error = run_ic.outcome(
        {"status": status, "started_at": 1.0, "error_code": "no_finance"}, 1.0
    )
    assert (
        (native, step_status) == (status, "failed")
        and "no_finance" in error
        and status in error
    )


def test_a_start_receipt_with_no_started_at_is_not_followed():
    assert run_ic.wait(("t", "s", "a"), "job", "sess", None, None) is False


def test_the_start_receipt_carries_the_started_at_the_step_binds():
    from services.finance import investment_case_runner as runner_mod

    src = open(runner_mod.__file__).read()
    assert '"started_at": record["started_at"]' in src
    assert math.isfinite(1.0)


# ── mutant pins: the envelope, the export rating and the reason words ────────


def test_a_run_entry_naming_the_case_is_checked_like_any_other_run():
    assert run_envelope._check_run({"tool": "run_investment_case", "kind": "x"}) == {
        "tool": "run_investment_case",
        "kind": "x",
    }
    for bad in (
        {"tool": "no_such_tool"},
        {"tool": "run_investment_case", "restore": "final"},
        {"tool": "run_investment_case", "kind": 3},
        {"tool": "run_investment_case", "extra": 1},
    ):
        with pytest.raises(run_envelope.EnvelopeError) as err:
            run_envelope._check_run(bad)
        assert "run_investment_case" in str(err.value) and "A run names one of" in str(
            err.value
        )
    with pytest.raises(run_envelope.EnvelopeError):
        run_envelope._check_run("run_investment_case")


def test_only_a_listed_solving_run_is_held_to_the_solve_budget(monkeypatch):
    from services.results import finance_case

    monkeypatch.setattr(finance_case, "baseline_plan", lambda *a, **k: None)
    seen = []

    def standing(session, envelope, task, ctx, solve=False):
        seen.append(solve)
        return "probe"

    monkeypatch.setattr(run_envelope, "_standing", standing)
    envelope = {
        "runs": [{"tool": "run_investment_case"}, {"tool": "solve_queue_enqueue"}],
        "edits": [],
    }

    def refusal(tool):
        return run_envelope._refusal(
            SimpleNamespace(run_step_call="c"),
            envelope,
            {},
            tool,
            {},
            "write",
            False,
            None,
            SimpleNamespace(network=None, solver_state={}),
            {"call_id": "c"},
        )

    assert refusal("run_investment_case") == "probe" and seen == [
        False
    ]  # a case spends no solve
    assert refusal("solve_queue_enqueue") == "probe" and seen == [False, True]
    envelope["runs"] = [{"tool": "run_investment_case"}]
    refusal("solve_queue_enqueue")
    assert seen[-1] is False  # unlisted: never held as a solve


def test_a_case_is_admitted_with_the_solve_budget_used_up_but_a_solve_is_not(
    monkeypatch,
):
    from harness import runner as harness_runner

    monkeypatch.setattr(harness_runner, "budget_spent", lambda task: False)
    monkeypatch.setattr(run_envelope, "_now", lambda: 0.0)
    monkeypatch.setattr(run_envelope, "_epoch", lambda value: 1e9)
    monkeypatch.setattr(run_envelope, "_handover_unbound", lambda *a: False)
    envelope = {
        "state": "granted",
        "task_id": "t",
        "project_id": "p",
        "owner_id": "u",
        "expires_at": "x",
        "budget": {"solves": 1},
        "chain": [],
        "fingerprint": "f",
        "audit": [{"event": "used", "call_id": "x", "tool": "solve_queue_enqueue"}],
    }
    task = {"task_id": "t", "project_id": "p", "owner_id": "u"}
    session = SimpleNamespace(owner_user_id="u")
    ctx = SimpleNamespace(project_uuid="p")
    monkeypatch.setattr(run_envelope, "_fingerprint", lambda c: "f")
    from services import assistant_tasks

    monkeypatch.setattr(assistant_tasks, "_stale", lambda t, current: False)
    assert run_envelope._standing(session, envelope, task, ctx, solve=False) is None
    assert (
        run_envelope._standing(session, envelope, task, ctx, solve=True) == "exhausted"
    )


@pytest.mark.parametrize("rating", ["70", None, float("inf")])
def test_a_rating_that_is_not_a_finite_number_has_no_limit_to_hold_the_export_to(
    direct, rating
):
    n = _site()
    n.links["p_nom"] = n.links["p_nom"].astype(object)
    n.links.at["grid_export", "p_nom"] = rating
    result = _export(direct, n)
    assert result["verdict"] == "not_evaluated" and "export_link_unrated" in str(result)


def test_each_new_reason_key_has_its_exact_words():
    expected = {
        "irr_not_established": "the case could not establish the project pre-tax IRR",
        "no_debt_service": "the case has no debt service, so there is no DSCR",
        "dscr_not_finite": "a year with debt service has no finite DSCR",
        "dscr_unreadable": "the case's debt section cannot be read",
    }
    for key, words in expected.items():
        assert ic._REASON_WORDS[key] == words
        assert ic.reason_words({"reasons": [key]}) == words[0].upper() + words[1:]


def test_the_returns_are_read_only_from_a_current_established_case(monkeypatch):
    fact = _read(monkeypatch, ic.read_returns, report=_report())
    assert fact["state"] == ic.ESTABLISHED
    assert (
        fact["returns"]["irr"] == pytest.approx(0.05)
        and fact["returns"]["currency"] == "EUR"
    )
    stale = _read(monkeypatch, ic.read_returns, report=_report(), current=False)
    assert stale["returns"] is None and stale["state"] != ic.ESTABLISHED
    changed = _read(monkeypatch, ic.read_returns, report=_report(hash_="other"))
    assert changed["returns"] is None and changed["reasons"] == ["report_changed"]


def test_an_empty_service_list_is_no_debt_service_whatever_the_cover_list_holds(
    monkeypatch,
):
    report = _report(service=(), dscr=(None, 1.5, 1.3))
    fact = _read(monkeypatch, ic.read_min_dscr, report=report)
    assert fact["min_dscr"] is None and fact["reasons"] == ["no_debt_service"]


# ── GK B (53) F1–F3: the case never runs an uncounted baseline solve ────────


class _FlipLock:
    """The network lock, entering which a kept asset appears (an edit landing after admission)."""

    def __init__(self, flip):
        self.flip = flip

    def __enter__(self):
        self.flip()

    def __exit__(self, *exc):
        return False


def _build_under(monkeypatch, *, in_run):
    """`_ic_build_case`'s closure with the plan reading `kept` only once the lock is entered."""
    import routers.results as results
    from models.finance import FinanceInputs
    from services import assistant_tasks, workflow_tools
    from services.results import baseline, finance_case
    from services.pypsa_service import PyPSAService

    state = {"kept": (), "solved": 0}

    def plan(n, cfg, owner=None):
        return baseline.BaselinePlan(removed=(), kept=state["kept"], flags=())

    def solve(snap, cfg, plan):
        state["solved"] += 1
        raise baseline.BaselineRefused("stop", "the probe stops after the solve")

    def flip():
        state["kept"] = (("StorageUnit", "old_bess"),)

    monkeypatch.setattr(finance_case, "baseline_plan", plan)
    monkeypatch.setattr(baseline, "solve_baseline", solve)
    monkeypatch.setattr(workflow_tools, "_network_copy", lambda n: n)
    monkeypatch.setattr(
        FinanceInputs, "model_validate", classmethod(lambda cls, raw: raw)
    )
    monkeypatch.setattr(PyPSAService, "get_lock", staticmethod(lambda: _FlipLock(flip)))
    token = assistant_tasks.RUN_CONTEXT.set(object() if in_run else None)
    try:
        build = results._ic_build_case(
            object(), SimpleNamespace(finance={}), owner=None, lost_load=None
        )
    finally:
        assistant_tasks.RUN_CONTEXT.reset(token)
    return build, state


def test_a_kept_asset_added_after_admission_is_refused_in_the_run_not_solved(
    monkeypatch,
):
    """F1: the plan is read again under the build's own lock; a run's case refuses there."""
    from services.finance.case import FinanceRefused

    build, state = _build_under(monkeypatch, in_run=True)
    with pytest.raises(FinanceRefused) as err:
        build()
    assert err.value.code == "baseline_solve" and state["solved"] == 0


def test_the_same_case_outside_a_run_still_solves_its_baseline(monkeypatch):
    from services.finance.case import FinanceRefused

    build, state = _build_under(monkeypatch, in_run=False)
    with pytest.raises(FinanceRefused) as err:
        build()
    assert err.value.code == "stop" and state["solved"] == 1


def test_the_admission_plans_for_the_owner_the_call_names(monkeypatch):
    """F3: the owner is the call's own `owner` argument."""
    from services.results import baseline, finance_case

    seen = []

    def plan(n, cfg, owner=None):
        seen.append(owner)
        return baseline.BaselinePlan(removed=(), kept=(), flags=())

    monkeypatch.setattr(finance_case, "baseline_plan", plan)
    ctx = SimpleNamespace(network=object(), solver_state={"solver_config": object()})
    assert run_envelope._case_solves_baseline(ctx, "acme") is False
    assert run_envelope._case_solves_baseline(ctx) is False
    assert seen == ["acme", None]
    monkeypatch.setattr(run_envelope, "_standing", lambda *a, **k: None)
    monkeypatch.setattr(run_envelope, "_names_another_project", lambda *a: False)
    envelope = {"runs": [{"tool": "run_investment_case"}], "edits": []}
    for args in ({"owner": "bob"}, None, "not a dict"):
        run_envelope._refusal(
            SimpleNamespace(run_step_call="c"),
            envelope,
            {},
            "run_investment_case",
            args,
            "write",
            False,
            None,
            ctx,
            {"call_id": "c"},
        )
    assert seen[2:] == ["bob", None, None]


@pytest.mark.parametrize("raises", [False, True])
def test_the_runners_own_step_marker_is_gone_after_the_dispatch(monkeypatch, raises):
    """F2: `run_step_call` names the call during the dispatch and is None afterwards
    (a raising dispatch too), so a later call with that id is a model turn's: `own_step_only`."""
    from harness import eventlog, loop, runner

    seen = {}

    def dispatch_tool_call(session, call, results, turn_ctx=None, guided=False):
        seen["during"] = session.run_step_call
        seen["id"] = call["id"]
        if raises:
            raise RuntimeError("dispatch failed")
        return iter(())

    monkeypatch.setattr(loop, "dispatch_tool_call", dispatch_tool_call)
    monkeypatch.setattr(eventlog, "message", lambda *a, **k: None)
    monkeypatch.setattr(eventlog, "record", lambda *a, **k: None)
    monkeypatch.setattr(
        runner,
        "_tasks",
        lambda: SimpleNamespace(
            release_claim=lambda *a: None, _effectful=lambda tool: False
        ),
    )
    fake = SimpleNamespace(wake=lambda path: None, _cards={})
    session = SimpleNamespace(task_id="t", session_id="s", run_step_call=None)
    step = {"id": "s1", "tool": "run_investment_case"}
    ctx = SimpleNamespace()
    try:
        runner.Runner.dispatch(fake, "p", ctx, session, step, {}, "approve")
    except RuntimeError:
        assert raises
    assert seen["during"] == seen["id"] and seen["id"].startswith("run-s1-")
    assert session.run_step_call is None
    from services.results import baseline

    plan = baseline.BaselinePlan(removed=(), kept=(), flags=())
    assert (
        _case_refusal(
            monkeypatch, plan=plan, call=seen["id"], step=session.run_step_call
        )
        == "own_step_only"
    )

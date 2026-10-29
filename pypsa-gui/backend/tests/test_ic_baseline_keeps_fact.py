"""The `ic.baseline_keeps` gate fact (study harness issue 20): the owned
existing assets the microgrid case's baseline keeps (the island as it stands
today, owner decision M2e (a)), guarded against the template's own
`MG_EXISTING`, read from the stored case through
`GET /results/investment_case`, never by re-running it."""
from __future__ import annotations

import json

import pytest

from services import ic_counterfactual as F

IDS = ("eh_microgrid", "eh_microgrid_staged")
SITE_TODAY = ["counterfactual_basis:site_today"]
#: The disclosed baseline flags that keep a case established (GK A).
SHEDS = ["counterfactual_basis:site_today", "counterfactual_shed_at_voll:12.5",
         "baseline_island_rows_relaxed"]
RULES = [{"id": "ic.counterfactual", "fact": "ic.counterfactual", "op": "eq",
          "value": "established", "severity": "warn", "basis": "issue 12"},
         {"id": "ic.baseline_keeps", "fact": "ic.baseline_keeps", "op": "nonempty",
          "severity": "warn", "basis": "issue 12"}]


def _template(tid):
    from project_templates import eh_templates as T
    from routers.projects import _solver_config_from_dict

    return T.BUILDERS[tid](), _solver_config_from_dict(T.solver_config(tid))


def _price_diesel_1(n):
    n.generators.at["diesel_1", "overnight_cost"] = 1e5


def _rename_diesel_1(n):
    # The value flows still own `diesel_1`, which the network no longer has.
    # (Static and time-varying rows by hand: pypsa's `rename_component_names`
    # fails on this network's tables.)
    c = n.components.generators
    c.static.rename(index={"diesel_1": "gen_a"}, inplace=True)
    for df in c.dynamic.values():
        df.rename(columns={"diesel_1": "gen_a"}, inplace=True)


# ── the fact over a built network (no solve) ─────────────────────────────────

@pytest.mark.parametrize("tid", IDS)
def test_both_microgrids_keep_the_island_as_it_stands(tid):
    n, cfg = _template(tid)
    fact = F.baseline_keeps(n, cfg, F.existing_assets(tid))
    assert fact["state"] == F.ESTABLISHED and fact["reasons"] == []
    assert sorted(fact["kept"]) == sorted(F.existing_assets(tid))


def test_a_priced_diesel_is_not_established_and_named():
    n, cfg = _template("eh_microgrid")
    _price_diesel_1(n)
    fact = F.baseline_keeps(n, cfg, F.existing_assets("eh_microgrid"))
    assert fact["state"] == F.NOT_ESTABLISHED
    assert fact["reasons"] == ["baseline_drops:diesel_1"]
    assert "diesel_1" not in fact["kept"]


def test_a_renamed_unit_cannot_slip_past_the_guard():
    """The guard compares the template's MG_EXISTING as an exact set: the
    two `diesel_*` rows left kept do not stand in for the missing one."""
    n, cfg = _template("eh_microgrid")
    _rename_diesel_1(n)
    fact = F.baseline_keeps(n, cfg, F.existing_assets("eh_microgrid"))
    assert {"diesel_2", "diesel_3"} <= set(fact["kept"])
    assert fact["state"] == F.NOT_ESTABLISHED
    assert fact["reasons"] == ["baseline_drops:diesel_1"]


def test_a_template_without_a_declared_set_lists_what_is_kept():
    n, cfg = _template("eh_microgrid")
    _price_diesel_1(n)
    fact = F.baseline_keeps(n, cfg, F.existing_assets("eh_site"))
    assert F.existing_assets("eh_site") is None
    assert fact["state"] == F.ESTABLISHED and "diesel_2" in fact["kept"]


def test_the_text_discloses_the_shed_and_the_relaxed_island_rows():
    """Both keep the case established; the report quotes the case's own
    sentences for them, never its own wording."""
    from services.finance.baseline_text import flag_text
    from services.finance.export_xlsx import BASELINE_CAVEATS

    caveats = dict(BASELINE_CAVEATS)
    assert F.classify({"status": "done", "flags": SHEDS,
                       "report": {"present": True, "stale": False}})["state"] == F.ESTABLISHED
    text = F.counterfactual_text(SHEDS)
    assert caveats["counterfactual_shed_at_voll"] in text
    assert flag_text("baseline_island_rows_relaxed") in text


# ── through the real investment-case route (case builder stubbed) ────────────

@pytest.fixture
def ic(client, install_network, session_ctx, monkeypatch, tmp_path):
    """Install a template's network and solver config, stamp it solved, run
    the investment case through its real route and runner (the case builder
    stubbed with the SAM s2 case and ``flags``; the dispatch gate passed, as
    the stub reads none), then gate both rules as the harness does."""
    import dataclasses

    import routers.results as R
    from tests.fixtures.investment_case.sam import sam_case as S
    from tests.test_investment_case_routes import STUDY_URL, _fake_build, _poll

    from services import run_gates, workflow_tools
    from services.flow_service import TEMPLATE_FILE

    monkeypatch.setattr(R, "_dispatch_ready", lambda n: True)

    def setup(tid, mutate=None):
        n, cfg = _template(tid)
        if mutate is not None:
            mutate(n)
        install_network(n)
        ctx = session_ctx(client)
        ctx.solver_state["solver_config"] = cfg
        ctx.solver_state.update(status="completed", condition="optimal")
        run_gates._on(ctx, lambda: workflow_tools._record_solve_inputs(ctx.network, cfg))
        assert workflow_tools.solve_freshness(ctx)["results_current"] is True
        (tmp_path / TEMPLATE_FILE).write_text(json.dumps({"id": tid}), "utf-8")

    def run(flags):
        _fake_build(monkeypatch, case_fn=lambda: dataclasses.replace(
            S.to_finance_case("s2"), flags=tuple(flags)))
        assert client.post(STUDY_URL, json={}).status_code == 200
        _poll(client)

    def gate():
        env = run_gates.Env(task={}, ctx=session_ctx(client), project=None, project_dir=tmp_path)
        return run_gates.evaluate([run_gates.check_rule(r) for r in RULES], env)

    return setup, run, gate


@pytest.mark.parametrize("tid", IDS)
@pytest.mark.parametrize("flags", [SITE_TODAY, SHEDS], ids=["site_today", "sheds"])
def test_a_finished_case_evaluates_the_investment_rules(ic, tid, flags):
    setup, run, gate = ic
    setup(tid)
    run(flags)
    cf, keeps = gate()
    assert cf["verdict"] == "pass" and cf["facts"][0]["value"] == "established"
    assert keeps["verdict"] == "pass" and "reason" not in keeps
    assert sorted(keeps["facts"][0]["value"]) == sorted(F.existing_assets(tid))
    assert keeps["facts"][0]["source"] == "services.ic_counterfactual.read_baseline_keeps:kept"


@pytest.mark.parametrize("mutate", [_price_diesel_1, _rename_diesel_1], ids=["priced", "renamed"])
def test_a_dropped_existing_asset_leaves_the_rule_unevaluated_with_its_name(ic, mutate):
    setup, run, gate = ic
    setup("eh_microgrid", mutate)
    run(SITE_TODAY)
    _cf, keeps = gate()
    assert keeps["verdict"] == "not_evaluated"
    assert keeps["facts"][0]["value"] == F.NOT_ESTABLISHED
    assert keeps["reason"] == "not_established: baseline_drops:diesel_1"


def test_no_case_is_not_run(ic):
    setup, _run, gate = ic
    setup("eh_microgrid")
    _cf, keeps = gate()
    assert keeps["verdict"] == "not_evaluated" and keeps["facts"][0]["value"] == F.NOT_RUN
    assert keeps["reason"] == "not_run: no_case"


def test_a_case_whose_network_was_edited_is_not_run(ic, client, session_ctx):
    setup, run, gate = ic
    setup("eh_microgrid")
    run(SITE_TODAY)
    session_ctx(client).network.generators.loc["diesel_1", "marginal_cost"] += 5.0
    _cf, keeps = gate()
    assert keeps["facts"][0]["value"] == F.NOT_RUN
    assert keeps["reason"] == "not_run: case_stale, results_not_current"


# ── one live solve through the real route ───────────────────────────────────

@pytest.mark.live_solve
@pytest.mark.slow
def test_the_live_microgrid_case_keeps_the_island_and_evaluates_the_rules(
        client, install_network, session_ctx, tmp_path):
    """The real case (no stub) on a solved `eh_microgrid`: both rules pass,
    and the fact's kept names are the case's own baseline plan for the run
    (the route's call, `baseline_plan(n, cfg, owner)`) as well as MG_EXISTING."""
    import queue
    import threading

    from tests.test_investment_case_routes import STUDY_URL, _poll

    from services import run_gates, workflow_tools
    from services.flow_service import TEMPLATE_FILE
    from services.results import baseline as Bl
    from services.results.finance_case import baseline_plan
    from services.solver_service import run_simulation

    n, cfg = _template("eh_microgrid")
    install_network(n)
    ctx = session_ctx(client)
    ctx.solver_state["solver_config"] = cfg
    state: dict = {}

    def solve():
        from services.pypsa_service import PyPSAService
        return run_simulation(cfg, PyPSAService.get_network(), PyPSAService.get_lock(),
                              threading.Event(), queue.SimpleQueue(),
                              state_update=lambda **k: state.update(k))
    status, cond = run_gates._on(ctx, solve)
    assert status in ("ok", "optimal"), (status, cond)
    ctx.solver_state.update(status="completed", condition="optimal",
                            last_lost_load=state.get("last_lost_load"))
    run_gates._on(ctx, lambda: workflow_tools._record_solve_inputs(ctx.network, cfg))
    (tmp_path / TEMPLATE_FILE).write_text(json.dumps({"id": "eh_microgrid"}), "utf-8")
    Bl.clear_cache()
    try:
        assert client.post(STUDY_URL, json={}).status_code == 200
        body = _poll(client, timeout=300.0)
        assert body["status"] == "done", body
        env = run_gates.Env(task={}, ctx=ctx, project=None, project_dir=tmp_path)
        cf, keeps = run_gates.evaluate([run_gates.check_rule(r) for r in RULES], env)
    finally:
        Bl.clear_cache()
    assert cf["verdict"] == "pass", cf
    assert keeps["verdict"] == "pass", keeps
    plan = run_gates._on(ctx, lambda: baseline_plan(ctx.network, cfg, body.get("owner")))
    assert keeps["facts"][0]["value"] == [name for _c, name in plan.kept]
    assert sorted(keeps["facts"][0]["value"]) == sorted(F.existing_assets("eh_microgrid"))

"""The staged data centre template produces an investment case (slice D4).

A project created from `eh_datacenter_staged` carries, in its solver settings,
an inline commercial config (the PoC `grid_import`, a synthetic network
tariff, the `single_owner` value flows) and the finance inputs. After a
multi-period solve the finance adapter builds a STAGED case: the existing
campus is in service at the first period with a typed overnight cost of 0
(no capex), and the per-period candidates the plan builds are stages with
their own COD (P5c). Must-check 2: existing assets with a 2027 COD are
neither refused nor charged capex.
"""
from __future__ import annotations

import json
import pathlib
import queue
import sys
import threading
from datetime import date

import numpy as np
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TPL_DIR = BACKEND / "project_templates"
sys.path.insert(0, str(TPL_DIR))
import eh_templates as T  # noqa: E402

TID = "eh_datacenter_staged"
YEARS = [2027, 2030, 2035]
EXISTING = ("genset_1", "genset_2", "genset_3", "genset_4", "rooftop_pv", "ups_battery",
            "site_transformer")
CANDIDATES = tuple(f"{k}_new_{y}" for y in YEARS for k in ("genset", "bess"))


def _no_ref(ref):
    raise AssertionError(f"the template's commercial config names a Library ref: {ref!r}")


# ── 1. the commercial block ─────────────────────────────────────────────────

def test_the_solver_config_ships_an_inline_commercial_block_that_binds_unchanged(tmp_path):
    from models.commercial import CommercialConfig
    from services.commercial import binding
    from services.commercial import value_flow_templates as VFT

    cfg = T.solver_config(TID)
    raw = cfg["commercial"]
    parsed = CommercialConfig.model_validate(raw)
    assert parsed.poc_link == "grid_import" and parsed.timezone
    assert parsed.import_tariff is not None and parsed.import_tariff_ref is None
    assert parsed.export_price_ref is None and parsed.connection is None
    assert parsed.contracts == [] and parsed.grid_cfe_share_ref is None
    n = T.BUILDERS[TID]()
    # It binds like PUT /solver_config does, and the stored dict is the shipped one.
    assert binding.bind_commercial(n, parsed, project_dir=None, resolve_ref=_no_ref,
                                   resolve_item=_no_ref) == raw
    # The value flows are the single_owner template built on this network: one
    # owner, the site, owning the site assets (existing and candidates).
    vf = raw["value_flows"]
    built = VFT.build("single_owner", n, parsed.model_copy(update={"value_flows": None}))
    assert vf == built.config.model_dump(mode="json")
    owners = {o["owner"] for o in vf["asset_owners"]}
    assert owners == {"site"}
    owned = {o["asset_id"] for o in vf["asset_owners"]}
    assert set(EXISTING) | set(CANDIDATES) <= owned
    from models.commercial import ValueFlowConfig

    # Not stale on the network a project loads (the template's network.nc).
    import pypsa

    n.export_to_netcdf(str(tmp_path / "network.nc"))
    loaded = pypsa.Network(str(tmp_path / "network.nc"))
    assert VFT.template_status(ValueFlowConfig.model_validate(vf), loaded, parsed) == []


def test_the_tariff_is_marked_synthetic_in_the_ledger():
    meta = T.TEMPLATE_META[TID]
    tariff = T.solver_config(TID)["commercial"]["import_tariff"]
    assert "synthetic" in tariff["name"].lower()
    row = next(r for r in meta["ledger"] if r["item"] == "import tariff")
    assert row["source"] == "synthetic" and tariff["id"] in row["value"]


# ── 2. the finance inputs ───────────────────────────────────────────────────

def test_the_finance_inputs_validate_and_type_the_existing_campus():
    from models.finance import ESCALATION_CLASSES, FinanceInputs

    cfg = T.solver_config(TID)
    fin = FinanceInputs.model_validate(cfg["finance"])
    assert cfg["finance"] == fin.model_dump(mode="json")          # stored normalised
    assert fin.cod_by_asset == {a: date(2027, 1, 1) for a in EXISTING}
    # Stage capex is paid stage_capex_lead_years before its COD: the 2030
    # stage's lead year is not before financial close.
    assert fin.financial_close < date(2027, 1, 1)
    assert fin.financial_close.year <= 2030 - fin.stage_capex_lead_years
    assert 2027 + fin.analysis_years == 2045
    assert fin.replacement_rule == "part_lifetimes"
    assert fin.wacc_nominal == cfg["discount_rate"] == 0.07
    assert set(fin.escalation) == set(ESCALATION_CLASSES)
    assert fin.contingency_share == 0.0 and fin.annualise is False


def test_the_existing_assets_are_typed_at_an_overnight_cost_of_zero():
    n = T.BUILDERS[TID]()
    for a in EXISTING:
        for frame in ("generators", "storage_units", "links"):
            df = getattr(n, frame)
            if a in df.index:
                assert df.at[a, "overnight_cost"] == 0.0, a
    # the PoC meter link stays untyped (not an investment) and the candidates
    # keep their pack prices
    assert n.links.at["grid_import", "overnight_cost"] != n.links.at["grid_import",
                                                                     "overnight_cost"]
    assert (n.generators.loc[[f"genset_new_{y}" for y in YEARS], "overnight_cost"] > 0).all()


def test_the_ledger_says_the_battery_inverter_is_replaced():
    ledger = {r["item"]: r for r in T.TEMPLATE_META[TID]["ledger"]}
    note = ledger["battery inverter replacement"]["note"]
    assert "part_not_replaced" not in note and "part_lifetimes" in note


# ── 3. the lossy PoC ────────────────────────────────────────────────────────

def test_the_staged_poc_chain_is_loss_free_and_says_so():
    n = T.BUILDERS[TID]()
    assert n.links.at["grid_import", "efficiency"] == 1.0
    assert n.links.at["site_transformer", "efficiency"] == 1.0
    flat = T.build_eh_datacenter()
    assert flat.links.at["grid_import", "efficiency"] == 0.995           # unchanged
    assert flat.links.at["site_transformer", "efficiency"] == 0.99
    ledger = {r["item"]: r for r in T.TEMPLATE_META[TID]["ledger"]}
    assert "not modelled" in ledger["PoC and transformer losses"]["note"]


# ── the route binds what the template ships ────────────────────────────────

def test_the_study_notes_say_the_poc_is_loss_free_and_the_connection_carries_the_peak():
    """Review of #283: the Guided user sees the loss-free PoC and the grid-only
    comparison's 2035 overload in the notes, not only in the ledger."""
    n = T.BUILDERS[TID]()
    notes = " ".join(T.TEMPLATE_META[TID]["study_notes"])
    year, it_mw, pue = T.DC_PHASES[-1]
    assert "losses are not modelled" in notes
    # The connection carries the 2035 peak (owner decision 2026-10-15), so the
    # grid-only comparison can run: the note says so, and it holds.
    poc = float(n.links.at["grid_import", "p_nom"])
    assert f"{poc:g} MW connection carries the {year} peak" in notes
    assert float(n.loads_t.p_set.sum(axis=1).loc[year].max()) <= poc
    # The excess is shed at the template's own VoLL (owner decision 2026-10-15).
    assert f"{T.solver_config(TID)['voll']:,.0f} EUR/MWh VoLL" in notes
    assert "counterfactual_shed_at_voll" in notes


def test_from_template_binds_the_commercial_block_and_the_finance_inputs(
        client, tmp_path, monkeypatch, tmp_projects_dir):
    from routers import projects as projects_router

    src = tmp_path / TID
    src.mkdir()
    T.BUILDERS[TID]().export_to_netcdf(str(src / "network.nc"))
    T.write_sidecars(src, TID)
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)
    r = client.post(f"/api/projects/from_template/{TID}", params={"name": "dcs"})
    assert r.status_code == 200, r.text
    assert r.json().get("solver_config_issues", []) == []
    cfg = client.get("/api/simulation/solver_config").json()
    want = T.solver_config(TID)
    assert cfg["commercial"] == want["commercial"]
    fin = client.get("/api/simulation/finance").json()
    assert fin["status"] == "ok" and fin["finance"] == want["finance"]
    vf = client.get("/api/simulation/commercial/value_flows").json()
    assert vf["status"] == "ok"


def test_from_template_refuses_a_commercial_block_that_does_not_bind(
        client, tmp_path, monkeypatch, tmp_projects_dir):
    """A sidecar whose commercial block does not bind is stripped and reported,
    never stored unbound."""
    from routers import projects as projects_router

    src = tmp_path / TID
    src.mkdir()
    T.BUILDERS[TID]().export_to_netcdf(str(src / "network.nc"))
    T.write_sidecars(src, TID)
    cfg = json.loads((src / T.SOLVER_FILE).read_text())
    cfg["commercial"]["poc_link"] = "no_such_link"
    (src / T.SOLVER_FILE).write_text(json.dumps(cfg))
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)
    r = client.post(f"/api/projects/from_template/{TID}", params={"name": "dcs_bad"})
    assert r.status_code == 200, r.text
    issues = r.json()["solver_config_issues"]
    assert [i["field"] for i in issues] == ["commercial"]
    assert client.get("/api/simulation/solver_config").json()["commercial"] is None


def test_an_unexpected_bind_error_still_leaves_an_openable_project(
        client, tmp_path, monkeypatch, tmp_projects_dir):
    """Review of #283 (minor 1): an error the bind does not expect is reported
    as an issue, never a 500 after the project exists; the project has its
    metadata and opens."""
    from routers import projects as projects_router
    from services.commercial import binding

    src = tmp_path / TID
    src.mkdir()
    T.BUILDERS[TID]().export_to_netcdf(str(src / "network.nc"))
    T.write_sidecars(src, TID)
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)

    def boom(*a, **k):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(binding, "bind_commercial_on_context", boom)
    r = client.post(f"/api/projects/from_template/{TID}", params={"name": "dcs_err"})
    assert r.status_code == 200, r.text
    issues = r.json()["solver_config_issues"]
    assert [(i["field"], i["code"]) for i in issues] == [("commercial", "commercial_bind_failed")]
    assert "unexpected" not in issues[0]["message"]          # no internals leak
    # The list reads metadata.json (bus_count 0 when it is missing).
    rows = {row["name"]: row for row in client.get("/api/projects/").json()}
    assert rows["dcs_err"]["bus_count"] > 0
    assert client.get("/api/simulation/solver_config").json()["commercial"] is None
    # The finance block is independent of the commercial one and still binds.
    assert client.get("/api/simulation/finance").json()["status"] == "ok"
    assert client.get("/api/projects/dcs_err").status_code == 200


# ── 5. the case computes (must-check 2) ─────────────────────────────────────

@pytest.fixture
def staged_solved(reset_backend, monkeypatch):
    """The template solved multi-period through run_simulation with its own
    solver settings (island rows from its sidecar): (n, cfg, solve state)."""
    import routers.simulation as sim_router
    from routers.projects import _solver_config_from_dict
    from services.adequacy.island_config import SIDECAR_NAME, load_island_config
    from services.pypsa_service import PyPSAService
    from services.solver import island as I
    from services.solver_service import run_simulation
    from tests.conftest import install_network_into_backend

    isl = load_island_config(TPL_DIR / TID / SIDECAR_NAME)
    isl["cuts"] = []
    monkeypatch.setattr(I, "load_project_island", lambda: isl)

    install_network_into_backend(T.BUILDERS[TID]())
    cfg = _solver_config_from_dict(T.solver_config(TID))
    sim_router._state["solver_config"] = cfg
    n = PyPSAService.get_network()
    state: dict = {}
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **k: state.update(k))
    assert status in ("ok", "optimal"), (status, cond)
    return n, cfg, state


def _staged_finance(n, cfg, state, *, site_today: bool):
    import routers.results as R
    from models.finance import FinanceInputs
    from services.finance.engine import run_case
    from services.results import baseline as Bl
    from services.results.finance_case import baseline_plan, build_finance_case

    base = None
    if site_today:
        Bl.clear_cache()
        plan = baseline_plan(n, cfg)
        base = Bl.solve_baseline(n, cfg, plan) if plan is not None and plan.needed else None
    case = build_finance_case(n, cfg, FinanceInputs.model_validate(cfg.finance),
                              result_df=R._result_df, lost_load=state.get("last_lost_load"),
                              baseline=base)
    return n, case, run_case(case)


@pytest.fixture
def staged_case(staged_solved):
    """As the investment-case route does: the solve's lost-load capture, and the
    baseline (the site as it stands today) when the owner keeps existing assets,
    which the staged campus does (owner decision 2026-10-15)."""
    return _staged_finance(*staged_solved, site_today=True)


@pytest.fixture
def staged_case_analytic(staged_solved):
    """The same solve with the analytic counterfactual (no baseline)."""
    return _staged_finance(*staged_solved, site_today=False)


def _built(n, a):
    df = n.generators if a.startswith("genset") else n.storage_units
    return float(df.at[a, "p_nom_opt"])


@pytest.mark.live_solve
def test_the_staged_template_produces_an_investment_case(staged_case):
    n, case, r = staged_case
    built = {a: _built(n, a) for a in CANDIDATES}
    later_built = [a for a in CANDIDATES if not a.endswith("2027") and built[a] > 1e-6]
    assert later_built, built                         # the plan stages
    assets = {a.name: a for a in case.assets}
    # The existing campus: in the case, COD 2027, overnight 0, never refused.
    for a in EXISTING:
        assert a in assets, a
        assert assets[a].overnight_cost == 0.0, a
        assert case.asset_cod.get(a, case.cod) == date(2027, 1, 1), a
    assert case.cod == date(2027, 1, 1)
    # Unbuilt later candidates are not investments.
    for a in CANDIDATES:
        y = int(a.rsplit("_", 1)[1])
        if y > 2027 and built[a] <= 1e-6:
            assert f"not_built:{a}" in case.flags and a not in assets, a
    # Built later candidates are stages with their COD and capex a year before it.
    tl = r.op.tl
    stage_by_year: dict[int, float] = {}
    for a in later_built:
        y = int(a.rsplit("_", 1)[1])
        assert case.asset_cod[a] == date(y, 1, 1), a
        assert assets[a].overnight_cost > 0, a
        stage_by_year[y - 1] = stage_by_year.get(y - 1, 0.0) + assets[a].overnight_cost
    for y, total in stage_by_year.items():
        esc = (1 + case.inputs.escalation["capex"]) ** (y - case.base_year)
        assert r.op.stage_capex[tl.index(y)] == pytest.approx(total * esc, rel=1e-9), y
    assert sum(r.op.stage_capex) == pytest.approx(
        sum(r.op.stage_capex[tl.index(y)] for y in stage_by_year), rel=1e-12)
    # Stage-1 capex is the built 2027 candidates only (the existing campus is 0).
    first = sum(assets[a].overnight_cost for a in CANDIDATES
                if a.endswith("2027") and a in assets)
    assert sum(r.op.capex) == pytest.approx(first, rel=1e-9, abs=1e-6)
    # The headline metrics are computed.
    assert r.op.status == {"operating": "ok", "capex": "ok", "terminal": "ok"}, r.op.reasons
    for k in ("project_pre_tax_npv", "project_pre_tax_irr"):
        assert r.metrics[k] is not None, (k, r.reasons)
    # Compared with the site as it stands today (owner decision 2026-10-15):
    # the existing 4 x 10 MW gensets and the PV stay, and they carry the 2027
    # peak above the 15 MW energized import, so the comparison sheds nothing.
    # The new stages are removed; the island rows they meet are relaxed.
    assert "counterfactual_basis:site_today" in case.flags
    assert "baseline_island_rows_relaxed" in case.flags
    assert not [f for f in r.flags if f.startswith(("counterfactual_shed",
                                                    "counterfactual_exceeds"))], r.flags


@pytest.fixture
def battery_built(monkeypatch):
    """The 2030 battery candidate forced to build 2 MW, the solve dispatching it."""
    orig = T.BUILDERS[TID]

    def forced():
        n = orig()
        n.storage_units.loc["bess_new_2030", "p_nom_min"] = 2.0
        return n
    monkeypatch.setitem(T.BUILDERS, TID, forced)


@pytest.mark.live_solve
def test_a_staged_plan_that_builds_a_battery_gets_an_npv(battery_built, staged_case):
    """Review of #283 (minor 2): a staged case that builds storage is a case:
    the battery is a stage with its COD and capex, and every line, the C5
    degradation pair included, is established, so the NPV is computed.
    (`battery_built` before `staged_case`: the builder is patched first.)"""
    n, case, r = staged_case
    assert float(n.storage_units.at["bess_new_2030", "p_nom_opt"]) == pytest.approx(2.0)
    assets = {a.name: a for a in case.assets}
    assert "bess_new_2030" in assets and assets["bess_new_2030"].overnight_cost > 0
    assert case.asset_cod["bess_new_2030"] == date(2030, 1, 1)
    assert not [ln.key for t in (*case.templates, *case.counterfactual) for ln in t.lines
                if ln.amount is None]
    assert r.metrics["project_pre_tax_npv"] is not None, r.reasons


@pytest.fixture
def connection_45(monkeypatch):
    """The staged template with a 45 MW connection: the 2035 peak (52.5 MW)
    no longer fits, so the grid-only comparison sheds in 2035 only."""
    monkeypatch.setattr(T, "_DC_POC_MW", 45.0)
    monkeypatch.setattr(T, "DC_ENERGIZATION_MW", {2027: 20.0, 2030: 45.0, 2035: 45.0})


@pytest.mark.live_solve
def test_a_multi_period_shed_is_priced_per_period(connection_45, staged_case_analytic):
    """#294 review (item 5): each period's shed line is
    −VoLL · Σ_period w · max(0, load − min(PoC, E)); a period that sheds
    nothing has no line, on the analytic path. (`connection_45` before the case.)"""
    n, case, _r = staged_case_analytic
    voll = T.solver_config(TID)["voll"]
    period = n.snapshots.get_level_values(0)
    load = n.loads_t.p_set.sum(axis=1).to_numpy(float)
    e = n.links_t["eh_energization_mw"]["grid_import"].to_numpy(float)
    cap = np.minimum(float(n.links.at["grid_import", "p_nom"]), e)
    w = n.snapshot_weightings.objective.to_numpy(float)
    shed_lines = {ln.period: ln.amount for t in case.counterfactual for ln in t.lines
                  if ln.key == "counterfactual:shed_at_voll"}
    for y in T.DC_PHASES:
        m = period == y[0]
        want = voll * float((w[m] * np.clip(load[m] - cap[m], 0.0, None)).sum())
        if want > 0:
            assert shed_lines[str(y[0])] == pytest.approx(-want, rel=1e-9), y[0]
        else:
            assert str(y[0]) not in shed_lines, y[0]
    assert set(shed_lines) == {"2035"}

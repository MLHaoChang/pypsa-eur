"""
Fixed cost on every economic surface == what the LP objective actually pays.

PyPSA 1.1.2 charges `periodized_cost = capital_cost (annuitised) + fom_cost`
per unit of extendable capacity (`pypsa/costs.py::periodized_cost`, called by
`Component.periodized_cost` with `fom_cost=static["fom_cost"]`; the
`Component.capital_cost` accessor is the SAME thing with `fom_cost=None`).
Every GUI surface priced its fixed cost off `capital_cost` alone, and the
statistics-based ones inherited the same omission from
`n.statistics.capex()` — which, despite its docstring ("total fixed costs
(investment + fom_cost)"), multiplies capacity by `comp.capital_cost` and
reports FOM separately through `n.statistics.fom()`.

So for any asset with a non-zero `fom_cost`:

    fixed_cost_eur         short by fom_cost x p_nom_opt x years
    cost_breakdown.total   short by the same, so objective_decomposition
                           showed a non-zero "gap" that was really FOM
    LCOE / LCOS / LCOH     too low
    net_profit_eur         too high

The oracle below is the LP itself: `n.objective` minus the hand-computed
variable cost, and PyPSA's own `periodized_cost` accessor, which is the
coefficient the LP used. Nothing here calls `periodized_capital_costs` to
build an expectation.

Every test in this module went red against the pre-fix code; the numbers in
the failure messages were exactly `fom_cost x p_nom_opt`.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

import routers.results as R
import routers.simulation as sim_router
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig

REL = 1e-9

# Generator: annuitised investment typed directly, plus fixed O&M.
GAS_CC = 1000.0     # EUR/MW/yr
GAS_FOM = 200.0     # EUR/MW/yr
GAS_MC = 10.0       # EUR/MWh
ELEC_LOAD = 100.0   # MW, flat
# Electrolyser Link: same shape on the class the LCOH surface covers.
EL_CC = 500.0
EL_FOM = 50.0
EL_MC = 2.0
EL_EFF = 0.7
H2_LOAD = 20.0      # MW_H2, flat
N_SNAPSHOTS = 4

_SOLVED: pypsa.Network | None = None


def _build() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=N_SNAPSHOTS, freq="h"))
    n.add("Carrier", "AC")
    n.add("Carrier", "gas")
    n.add("Carrier", "H2")
    n.add("Bus", "elec", carrier="AC")
    n.add("Bus", "h2", carrier="H2")
    n.add(
        "Generator", "gas", bus="elec", carrier="gas",
        p_nom=0.0, p_nom_extendable=True, p_nom_max=10_000.0,
        marginal_cost=GAS_MC, capital_cost=GAS_CC, fom_cost=GAS_FOM,
    )
    n.add(
        "Link", "electrolyzer", bus0="elec", bus1="h2", carrier="H2",
        efficiency=EL_EFF, p_nom=0.0, p_nom_extendable=True, p_nom_max=10_000.0,
        marginal_cost=EL_MC, capital_cost=EL_CC, fom_cost=EL_FOM,
    )
    n.add("Load", "demand", bus="elec", p_set=ELEC_LOAD)
    n.add("Load", "demand_h2", bus="h2", p_set=H2_LOAD)
    return n


def _solved() -> pypsa.Network:
    """Solve once per process; HiGHS on four snapshots is instantaneous."""
    global _SOLVED
    if _SOLVED is None:
        n = _build()
        n.optimize(solver_name="highs")
        _SOLVED = n
    return _SOLVED


@pytest.fixture()
def solved(reset_backend) -> pypsa.Network:
    """Install the solved network AFTER conftest's autouse reset."""
    n = _solved()
    ctx = PyPSAService._ensure_active()
    ctx.network = n
    sim_router._state["solver_config"] = SolverConfig()
    return n


def _live(n, accessor, attr, source="lopf"):
    acc = getattr(n, accessor, None)
    return getattr(acc, attr, None) if acc is not None else None


# ── The oracle: the LP's own fixed-cost term ─────────────────────────────

def _variable_cost(n) -> float:
    w = n.snapshot_weightings["objective"]
    gas = float((n.generators_t.p["gas"] * w).sum()) * GAS_MC
    el = float((n.links_t.p0["electrolyzer"].abs() * w).sum()) * EL_MC
    return gas + el


def _lp_fixed_term(n) -> float:
    """Everything in the objective that is not variable cost."""
    return float(n.objective) + float(n.objective_constant or 0.0) - _variable_cost(n)


def _gas_fixed(n) -> float:
    return (GAS_CC + GAS_FOM) * float(n.generators.at["gas", "p_nom_opt"])


def _el_fixed(n) -> float:
    return (EL_CC + EL_FOM) * float(n.links.at["electrolyzer", "p_nom_opt"])


# ── Upstream contract: what PyPSA 1.1.2 actually does ───────────────────

@pytest.mark.live_solve
def test_pypsa_charges_fom_in_the_objective_but_not_in_statistics_capex(solved):
    """
    The two facts every fix below rests on, measured rather than read off a
    docstring. If PyPSA ever folds FOM into `statistics.capex` (as its own
    docstring already claims) this fails and cost_breakdown must stop adding
    it — a loud upstream-drift signal, per the trustworthy-numbers spec.
    """
    n = solved
    assert n.generators.at["gas", "p_nom_opt"] > 0
    assert n.links.at["electrolyzer", "p_nom_opt"] > 0
    assert _lp_fixed_term(n) == pytest.approx(_gas_fixed(n) + _el_fixed(n), rel=REL)
    # The LP coefficient itself.
    assert float(n.c["Generator"].periodized_cost.sel(name="gas")) == pytest.approx(GAS_CC + GAS_FOM)
    assert float(n.c["Generator"].capital_cost.loc["gas"]) == pytest.approx(GAS_CC)
    capex = n.statistics.capex()
    fom = n.statistics.fom()
    gas_capex = float(capex.loc[("Generator", "gas")])
    gas_fom = float(fom.loc[("Generator", "gas")])
    assert gas_capex == pytest.approx(GAS_CC * n.generators.at["gas", "p_nom_opt"], rel=REL)
    assert gas_fom == pytest.approx(GAS_FOM * n.generators.at["gas", "p_nom_opt"], rel=REL)


# ── /results/asset_economics ─────────────────────────────────────────────

@pytest.mark.live_solve
def test_asset_economics_fixed_cost_is_the_objectives_fixed_cost_term(solved):
    from services.results.asset_economics import compute_asset_economics

    n = solved
    payload = compute_asset_economics(n, SolverConfig(), result_df=_live)
    assert payload["capital_costs_available"] is True
    gas = next(r for r in payload["generators"] if r["name"] == "gas")
    el = next(r for r in payload["links"] if r["name"] == "electrolyzer")

    assert gas["fixed_cost_eur"] == pytest.approx(_gas_fixed(n), rel=REL)
    assert el["fixed_cost_eur"] == pytest.approx(_el_fixed(n), rel=REL)
    assert gas["fixed_cost_eur"] + el["fixed_cost_eur"] == pytest.approx(
        _lp_fixed_term(n), rel=REL,
    )
    # FOM stays broken out as its own line, on the same (horizon) basis.
    assert gas["fom_cost_eur"] == pytest.approx(
        GAS_FOM * n.generators.at["gas", "p_nom_opt"], rel=REL)
    assert el["fom_cost_eur"] == pytest.approx(
        EL_FOM * n.links.at["electrolyzer", "p_nom_opt"], rel=REL)


@pytest.mark.live_solve
def test_asset_economics_lcoe_and_net_profit_carry_the_fom(solved):
    from services.results.asset_economics import compute_asset_economics

    n = solved
    payload = compute_asset_economics(n, SolverConfig(), result_df=_live)
    gas = next(r for r in payload["generators"] if r["name"] == "gas")
    assert gas["lcoe_eur_per_mwh"] == pytest.approx(
        (_gas_fixed(n) + gas["vom_cost_eur"]) / gas["energy_mwh"], rel=REL)
    assert gas["net_profit_eur"] == pytest.approx(
        gas["revenue_eur"] - _gas_fixed(n) - gas["vom_cost_eur"], rel=REL)


# ── /results/cost_breakdown + /results/objective_decomposition ───────────

@pytest.mark.live_solve
def test_cost_breakdown_total_reconciles_to_the_lp_objective(solved):
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    n = solved
    cb = compute_cost_breakdown(n, SolverConfig())
    assert cb is not None
    lp_total = float(n.objective) + float(n.objective_constant or 0.0)
    assert cb["total"] == pytest.approx(lp_total, rel=REL)
    assert cb["capex"] == pytest.approx(_lp_fixed_term(n), rel=REL)
    # FOM is broken out, top level and per class.
    expected_fom = (GAS_FOM * n.generators.at["gas", "p_nom_opt"]
                    + EL_FOM * n.links.at["electrolyzer", "p_nom_opt"])
    assert cb["fom"] == pytest.approx(expected_fom, rel=REL)
    by_comp = {r["component"]: r for r in cb["by_component"]}
    assert by_comp["Generator"]["capex"] == pytest.approx(_gas_fixed(n), rel=REL)
    assert by_comp["Generator"]["fom"] == pytest.approx(
        GAS_FOM * n.generators.at["gas", "p_nom_opt"], rel=REL)
    assert by_comp["Link"]["capex"] == pytest.approx(_el_fixed(n), rel=REL)
    # Everything here is new capacity, so the expansion figure is the same.
    assert cb["capex_expansion"] == pytest.approx(_lp_fixed_term(n), rel=REL)

    decomp = compute_objective_decomposition(n, cb)
    assert decomp["lp_total"] == pytest.approx(lp_total, rel=REL)
    assert abs(decomp["gap_eur"]) <= 1e-9 * lp_total


@pytest.mark.live_solve
def test_horizon_system_cost_reconciles_to_the_lp_objective(solved):
    from services.cost_totals import horizon_system_cost

    n = solved
    lp_total = float(n.objective) + float(n.objective_constant or 0.0)
    assert horizon_system_cost(n, SolverConfig()) == pytest.approx(lp_total, rel=REL)


# ── The other surfaces agree with asset_economics ────────────────────────

@pytest.mark.live_solve
def test_lcoh_capex_matches_the_links_fixed_cost(solved):
    n = solved
    payload = R.get_lcoh()
    row = next(r for r in payload["rows"] if r["name"] == "electrolyzer")
    assert row["capex_eur_per_year"] == pytest.approx(_el_fixed(n), rel=REL)
    assert row["fom_eur_per_year"] == pytest.approx(
        EL_FOM * n.links.at["electrolyzer", "p_nom_opt"], rel=REL)
    assert row["lcoh_eur_per_mwh_h2"] == pytest.approx(
        (row["capex_eur_per_year"] + row["vom_cost_eur"] + row["electricity_cost_eur"])
        / row["h2_produced_mwh"], rel=REL)
    assert payload["total"]["capex_eur_per_year"] == pytest.approx(_el_fixed(n), rel=REL)
    assert payload["total"]["fom_eur_per_year"] == pytest.approx(row["fom_eur_per_year"], rel=REL)


@pytest.mark.live_solve
def test_economics_by_carrier_capex_matches_the_fixed_cost(solved):
    n = solved
    ebc = R.get_economics_by_carrier()
    assert ebc["by_carrier"]["gas"]["capex_meur"]["total"] * 1e6 == pytest.approx(
        _gas_fixed(n), rel=1e-6)
    assert ebc["by_carrier"]["h2"]["capex_meur"]["total"] * 1e6 == pytest.approx(
        _el_fixed(n), rel=1e-6)


@pytest.mark.live_solve
def test_compare_capacity_capex_matches_the_fixed_cost(solved):
    from services.compare.capacity import _compute_capacity_summary

    n = solved
    summary = _compute_capacity_summary(n, [], False, True, cfg=SolverConfig())
    assert summary.capex_meur_by_carrier["gas"].total * 1e6 == pytest.approx(
        _gas_fixed(n), rel=1e-6)
    # p_nom was 0, so every MW is new: the new-build figure is the whole thing.
    assert summary.new_capex_meur_by_carrier["gas"].total * 1e6 == pytest.approx(
        _gas_fixed(n), rel=1e-6)


@pytest.mark.live_solve
def test_asset_costs_carry_the_lp_coefficient(solved):
    n = solved
    costs = sim_router.asset_costs()
    gas = costs["generators"]["gas"]
    assert gas["capital_cost"] == pytest.approx(GAS_CC)
    assert gas["fom_cost"] == pytest.approx(GAS_FOM)
    assert gas["fixed_cost"] == pytest.approx(
        float(n.c["Generator"].periodized_cost.sel(name="gas")), rel=REL)
    el = costs["links"]["electrolyzer"]
    assert el["fixed_cost"] == pytest.approx(
        float(n.c["Link"].periodized_cost.sel(name="electrolyzer")), rel=REL)


@pytest.mark.live_solve
def test_asset_detail_capex_and_fixed_cost_match_asset_economics(solved):
    import routers.asset_results as AR

    n = solved
    detail = AR.get_asset_results(
        component_class="Generator", name="gas", category="economics",
        source="lopf", from_=None, to=None, period=None,
        mode="chronological", metrics="",
    )
    scalars = detail["scalars"]
    assert scalars["fixed_cost_eur"] == pytest.approx(_gas_fixed(n), rel=1e-6)
    detail = AR.get_asset_results(
        component_class="Generator", name="gas", category="capacity",
        source="lopf", from_=None, to=None, period=None,
        mode="chronological", metrics="",
    )
    assert detail["scalars"]["capex_annual"] == pytest.approx(_gas_fixed(n), rel=1e-6)


@pytest.mark.live_solve
def test_statistics_surface_carries_fom_so_fixed_cost_can_be_reconciled(solved):
    """
    /results/statistics is a raw `n.statistics()` pass-through, and PyPSA's
    "Capital Expenditure" column is investment-only. The surface gains a
    "Fixed O&M" column so the fixed cost the objective paid is recoverable
    from it; the two columns together equal asset_economics's fixed cost.
    """
    n = solved
    rows = R.get_statistics()
    gas = next(r for r in rows if r.get("level_0") == "Generator" and r.get("level_1") == "gas")
    assert gas["Capital Expenditure"] + gas["Fixed O&M"] == pytest.approx(_gas_fixed(n), rel=REL)


# ── Hand-built networks: no solver, numbers written down ────────────────

def _flat_network() -> pypsa.Network:
    """
    G: capital_cost 1000, fom_cost 20, p_nom_opt 100, p = [50, 50] at 40 EUR.
        fixed = (1000 + 20) x 100 = 102_000   fom = 20 x 100 = 2_000
        revenue = 4_000   vom = 1_000   energy = 100
        lcoe = (102_000 + 1_000) / 100 = 1_030
        net  = 4_000 - 102_000 - 1_000 = -99_000
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "elec")
    n.add("Generator", "G", bus="elec", p_nom=100.0,
          marginal_cost=10.0, capital_cost=1000.0, fom_cost=20.0)
    sns = n.snapshots
    n.generators["p_nom_opt"] = 100.0
    n.generators_t["p"] = pd.DataFrame({"G": [50.0, 50.0]}, index=sns)
    n.buses_t["marginal_price"] = pd.DataFrame({"elec": [40.0, 40.0]}, index=sns)
    n._objective = 0.0
    return n


def _multi_period_network() -> pypsa.Network:
    """Same generator over two periods weighted 5 and 10 years."""
    n = pypsa.Network()
    hours = pd.date_range("2030-01-01", periods=2, freq="h")
    mi = pd.MultiIndex.from_product([[2030, 2040], hours], names=["period", "timestep"])
    mi.name = "snapshot"
    n.set_snapshots(mi)
    n.investment_periods = [2030, 2040]
    n.investment_period_weightings["years"] = [5, 10]
    n.add("Bus", "elec")
    n.add("Generator", "G", bus="elec", p_nom=100.0,
          marginal_cost=10.0, capital_cost=1000.0, fom_cost=20.0)
    n.generators["p_nom_opt"] = 100.0
    n.generators_t["p"] = pd.DataFrame({"G": [50.0] * len(mi)}, index=mi)
    n.buses_t["marginal_price"] = pd.DataFrame({"elec": [40.0] * len(mi)}, index=mi)
    n._objective = 0.0
    return n


def test_compute_asset_economics_adds_fom_to_fixed_cost_on_a_flat_network():
    from services.results.asset_economics import compute_asset_economics

    payload = compute_asset_economics(_flat_network(), SolverConfig(), result_df=_live)
    g = payload["generators"][0]
    assert g["fixed_cost_eur"] == pytest.approx(102_000.0)
    assert g["fom_cost_eur"] == pytest.approx(2_000.0)
    assert g["lcoe_eur_per_mwh"] == pytest.approx(1_030.0)
    assert g["net_profit_eur"] == pytest.approx(-99_000.0)


def test_compute_asset_economics_scales_fom_with_the_horizon_like_fixed_cost():
    """
    `fom_cost_eur` is published as the FOM component OF `fixed_cost_eur`, so
    it must be on the same horizon basis: 15 years here, split 5 / 10.
    """
    from services.results.asset_economics import compute_asset_economics

    payload = compute_asset_economics(_multi_period_network(), SolverConfig(), result_df=_live)
    g = payload["generators"][0]
    assert g["fixed_cost_eur"] == pytest.approx(102_000.0 * 15)
    assert g["fom_cost_eur"] == pytest.approx(2_000.0 * 15)
    by_p = {e["period"]: e for e in g["by_period"]}
    assert by_p[2030]["fixed_cost_eur"] == pytest.approx(102_000.0 * 5)
    assert by_p[2030]["fom_cost_eur"] == pytest.approx(2_000.0 * 5)
    assert by_p[2040]["fixed_cost_eur"] == pytest.approx(102_000.0 * 10)
    assert by_p[2040]["fom_cost_eur"] == pytest.approx(2_000.0 * 10)
    assert sum(e["fom_cost_eur"] for e in g["by_period"]) == pytest.approx(g["fom_cost_eur"])


def test_periodized_capital_costs_reports_fom_and_the_lp_fixed_rate():
    from services.solver_service import periodized_capital_costs

    n = _flat_network()
    entry = periodized_capital_costs(n, SolverConfig())["generators"]["G"]
    assert entry["capital_cost"] == pytest.approx(1000.0)
    assert entry["fom_cost"] == pytest.approx(20.0)
    assert entry["fixed_cost"] == pytest.approx(1020.0)


_SOLVED_MP: pypsa.Network | None = None
MP_PERIODS = (2030, 2040)
MP_YEARS = (5.0, 10.0)


def _solved_multi_period() -> pypsa.Network:
    """
    The generator over two investment periods weighted 5 and 10 years.

    `investment_period_weightings.objective` is set EQUAL to `years` so the LP
    objective and the years-weighted reporting basis coincide and the
    objective can serve as the oracle for the horizon total.
    """
    global _SOLVED_MP
    if _SOLVED_MP is None:
        n = pypsa.Network()
        hours = pd.date_range("2030-01-01", periods=N_SNAPSHOTS, freq="h")
        mi = pd.MultiIndex.from_product([list(MP_PERIODS), hours], names=["period", "timestep"])
        mi.name = "snapshot"
        n.set_snapshots(mi)
        n.investment_periods = list(MP_PERIODS)
        n.investment_period_weightings["years"] = list(MP_YEARS)
        n.investment_period_weightings["objective"] = list(MP_YEARS)
        n.add("Carrier", "AC")
        n.add("Carrier", "gas")
        n.add("Bus", "elec", carrier="AC")
        n.add(
            "Generator", "gas", bus="elec", carrier="gas",
            p_nom=0.0, p_nom_extendable=True, p_nom_max=10_000.0,
            marginal_cost=GAS_MC, capital_cost=GAS_CC, fom_cost=GAS_FOM,
            build_year=MP_PERIODS[0], lifetime=100.0,
        )
        n.add("Load", "demand", bus="elec", p_set=ELEC_LOAD)
        n.optimize(solver_name="highs", multi_investment_periods=True)
        _SOLVED_MP = n
    return _SOLVED_MP


@pytest.mark.live_solve
def test_cost_breakdown_folds_fom_into_every_period_and_breaks_it_out(reset_backend):
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.cost_totals import horizon_system_cost

    n = _solved_multi_period()
    cfg = SolverConfig(multi_investment_periods=True, investment_periods=list(MP_PERIODS))
    p_nom_opt = float(n.generators.at["gas", "p_nom_opt"])
    assert p_nom_opt > 0
    lp_total = float(n.objective) + float(n.objective_constant or 0.0)

    cb = compute_cost_breakdown(n, cfg)
    assert cb is not None
    assert cb["total"] == pytest.approx(lp_total, rel=REL)
    assert horizon_system_cost(n, cfg) == pytest.approx(lp_total, rel=REL)
    horizon = sum(MP_YEARS)
    assert cb["capex"] == pytest.approx((GAS_CC + GAS_FOM) * p_nom_opt * horizon, rel=REL)
    assert cb["fom"] == pytest.approx(GAS_FOM * p_nom_opt * horizon, rel=REL)
    gen = next(r for r in cb["by_component"] if r["component"] == "Generator")
    assert gen["fom"] == pytest.approx(GAS_FOM * p_nom_opt * horizon, rel=REL)
    by_p = {p["period"]: p for p in cb["by_period"]}
    for period, years in zip(MP_PERIODS, MP_YEARS):
        assert by_p[period]["capex"] == pytest.approx((GAS_CC + GAS_FOM) * p_nom_opt * years, rel=REL)
        assert by_p[period]["fom"] == pytest.approx(GAS_FOM * p_nom_opt * years, rel=REL)
        comp = next(r for r in by_p[period]["by_component"] if r["component"] == "Generator")
        assert comp["fom"] == pytest.approx(GAS_FOM * p_nom_opt * years, rel=REL)
    carrier_row = next(r for r in cb["by_carrier"] if r["component"] == "Generator")
    assert carrier_row["fom"] == pytest.approx(GAS_FOM * p_nom_opt * horizon, rel=REL)

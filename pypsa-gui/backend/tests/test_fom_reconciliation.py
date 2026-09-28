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

Three follow-ups live here too:

* **FOM units.** PyPSA adds `fom_cost` UNSCALED, per modelled horizon, while
  the GUI asks for it in EUR/MW/yr. The GUI's periodized-cost fill now scales
  it by `n.nyears` around the solve and every report, so a typed annual FOM
  costs `fom × nyears` per period. The networks below are weighted so
  `nyears = 0.5`, which makes an unscaled FOM visibly wrong.
* **New-capacity FOM** in `cost_breakdown.capex_expansion` counts only the
  periods in which the asset is active.
* **The objective gap.** `objective_decomposition` explains
  `lp_total − cost_breakdown.total` with named bridge terms and a residual
  that is ~0 on a plain solve.

The oracle is the LP itself: `n.objective` (+ `objective_constant`) minus
the hand-computed variable cost. Nothing here calls `periodized_capital_costs`
to build an expectation.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

import routers.results as R
import routers.simulation as sim_router
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig, with_periodized_cost_defaults

REL = 1e-9

# Generator: annuitised investment typed directly, plus annual fixed O&M.
GAS_CC = 1000.0     # EUR/MW/yr — annualised investment, typed directly
GAS_FOM = 200.0     # EUR/MW/yr — annual, as the GUI asks for it
GAS_MC = 10.0       # EUR/MWh
ELEC_LOAD = 100.0   # MW, flat
# Electrolyser Link: same shape on the class the LCOH surface covers.
EL_CC = 500.0
EL_FOM = 50.0
EL_MC = 2.0
EL_EFF = 0.7
H2_LOAD = 20.0      # MW_H2, flat
N_SNAPSHOTS = 4
# Each snapshot stands for 1095 h, so the model covers half a year:
# nyears = 4 x 1095 / 8760 = 0.5, and an annual FOM costs half of itself.
SNAPSHOT_WEIGHT = 1095.0
NYEARS = N_SNAPSHOTS * SNAPSHOT_WEIGHT / 8760.0

_SOLVED: pypsa.Network | None = None


def _gui_solve(n, cfg=None, **kwargs) -> None:
    """Solve the way `run_simulation` does: inside the periodized-cost fill."""
    with with_periodized_cost_defaults(n, cfg or SolverConfig()):
        n.optimize(solver_name="highs", **kwargs)


def _build() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=N_SNAPSHOTS, freq="h"))
    n.snapshot_weightings.loc[:, :] = SNAPSHOT_WEIGHT
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
        _gui_solve(n)
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


def _lp_total(n) -> float:
    return float(n.objective) + float(n.objective_constant or 0.0)


def _lp_fixed_term(n) -> float:
    """Everything in the objective that is not variable cost."""
    return _lp_total(n) - _variable_cost(n)


def _gas_fixed(n) -> float:
    return (GAS_CC + GAS_FOM) * NYEARS * float(n.generators.at["gas", "p_nom_opt"])


def _gas_fom(n) -> float:
    return GAS_FOM * NYEARS * float(n.generators.at["gas", "p_nom_opt"])


def _el_fixed(n) -> float:
    return (EL_CC + EL_FOM) * NYEARS * float(n.links.at["electrolyzer", "p_nom_opt"])


def _el_fom(n) -> float:
    return EL_FOM * NYEARS * float(n.links.at["electrolyzer", "p_nom_opt"])


# ── Upstream contract: what PyPSA 1.1.2 actually does ───────────────────

@pytest.mark.live_solve
def test_pypsa_charges_fom_unscaled_and_statistics_capex_leaves_it_out(reset_backend):
    """
    The facts every fix below rests on, measured on a RAW PyPSA solve (no GUI
    fill): the objective adds `fom_cost` per modelled horizon without scaling
    it by `nyears`, and `statistics.capex` is investment-only. If PyPSA ever
    changes either, this fails first and the GUI's compensation must change.
    """
    n = _build()
    n.optimize(solver_name="highs")
    p = float(n.generators.at["gas", "p_nom_opt"])
    q = float(n.links.at["electrolyzer", "p_nom_opt"])
    assert p > 0 and q > 0
    assert NYEARS == pytest.approx(0.5)
    # Unscaled: the full annual FOM for half a year of operation.
    assert _lp_fixed_term(n) == pytest.approx(
        (GAS_CC + GAS_FOM) * p + (EL_CC + EL_FOM) * q, rel=REL)
    assert float(n.c["Generator"].periodized_cost.sel(name="gas")) == pytest.approx(GAS_CC + GAS_FOM)
    assert float(n.c["Generator"].capital_cost.loc["gas"]) == pytest.approx(GAS_CC)
    assert float(n.statistics.capex().loc[("Generator", "gas")]) == pytest.approx(GAS_CC * p, rel=REL)
    assert float(n.statistics.fom().loc[("Generator", "gas")]) == pytest.approx(GAS_FOM * p, rel=REL)


# ── FOM units: annual in, per-horizon charged ────────────────────────────

@pytest.mark.live_solve
def test_a_gui_solve_charges_annual_fom_for_the_modelled_share_of_a_year(solved):
    n = solved
    assert _lp_fixed_term(n) == pytest.approx(_gas_fixed(n) + _el_fixed(n), rel=REL)


@pytest.mark.live_solve
def test_the_fill_restores_the_typed_costs_after_the_solve(solved):
    assert float(solved.generators.at["gas", "fom_cost"]) == GAS_FOM
    assert float(solved.links.at["electrolyzer", "fom_cost"]) == EL_FOM
    assert float(solved.generators.at["gas", "capital_cost"]) == GAS_CC
    assert float(solved.links.at["electrolyzer", "capital_cost"]) == EL_CC


def test_nested_fills_scale_fom_once_and_revert_exactly():
    from services.solver_service import fom_horizon_factor, fom_is_scaled

    n = _build()
    assert fom_horizon_factor(n) == pytest.approx(NYEARS)
    cfg = SolverConfig()
    with with_periodized_cost_defaults(n, cfg):
        assert fom_is_scaled(n)
        assert float(n.generators.at["gas", "fom_cost"]) == pytest.approx(GAS_FOM * NYEARS)
        assert float(n.generators.at["gas", "capital_cost"]) == pytest.approx(GAS_CC * NYEARS)
        with with_periodized_cost_defaults(n, cfg, for_back_calculation=True):
            assert float(n.generators.at["gas", "fom_cost"]) == pytest.approx(GAS_FOM * NYEARS)
        # The inner fill must not un-scale the outer one on its way out.
        assert fom_is_scaled(n)
        assert float(n.generators.at["gas", "fom_cost"]) == pytest.approx(GAS_FOM * NYEARS)
        assert float(n.generators.at["gas", "capital_cost"]) == pytest.approx(GAS_CC * NYEARS)
    assert not fom_is_scaled(n)
    assert float(n.generators.at["gas", "fom_cost"]) == GAS_FOM
    assert float(n.generators.at["gas", "capital_cost"]) == GAS_CC


def test_a_full_year_model_leaves_fom_untouched():
    from services.solver_service import fom_horizon_factor

    n = _build()
    n.snapshot_weightings.loc[:, :] = 8760.0 / N_SNAPSHOTS
    assert fom_horizon_factor(n) == pytest.approx(1.0)
    with with_periodized_cost_defaults(n, SolverConfig()):
        assert float(n.generators.at["gas", "fom_cost"]) == GAS_FOM
        assert float(n.generators.at["gas", "capital_cost"]) == GAS_CC


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
    # FOM stays broken out as its own line, on the same basis.
    assert gas["fom_cost_eur"] == pytest.approx(_gas_fom(n), rel=REL)
    assert el["fom_cost_eur"] == pytest.approx(_el_fom(n), rel=REL)


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
    assert cb["total"] == pytest.approx(_lp_total(n), rel=REL)
    assert cb["capex"] == pytest.approx(_lp_fixed_term(n), rel=REL)
    assert cb["fom"] == pytest.approx(_gas_fom(n) + _el_fom(n), rel=REL)
    by_comp = {r["component"]: r for r in cb["by_component"]}
    assert by_comp["Generator"]["capex"] == pytest.approx(_gas_fixed(n), rel=REL)
    assert by_comp["Generator"]["fom"] == pytest.approx(_gas_fom(n), rel=REL)
    assert by_comp["Link"]["capex"] == pytest.approx(_el_fixed(n), rel=REL)
    # Everything here is new capacity, so the expansion figure is the same.
    assert cb["capex_expansion"] == pytest.approx(_lp_fixed_term(n), rel=REL)

    decomp = compute_objective_decomposition(n, cb, SolverConfig())
    assert decomp["lp_total"] == pytest.approx(_lp_total(n), rel=REL)
    assert abs(decomp["gap_eur"]) <= 1e-9 * _lp_total(n)
    assert decomp["nonextendable_fixed_cost_eur"] == pytest.approx(0.0, abs=1e-9)
    assert abs(decomp["residual_gap_eur"]) <= 1e-9 * _lp_total(n)


@pytest.mark.live_solve
def test_horizon_system_cost_reconciles_to_the_lp_objective(solved):
    from services.cost_totals import horizon_system_cost

    assert horizon_system_cost(solved, SolverConfig()) == pytest.approx(_lp_total(solved), rel=REL)


# ── The other surfaces agree with asset_economics ────────────────────────

@pytest.mark.live_solve
def test_lcoh_capex_matches_the_links_fixed_cost(solved):
    n = solved
    payload = R.get_lcoh()
    row = next(r for r in payload["rows"] if r["name"] == "electrolyzer")
    assert row["capex_eur_per_year"] == pytest.approx(_el_fixed(n), rel=REL)
    assert row["fom_eur_per_year"] == pytest.approx(_el_fom(n), rel=REL)
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
    costs = sim_router.asset_costs()
    gas = costs["generators"]["gas"]
    assert gas["capital_cost"] == pytest.approx(GAS_CC * NYEARS)
    assert gas["fom_cost"] == pytest.approx(GAS_FOM * NYEARS)
    assert gas["fom_cost_annual"] == pytest.approx(GAS_FOM)
    assert gas["fixed_cost"] == pytest.approx((GAS_CC + GAS_FOM) * NYEARS)
    el = costs["links"]["electrolyzer"]
    assert el["fixed_cost"] == pytest.approx((EL_CC + EL_FOM) * NYEARS)


@pytest.mark.live_solve
def test_asset_detail_capex_and_fixed_cost_match_asset_economics(solved):
    import routers.asset_results as AR

    n = solved
    detail = AR.get_asset_results(
        component_class="Generator", name="gas", category="economics",
        source="lopf", from_=None, to=None, period=None,
        mode="chronological", metrics="",
    )
    assert detail["scalars"]["fixed_cost_eur"] == pytest.approx(_gas_fixed(n), rel=1e-6)
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

# Two unit-weighted hourly snapshots: the model covers 2/8760 of a year, so
# an annual FOM of 20 EUR/MW/yr is charged 20 x 2/8760 per MW, and an
# annualised capital_cost of 1000 EUR/MW/yr is charged 1000 x 2/8760.
FLAT_SHARE = 2 / 8760.0
FLAT_FOM_PER_HORIZON = 20.0 * FLAT_SHARE
FLAT_CC_PER_HORIZON = 1000.0 * FLAT_SHARE
FLAT_CAPEX = FLAT_CC_PER_HORIZON * 100


def _flat_network() -> pypsa.Network:
    """
    G: capital_cost 1000/yr, fom_cost 20/yr, p_nom_opt 100, p = [50, 50] at 40 EUR.
        fom     = 20 x 2/8760 x 100
        fixed   = 1000 x 2/8760 x 100 + fom
        revenue = 4_000   vom = 1_000   energy = 100
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
    fom = FLAT_FOM_PER_HORIZON * 100
    assert g["fixed_cost_eur"] == pytest.approx(FLAT_CAPEX + fom)
    assert g["fom_cost_eur"] == pytest.approx(fom)
    assert g["lcoe_eur_per_mwh"] == pytest.approx((FLAT_CAPEX + fom + 1_000.0) / 100)
    assert g["net_profit_eur"] == pytest.approx(4_000.0 - FLAT_CAPEX - fom - 1_000.0)


def test_compute_asset_economics_scales_fom_with_the_horizon_like_fixed_cost():
    """
    `fom_cost_eur` is published as the FOM component OF `fixed_cost_eur`, so
    it must be on the same horizon basis: 15 years here, split 5 / 10.
    """
    from services.results.asset_economics import compute_asset_economics

    payload = compute_asset_economics(_multi_period_network(), SolverConfig(), result_df=_live)
    g = payload["generators"][0]
    fom = FLAT_FOM_PER_HORIZON * 100
    fixed = FLAT_CAPEX + fom
    assert g["fixed_cost_eur"] == pytest.approx(fixed * 15)
    assert g["fom_cost_eur"] == pytest.approx(fom * 15)
    by_p = {e["period"]: e for e in g["by_period"]}
    assert by_p[2030]["fixed_cost_eur"] == pytest.approx(fixed * 5)
    assert by_p[2030]["fom_cost_eur"] == pytest.approx(fom * 5)
    assert by_p[2040]["fixed_cost_eur"] == pytest.approx(fixed * 10)
    assert by_p[2040]["fom_cost_eur"] == pytest.approx(fom * 10)
    assert sum(e["fom_cost_eur"] for e in g["by_period"]) == pytest.approx(g["fom_cost_eur"])


def test_periodized_capital_costs_reports_fom_and_the_lp_fixed_rate():
    from services.solver_service import periodized_capital_costs

    n = _flat_network()
    entry = periodized_capital_costs(n, SolverConfig())["generators"]["G"]
    assert entry["capital_cost"] == pytest.approx(FLAT_CC_PER_HORIZON)
    assert entry["fom_cost"] == pytest.approx(FLAT_FOM_PER_HORIZON)
    assert entry["fom_cost_annual"] == pytest.approx(20.0)
    assert entry["fixed_cost"] == pytest.approx(FLAT_CC_PER_HORIZON + FLAT_FOM_PER_HORIZON)
    # The resolver's fill must not leave either column scaled.
    assert float(n.generators.at["G", "fom_cost"]) == 20.0
    assert float(n.generators.at["G", "capital_cost"]) == 1000.0


# ── Multi-period: periods, activity, and the objective bridge ────────────

MP_PERIODS = (2030, 2040)
MP_YEARS = (10.0, 10.0)
# Objective weights DIFFERENT from years, as under auto-discount, so the
# period-weighting bridge term is exercised for real.
MP_OBJECTIVE = (7.0, 4.0)
MP_WEIGHT = 2190.0              # 4 snapshots x 2190 h = one full year per period
MP_NYEARS = 4 * MP_WEIGHT / 8760.0
_SOLVED_MP: pypsa.Network | None = None


def _solved_multi_period() -> pypsa.Network:
    """
    Two periods; `old` is extendable and retires after 2030, `new` is
    extendable and only exists from 2040, `nonext` is fixed capacity that
    retires after 2030. Snapshot weights make `nyears = 1` per period, so an
    annual FOM is charged as typed and every figure is easy to write down.
    """
    global _SOLVED_MP
    if _SOLVED_MP is None:
        n = pypsa.Network()
        hours = pd.date_range("2030-01-01", periods=4, freq="h")
        mi = pd.MultiIndex.from_product([list(MP_PERIODS), hours], names=["period", "timestep"])
        mi.name = "snapshot"
        n.set_snapshots(mi)
        n.snapshot_weightings.loc[:, :] = MP_WEIGHT
        n.investment_periods = list(MP_PERIODS)
        n.investment_period_weightings["years"] = list(MP_YEARS)
        n.investment_period_weightings["objective"] = list(MP_OBJECTIVE)
        n.add("Carrier", "AC")
        n.add("Bus", "elec", carrier="AC")
        n.add("Generator", "old", bus="elec", p_nom=20.0, p_nom_extendable=True,
              marginal_cost=1.0, capital_cost=1000.0, fom_cost=100.0,
              build_year=2030, lifetime=10.0)
        n.add("Generator", "new", bus="elec", p_nom=0.0, p_nom_extendable=True,
              marginal_cost=5.0, capital_cost=3000.0, fom_cost=150.0,
              build_year=2040, lifetime=30.0)
        n.add("Generator", "nonext", bus="elec", p_nom=5.0,
              marginal_cost=2.0, capital_cost=700.0, fom_cost=10.0,
              build_year=2030, lifetime=10.0)
        n.add("Load", "demand", bus="elec", p_set=ELEC_LOAD)
        _gui_solve(n, SolverConfig(multi_investment_periods=True,
                                   investment_periods=list(MP_PERIODS)),
                   multi_investment_periods=True)
        _SOLVED_MP = n
    return _SOLVED_MP


def _mp_cfg() -> SolverConfig:
    return SolverConfig(multi_investment_periods=True, investment_periods=list(MP_PERIODS))


@pytest.mark.live_solve
def test_the_multi_period_network_retires_what_it_should(reset_backend):
    n = _solved_multi_period()
    assert MP_NYEARS == pytest.approx(1.0)
    assert n.generators.at["old", "p_nom_opt"] > 20.0
    assert n.generators.at["new", "p_nom_opt"] > 0.0
    assert not bool(n.get_active_assets("Generator", 2040)["old"])
    assert not bool(n.get_active_assets("Generator", 2030)["new"])


@pytest.mark.live_solve
def test_new_capacity_fom_counts_only_active_periods(reset_backend):
    """
    `old` expands above its p_nom of 20 but only operates in 2030, so its new
    capacity's FOM is charged for 2030's 10 years, not the whole 20-year
    horizon. `new` exists only in 2040 — same rule the other way round.
    """
    from services.results.cost_breakdown import compute_cost_breakdown

    n = _solved_multi_period()
    cb = compute_cost_breakdown(n, _mp_cfg())
    old_delta = float(n.generators.at["old", "p_nom_opt"]) - 20.0
    new_delta = float(n.generators.at["new", "p_nom_opt"])
    assert old_delta > 0 and new_delta > 0
    expected = ((1000.0 + 100.0) * old_delta * MP_YEARS[0]
                + (3000.0 + 150.0) * new_delta * MP_YEARS[1])
    assert cb["capex_expansion"] == pytest.approx(expected, rel=REL)
    # Cross-check the investment share against PyPSA's own per-period
    # `expanded_capex` (a DataFrame, one column per period), × years.
    investment = n.statistics.expanded_capex()
    investment_total = sum(
        float(investment[p].sum()) * y for p, y in zip(MP_PERIODS, MP_YEARS))
    fom_share = 100.0 * old_delta * MP_YEARS[0] + 150.0 * new_delta * MP_YEARS[1]
    assert cb["capex_expansion"] == pytest.approx(investment_total + fom_share, rel=REL)


@pytest.mark.live_solve
def test_cost_breakdown_folds_fom_into_every_period_and_breaks_it_out(reset_backend):
    from services.results.cost_breakdown import compute_cost_breakdown

    n = _solved_multi_period()
    cb = compute_cost_breakdown(n, _mp_cfg())
    assert cb is not None
    old, new, nonext = (float(n.generators.at[g, "p_nom_opt"]) for g in ("old", "new", "nonext"))
    fom_2030 = 100.0 * old + 10.0 * nonext
    fom_2040 = 150.0 * new
    by_p = {p["period"]: p for p in cb["by_period"]}
    assert by_p[2030]["fom"] == pytest.approx(fom_2030 * MP_YEARS[0], rel=REL)
    assert by_p[2040]["fom"] == pytest.approx(fom_2040 * MP_YEARS[1], rel=REL)
    assert by_p[2030]["capex"] == pytest.approx(
        ((1000.0 + 100.0) * old + (700.0 + 10.0) * nonext) * MP_YEARS[0], rel=REL)
    assert by_p[2040]["capex"] == pytest.approx((3000.0 + 150.0) * new * MP_YEARS[1], rel=REL)
    assert cb["fom"] == pytest.approx(fom_2030 * MP_YEARS[0] + fom_2040 * MP_YEARS[1], rel=REL)


@pytest.mark.live_solve
def test_objective_decomposition_explains_the_whole_gap(reset_backend):
    """
    Reported and LP totals differ here for two named reasons — the fixed
    `nonext` generator (reporting counts it, the LP cannot size it) and the
    objective weights 7 / 4 against years 10 / 10 — and the bridge must
    account for all of it, leaving a residual of ~0.
    """
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    n = _solved_multi_period()
    cb = compute_cost_breakdown(n, _mp_cfg())
    d = compute_objective_decomposition(n, cb, _mp_cfg())
    lp_total = _lp_total(n)
    assert d["lp_total"] == pytest.approx(lp_total, rel=REL)
    assert abs(d["gap_eur"]) > 0.01 * lp_total  # there IS a gap to explain
    nonext = float(n.generators.at["nonext", "p_nom_opt"])
    assert d["nonextendable_fixed_cost_eur"] == pytest.approx(
        (700.0 + 10.0) * nonext * MP_YEARS[0], rel=REL)
    assert d["gap_eur"] == pytest.approx(
        -d["nonextendable_fixed_cost_eur"] + d["period_weighting_adjustment_eur"]
        + d["residual_gap_eur"], rel=REL)
    assert abs(d["residual_gap_eur"]) <= 1e-9 * lp_total
    assert d["lp_basis_total"] == pytest.approx(lp_total, rel=REL)


@pytest.mark.live_solve
def test_objective_decomposition_closes_the_golden_fixtures_gap(reset_backend):
    """
    The golden network's reported total exceeds its LP total by tens of
    millions of EUR, almost all of it the non-extendable Line's capital_cost
    (1 M EUR/MVA/yr × 500 MVA, charged for 24/8760 of a year per period). The
    bridge must explain it to within float noise.
    """
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition
    from tests.golden import fixture as gf

    n = gf.solve_golden_network()
    cfg = SolverConfig(discount_rate=gf.GOLDEN_DISCOUNT_RATE, multi_investment_periods=True,
                       investment_periods=list(gf.GOLDEN_PERIODS))
    cb = compute_cost_breakdown(n, cfg)
    d = compute_objective_decomposition(n, cb, cfg)
    line_fixed = (1_000_000.0 * gf.SNAPSHOTS_PER_PERIOD / 8760.0) * 500.0 * sum(gf.GOLDEN_YEARS)
    assert d["gap_eur"] < -0.9 * line_fixed
    assert d["nonextendable_fixed_cost_eur"] >= line_fixed
    assert abs(d["residual_gap_eur"]) <= 1e-6 * abs(d["lp_total"])


# ── Activity: a retired asset stops paying fixed cost on EVERY surface ───

@pytest.fixture()
def solved_mp(reset_backend) -> pypsa.Network:
    """The retirement network, installed for the router-backed surfaces."""
    n = _solved_multi_period()
    ctx = PyPSAService._ensure_active()
    ctx.network = n
    sim_router._state["solver_config"] = _mp_cfg()
    return n


def _mp_expected_fixed(n) -> dict[str, float]:
    """Horizon fixed cost per generator: rate x capacity x ACTIVE years only."""
    return {
        # `old` and `nonext` retire after 2030; `new` only exists from 2040.
        "old": (1000.0 + 100.0) * float(n.generators.at["old", "p_nom_opt"]) * MP_YEARS[0],
        "nonext": (700.0 + 10.0) * float(n.generators.at["nonext", "p_nom_opt"]) * MP_YEARS[0],
        "new": (3000.0 + 150.0) * float(n.generators.at["new", "p_nom_opt"]) * MP_YEARS[1],
    }


@pytest.mark.live_solve
def test_asset_economics_charges_only_active_periods(solved_mp):
    from services.results.asset_economics import compute_asset_economics
    from services.results.cost_breakdown import compute_cost_breakdown

    n = solved_mp
    expected = _mp_expected_fixed(n)
    payload = compute_asset_economics(n, _mp_cfg(), result_df=_live)
    rows = {r["name"]: r for r in payload["generators"]}
    for name, value in expected.items():
        assert rows[name]["fixed_cost_eur"] == pytest.approx(value, rel=REL), name
    old_by_p = {e["period"]: e for e in rows["old"]["by_period"]}
    assert old_by_p[2040]["fixed_cost_eur"] == pytest.approx(0.0, abs=1e-9)
    assert old_by_p[2030]["fixed_cost_eur"] == pytest.approx(expected["old"], rel=REL)
    # ...and the per-asset figures now add up to Capacity Expansion's.
    cb = compute_cost_breakdown(n, _mp_cfg())
    assert sum(expected.values()) == pytest.approx(cb["capex"], rel=REL)


@pytest.mark.live_solve
def test_asset_detail_charges_only_active_periods(solved_mp):
    import routers.asset_results as AR

    n = solved_mp
    detail = AR.get_asset_results(
        component_class="Generator", name="old", category="economics",
        source="lopf", from_=None, to=None, period=None,
        mode="chronological", metrics="",
    )
    assert detail["scalars"]["fixed_cost_eur"] == pytest.approx(_mp_expected_fixed(n)["old"], rel=1e-6)


@pytest.mark.live_solve
def test_compare_capacity_and_economics_charge_only_active_periods(solved_mp):
    from services.compare.capacity import _compute_capacity_summary
    from services.compare.economics import _compute_economics_summary

    n = solved_mp
    total = sum(_mp_expected_fixed(n).values())
    periods = list(MP_PERIODS)
    cap = _compute_capacity_summary(n, periods, True, True, cfg=_mp_cfg())
    assert sum(v.total for v in cap.capex_meur_by_carrier.values()) * 1e6 == pytest.approx(total, rel=1e-6)
    econ = _compute_economics_summary(n, periods, True, True, cfg=_mp_cfg(), result_df=_live)
    assert sum(v.capex_meur.total for v in econ.by_carrier.values()) * 1e6 == pytest.approx(total, rel=1e-6)
    by_2040 = sum(v.capex_meur.by_period.get("2040", 0.0) for v in econ.by_carrier.values()) * 1e6
    assert by_2040 == pytest.approx(_mp_expected_fixed(n)["new"], rel=1e-6)

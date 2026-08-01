"""The five causes of `blocked`, each reached by constructing the state."""
import pandas as pd
import pytest

from services.asset_results import compute as C
from services.asset_results import registry as reg
from tests.conftest import build_network


def test_unsolved_network_blocks_dispatch_with_a_run_remedy():
    n = build_network(solve=False)
    pre = C.preconditions(n, "Generator", "gas")
    st = pre[reg.REQ_DISPATCH]
    assert st.status == "blocked"
    assert st.remedy.action == "run_simulation"


def test_solved_network_clears_the_dispatch_precondition():
    n = build_network(solve=True)
    assert C.preconditions(n, "Generator", "gas")[reg.REQ_DISPATCH].status == "ok"


def test_stale_dispatch_blocks_and_says_so():
    n = build_network(solve=True)
    n.add("Generator", "late_addition", bus="B1", p_nom=1.0)  # topology moved
    st = C.preconditions(n, "Generator", "gas")[reg.REQ_DISPATCH]
    assert st.status == "blocked"
    assert "stale" in st.reason.lower() or "changed" in st.reason.lower()


def test_non_committable_generator_blocks_the_uc_metrics():
    n = build_network(solve=True)
    st = C.preconditions(n, "Generator", "gas")[reg.REQ_COMMITTABLE]
    assert st.status == "blocked"
    assert st.remedy.action == "open_properties"
    assert "gas" in st.reason


def test_ac_pf_precondition_is_blocked_until_the_stage_runs():
    n = build_network(solve=True)
    st = C.preconditions(n, "Generator", "gas")[reg.REQ_AC_PF]
    assert st.status == "blocked"
    assert st.remedy.action == "run_ac_pf"


def test_carrier_without_co2_blocks_the_emissions_metrics():
    n = build_network(solve=True)
    st = C.preconditions(n, "Generator", "gas")[reg.REQ_CO2]
    assert st.status == "blocked"
    assert "co2_emissions" in st.reason


def test_carrier_with_co2_clears_the_emissions_precondition():
    n = build_network(solve=False)
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.optimize(solver_name="highs")
    assert C.preconditions(n, "Generator", "gas")[reg.REQ_CO2].status == "ok"


def test_preconditions_returns_exactly_the_declared_set():
    """
    Every id a metric can list in `requires` must be evaluated here, and
    nothing else should be. A metric requiring an id this map omits would
    silently resolve `ok` (unlisted preconditions are treated as ok in
    `resolve_metric`), turning a genuinely unavailable result into a ticked
    checkbox with an empty chart.
    """
    n = build_network(solve=True)
    got = set(C.preconditions(n, "Generator", "gas"))
    assert got == {reg.REQ_DISPATCH, reg.REQ_AC_PF, reg.REQ_DUALS,
                   reg.REQ_COMMITTABLE, reg.REQ_CO2, reg.REQ_ANNUITY}
    declared = {r for m in reg.METRICS for r in m.requires}
    assert declared <= got, f"metrics require unevaluated preconditions: {declared - got}"


def test_attr_for_maps_every_class():
    for cls, attr in [
        ("Bus", "buses"), ("Generator", "generators"), ("Load", "loads"),
        ("Line", "lines"), ("Transformer", "transformers"), ("Link", "links"),
        ("StorageUnit", "storage_units"), ("Store", "stores"),
    ]:
        assert C.attr_for(cls) == attr
    with pytest.raises(KeyError):
        C.attr_for("Nonsense")


def test_summary_identity_reports_class_carrier_and_bus():
    n = build_network(solve=True)
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    ident = C.summary_identity(ctx)
    assert ident["name"] == "gas"
    assert ident["class"] == "Generator"
    assert ident["carrier"] == "gas"
    assert ident["bus"] == "B1"


def test_summary_params_works_on_an_unsolved_network():
    n = build_network(solve=False)
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    params = C.summary_params(ctx)
    assert params["p_nom"] == pytest.approx(200.0)
    assert params["marginal_cost"] == pytest.approx(50.0)


def test_build_ctx_carries_weights_matching_the_snapshot_count():
    n = build_network(solve=True, gens_weight=3.0)
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    assert len(ctx.weights) == len(n.snapshots)
    assert float(ctx.weights.iloc[0]) == pytest.approx(3.0)


def test_multi_period_weights_apply_the_years_multiplier_exactly_once():
    """
    `snapshot_weights` already folds in investment_period_weightings.years
    for a MultiIndex. Applying the years map a second time in build_ctx would
    give weight x years^2 and inflate every energy and cost total.
    """
    n = build_network(solve=False)
    base = n.snapshots
    mi = pd.MultiIndex.from_product([[2026, 2031], base], names=["period", "timestep"])
    mi.name = "snapshot"
    n.set_snapshots(mi)
    n.investment_periods = [2026, 2031]
    n.investment_period_weightings["years"] = 5.0
    n.snapshot_weightings["generators"] = 3.0

    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    # 3.0 (snapshot weight) x 5.0 (years) = 15.0 — NOT 75.0.
    assert float(ctx.weights.iloc[0]) == pytest.approx(15.0)
    assert ctx.is_multi is True

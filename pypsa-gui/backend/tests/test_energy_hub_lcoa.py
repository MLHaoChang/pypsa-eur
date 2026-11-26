"""
template-e2e 28a — the levelised cost of ammonia in ``compute_tea`` via
``services.results.lcoa`` (mirrors LCOH; ADR-0001: null + a flag, never 0).
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from models.energy_hub import TeaBlock
from services.adequacy import eh_report as R
from services.results.lcoa import DEFAULT_MWH_PER_T, ammonia_synthesis_links, compute_lcoa
from services.solver_service import SolverConfig


def _raw(n, accessor, attr, source="lopf"):
    return getattr(getattr(n, accessor), attr)


def _hb_network(*, dispatch=(2.0, 0.0), mwh_per_t=5.0, capital_cost=0.0, carrier="Haber-Bosch",
                hub_price=50.0, h2_price=80.0):
    """A 'solved' two-hour hub: a Haber-Bosch Link drawing electricity (bus0) and H2 (bus2)."""
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(2))
    n.add("Bus", "hub", carrier="AC")
    n.add("Bus", "h2", carrier="H2")
    n.add("Bus", "nh3", carrier="NH3")
    if mwh_per_t is not None:
        n.buses["mwh_per_t"] = float("nan")
        n.buses.at["nh3", "mwh_per_t"] = mwh_per_t
    n.add("Link", "hb", bus0="hub", bus1="nh3", bus2="h2", carrier=carrier, p_nom=10.0,
          efficiency=4.0, efficiency2=-5.0, marginal_cost=1.0, capital_cost=capital_cost)
    n.links.at["hb", "p_nom_opt"] = 10.0
    n.links_t.p0 = pd.DataFrame({"hb": list(dispatch)}, index=n.snapshots)
    n.buses_t.marginal_price = pd.DataFrame(
        {"hub": [hub_price] * 2, "h2": [h2_price] * 2, "nh3": [0.0] * 2}, index=n.snapshots)
    return n


def test_tea_block_carries_lcoa_flag_fields():
    t = TeaBlock()
    assert t.lcoa_eur_per_t is None and t.lcoa_status is None and t.lcoa_note is None
    assert t.currency is None


def test_lcoa_prices_electricity_h2_and_vom_per_tonne():
    # 2 MW_el for 1 h: 8 MWh NH3 = 1.6 t; VOM 2 + electricity 2×50 + H2 2×5×80 = 902 EUR.
    out = compute_lcoa(_hb_network(), SolverConfig(), result_df=_raw)
    assert out["total"]["nh3_t"] == pytest.approx(1.6)
    assert out["total"]["cost_eur"] == pytest.approx(902.0)
    assert out["total"]["lcoa_eur_per_t"] == pytest.approx(902.0 / 1.6)
    assert out["currency"] == "EUR"


def test_lcoa_h2_input_moves_with_the_h2_price():
    lo = compute_lcoa(_hb_network(h2_price=80.0), SolverConfig(), result_df=_raw)["total"]["cost_eur"]
    hi = compute_lcoa(_hb_network(h2_price=90.0), SolverConfig(), result_df=_raw)["total"]["cost_eur"]
    assert hi - lo == pytest.approx(2.0 * 5.0 * 10.0)


def test_lcoa_counts_the_fixed_cost():
    base = compute_lcoa(_hb_network(), SolverConfig(), result_df=_raw)["total"]["cost_eur"]
    fixed = compute_lcoa(_hb_network(capital_cost=1000.0), SolverConfig(), result_df=_raw)["total"]["cost_eur"]
    assert fixed > base


def test_lcoa_falls_back_to_pypsa_eur_mwh_per_t():
    out = compute_lcoa(_hb_network(mwh_per_t=None), SolverConfig(), result_df=_raw)
    assert out["total"]["nh3_t"] == pytest.approx(8.0 / DEFAULT_MWH_PER_T)


def test_links_found_by_carrier_or_by_nh3_output_bus():
    assert ammonia_synthesis_links(_hb_network()) == ["hb"]
    assert ammonia_synthesis_links(_hb_network(carrier="custom")) == ["hb"]
    n = _hb_network()
    n.buses.at["nh3", "carrier"] = "AC"
    n.links.at["hb", "carrier"] = "custom"
    assert ammonia_synthesis_links(n) == []


def test_compute_tea_records_lcoa_and_leaves_lcoh_skipped():
    tea = R.compute_tea(cost_eur=100.0, served_energy_mwh=10.0, network=_hb_network(),
                        cfg=SolverConfig())
    assert tea.lcoa_status == "ok"
    assert tea.lcoa_eur_per_t is not None and tea.lcoa_eur_per_t > 0
    assert tea.currency == "EUR"
    # Hydrogen is untouched: no electrolyser here, so LCOH stays skipped.
    assert tea.lcoh_eur_per_kg is None and tea.lcoh_status == "skipped"


def test_compute_tea_skips_lcoa_without_ammonia_synthesis():
    n = _hb_network()
    n.remove("Link", "hb")
    tea = R.compute_tea(cost_eur=100.0, served_energy_mwh=10.0, network=n)
    assert tea.lcoa_eur_per_t is None
    assert tea.lcoa_status == "skipped"


def test_compute_tea_flags_lcoa_not_established_when_no_nh3_made():
    tea = R.compute_tea(cost_eur=100.0, served_energy_mwh=10.0,
                        network=_hb_network(dispatch=(0.0, 0.0)), cfg=SolverConfig())
    assert tea.lcoa_eur_per_t is None
    assert tea.lcoa_status == "not_established"


def test_compute_tea_flags_lcoa_not_established_on_a_mip():
    n = _hb_network()
    n.links.at["hb", "committable"] = True
    tea = R.compute_tea(cost_eur=100.0, served_energy_mwh=10.0, network=n,
                        cfg=SolverConfig(process_uc="binary"))
    assert tea.lcoa_eur_per_t is None
    assert tea.lcoa_status == "not_established"
    assert "MIP" in (tea.lcoa_note or "")


def test_the_ammonia_template_has_one_synthesis_link_and_its_t_basis():
    from project_templates import eh_templates as T

    n = T.BUILDERS["eh_ammonia_staged"]()
    assert ammonia_synthesis_links(n) == ["haber_bosch"]
    assert n.buses.at["nh3", "mwh_per_t"] == pytest.approx(5.166)
    # The hydrogen hub's electrolysers are not ammonia synthesis.
    assert ammonia_synthesis_links(T.BUILDERS["eh_h2_hub_staged"]()) == []

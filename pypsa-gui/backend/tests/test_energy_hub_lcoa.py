"""
template-e2e 28a — the levelised cost of ammonia in ``compute_tea`` via
``services.results.lcoa`` (mirrors LCOH; ADR-0001: null + a flag, never 0).
GK B2 (6) conditions A1-A6 are named on the tests that pin them.
"""

from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from models.energy_hub import TeaBlock
from services.adequacy import eh_report as R
from services.results.lcoa import (
    DEFAULT_MWH_PER_T,
    ammonia_synthesis_links,
    compute_lcoa,
)
from services.solver_service import SolverConfig
from tests.eh_stage_fixtures import certifiable_weak_network

# Distinct literals per part (A6): 2 then 3 MW_el, VOM 1.5, electricity 50 then 60,
# H2 80 then 90 at 5 MWh_H2 per MWh_el, 4 MWh_NH3 per MWh_el, 5 MWh per t,
# capital 1000 + FOM 7 EUR/MW/a on 10 MW over the 2 h of 8760 h.
NH3_T = (2 + 3) * 4.0 / 5.0  # 4 t
VOM = (2 + 3) * 1.5  # 7.5
ELECTRICITY = 2 * 50.0 + 3 * 60.0  # 280
H2 = 2 * 5 * 80.0 + 3 * 5 * 90.0  # 2150
FIXED = (1000.0 + 7.0) * 10.0 * 2 / 8760.0  # 2.299...
COST = FIXED + VOM + ELECTRICITY + H2


def _raw(n, accessor, attr, source="lopf"):
    return getattr(getattr(n, accessor), attr)


def _hb_network(
    *, dispatch=(2.0, 3.0), unit="MWh", mwh_per_t=5.0, carrier="Haber-Bosch"
):
    """A 'solved' two-hour hub: a Haber-Bosch Link drawing electricity (bus0) and H2 (bus2)."""
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(2))
    n.add("Bus", "hub", carrier="AC")
    n.add("Bus", "h2", carrier="H2")
    n.add("Bus", "nh3", carrier="NH3", unit=unit)
    if mwh_per_t is not None:
        n.buses["mwh_per_t"] = float("nan")
        n.buses.at["nh3", "mwh_per_t"] = mwh_per_t
    n.add(
        "Link",
        "hb",
        bus0="hub",
        bus1="nh3",
        bus2="h2",
        carrier=carrier,
        p_nom=10.0,
        efficiency=4.0,
        efficiency2=-5.0,
        marginal_cost=1.5,
        capital_cost=1000.0,
    )
    n.links["fom_cost"] = 7.0
    n.links.at["hb", "p_nom_opt"] = 10.0
    n.links_t.p0 = pd.DataFrame({"hb": list(dispatch)}, index=n.snapshots)
    n.buses_t.marginal_price = pd.DataFrame(
        {"hub": [50.0, 60.0], "h2": [80.0, 90.0], "nh3": [0.0, 0.0]}, index=n.snapshots
    )
    return n


def _lcoa(n, **kw):
    return compute_lcoa(n, SolverConfig(), result_df=_raw, **kw)


def _tea(n, cfg=...):
    return R.compute_tea(
        cost_eur=100.0,
        served_energy_mwh=10.0,
        network=n,
        cfg=SolverConfig() if cfg is ... else cfg,
    )


def test_tea_block_carries_lcoa_flag_fields():
    t = TeaBlock()
    assert t.lcoa_eur_per_t is None and t.lcoa_status is None and t.lcoa_note is None
    assert t.currency is None


# ── A6: exact, every part ────────────────────────────────────────────────────
def test_lcoa_is_exact_over_each_part():
    out = _lcoa(_hb_network())
    row = out["rows"][0]
    assert row["fixed_eur"] == pytest.approx(FIXED)
    assert row["vom_eur"] == pytest.approx(VOM)
    assert row["electricity_eur"] == pytest.approx(ELECTRICITY)
    assert row["inputs_eur"] == pytest.approx(H2)
    assert out["total"]["nh3_t"] == pytest.approx(NH3_T)
    assert out["total"]["cost_eur"] == pytest.approx(COST)
    assert out["total"]["lcoa_eur_per_t"] == pytest.approx(COST / NH3_T)
    # The same figure as a literal worked by hand: 2439.7991 EUR / 4 t.
    assert out["total"]["lcoa_eur_per_t"] == pytest.approx(609.9497716894978, rel=1e-12)
    assert out["currency"] == "EUR"


def test_two_synthesis_links_sum_numerators_and_denominators():
    n = _hb_network()
    n.add(
        "Link",
        "hb2",
        bus0="hub",
        bus1="nh3",
        bus2="h2",
        carrier="Haber-Bosch",
        p_nom=4.0,
        efficiency=3.0,
        efficiency2=-4.0,
        marginal_cost=0.5,
        capital_cost=0.0,
    )
    n.links.at["hb2", "p_nom_opt"] = 4.0
    n.links.at["hb2", "fom_cost"] = 0.0
    n.links_t.p0["hb2"] = [1.0, 1.0]
    t2 = 2 * 3.0 / 5.0
    cost2 = 2 * 0.5 + (50.0 + 60.0) + (4 * 80.0 + 4 * 90.0)
    total = _lcoa(n)["total"]
    assert total["nh3_t"] == pytest.approx(NH3_T + t2)
    assert total["lcoa_eur_per_t"] == pytest.approx((COST + cost2) / (NH3_T + t2))


# ── A2: units, failing closed ────────────────────────────────────────────────
def test_a_bus_in_t_takes_no_conversion():
    out = _lcoa(_hb_network(unit="t", mwh_per_t=None))
    assert out["total"]["nh3_t"] == pytest.approx(NH3_T * 5.0)


def test_an_mwh_bus_without_its_factor_takes_pypsa_eurs_and_says_so():
    out = _lcoa(_hb_network(mwh_per_t=None))
    assert out["total"]["nh3_t"] == pytest.approx(NH3_T * 5.0 / DEFAULT_MWH_PER_T)
    assert "PyPSA-Eur" in out["total"]["t_basis"][0]
    assert "PyPSA-Eur" in _tea(_hb_network(mwh_per_t=None)).lcoa_note


@pytest.mark.parametrize("unit", ["kg", "", "MW"])
def test_any_other_unit_is_not_established(unit):
    tea = _tea(_hb_network(unit=unit))
    assert tea.lcoa_eur_per_t is None
    assert tea.lcoa_status == "not_established"
    assert "unit" in tea.lcoa_note


def test_the_ammonia_template_reads_its_tonnes_from_the_nh3_bus():
    from project_templates import eh_templates as T
    from services.results.lcoa import _mwh_per_t

    n = T.BUILDERS["eh_ammonia_staged"]()
    assert ammonia_synthesis_links(n) == ["haber_bosch"]
    assert _mwh_per_t(n, "nh3") == (
        pytest.approx(5.166),
        "5.166 MWh/t from the NH3 bus",
    )
    # The hydrogen hub's electrolysers are not ammonia synthesis.
    assert ammonia_synthesis_links(T.BUILDERS["eh_h2_hub_staged"]()) == []


# ── A3: selection ────────────────────────────────────────────────────────────
def test_links_found_by_carrier_or_by_nh3_made_from_h2():
    assert ammonia_synthesis_links(_hb_network()) == ["hb"]
    assert ammonia_synthesis_links(_hb_network(carrier="custom")) == ["hb"]


def test_an_nh3_import_link_is_not_synthesis():
    n = _hb_network()
    n.add("Bus", "port", carrier="NH3 import")
    n.add(
        "Link", "nh3_import", bus0="port", bus1="nh3", carrier="NH3 import", p_nom=50.0
    )
    n.add(
        "Link",
        "nh3_purchase",
        bus0="hub",
        bus1="nh3",
        carrier="NH3 purchase",
        p_nom=50.0,
    )
    assert ammonia_synthesis_links(n) == ["hb"]
    n.links.at["hb", "carrier"] = "custom"
    n.buses.at["h2", "carrier"] = "AC"
    assert ammonia_synthesis_links(n) == []


# ── A4: no partial sums ──────────────────────────────────────────────────────
def _missing_h2_price(n):
    n.buses_t.marginal_price = n.buses_t.marginal_price.drop(columns="h2")


def _nan_hub_price(n):
    n.buses_t.marginal_price.loc[1, "hub"] = float("nan")


def _nan_capacity(n):
    n.links.at["hb", "p_nom_opt"] = float("nan")


def _no_capacity_column(n):
    n.links.drop(columns="p_nom_opt", inplace=True)


def _nan_fom(n):
    n.links.at["hb", "fom_cost"] = float("nan")


def _nan_dispatch(n):
    n.links_t.p0.loc[0, "hb"] = float("nan")


def _nan_weight(n):
    n.snapshot_weightings.loc[0, "objective"] = float("nan")


@pytest.mark.parametrize(
    "break_it, part",
    [
        (_missing_h2_price, "price of bus h2"),
        (_nan_hub_price, "price of bus hub"),
        (_nan_capacity, "solved capacity"),
        (_no_capacity_column, "solved capacity"),
        (_nan_fom, "fixed O&M"),
        (_nan_dispatch, "dispatch"),
        (_nan_weight, "weighting"),
    ],
)
def test_a_part_that_cannot_be_valued_is_not_established_and_named(break_it, part):
    n = _hb_network()
    break_it(n)
    tea = _tea(n)
    assert tea.lcoa_eur_per_t is None
    assert tea.lcoa_status == "not_established"
    assert part in tea.lcoa_note


# ── A1: the solve that produced the network ──────────────────────────────────
def _committable(n):
    n.links.at["hb", "committable"] = True
    return n


def test_a_mip_solve_is_not_established_even_under_an_lp_live_config():
    from harness.solve_validity import APPLIED_KEY, applied_record

    n = _committable(_hb_network())
    n.meta[APPLIED_KEY] = applied_record("highs", {}, {}, "binary")
    tea = _tea(n, cfg=SolverConfig(process_uc="linearised"))
    assert tea.lcoa_eur_per_t is None
    assert tea.lcoa_status == "not_established"
    assert "MIP" in tea.lcoa_note


def test_the_studys_own_mip_config_withholds_it():
    tea = _tea(_committable(_hb_network()), cfg=SolverConfig(process_uc="binary"))
    assert tea.lcoa_status == "not_established" and "MIP" in tea.lcoa_note


def test_without_a_config_a_committable_network_fails_closed():
    tea = _tea(_committable(_hb_network()), cfg=None)
    assert tea.lcoa_eur_per_t is None and tea.lcoa_status == "not_established"


# ── compute_tea: the flags, and hydrogen unchanged (A5) ─────────────────────
def test_compute_tea_records_lcoa_and_leaves_lcoh_skipped():
    tea = _tea(_hb_network())
    assert tea.lcoa_status == "ok"
    assert tea.lcoa_eur_per_t == pytest.approx(COST / NH3_T)
    assert tea.currency == "EUR"
    assert tea.lcoh_eur_per_kg is None and tea.lcoh_status == "skipped"


def test_compute_tea_flags_lcoa_not_established_when_no_nh3_made():
    tea = _tea(_hb_network(dispatch=(0.0, 0.0)))
    assert tea.lcoa_eur_per_t is None
    assert tea.lcoa_status == "not_established"


def test_a_study_without_ammonia_keeps_its_tea_block_but_the_new_null_fields():
    tea = R.compute_tea(
        cost_eur=100.0, served_energy_mwh=10.0, network=certifiable_weak_network()
    )
    dump = tea.model_dump(mode="json")
    new = {
        k: dump.pop(k)
        for k in ("lcoa_eur_per_t", "lcoa_status", "lcoa_note", "currency")
    }
    assert new == {
        "lcoa_eur_per_t": None,
        "lcoa_status": "skipped",
        "lcoa_note": "Cost of ammonia skipped: the network has no ammonia-synthesis Links",
        "currency": None,
    }
    assert dump == {
        "lcoe_eur_per_mwh": 10.0,
        "lcoh_eur_per_kg": None,
        "notes": "LCOE = cost_at_target_eur / served_energy_mwh (ex-shed cost)",
        "lcoh_status": "skipped",
        "lcoh_note": "LCOH skipped: the network has no electrolyser Links",
    }


def test_a_failing_ammonia_engine_is_flagged_and_leaves_hydrogen_alone(monkeypatch):
    import services.results.lcoa as L

    def boom(n):
        raise RuntimeError("bad buses")

    monkeypatch.setattr(L, "ammonia_synthesis_links", boom)
    tea = R.compute_tea(
        cost_eur=100.0, served_energy_mwh=10.0, network=certifiable_weak_network()
    )
    assert tea.lcoh_status == "skipped"
    assert tea.lcoa_status == "not_established" and "bad buses" in tea.lcoa_note


def test_an_unsolved_network_is_not_established():
    n = _hb_network()
    n.links_t.p0 = pd.DataFrame(index=n.snapshots)
    tea = _tea(n)
    assert tea.lcoa_eur_per_t is None
    assert tea.lcoa_status == "not_established" and "solved dispatch" in tea.lcoa_note


def test_an_output_bus_is_not_charged():
    n = _hb_network()
    n.add("Bus", "heat", carrier="heat")
    n.links["bus3"] = ""
    n.links["efficiency3"] = 0.0
    n.links.at["hb", "bus3"] = "heat"
    n.links.at["hb", "efficiency3"] = 0.3
    n.buses_t.marginal_price["heat"] = [40.0, 45.0]
    assert _lcoa(n)["total"]["cost_eur"] == pytest.approx(COST)


def test_the_ammonia_synthesis_carrier_counts_without_an_h2_bus():
    n = _hb_network(carrier="Ammonia synthesis")
    n.buses.at["h2", "carrier"] = "AC"
    assert ammonia_synthesis_links(n) == ["hb"]


def test_a_missing_generators_weighting_is_not_established():
    n = _hb_network()
    n.snapshot_weightings.loc[1, "generators"] = float("nan")
    tea = _tea(n)
    assert tea.lcoa_status == "not_established" and "weighting" in tea.lcoa_note


def test_a_synthesis_link_with_no_dispatch_column_is_not_established():
    n = _hb_network()
    n.links_t.p0 = pd.DataFrame({"other": [1.0, 1.0]}, index=n.snapshots)
    tea = _tea(n)
    assert tea.lcoa_status == "not_established" and "dispatch of hb" in tea.lcoa_note


def test_an_idle_built_link_keeps_its_fixed_cost_in_the_fleet():
    # GK B2 (7) L1: hb2 is built (4 MW, capital 500 + FOM 3) but made no NH3.
    n = _hb_network()
    n.add(
        "Link",
        "hb2",
        bus0="hub",
        bus1="nh3",
        bus2="h2",
        carrier="Haber-Bosch",
        p_nom=4.0,
        efficiency=4.0,
        efficiency2=-5.0,
        marginal_cost=1.5,
        capital_cost=500.0,
    )
    n.links.at["hb2", "p_nom_opt"] = 4.0
    n.links.at["hb2", "fom_cost"] = 3.0
    n.links_t.p0["hb2"] = [0.0, 0.0]
    total = _lcoa(n)["total"]
    assert total["nh3_t"] == pytest.approx(NH3_T)
    # (2439.7991 + 503 x 4 x 2 / 8760) EUR / 4 t
    assert total["lcoa_eur_per_t"] == pytest.approx(610.0646118721462, rel=1e-12)


def test_an_unbuilt_link_adds_nothing():
    n = _hb_network()
    n.add(
        "Link",
        "hb2",
        bus0="hub",
        bus1="nh3",
        bus2="h2",
        carrier="Haber-Bosch",
        p_nom=0.0,
        efficiency=4.0,
        efficiency2=-5.0,
        capital_cost=500.0,
    )
    n.links.at["hb2", "p_nom_opt"] = 0.0
    n.links.at["hb2", "fom_cost"] = 3.0
    n.links_t.p0["hb2"] = [0.0, 0.0]
    assert _lcoa(n)["total"]["lcoa_eur_per_t"] == pytest.approx(
        609.9497716894978, rel=1e-12
    )


def test_a_two_bus_h2_to_nh3_link_is_synthesis_and_priced_on_its_h2():
    # GK B2 (7) L2: bus0 = H2, bus1 = NH3, no further bus; 1 then 2 MWh_H2 at 80 then 90.
    n = _hb_network()
    n.remove("Link", "hb")
    n.add(
        "Link",
        "h2_to_nh3",
        bus0="h2",
        bus1="nh3",
        carrier="custom",
        p_nom=5.0,
        efficiency=0.8,
        marginal_cost=0.0,
        capital_cost=0.0,
    )
    n.links.at["h2_to_nh3", "p_nom_opt"] = 5.0
    n.links.at["h2_to_nh3", "fom_cost"] = 0.0
    n.links_t.p0 = pd.DataFrame({"h2_to_nh3": [1.0, 2.0]}, index=n.snapshots)
    assert ammonia_synthesis_links(n) == ["h2_to_nh3"]
    total = _lcoa(n)["total"]
    assert total["nh3_t"] == pytest.approx(0.48)
    # (1 x 80 + 2 x 90) EUR / (3 x 0.8 / 5) t
    assert total["lcoa_eur_per_t"] == pytest.approx(541.6666666666667, rel=1e-12)
    # An import Link from a non-H2 bus is still not synthesis.
    n.add("Bus", "port", carrier="NH3 import")
    n.add(
        "Link", "nh3_import", bus0="port", bus1="nh3", carrier="NH3 import", p_nom=50.0
    )
    assert ammonia_synthesis_links(n) == ["h2_to_nh3"]


def test_an_unbuilt_link_on_an_unpriced_bus_is_skipped_not_valued():
    # GK B2 (7) N1: an unbuilt link is never valued, so its bus having no price does not
    # withhold the figure, and the note counts only the built link.
    n = _hb_network()
    n.add("Bus", "spare", carrier="AC")
    n.add(
        "Link",
        "hb_unbuilt",
        bus0="spare",
        bus1="nh3",
        bus2="h2",
        carrier="Haber-Bosch",
        p_nom=0.0,
        efficiency=4.0,
        efficiency2=-5.0,
    )
    n.links.at["hb_unbuilt", "p_nom_opt"] = 0.0
    n.links.at["hb_unbuilt", "fom_cost"] = 0.0
    n.links_t.p0["hb_unbuilt"] = [0.0, 0.0]
    tea = _tea(n)
    assert tea.lcoa_status == "ok"
    assert tea.lcoa_eur_per_t == pytest.approx(609.9497716894978, rel=1e-12)
    assert "over 1 ammonia-synthesis Link(s)" in tea.lcoa_note

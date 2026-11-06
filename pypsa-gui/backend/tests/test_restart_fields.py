"""Grid-to-process issue 12b-2: the ride-through curve and restart time on a process unit, end to end, and the B
template's minutes-scale acceptance (design §3.B, §4.3)."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "project_templates"))

import eh_concentrator as C  # noqa: E402
import eh_templates as T  # noqa: E402
from gridspine.drivers.process_chain import restart_units  # noqa: E402
from gridspine.static import motor_start as ms  # noqa: E402
from gridspine.static import restart_sequence as rs  # noqa: E402
from gridspine.static import ride_through as rt  # noqa: E402
from models.process_unit import CRITICALITY_TIERS, PROCESS_CUSTOM_COLUMNS  # noqa: E402
from services.adequacy.eh_columns import coerce_eh_value  # noqa: E402

TID = "eh_concentrator_staged"
NEW = ("ride_through_curve", "restart_time_h")


@pytest.fixture(scope="module")
def b():
    return T.BUILDERS[TID]()


def test_the_restart_tier_order_is_the_gui_criticality_tiers():
    # GK A carry-over: gridspine cannot import the GUI, so the two tuples are pinned equal here.
    assert rs.TIER_ORDER == CRITICALITY_TIERS


def test_both_fields_are_registered_on_link():
    assert PROCESS_CUSTOM_COLUMNS["Link"]["ride_through_curve"] == "ride_through_curve"
    assert PROCESS_CUSTOM_COLUMNS["Link"]["restart_time_h"] == "float_nonneg"


@pytest.mark.parametrize(
    "bad, match",
    [
        ("20", "pairs"),
        ("0:0.5", "depth"),
        ("120:0.5", "depth"),
        ("20:0", "duration"),
        ("20:x", "numbers"),
        ("20:1, 20:2", "twice"),
    ],
)
def test_a_malformed_curve_is_refused_with_its_label(bad, match):
    with pytest.raises(ValueError, match=f"Ride-through curve.*{match}"):
        coerce_eh_value("Link", "ride_through_curve", bad)


def test_a_curve_is_stored_as_typed_and_empty_is_not_set():
    assert (
        coerce_eh_value("Link", "ride_through_curve", " 20:0.5, 40:0.15 ")
        == "20:0.5, 40:0.15"
    )
    assert coerce_eh_value("Link", "ride_through_curve", None) == ""
    with pytest.raises(ValueError):
        coerce_eh_value("Link", "restart_time_h", -1.0)


def _fingerprints(n):
    from services import workflow_tools as W
    from services.solver_service import SolverConfig

    ctx = SimpleNamespace(
        network=n, solver_state={"solver_config": SolverConfig()}, user_ts={}
    )
    return W._network_input_fingerprint(ctx), W._network_input_fingerprint(
        ctx, exact=True
    )


def _owner_digest(n):
    from services.results.finance_case import owner_assets_digest

    owners = [{"component": "Link", "asset_id": m, "owner": "site"} for m in C.MILLS]
    return owner_assets_digest(n, {"value_flows": {"asset_owners": owners}})


def test_neither_field_moves_the_lp_fingerprint_nor_the_investment_digest(b):
    n = b.copy()
    for c in NEW:
        n.links[c] = "" if c == "ride_through_curve" else math.nan
    before = (_fingerprints(n), _owner_digest(n))
    n.links.loc["mill_1", list(NEW)] = ["50:0.2", 7.0]
    assert (_fingerprints(n), _owner_digest(n)) == before


def test_the_heat_demo_units_read_unknown_on_every_screen():
    from services.process_heat import add_hybrid_supply, build_two_level_heat_demo

    n = build_two_level_heat_demo()
    level = str(n.links.at[n.links.index[n.links["eh_process"].eq(True)][0], "bus1"])
    add_hybrid_supply(
        n,
        level,
        gas_bus="gas",
        elec_bus="grid",
        gas={"name": "extra_gas", "p_nom": 1.0, "efficiency": 0.9},
        electric={"name": "extra_el", "p_nom": 1.0, "efficiency": 0.99},
    )
    units = restart_units(n)
    assert {"extra_gas"} <= set(units["unit"]) and len(units) >= 3
    # Heat counted in MWh with no tonne factor: no rate, no mass pull; no curve, no restart time.
    assert units[["rate_tph", "mass_pull", "restart_time_h"]].isna().all().all()
    assert units["ride_through_curve"].isna().all()
    lost = rt.lost_tonnes_table(units)
    assert (lost["verdict"] == "unknown").all()
    assert rt.lost_tonnes_total(lost)["incomplete"] is True


# --- the B template ----------------------------------------------------------


def test_b_mills_carry_the_ledgered_data_and_nothing_else_does(b):
    mills = b.links.loc[list(C.MILLS)]
    assert (mills["motor_kw"] == C.MILL_MOTOR_KW).all() and (
        mills["start_method"] == "dol"
    ).all()
    assert (mills["restart_time_h"] == 4.0).all() and (
        mills["ride_through_curve"] == C.MILL_RIDE_THROUGH
    ).all()
    others = b.links.drop(list(C.MILLS))
    assert (
        others["motor_kw"].isna().all() and (others["ride_through_curve"] == "").all()
    )
    items = {r["item"]: r for r in T.TEMPLATE_META[TID]["ledger"]}
    for item in ("mill motor and start", "mill restart time and ride-through curve"):
        assert items[item]["source"] == "demo"


def test_b_mill_rate_and_mass_pull_come_from_the_flowsheet(b):
    units = restart_units(b).set_index("unit")
    for m in C.MILLS:
        assert units.at[m, "rate_tph"] == pytest.approx(C.MILL_TPH)
        assert units.at[m, "mass_pull"] == pytest.approx(C.MASS_PULL)
        assert "flotation / flotation_2035" in units.at[m, "mass_pull_basis"]


@pytest.mark.parametrize(
    "method, verdict, du", [("dol", "fail", 29.63), ("vfd", "pass", 1.62)]
)
def test_b_a_dol_mill_start_fails_on_150_mva_and_a_vfd_passes(b, method, verdict, du):
    # Hand values (12a's formula): S_start = k × 6 MW / 0.85; dU = S_start × sin(phi_start) / (150 / c_max 1.1).
    k, pf = ms.START_MULTIPLE[method], ms.START_PF[method]
    s = k * C.MILL_MOTOR_KW / 1000.0 / 0.85
    hand = s * math.sqrt(1 - pf**2) / (C.FAULT_LEVEL_MVA / 1.1) * 100.0
    assert hand == pytest.approx(du, abs=0.01)
    row = (
        restart_units(b)
        .query("unit == 'mill_1'")
        .assign(poc="PoC", start_method=method)
    )
    r = ms.motor_start_table(
        row, {"PoC": ms.Source(sk_mva=C.FAULT_LEVEL_MVA)}, None, 3.0
    ).iloc[0]
    assert r["verdict"] == verdict and r["du_pct"] == pytest.approx(hand, rel=1e-9)


def test_b_restart_lost_tonnes_equal_the_sum_over_the_tripped_units(b):
    units = restart_units(b)
    trips = rt.trip_table(units)
    tripped = set(trips.loc[trips["verdict"].isin(("trips", "unknown")), "unit"])
    chosen = units[units["unit"].isin(tripped)]
    table, _, total = rs.restart_sequence(
        chosen, "trip", poc_source=ms.Source(sk_mva=C.FAULT_LEVEL_MVA), limit_pct=3.0
    )
    known = chosen.dropna(subset=["restart_time_h", "rate_tph", "mass_pull"])
    expected = float(
        (known["restart_time_h"] * known["rate_tph"] * known["mass_pull"]).sum()
    )
    assert set(known["unit"]) == set(
        C.MILLS
    )  # the crusher and flotation carry no restart time
    assert total["lost_restart_t"] == pytest.approx(expected, rel=1e-12)
    assert expected == pytest.approx(3 * 4.0 * 550.0 * C.MASS_PULL, rel=1e-12)
    # DOL starts fail and three units are unknown: the total is a lower bound with both counted.
    assert total["fail_count"] == 3 and total["lower_bound"] is True
    assert table.set_index("unit").loc[list(C.MILLS), "start_verdict"].eq("fail").all()


def test_b_mills_trip_on_the_demo_dips_and_the_total_is_labelled_demo(b):
    lost = rt.lost_tonnes_table(restart_units(b))
    mills = lost.set_index("unit").loc[list(C.MILLS)]
    assert (mills["verdict"] == "known").all() and (mills["trips_per_year"] > 0).all()
    total = rt.lost_tonnes_total(lost)
    assert total["dip_stats_source"] == "demo" and total["incomplete"] is True

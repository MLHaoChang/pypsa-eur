"""DC grid readiness 06: reserve products on backup storage (the LP rows).

Hand cases on a one-bus site: a DC-03 string ``backup_bess_1`` (5 MW, its
discharge held by a p_max_pu series of 0), a free supply and no load, so the
only thing the LP can earn is the reserve.
"""
from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pypsa
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from models.commercial import PriceSeriesRef  # noqa: E402
from models.reserve_products import ReserveProduct  # noqa: E402
from services.commercial import settlement_inputs as SI  # noqa: E402
from services.solver import reserve_products as RP  # noqa: E402

TOL = 1e-6
STRING = "backup_bess_1"


def _ref(pid: str) -> PriceSeriesRef:
    return PriceSeriesRef(id=f"series-{pid}", version=1, hash=f"hash-{pid}-0000", source="user")


def _product(pid="fcr", kind="fast_symmetric", direction="symmetric", sustain_h=0.5, **kw) -> ReserveProduct:
    return ReserveProduct(id=pid, kind=kind, direction=direction, sustain_h=sustain_h,
                          capacity_price=_ref(pid), **kw)


def _site(snaps=4, energy_mwh=25.0, p_nom=5.0, hold=True, eta=1.0, weights=1.0) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=snaps, freq="h"))
    n.snapshot_weightings.loc[:, :] = weights
    n.add("Bus", "site")
    n.add("Generator", "supply", bus="site", p_nom=100.0, marginal_cost=1.0)
    n.add("StorageUnit", STRING, bus="site", p_nom=p_nom, max_hours=energy_mwh / p_nom,
          efficiency_store=eta, efficiency_dispatch=eta, cyclic_state_of_charge=True)
    if hold:
        n.storage_units_t.p_max_pu = pd.DataFrame(0.0, index=n.snapshots, columns=[STRING])
    return n


def _bind(n, products, prices) -> None:
    SI.write_reference_series(
        n, {RP.price_column(p.id): (pd.Series(prices[p.id], index=n.snapshots, dtype=float), p.capacity_price)
            for p in products}, frame=RP.PRICE_FRAME)


def _cfg(products):
    return SimpleNamespace(commercial=SimpleNamespace(reserve_products=products))


def _solve(n, products, floors, prices=None):
    prices = prices or {p.id: 10.0 for p in products}
    _bind(n, products, prices)
    fn = RP._wrap_with_reserve_products(n, None, _cfg(products), backup=(floors, frozenset({STRING})))
    status, _ = n.optimize(solver_name="highs", extra_functionality=fn, include_objective_constant=False)
    assert status == "ok"
    assert RP.persist_solution(n)
    frame, flags = RP.solved(n)
    assert flags == []
    return frame


def test_hand_case_symmetric_product_sits_the_soc_between_its_bands():
    """Floor 20, energy 25, d 0.5 h, η 1: up needs soc − 0.5 r ≥ 20, down
    soc + 0.5 r ≤ 25, power r ≤ 5. r = 5 pins soc at 22.5; revenue
    Σ w·price·r = 4 × 10 × 5 = 200 is the objective's only term."""
    n = _site()
    p = _product()
    frame = _solve(n, [p], {STRING: 20.0})
    assert frame[f"{STRING}|fcr"].to_numpy() == pytest.approx([5.0] * 4, abs=TOL)
    assert n.storage_units_t.state_of_charge[STRING].to_numpy() == pytest.approx([22.5] * 4, abs=TOL)
    assert n.objective == pytest.approx(-200.0, abs=1e-4)
    # The DC-03 hold stays: no scheduled discharge.
    assert (n.storage_units_t.p_dispatch[STRING].abs() <= TOL).all()


def test_without_headroom_nothing_is_committed():
    """The floor is the whole usable energy (a string sized exactly for its
    backup): no up energy and no down room, so r = 0 and nothing is earned."""
    n = _site()
    frame = _solve(n, [_product()], {STRING: 25.0})
    assert frame.to_numpy() == pytest.approx(0.0, abs=TOL)
    assert n.objective == pytest.approx(0.0, abs=1e-6)


def test_the_floor_holds_in_every_snapshot_on_both_sides():
    n = _site(snaps=6)
    up = _product("afrr_up", kind="secondary", direction="up", sustain_h=1.0)
    frame = _solve(n, [up], {STRING: 20.0}, prices={"afrr_up": np.arange(6.0) + 1})
    soc = n.storage_units_t.state_of_charge[STRING]
    need = frame[f"{STRING}|afrr_up"] * 1.0
    assert (soc - need >= 20.0 - TOL).all()
    assert (soc.shift(1).fillna(soc.iloc[-1]) - need >= 20.0 - TOL).all()
    # Up only: the store sits full and the whole 5 MWh above the floor backs 5 MW for 1 h.
    assert need.to_numpy() == pytest.approx([5.0] * 6, abs=TOL)


def test_up_power_reads_availability_on_a_non_string_storage():
    """R1: a storage that is not a DC-03 string reads p_max_pu(t) as its
    availability: at 0.4 in one snapshot it commits 2 MW there."""
    n = _site(hold=False)
    n.storage_units.rename(index={STRING: "ups_battery"}, inplace=True)
    n.storage_units_t.p_max_pu = pd.DataFrame({"ups_battery": [1.0, 0.4, 1.0, 1.0]}, index=n.snapshots)
    up = _product("afrr_up", kind="secondary", direction="up", sustain_h=1.0)
    frame = _solve(n, [up], {"ups_battery": 15.0})
    assert frame["ups_battery|afrr_up"].to_numpy() == pytest.approx([5.0, 2.0, 5.0, 5.0], abs=TOL)


def test_a_product_cap_binds():
    n = _site()
    frame = _solve(n, [_product(max_commit_mw=3.0)], {STRING: 20.0})
    assert frame.to_numpy() == pytest.approx(3.0, abs=TOL)


def test_weights_are_objective_hours():
    """A snapshot of weight 3 earns 3 h × price × r in the objective."""
    n = _site()
    n.snapshot_weightings["objective"] = 3.0   # generators and stores stay 1
    _solve(n, [_product()], {STRING: 20.0})
    assert n.objective == pytest.approx(-3 * 200.0, abs=1e-4)


def test_no_products_leaves_the_chain_untouched():
    n = _site()
    fn = object()
    assert RP._wrap_with_reserve_products(n, fn, _cfg([]), backup=({}, frozenset())) is fn
    assert RP._wrap_with_reserve_products(n, fn, SimpleNamespace(), backup=({}, frozenset())) is fn


# ── refusals before the solve (R8) ──────────────────────────────────────────


def _refused(n, products, floors):
    _bind(n, [p for p in products], {p.id: 1.0 for p in products})
    with pytest.raises(RP.ReserveRefusal) as exc:
        RP._wrap_with_reserve_products(n, None, _cfg(products), backup=(floors, frozenset({STRING})))
    return exc.value.kind


def test_a_storage_without_a_floor_is_not_eligible():
    assert _refused(_site(), [_product()], {}) == RP.NOT_ELIGIBLE
    assert _refused(_site(), [_product()], {STRING: float("nan")}) == RP.NOT_ELIGIBLE
    assert _refused(_site(), [_product(storages=["other"])], {STRING: 20.0}) == RP.NOT_ELIGIBLE


def test_a_floor_above_the_usable_energy_is_refused():
    assert _refused(_site(), [_product()], {STRING: 25.5}) == RP.FLOOR_EXCEEDS


def test_an_unbound_or_stale_price_is_refused():
    n = _site()
    p = _product()
    with pytest.raises(RP.ReserveRefusal) as exc:
        RP._wrap_with_reserve_products(n, None, _cfg([p]), backup=({STRING: 20.0}, frozenset({STRING})))
    assert exc.value.kind == RP.PRICE_MISSING
    _bind(n, [p], {"fcr": 1.0})
    newer = p.model_copy(update={"capacity_price": p.capacity_price.model_copy(update={"version": 2})})
    with pytest.raises(RP.ReserveRefusal) as exc:
        RP._wrap_with_reserve_products(n, None, _cfg([newer]), backup=({STRING: 20.0}, frozenset({STRING})))
    assert exc.value.kind == RP.PRICE_STALE


def test_a_non_cyclic_initial_soc_below_the_floor_is_refused():
    n = _site()
    n.storage_units.at[STRING, "cyclic_state_of_charge"] = False
    n.storage_units.at[STRING, "state_of_charge_initial"] = 10.0
    assert _refused(n, [_product()], {STRING: 20.0}) == RP.INITIAL_SOC


def test_the_initial_soc_path_bounds_the_first_snapshot():
    """R2, non-cyclic: soc(t0−1) is state_of_charge_initial = 22, so the first
    snapshot's up reserve is at most (22 − 20) / 1 h = 2 MW."""
    n = _site()
    n.storage_units.at[STRING, "cyclic_state_of_charge"] = False
    n.storage_units.at[STRING, "state_of_charge_initial"] = 22.0
    up = _product("afrr_up", kind="secondary", direction="up", sustain_h=1.0)
    frame = _solve(n, [up], {STRING: 20.0})
    assert frame[f"{STRING}|afrr_up"].iloc[0] == pytest.approx(2.0, abs=TOL)


# ── the model ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kw", [
    {"kind": "fast_symmetric", "direction": "up"},
    {"kind": "secondary", "direction": "symmetric"},
    {"sustain_h": 0.0}, {"sustain_h": float("nan")}, {"max_commit_mw": -1.0},
])
def test_the_product_model_refuses_bad_shapes(kw):
    base = {"id": "x", "kind": "secondary", "direction": "up", "sustain_h": 1.0, "capacity_price": _ref("x")}
    with pytest.raises(ValueError):
        ReserveProduct(**{**base, **kw})


def test_the_outage_redispatch_carries_no_reserves():
    import dataclasses

    from services.solver_service import SolverConfig

    class C(SimpleNamespace):
        def model_copy(self, update):
            return C(**{**self.__dict__, **update})

    cfg = dataclasses.replace(SolverConfig())
    cfg.commercial = C(reserve_products=[_product()])
    out = RP.without_reserves(cfg)
    assert RP.products_of(out) == [] and RP.products_of(cfg) != []
    # The solve's own form: a plain dict.
    cfg.commercial = {"poc_link": "poc", "reserve_products": [_product().model_dump(mode="json")]}
    out = RP.without_reserves(cfg)
    assert RP.products_of(out) == [] and RP.products_of(cfg)[0].id == "fcr"
    assert cfg.commercial["reserve_products"] and out.commercial["poc_link"] == "poc"


@pytest.mark.parametrize("direction, sustain_h, want", [("up", 1.0, 4.5), ("down", 2.0, 5.0 / 1.8)])
def test_efficiencies_sit_on_the_right_side(direction, sustain_h, want):
    """η 0.9 each way, floor 20, energy 25. Up: soc − r·1/0.9 ≥ 20 at soc 25
    gives r = 4.5. Down: the floor holds (soc ≥ 20) and soc + r·2·0.9 ≤ 25
    gives r = 5/1.8 (η misplaced, 5/(2/0.9) = 2.25)."""
    n = _site(eta=0.9)
    p = _product(f"x_{direction}", kind="secondary", direction=direction, sustain_h=sustain_h)
    frame = _solve(n, [p], {STRING: 20.0})
    assert frame.iloc[:, 0].to_numpy() == pytest.approx([want] * 4, abs=1e-5)
    assert (n.storage_units_t.state_of_charge[STRING] >= 20.0 - TOL).all()


def test_two_investment_periods_weight_revenue_by_the_period_weight():
    """R6/L1: the LP weights revenue as PyPSA weights marginal cost, hours ×
    ipw.objective: each period repeats the 4 snapshots, 4 × 10 × 5 MW at 1.0
    and at 0.5 = 300 (without the period weight, 400)."""
    n = _site()
    n.set_investment_periods([2030, 2035])
    n.investment_period_weightings.loc[:, "objective"] = [1.0, 0.5]
    n.investment_period_weightings.loc[:, "years"] = [5.0, 5.0]
    n.storage_units.at[STRING, "cyclic_state_of_charge_per_period"] = True
    n.storage_units_t.p_max_pu = pd.DataFrame(0.0, index=n.snapshots, columns=[STRING])
    _bind(n, [_product()], {"fcr": 10.0})
    fn = RP._wrap_with_reserve_products(n, None, _cfg([_product()]), backup=({STRING: 20.0}, frozenset({STRING})))
    status, _ = n.optimize(solver_name="highs", extra_functionality=fn, multi_investment_periods=True,
                           include_objective_constant=False)
    assert status == "ok"
    assert n.objective == pytest.approx(-300.0, abs=1e-4)
    hours, period = RP.reserve_weights(n)
    assert period.to_numpy() == pytest.approx([1.0] * 4 + [0.5] * 4)
    assert hours.to_numpy() == pytest.approx([1.0] * 8)


# ── R1: the DC-03 hold is not availability ─────────────────────────────────


def test_a_string_reserves_against_its_static_availability_never_dispatching():
    """The string's p_max_pu series (0) is DC-03's hold: scheduled discharge
    stays 0, and up-reserve is bounded by p_nom × static p_max_pu = 4 MW."""
    n = _site()
    n.storage_units.at[STRING, "p_max_pu"] = 0.8
    up = _product("afrr_up", kind="secondary", direction="up", sustain_h=1.0)
    frame = _solve(n, [up], {STRING: 20.0})
    assert frame[f"{STRING}|afrr_up"].to_numpy() == pytest.approx([4.0] * 4, abs=TOL)
    assert (n.storage_units_t.p_dispatch[STRING].abs() <= TOL).all()


def test_a_storage_merely_named_like_a_string_reads_its_series():
    """Strings come from the spec's sizing, never a name glob: a storage named
    backup_bess_x outside that set takes p_max_pu(t), 0.4 in one snapshot."""
    n = _site(hold=False)
    n.storage_units.rename(index={STRING: "backup_bess_x"}, inplace=True)
    n.storage_units_t.p_max_pu = pd.DataFrame({"backup_bess_x": [1.0, 0.4, 1.0, 1.0]}, index=n.snapshots)
    up = _product("afrr_up", kind="secondary", direction="up", sustain_h=1.0)
    _bind(n, [up], {"afrr_up": 10.0})
    fn = RP._wrap_with_reserve_products(n, None, _cfg([up]), backup=({"backup_bess_x": 15.0}, frozenset({STRING})))
    n.optimize(solver_name="highs", extra_functionality=fn, include_objective_constant=False)
    RP.persist_solution(n)
    frame, _ = RP.solved(n)
    assert frame["backup_bess_x|afrr_up"].to_numpy() == pytest.approx([5.0, 2.0, 5.0, 5.0], abs=TOL)


def test_a_string_whose_hold_was_edited_is_refused():
    n = _site()
    n.storage_units_t.p_max_pu.loc[n.snapshots[1], STRING] = 0.5
    assert _refused(n, [_product()], {STRING: 20.0}) == RP.STRING_AVAIL


# ── soc(t−1) through PyPSA's balance: standing loss and weighted snapshots ──


def test_the_previous_soc_follows_pypsa_with_standing_loss_and_weighted_hours():
    """Non-cyclic from 24 MWh, 1 %/h standing loss, 2-hour snapshots: at the
    first snapshot soc(t−1) is the initial 24, so r ≤ (24 − 20)/1 h = 4 MW,
    although the store could charge to 25 within it. Every later snapshot
    holds the floor on PyPSA's own previous SoC."""
    n = _site()
    n.snapshot_weightings["stores"] = 2.0
    n.snapshot_weightings["objective"] = 2.0
    n.storage_units.at[STRING, "standing_loss"] = 0.01
    n.storage_units.at[STRING, "cyclic_state_of_charge"] = False
    n.storage_units.at[STRING, "state_of_charge_initial"] = 24.0
    up = _product("afrr_up", kind="secondary", direction="up", sustain_h=1.0)
    frame = _solve(n, [up], {STRING: 20.0}, prices={"afrr_up": 100.0})
    r = frame[f"{STRING}|afrr_up"]
    assert r.iloc[0] == pytest.approx(4.0, abs=1e-5)
    soc = n.storage_units_t.state_of_charge[STRING]
    prev = pd.concat([pd.Series([24.0]), soc.iloc[:-1].reset_index(drop=True)]).to_numpy()
    assert (prev - r.to_numpy() >= 20.0 - 1e-5).all()

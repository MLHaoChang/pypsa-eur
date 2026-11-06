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


# ── the floor from a saved DC project (GK A R3, R7, R9) ─────────────────────


def _dc_project(tmp_path, backup: dict, tid: str = "eh_datacenter"):
    """A saved data-centre project as ``project_spec`` reads it: the
    template's network built from a dc_load spec, its island sidecar and its
    ``eh_template.json``."""
    import json

    from models.flex_archetypes import DataCentreLoadSpec
    from project_templates import eh_templates as t
    from services.adequacy import dc_backup

    spec = DataCentreLoadSpec.model_validate({**t.dc_load_preset(tid), "backup": backup})
    n = t.BUILDERS[tid](load_spec=spec)
    island = t.island_config(tid, None, load_spec=spec)
    (tmp_path / "island_config.json").write_text(json.dumps(island))
    (tmp_path / dc_backup.META_FILE).write_text(
        json.dumps({"id": tid, "dc_load": {"spec": spec.model_dump(mode="json")}}))
    return n


def _strings(n) -> list[str]:
    from services.adequacy import dc_backup

    return [s for s in n.storage_units.index if s.startswith(dc_backup.STRING_PREFIX)]


def test_the_floor_of_a_saved_hybrid_project_is_each_strings_sized_energy(tmp_path):
    n = _dc_project(tmp_path, {"mode": "hybrid"})
    floors, strings = RP.derive_backup(n, tmp_path)
    built = _strings(n)
    assert built and sorted(strings) == sorted(built)  # spares included (GK A Q2)
    su = n.storage_units
    for s in built:
        # No headroom: each string is built holding exactly its usable backup energy.
        assert floors[s] == pytest.approx(su.at[s, "p_nom"] * su.at[s, "max_hours"], rel=1e-12)


def test_a_spec_edit_moves_the_floor_and_a_stale_network_is_refused(tmp_path):
    from project_templates import eh_templates as t
    from services.adequacy import dc_backup

    n = _dc_project(tmp_path, {"mode": "hybrid"})
    before, strings = RP.derive_backup(n, tmp_path)
    s = sorted(strings)[0]
    product = _product(storages=[s])
    _bind(n, [product], {product.id: 10.0})
    RP.plan(n, [product], before, strings)  # the built network holds its floor
    # The spec moves; the saved network does not (save_backup never touches it).
    dc_backup.save_backup(tmp_path, t, {"mode": "hybrid", "autonomy_h": 8.0})
    after, _ = RP.derive_backup(n, tmp_path)
    assert after[s] > before[s] + 1.0
    with pytest.raises(RP.ReserveRefusal) as e:
        RP.plan(n, [product], after, strings)
    assert e.value.kind == RP.FLOOR_EXCEEDS


def test_headroom_raises_the_energy_but_never_the_floor(tmp_path):
    from models.flex_archetypes import BackupSpec

    n = _dc_project(tmp_path, {"mode": "hybrid", "reserve_headroom_h": 1.0})
    floors, strings = RP.derive_backup(n, tmp_path)
    b = BackupSpec()
    eta = np.sqrt(b.bess.round_trip_efficiency)
    su = n.storage_units
    for s in strings:
        floor = b.string_mw * (b.autonomy_h + b.ride_through_s / 3600.0) / eta
        assert floors[s] == pytest.approx(floor, rel=1e-12)
        assert su.at[s, "p_nom"] * su.at[s, "max_hours"] == pytest.approx(
            floor + b.string_mw * 1.0 / eta, rel=1e-12)


def test_a_project_without_a_saved_spec_has_nothing_eligible(tmp_path):
    n = _dc_project(tmp_path, {"mode": "hybrid"})
    (tmp_path / "eh_template.json").unlink()
    assert RP.derive_backup(n, tmp_path) == ({}, frozenset())
    assert RP.derive_backup(n, None) == ({}, frozenset())


def _ups_site(tmp_path, energy_mwh: float, ride_through_s=600.0, eta=0.9, crit_mw=10.0):
    """A genset-mode DC project whose one ride-through storage is a UPS: its
    floor is P_crit x ride_through_s / 3600 / eta_dis (GK A R7)."""
    import json

    from project_templates import eh_templates as t
    from models.flex_archetypes import DataCentreLoadSpec
    from services.adequacy import dc_backup

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "site")
    n.add("Generator", "supply", bus="site", p_nom=100.0, marginal_cost=1.0)
    n.add("Load", "crit", bus="site", p_set=pd.Series([crit_mw * 0.5, crit_mw, crit_mw * 0.8, 0.0],
                                                       index=n.snapshots))
    n.add("StorageUnit", "ups", bus="site", p_nom=5.0, max_hours=energy_mwh / 5.0,
          efficiency_store=eta, efficiency_dispatch=eta, cyclic_state_of_charge=True)
    island = {"units": {"crit": {"kind": "load", "critical": True}},
              "requirements": {"ride_through_storage": ["ups"]}}
    spec = DataCentreLoadSpec.model_validate(
        {**t.dc_load_preset("eh_datacenter"),
         "backup": {"mode": "genset", "ride_through_s": ride_through_s}})
    (tmp_path / "island_config.json").write_text(json.dumps(island))
    (tmp_path / dc_backup.META_FILE).write_text(
        json.dumps({"id": "eh_datacenter", "dc_load": {"spec": spec.model_dump(mode="json")}}))
    return n


UPS_FLOOR = 10.0 * 600.0 / 3600.0 / 0.9  # P_crit (the peak) x 600 s / 0.9


@pytest.mark.parametrize("above,want_mw", [(0.0, 0.0), (0.45, 0.45 * 0.9 / 0.5)])
def test_ups_floor_by_hand_at_and_just_above(tmp_path, above, want_mw):
    n = _ups_site(tmp_path, UPS_FLOOR + above)
    floors, strings = RP.derive_backup(n, tmp_path)
    assert strings == frozenset()
    assert floors == {"ups": pytest.approx(UPS_FLOOR, rel=1e-12)}
    up = _product("up", kind="secondary", direction="up", sustain_h=0.5)
    _bind(n, [up], {"up": 10.0})
    fn = RP._wrap_with_reserve_products(n, None, _cfg([up]), backup=(floors, strings))
    status, _ = n.optimize(solver_name="highs", extra_functionality=fn,
                           include_objective_constant=False)
    assert status == "ok"
    assert RP.persist_solution(n)
    frame, _ = RP.solved(n)
    # Full SoC; the up band reserves r x 0.5 h / 0.9 above the floor.
    assert frame["ups|up"].to_numpy() == pytest.approx(np.full(4, want_mw), abs=1e-6)


def test_ups_just_below_its_floor_is_refused(tmp_path):
    n = _ups_site(tmp_path, UPS_FLOOR - 0.01)
    floors, strings = RP.derive_backup(n, tmp_path)
    up = _product("up", kind="secondary", direction="up", sustain_h=0.5)
    _bind(n, [up], {"up": 10.0})
    with pytest.raises(RP.ReserveRefusal) as e:
        RP.plan(n, [up], floors, strings)
    assert e.value.kind == RP.FLOOR_EXCEEDS


def test_a_ups_without_its_efficiency_or_critical_load_is_not_eligible(tmp_path):
    n = _ups_site(tmp_path, UPS_FLOOR + 1.0)
    n.storage_units.loc["ups", "efficiency_dispatch"] = 0.0
    assert RP.derive_backup(n, tmp_path)[0] == {}
    n = _ups_site(tmp_path, UPS_FLOOR + 1.0)
    n.remove("Load", "crit")
    assert RP.derive_backup(n, tmp_path)[0] == {}


# ── headroom (GK A R4, GK B L8) ──────────────────────────────────────────────


def _hbk(**kw):
    from models.flex_archetypes import BackupSpec

    bess = {"power_mw": 1.0, "energy_mwh": 1.0, "round_trip_efficiency": 0.81,
            "depth_of_discharge": 0.9, "calendar_fade_per_year": 0.0,
            "cycle_fade_per_full_cycle": 0.0}
    return BackupSpec(mode="bess", bess=bess, **kw)


def test_no_headroom_dumps_and_sizes_as_before():
    from services.adequacy import dc_backup

    for spec in (_hbk(), _hbk(reserve_headroom_h=0.0)):
        assert "reserve_headroom_h" not in spec.model_dump(mode="json")
        assert "reserve_headroom_h" not in spec.model_dump(exclude_unset=True)
    plain = dc_backup.size_bess([10.0], firm_genset_mw=0.0, backup=_hbk(), redundancy="N+1", years=10)
    zero = dc_backup.size_bess([10.0], firm_genset_mw=0.0, backup=_hbk(reserve_headroom_h=0.0),
                               redundancy="N+1", years=10)
    assert plain == zero
    assert not {k for k in plain if "headroom" in k or "backup_usable" in k}
    assert "reserve_headroom_h" in _hbk(reserve_headroom_h=2.0).model_dump()


def test_headroom_adds_usable_energy_at_the_backup_energys_dod():
    from services.adequacy import dc_backup

    kw = dict(firm_genset_mw=0.0, redundancy="N+1", years=10)
    plain = dc_backup.size_bess([10.0], backup=_hbk(), **kw)
    more = dc_backup.size_bess([10.0], backup=_hbk(reserve_headroom_h=2.0), **kw)
    eta = 0.9
    assert more["e_backup_usable_mwh_per_string"] == pytest.approx(plain["e_usable_mwh_per_string"])
    assert more["e_headroom_usable_mwh_per_string"] == pytest.approx(5.0 * 2.0 / eta)
    assert more["e_usable_mwh_per_string"] == pytest.approx(
        plain["e_usable_mwh_per_string"] + 5.0 * 2.0 / eta)
    assert more["e_mwh_per_string"] == pytest.approx(more["e_usable_mwh_per_string"] / 0.9)
    assert (more["strings"], more["p_mw"]) == (plain["strings"], plain["p_mw"])
    assert {"key": "reserve_headroom_h", "value": 2.0, "unit": "h", "assumed": False,
            "source": dc_backup.SET_SOURCE} in more["inputs"]


def test_headroom_capex_is_priced_once_in_the_energy_lines():
    from services.adequacy import dc_backup

    prices = {"gas-genset.investment.overnight": 0.0, "gas-genset.investment.fom_share": 0.0,
              "gas-genset.investment.lifetime": 20.0, "battery.power.overnight": 0.0,
              "battery.power.fom_share": 0.0, "battery.power.lifetime": 20.0,
              "battery.energy.overnight": 100.0, "battery.energy.fom_share": 0.0,
              "battery.energy.lifetime": 20.0, "discount_rate": 0.0, "currency": "EUR"}
    prices = {k: prices.get(k, 0.0) for k in (*dc_backup.PACK_KEYS, *prices)}
    args = dict(genset_mw=0.0, heat_rate=2.8, run_hours_per_year=0.0, years=1, prices=prices)
    kw = dict(firm_genset_mw=0.0, redundancy="N+1", years=1)
    out = {}
    for h in (0.0, 2.0):
        b = _hbk(reserve_headroom_h=h)
        sizing = dc_backup.size_bess([10.0], backup=b, **kw)
        out[h] = (sizing, dc_backup.backup_cost_delta(sizing, b, **args))
    (s0, c0), (s2, c2) = out[0.0], out[2.0]
    assert "reserve_headroom_mwh" not in c0
    assert not [r for r in c0["inputs"] if r["key"] == "reserve_headroom_mwh"]
    head = c2["reserve_headroom_mwh"]
    assert head == pytest.approx(s2["strings"] * 5.0 * 2.0 / 0.9 / 0.9)  # usable / DoD, no fade
    assert head == pytest.approx(s2["e_mwh"] - s0["e_mwh"])
    # Exactly once: the extra energy's capex is its MWh x price, in the energy line only.
    def line(c, name):
        return next(r["npv"] for r in c["lines"] if (r["leg"], r["line"]) == ("bess", name))

    assert line(c2, "energy_capex_and_repurchase") - line(c0, "energy_capex_and_repurchase") \
        == pytest.approx(head * 100.0, rel=1e-9)
    assert line(c2, "power_capex_and_repurchase") == line(c0, "power_capex_and_repurchase")
    # The remaining-life credit scales with it (20-year life, 1-year horizon).
    assert c2["npv_bess"] - c0["npv_bess"] == pytest.approx(head * 100.0 / 20.0, rel=1e-9)

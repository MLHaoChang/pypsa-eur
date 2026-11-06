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
    return PriceSeriesRef(
        id=f"series-{pid}", version=1, hash=f"hash-{pid}-0000", source="user"
    )


def _product(
    pid="fcr", kind="fast_symmetric", direction="symmetric", sustain_h=0.5, **kw
) -> ReserveProduct:
    return ReserveProduct(
        id=pid,
        kind=kind,
        direction=direction,
        sustain_h=sustain_h,
        capacity_price=_ref(pid),
        **kw,
    )


def _site(
    snaps=4, energy_mwh=25.0, p_nom=5.0, hold=True, eta=1.0, weights=1.0
) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=snaps, freq="h"))
    n.snapshot_weightings.loc[:, :] = weights
    n.add("Bus", "site")
    n.add("Generator", "supply", bus="site", p_nom=100.0, marginal_cost=1.0)
    n.add(
        "StorageUnit",
        STRING,
        bus="site",
        p_nom=p_nom,
        max_hours=energy_mwh / p_nom,
        efficiency_store=eta,
        efficiency_dispatch=eta,
        cyclic_state_of_charge=True,
    )
    if hold:
        n.storage_units_t.p_max_pu = pd.DataFrame(
            0.0, index=n.snapshots, columns=[STRING]
        )
    return n


def _bind(n, products, prices) -> None:
    SI.write_reference_series(
        n,
        {
            RP.price_column(p.id): (
                pd.Series(prices[p.id], index=n.snapshots, dtype=float),
                p.capacity_price,
            )
            for p in products
        },
        frame=RP.PRICE_FRAME,
    )


def _cfg(products):
    return SimpleNamespace(commercial=SimpleNamespace(reserve_products=products))


def _solve(n, products, floors, prices=None):
    prices = prices or {p.id: 10.0 for p in products}
    _bind(n, products, prices)
    fn = RP._wrap_with_reserve_products(
        n, None, _cfg(products), backup=(floors, frozenset({STRING}))
    )
    status, _ = n.optimize(
        solver_name="highs", extra_functionality=fn, include_objective_constant=False
    )
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
    assert n.storage_units_t.state_of_charge[STRING].to_numpy() == pytest.approx(
        [22.5] * 4, abs=TOL
    )
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
    n.storage_units_t.p_max_pu = pd.DataFrame(
        {"ups_battery": [1.0, 0.4, 1.0, 1.0]}, index=n.snapshots
    )
    up = _product("afrr_up", kind="secondary", direction="up", sustain_h=1.0)
    frame = _solve(n, [up], {"ups_battery": 15.0})
    assert frame["ups_battery|afrr_up"].to_numpy() == pytest.approx(
        [5.0, 2.0, 5.0, 5.0], abs=TOL
    )


def test_a_product_cap_binds():
    n = _site()
    frame = _solve(n, [_product(max_commit_mw=3.0)], {STRING: 20.0})
    assert frame.to_numpy() == pytest.approx(3.0, abs=TOL)


def test_weights_are_objective_hours():
    """A snapshot of weight 3 earns 3 h × price × r in the objective."""
    n = _site()
    n.snapshot_weightings["objective"] = 3.0  # generators and stores stay 1
    _solve(n, [_product()], {STRING: 20.0})
    assert n.objective == pytest.approx(-3 * 200.0, abs=1e-4)


def test_no_products_leaves_the_chain_untouched():
    n = _site()
    fn = object()
    assert (
        RP._wrap_with_reserve_products(n, fn, _cfg([]), backup=({}, frozenset())) is fn
    )
    assert (
        RP._wrap_with_reserve_products(
            n, fn, SimpleNamespace(), backup=({}, frozenset())
        )
        is fn
    )


# ── refusals before the solve (R8) ──────────────────────────────────────────


def _refused(n, products, floors):
    _bind(n, [p for p in products], {p.id: 1.0 for p in products})
    with pytest.raises(RP.ReserveRefusal) as exc:
        RP._wrap_with_reserve_products(
            n, None, _cfg(products), backup=(floors, frozenset({STRING}))
        )
    return exc.value.kind


def test_a_storage_without_a_floor_is_not_eligible():
    assert _refused(_site(), [_product()], {}) == RP.NOT_ELIGIBLE
    assert _refused(_site(), [_product()], {STRING: float("nan")}) == RP.NOT_ELIGIBLE
    assert (
        _refused(_site(), [_product(storages=["other"])], {STRING: 20.0})
        == RP.NOT_ELIGIBLE
    )


def test_a_floor_above_the_usable_energy_is_refused():
    assert _refused(_site(), [_product()], {STRING: 25.5}) == RP.FLOOR_EXCEEDS


def test_an_unbound_or_stale_price_is_refused():
    n = _site()
    p = _product()
    with pytest.raises(RP.ReserveRefusal) as exc:
        RP._wrap_with_reserve_products(
            n, None, _cfg([p]), backup=({STRING: 20.0}, frozenset({STRING}))
        )
    assert exc.value.kind == RP.PRICE_MISSING
    _bind(n, [p], {"fcr": 1.0})
    newer = p.model_copy(
        update={"capacity_price": p.capacity_price.model_copy(update={"version": 2})}
    )
    with pytest.raises(RP.ReserveRefusal) as exc:
        RP._wrap_with_reserve_products(
            n, None, _cfg([newer]), backup=({STRING: 20.0}, frozenset({STRING}))
        )
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


@pytest.mark.parametrize(
    "kw",
    [
        {"kind": "fast_symmetric", "direction": "up"},
        {"kind": "secondary", "direction": "symmetric"},
        {"sustain_h": 0.0},
        {"sustain_h": float("nan")},
        {"max_commit_mw": -1.0},
    ],
)
def test_the_product_model_refuses_bad_shapes(kw):
    base = {
        "id": "x",
        "kind": "secondary",
        "direction": "up",
        "sustain_h": 1.0,
        "capacity_price": _ref("x"),
    }
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
    cfg.commercial = {
        "poc_link": "poc",
        "reserve_products": [_product().model_dump(mode="json")],
    }
    out = RP.without_reserves(cfg)
    assert RP.products_of(out) == [] and RP.products_of(cfg)[0].id == "fcr"
    assert out.commercial == {
        "poc_link": "poc"
    }  # the config as it reads without products
    cfg.commercial = {"reserve_products": [_product().model_dump(mode="json")]}
    assert RP.without_reserves(cfg).commercial is None
    assert cfg.commercial["reserve_products"] and out.commercial["poc_link"] == "poc"


@pytest.mark.parametrize(
    "direction, sustain_h, want", [("up", 1.0, 4.5), ("down", 2.0, 5.0 / 1.8)]
)
def test_efficiencies_sit_on_the_right_side(direction, sustain_h, want):
    """η 0.9 each way, floor 20, energy 25. Up: soc − r·1/0.9 ≥ 20 at soc 25
    gives r = 4.5. Down: the floor holds (soc ≥ 20) and soc + r·2·0.9 ≤ 25
    gives r = 5/1.8 (η misplaced, 5/(2/0.9) = 2.25)."""
    n = _site(eta=0.9)
    p = _product(
        f"x_{direction}", kind="secondary", direction=direction, sustain_h=sustain_h
    )
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
    fn = RP._wrap_with_reserve_products(
        n, None, _cfg([_product()]), backup=({STRING: 20.0}, frozenset({STRING}))
    )
    status, _ = n.optimize(
        solver_name="highs",
        extra_functionality=fn,
        multi_investment_periods=True,
        include_objective_constant=False,
    )
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
    n.storage_units_t.p_max_pu = pd.DataFrame(
        {"backup_bess_x": [1.0, 0.4, 1.0, 1.0]}, index=n.snapshots
    )
    up = _product("afrr_up", kind="secondary", direction="up", sustain_h=1.0)
    _bind(n, [up], {"afrr_up": 10.0})
    fn = RP._wrap_with_reserve_products(
        n, None, _cfg([up]), backup=({"backup_bess_x": 15.0}, frozenset({STRING}))
    )
    n.optimize(
        solver_name="highs", extra_functionality=fn, include_objective_constant=False
    )
    RP.persist_solution(n)
    frame, _ = RP.solved(n)
    assert frame["backup_bess_x|afrr_up"].to_numpy() == pytest.approx(
        [5.0, 2.0, 5.0, 5.0], abs=TOL
    )


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
    prev = pd.concat(
        [pd.Series([24.0]), soc.iloc[:-1].reset_index(drop=True)]
    ).to_numpy()
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

    spec = DataCentreLoadSpec.model_validate(
        {**t.dc_load_preset(tid), "backup": backup}
    )
    n = t.BUILDERS[tid](load_spec=spec)
    island = t.island_config(tid, None, load_spec=spec)
    (tmp_path / "island_config.json").write_text(json.dumps(island))
    (tmp_path / dc_backup.META_FILE).write_text(
        json.dumps({"id": tid, "dc_load": {"spec": spec.model_dump(mode="json")}})
    )
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
        assert floors[s] == pytest.approx(
            su.at[s, "p_nom"] * su.at[s, "max_hours"], rel=1e-12
        )


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
            floor + b.string_mw * 1.0 / eta, rel=1e-12
        )


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
    n.add(
        "Load",
        "crit",
        bus="site",
        p_set=pd.Series(
            [crit_mw * 0.5, crit_mw, crit_mw * 0.8, 0.0], index=n.snapshots
        ),
    )
    n.add(
        "StorageUnit",
        "ups",
        bus="site",
        p_nom=5.0,
        max_hours=energy_mwh / 5.0,
        efficiency_store=eta,
        efficiency_dispatch=eta,
        cyclic_state_of_charge=True,
    )
    island = {
        "units": {"crit": {"kind": "load", "critical": True}},
        "requirements": {"ride_through_storage": ["ups"]},
    }
    spec = DataCentreLoadSpec.model_validate(
        {
            **t.dc_load_preset("eh_datacenter"),
            "backup": {"mode": "genset", "ride_through_s": ride_through_s},
        }
    )
    (tmp_path / "island_config.json").write_text(json.dumps(island))
    (tmp_path / dc_backup.META_FILE).write_text(
        json.dumps(
            {"id": "eh_datacenter", "dc_load": {"spec": spec.model_dump(mode="json")}}
        )
    )
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
    status, _ = n.optimize(
        solver_name="highs", extra_functionality=fn, include_objective_constant=False
    )
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

    bess = {
        "power_mw": 1.0,
        "energy_mwh": 1.0,
        "round_trip_efficiency": 0.81,
        "depth_of_discharge": 0.9,
        "calendar_fade_per_year": 0.0,
        "cycle_fade_per_full_cycle": 0.0,
    }
    return BackupSpec(mode="bess", bess=bess, **kw)


def test_no_headroom_dumps_and_sizes_as_before():
    from services.adequacy import dc_backup

    for spec in (_hbk(), _hbk(reserve_headroom_h=0.0)):
        assert "reserve_headroom_h" not in spec.model_dump(mode="json")
        assert "reserve_headroom_h" not in spec.model_dump(exclude_unset=True)
    plain = dc_backup.size_bess(
        [10.0], firm_genset_mw=0.0, backup=_hbk(), redundancy="N+1", years=10
    )
    zero = dc_backup.size_bess(
        [10.0],
        firm_genset_mw=0.0,
        backup=_hbk(reserve_headroom_h=0.0),
        redundancy="N+1",
        years=10,
    )
    assert plain == zero
    assert not {k for k in plain if "headroom" in k or "backup_usable" in k}
    assert "reserve_headroom_h" in _hbk(reserve_headroom_h=2.0).model_dump()


def test_headroom_adds_usable_energy_at_the_backup_energys_dod():
    from services.adequacy import dc_backup

    kw = dict(firm_genset_mw=0.0, redundancy="N+1", years=10)
    plain = dc_backup.size_bess([10.0], backup=_hbk(), **kw)
    more = dc_backup.size_bess([10.0], backup=_hbk(reserve_headroom_h=2.0), **kw)
    eta = 0.9
    assert more["e_backup_usable_mwh_per_string"] == pytest.approx(
        plain["e_usable_mwh_per_string"]
    )
    assert more["e_headroom_usable_mwh_per_string"] == pytest.approx(5.0 * 2.0 / eta)
    assert more["e_usable_mwh_per_string"] == pytest.approx(
        plain["e_usable_mwh_per_string"] + 5.0 * 2.0 / eta
    )
    assert more["e_mwh_per_string"] == pytest.approx(
        more["e_usable_mwh_per_string"] / 0.9
    )
    assert (more["strings"], more["p_mw"]) == (plain["strings"], plain["p_mw"])
    assert {
        "key": "reserve_headroom_h",
        "value": 2.0,
        "unit": "h",
        "assumed": False,
        "source": dc_backup.SET_SOURCE,
    } in more["inputs"]


def test_headroom_capex_is_priced_once_in_the_energy_lines():
    from services.adequacy import dc_backup

    prices = {
        "gas-genset.investment.overnight": 0.0,
        "gas-genset.investment.fom_share": 0.0,
        "gas-genset.investment.lifetime": 20.0,
        "battery.power.overnight": 0.0,
        "battery.power.fom_share": 0.0,
        "battery.power.lifetime": 20.0,
        "battery.energy.overnight": 100.0,
        "battery.energy.fom_share": 0.0,
        "battery.energy.lifetime": 20.0,
        "discount_rate": 0.0,
        "currency": "EUR",
    }
    prices = {k: prices.get(k, 0.0) for k in (*dc_backup.PACK_KEYS, *prices)}
    args = dict(
        genset_mw=0.0, heat_rate=2.8, run_hours_per_year=0.0, years=1, prices=prices
    )
    kw = dict(firm_genset_mw=0.0, redundancy="N+1", years=1)
    out = {}
    for h in (0.0, 2.0):
        b = _hbk(reserve_headroom_h=h)
        sizing = dc_backup.size_bess([10.0], backup=b, **kw)
        out[h] = (sizing, dc_backup.backup_cost_delta(sizing, b, **args))
    (s0, c0), (s2, c2) = out[0.0], out[2.0]
    assert "reserve_headroom_mwh" not in c0
    assert not [r for r in c0["inputs"] if r["key"] == "reserve_headroom_mwh"]
    # GK B L8: headroom is disclosed as a cost with no revenue of its own.
    assert dc_backup.HEADROOM_NOTE not in c0["notes"]
    assert dc_backup.HEADROOM_NOTE in c2["notes"]
    head = c2["reserve_headroom_mwh"]
    assert head == pytest.approx(
        s2["strings"] * 5.0 * 2.0 / 0.9 / 0.9
    )  # usable / DoD, no fade
    assert head == pytest.approx(s2["e_mwh"] - s0["e_mwh"])

    # Exactly once: the extra energy's capex is its MWh x price, in the energy line only.
    def line(c, name):
        return next(
            r["npv"] for r in c["lines"] if (r["leg"], r["line"]) == ("bess", name)
        )

    assert line(c2, "energy_capex_and_repurchase") - line(
        c0, "energy_capex_and_repurchase"
    ) == pytest.approx(head * 100.0, rel=1e-9)
    assert line(c2, "power_capex_and_repurchase") == line(
        c0, "power_capex_and_repurchase"
    )
    # The remaining-life credit scales with it (20-year life, 1-year horizon).
    assert c2["npv_bess"] - c0["npv_bess"] == pytest.approx(
        head * 100.0 / 20.0, rel=1e-9
    )


# ── settlement (GK B L1, L3, L5, L7; GK A R6) ────────────────────────────────


def _lines(n, products, floors):
    from services.results import billing as B

    return B.reserve_lines(
        n, _cfg(products), "site", backup=(floors, frozenset({STRING}))
    )


def test_two_periods_settle_per_period_without_the_period_weight(tmp_path):
    """Each period: 4 h x 10/MWh x 5 MW = 200, at period weights 1.0 and 0.5
    (the LP's 300 is the objective's; settlement leaves years to the case)."""
    n = _site()
    n.set_investment_periods([2030, 2035])
    n.investment_period_weightings.loc[:, "objective"] = [1.0, 0.5]
    n.storage_units.at[STRING, "cyclic_state_of_charge_per_period"] = True
    n.storage_units_t.p_max_pu = pd.DataFrame(0.0, index=n.snapshots, columns=[STRING])
    fcr = _product()
    _bind(n, [fcr], {"fcr": 10.0})
    floors = {STRING: 20.0}
    fn = RP._wrap_with_reserve_products(
        n, None, _cfg([fcr]), backup=(floors, frozenset({STRING}))
    )
    status, _ = n.optimize(
        solver_name="highs",
        extra_functionality=fn,
        multi_investment_periods=True,
        include_objective_constant=False,
    )
    assert status == "ok" and RP.persist_solution(n)
    lines, flags = _lines(n, [fcr], floors)
    assert flags == []
    assert [(ln.period, ln.amount) for ln in lines] == [
        (2030, pytest.approx(200.0, abs=1e-4)),
        (2035, pytest.approx(200.0, abs=1e-4)),
    ]
    assert {(ln.payer, ln.payee, ln.value_stream, ln.contract_id) for ln in lines} == {
        ("tso", "site", "reserve_capacity", "reserve:fcr")
    }
    assert all(ln.flags == [] for ln in lines)  # symmetric: no activation flag
    # A saved and reloaded multi-period solve is still current (the key reads the
    # stored weightings, not the live-solve `_multi_invest` flag) ...
    path = tmp_path / "n.nc"
    n.export_to_netcdf(path)
    m = pypsa.Network(path)
    assert RP.solved(m)[1] == []
    # ... and a period weight changed after the solve is stale (GK B L5).
    m.investment_period_weightings.loc[2035, "objective"] = 0.25
    assert RP.solved(m) == (
        None,
        ["reserve_not_solved", "reserve_inputs_changed_since_solve"],
    )


def test_three_hourly_snapshots_earn_three_hours_each():
    n = _site(weights=3.0)
    up = _product("up", kind="secondary", direction="up", sustain_h=0.5)
    floors = {STRING: 20.0}
    frame = _solve(n, [up], floors, {"up": 10.0})
    r = frame[f"{STRING}|up"].to_numpy()
    (line,), _ = _lines(n, [up], floors)
    assert r == pytest.approx(np.full(4, 5.0), abs=1e-6)
    assert line.amount == pytest.approx(float((3.0 * 10.0 * r).sum()), abs=1e-6)  # 600
    assert line.flags == ["activation_not_valued"]


def test_a_solved_product_with_no_mw_settles_to_zero_unflagged():
    n = _site(energy_mwh=20.0)  # no headroom above the 20 MWh floor
    up = _product("up", kind="secondary", direction="up", sustain_h=0.5)
    floors = {STRING: 20.0}
    _solve(n, [up], floors, {"up": 10.0})
    (line,), flags = _lines(n, [up], floors)
    assert (line.amount, flags) == (0.0, [])
    assert "reserve_not_solved" not in line.flags


def test_no_solve_settles_to_none_never_zero():
    n = _site()
    fcr = _product()
    _bind(n, [fcr], {"fcr": 10.0})
    (line,), flags = _lines(n, [fcr], {STRING: 20.0})
    assert line.amount is None and "reserve_not_solved" in line.flags
    assert flags == ["reserve_not_solved"]


@pytest.mark.parametrize("change", ["product", "floor"])
def test_a_stale_solve_settles_to_none(change):
    n = _site()
    fcr = _product()
    floors = {STRING: 20.0}
    _solve(n, [fcr], floors)
    now_products, now_floors = [fcr], dict(floors)
    if change == "product":
        now_products = [_product(max_commit_mw=1.0)]
    else:
        now_floors[STRING] = 21.0  # the DC-03 spec moved since the solve
    (line,), flags = _lines(n, now_products, now_floors)
    assert line.amount is None
    assert {"reserve_not_solved", "reserve_inputs_changed_since_solve"} <= set(
        line.flags
    )


def test_an_hours_change_after_the_solve_is_stale():
    """GK B L5: the settlement multiplies the persisted MW by the current hours;
    a solve made under other hours is stale, never re-priced."""
    n = _site()
    fcr = _product()
    floors = {STRING: 20.0}
    _solve(n, [fcr], floors)
    n.snapshot_weightings.loc[n.snapshots[0], "objective"] *= 2.0
    frame, flags = RP.solved(n)
    assert frame is None
    assert flags == ["reserve_not_solved", "reserve_inputs_changed_since_solve"]
    (line,), _ = _lines(n, [fcr], floors)
    assert line.amount is None and "reserve_not_solved" in line.flags


def test_a_missing_planned_column_is_no_solve_never_zero():
    """GK B L5: a current solve whose frame lacks a planned ``<storage>|<product>``
    column reads as not solved, never as 0 MW committed."""
    n = _site()
    fcr = _product()
    floors = {STRING: 20.0}
    _solve(n, [fcr], floors)
    frame = n.storage_units_t[RP.OUT_FRAME]
    n.storage_units_t[RP.OUT_FRAME] = frame.drop(columns=[f"{STRING}|fcr"]).assign(
        other=0.0
    )
    assert RP.solved(n) == (None, ["reserve_not_solved"])
    (line,), _ = _lines(n, [fcr], floors)
    assert line.amount is None and "reserve_not_solved" in line.flags


def test_a_solve_survives_a_netcdf_round_trip(tmp_path):
    """The weights key reads stored weightings only, so a saved and reloaded
    solve is still current."""
    n = _site()
    fcr = _product()
    _solve(n, [fcr], {STRING: 20.0})
    path = tmp_path / "n.nc"
    n.export_to_netcdf(path)
    m = pypsa.Network(path)
    frame, flags = RP.solved(m)
    assert flags == [] and f"{STRING}|fcr" in frame.columns


def test_no_products_settle_nothing():
    from services.results import billing as B

    assert B.reserve_lines(_site(), _cfg([]), backup=({}, frozenset())) == ([], [])


def test_the_objective_revenue_is_the_lps_own_term():
    n = _site()
    _solve(n, [_product()], {STRING: 20.0})
    assert RP.objective_revenue(n) == pytest.approx(-n.objective, abs=1e-6)
    assert RP.objective_revenue(_site()) == 0.0


def test_the_objective_decomposition_names_the_reserve_revenue():
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition
    from services.solver_service import SolverConfig

    n = _site()
    n.add("Load", "l", bus="site", p_set=1.0)  # 4 MWh at 1/MWh: cost 4
    _solve(n, [_product()], {STRING: 20.0})
    cfg = SolverConfig()
    od = compute_objective_decomposition(n, compute_cost_breakdown(n, cfg), cfg)
    assert od["reserve_revenue_eur"] == pytest.approx(
        200.0, abs=1e-6
    )  # 4 h x 10 x 5 MW
    assert od["lp_total"] == pytest.approx(4.0 - 200.0, abs=1e-6)
    assert od["residual_gap_eur"] == pytest.approx(0.0, abs=1e-6)
    plain = _site()
    plain.add("Load", "l", bus="site", p_set=1.0)
    plain.optimize(solver_name="highs")
    assert "reserve_revenue_eur" not in compute_objective_decomposition(
        plain, compute_cost_breakdown(plain, cfg), cfg
    )


# ── the ledger and the investment case (GK B L6, L9) ─────────────────────────


def _reserve_row(amount, flags=()):
    return {
        "period": "_",
        "contract_id": "reserve:fcr",
        "payer": "tso",
        "payee": "site",
        "value_stream": "reserve_capacity",
        "quantity_mwh": None,
        "amount": amount,
        "flags": list(flags),
    }


def test_a_reserve_payment_is_an_ancillary_line_from_the_counterparty():
    from services.commercial import participants as P
    from tests.test_value_flow_ledger import VF, _inputs

    ledger = P.build_ledger(
        _inputs(reserve=[_reserve_row(30.0, ["activation_not_valued"])]), VF
    )
    (ln,) = [x for x in ledger.periods["_"] if x.source == "reserve"]
    assert (ln.payer, ln.payee, ln.value_stream, ln.source_id, ln.amount) == (
        "tso",
        "site",
        "ancillary",
        "fcr",
        30.0,
    )
    assert ln.flags == ["activation_not_valued"]
    none = P.build_ledger(
        _inputs(reserve=[_reserve_row(None, ["reserve_not_solved"])]), VF
    )
    (ln,) = [x for x in none.periods["_"] if x.source == "reserve"]
    assert ln.amount is None and "reserve_not_solved" in ln.flags


def test_the_reconciliation_bridges_the_reserve_payment():
    from services.commercial import participants as P
    from tests.test_value_flow_ledger import VF, _inputs

    def rec(inputs):
        result = P.check_conservation(P.build_ledger(inputs, VF), inputs, VF)
        (c,) = [c for c in result.periods["_"].checks if c["name"] == "reconciliation"]
        return c["ok"]

    assert rec(_inputs()) is True
    assert rec(_inputs(reserve=[_reserve_row(30.0)])) is True
    assert rec(_inputs(reserve=[_reserve_row(None)])) is None  # unknown, never 0


def test_the_revenue_rows_show_reserve_only_when_the_case_has_it():
    from services.finance import revenue_streams as RS

    def rows(streams):
        lines = {f"k{i}": np.array([10.0, 10.0]) for i, _ in enumerate(streams)}
        meta = {f"k{i}": SimpleNamespace(stream=s) for i, s in enumerate(streams)}
        op = SimpleNamespace(
            status={"operating": "ok"},
            revenue=np.zeros(2),
            costs=np.zeros(2),
            tl=SimpleNamespace(n=2, years=[2030, 2031]),
            lines=lines,
            line_meta=meta,
            legs={},
            replacement=np.zeros(2),
        )
        out = RS.revenue_by_stream(op, escalation={"export": 0.01})["rows"]
        rows.last = out
        return [(r["id"], r["label"], r["values"]) for r in out]

    plain = rows(["energy_export"])
    assert [r[0] for r in plain] == list(RS.ROWS)
    got = rows(["energy_export", "ancillary"])
    assert [r[0] for r in got] == [*RS.ROWS[:-1], "reserve", "other"]
    assert ("reserve", "Reserve capacity revenue", [10.0, 10.0]) in got
    (row,) = [r for r in rows.last if r["id"] == "reserve"]
    assert (row["note"], row["escalation_class"], row["escalation_rate"]) == (
        "escalates at the export market rate",
        "export",
        0.01,
    )
    assert all("note" not in r for r in rows.last if r["id"] != "reserve")


def _cash_case(lines, escalation):
    from tests.test_finance_cashflow import _case

    return _case(lines, fin_over={"escalation": escalation})


def test_the_reserve_line_escalates_at_the_export_rate_exactly():
    """GK B L6b: hand values over the first two operating years (base 2031, COD 2033)."""
    from services.finance.case import TemplateLine
    from services.finance.cashflow import build_operating, esc_class_for
    from services.finance.timeline import build_timeline

    assert esc_class_for("ancillary") == "export"
    res = TemplateLine("reserve:fcr", "ancillary", 1_000.0, esc_class_for("ancillary"))
    case = _cash_case(
        [res], {"opex": 0.02, "tariff": 0.03, "export": 0.01, "capex": 0.02}
    )
    tl = build_timeline(case)
    op = build_operating(case, tl)
    assert op.lines["reserve:fcr"][tl.index(2033)] == pytest.approx(
        1_000.0 * 1.01**2, rel=1e-12
    )
    assert op.lines["reserve:fcr"][tl.index(2034)] == pytest.approx(
        1_000.0 * 1.01**3, rel=1e-12
    )


def test_a_missing_export_rate_never_escalates_the_reserve_line_at_another():
    from services.finance.case import TemplateLine
    from services.finance.cashflow import build_operating, esc_class_for
    from services.finance.timeline import build_timeline

    res = TemplateLine("reserve:fcr", "ancillary", 1_000.0, esc_class_for("ancillary"))
    case = _cash_case(
        [res], {"opex": 0.02, "tariff": 0.03, "ppa": 0.015, "capex": 0.02}
    )
    op = build_operating(case, build_timeline(case))
    assert "escalation_missing:export:reserve:fcr" in op.reasons["operating"]
    assert op.status["operating"] == "not_established" and op.revenue is None


def test_the_owner_generation_split_leaves_a_reserve_line_whole():
    from services.results import finance_case as FC

    acc = FC._Acc(
        stream="ancillary",
        esc_class="export",
        amount=500.0,
        source="reserve",
        source_id="fcr",
        counterparty="tso",
    )
    inputs = SimpleNamespace(
        export_split={"_": {"export_price": {("Generator", "pv"): 80.0}}}
    )
    vf = SimpleNamespace(
        asset_owners=[
            SimpleNamespace(component="Generator", asset_id="pv", owner="owner")
        ]
    )
    flags: list[str] = []
    (ln,) = FC._template_lines(
        {"reserve:fcr": acc},
        k="_",
        year=2030,
        base_year=2030,
        inputs=inputs,
        vf=vf,
        owner="owner",
        owned={("Generator", "pv")},
        owner_gens={"pv"},
        contracts={},
        fin=None,
        gen={"pv": 100.0},
        site_gens=["pv"],
        gen_links=[],
        flags=flags,
    )
    assert (ln.key, ln.amount, ln.esc_class, ln.stream, ln.degrades_with) == (
        "reserve:fcr",
        500.0,
        "export",
        "ancillary",
        None,
    )
    assert flags == []


# ── the runners (GK A R5) ────────────────────────────────────────────────────


def test_the_sizing_loop_names_reserve_products_and_its_chain_carries_the_rows(
    monkeypatch,
):
    """The loop refuses a project with commercial bindings by name (so it never
    solves without its reserves silently); its LP chain (`_prepare`, the
    project's `build_lp_chain`) carries the rows all the same."""
    from services.adequacy import sizing_loop_runner as slr
    from services.solver_service import SolverConfig

    n = _site()
    fcr = _product()
    _bind(n, [fcr], {"fcr": 10.0})
    cfg = SolverConfig(commercial={"reserve_products": [fcr.model_dump(mode="json")]})
    assert any("commercial bindings" in r for r in slr.refusals(cfg))
    monkeypatch.setattr(
        RP, "project_backup", lambda _n: ({STRING: 20.0}, frozenset({STRING}))
    )
    undo: list = []
    status, _ = n.optimize(solver_name="highs", **slr._prepare(n, cfg, undo))
    slr._restore(undo)
    assert status == "ok" and RP.persist_solution(n)
    assert RP.solved(n)[0][f"{STRING}|fcr"].to_numpy() == pytest.approx(
        np.full(4, 5.0), abs=1e-6
    )


@pytest.mark.live_solve
def test_dc03_assess_is_byte_equal_with_reserve_products_set():
    """The DtC proof re-dispatches an outage: the obligations lapse with the grid."""
    import json

    from project_templates import eh_templates as t
    from services.adequacy import dc_backup
    from services.solver_service import SolverConfig
    from tests.test_dc_backup import _dc, _run

    spec = _dc("bess")
    genset = spec.model_copy(
        update={"backup": spec.backup.model_copy(update={"mode": "genset"})}
    )
    fcr = _product()
    outs = []
    for commercial in (None, {"reserve_products": [fcr.model_dump(mode="json")]}):
        run = _run()
        run["cfg"] = SolverConfig(voll=500.0, commercial=commercial)
        outs.append(
            dc_backup.assess(
                t.build_eh_datacenter(load_spec=genset),
                t.island_config("eh_datacenter"),
                spec,
                years=20,
                prices=dc_backup.pack_prices(t.TEMPLATE_PACK_VERSION),
                **run,
            )
        )
    assert json.dumps(outs[0], sort_keys=True, default=str) == json.dumps(
        outs[1], sort_keys=True, default=str
    )


# ── the view (UX: storage view, DC backup line) ──────────────────────────────


def test_the_view_shows_floor_energy_soc_and_committed_mw():
    from services.results.reserves import reserve_view

    n = _site()
    up = _product("up", kind="secondary", direction="up", sustain_h=0.5)
    floors = {STRING: 20.0}
    _solve(n, [up], floors, {"up": 10.0})
    v = reserve_view(n, _cfg([up]), solved=True, backup=(floors, frozenset({STRING})))
    (s,) = v["storages"]
    assert (s["name"], s["string"], s["floor_mwh"], s["e_cap_mwh"]) == (
        STRING,
        True,
        20.0,
        25.0,
    )
    assert s["committed"]["up"] == pytest.approx([5.0] * 4, abs=1e-6)
    assert len(s["soc_mwh"]) == 4 and v["flags"] == []
    assert v["products"][0]["direction"] == "up" and v["lines"][0][
        "amount"
    ] == pytest.approx(200.0)
    assert "not modelled" in v["not_modelled"]
    # UX: the energy an up product ties up above the floor, r d / eta_dis = 5 x 0.5.
    assert s["reserved_up_mwh"] == pytest.approx([2.5] * 4, abs=1e-6)
    assert s["reserved_down_mwh"] == pytest.approx([0.0] * 4, abs=1e-9)


def test_reserved_energy_follows_rows_3_and_4_with_efficiencies():
    """Symmetric product, eta 0.9 both ways: up r d / 0.9 above the floor,
    down r d x 0.9 below the energy capacity, as the LP's rows 3 and 4."""
    n = _site(eta=0.9)
    fcr = _product()
    frame = _solve(n, [fcr], {STRING: 20.0})
    r = frame[f"{STRING}|fcr"].to_numpy()
    up, down = RP.reserved_energy(n, frame, [fcr], STRING)
    assert up == pytest.approx(r * 0.5 / 0.9)
    assert down == pytest.approx(r * 0.5 * 0.9)
    soc = n.storage_units_t.state_of_charge[STRING].to_numpy()
    assert (soc - up >= 20.0 - 1e-6).all() and (soc + down <= 25.0 + 1e-6).all()


def test_the_view_never_shows_zero_for_a_missing_solve():
    from services.results.reserves import reserve_view

    n = _site()
    fcr = _product()
    _bind(n, [fcr], {"fcr": 10.0})
    v = reserve_view(
        n, _cfg([fcr]), solved=False, backup=({STRING: 20.0}, frozenset({STRING}))
    )
    assert v["storages"][0]["committed"] is None and v["storages"][0]["soc_mwh"] is None
    assert v["storages"][0]["reserved_up_mwh"] is None
    assert v["flags"] == ["reserve_not_solved"] and v["lines"][0]["amount"] is None
    assert reserve_view(n, _cfg([]), solved=True, backup=({}, frozenset())) is None


def test_the_view_names_its_currency_and_money_year():
    """UX: the revenue header names the currency and money year (the investment
    case's base year for these lines: a flat horizon's weight-majority year)."""
    from services.results.reserves import reserve_view

    n = _site()
    fcr = _product()
    _bind(n, [fcr], {"fcr": 10.0})
    backup = ({STRING: 20.0}, frozenset({STRING}))
    stated = SimpleNamespace(
        commercial=SimpleNamespace(reserve_products=[fcr]), finance={"currency": "gbp"}
    )
    v = reserve_view(n, stated, solved=False, backup=backup)
    assert (v["currency"], v["money_year"]) == ("GBP", 2030)
    assert reserve_view(n, _cfg([fcr]), solved=False, backup=backup)["currency"] is None
    n.meta[RP.CURRENCY_META] = {"fcr": "EUR"}
    assert (
        reserve_view(n, _cfg([fcr]), solved=False, backup=backup)["currency"] == "EUR"
    )


def test_the_reserves_route_is_204_without_products(client):
    assert client.get("/api/results/reserves").status_code == 204


# ── the run fingerprint (GK A R4, GK B L4) ───────────────────────────────────


def test_the_floors_enter_the_fingerprint_only_with_products(tmp_path):
    """A spec edit moves the floors without touching the network: with
    products it is a new run identity; without, the fingerprint is unchanged
    (a config without products adds nothing to it)."""
    from project_templates import eh_templates as t
    from services.adequacy import dc_backup
    from services.solver_service import SolverConfig
    from services.workflow_tools import _network_input_fingerprint

    n = _dc_project(tmp_path, {"mode": "hybrid"})
    s = _strings(n)[0]
    plain = SolverConfig()
    with_products = SolverConfig(
        commercial={
            "reserve_products": [_product(storages=[s]).model_dump(mode="json")]
        }
    )

    def fp(cfg):
        return _network_input_fingerprint(
            SimpleNamespace(
                network=n,
                solver_state={"solver_config": cfg},
                user_ts={},
                storage_dir=str(tmp_path),
            )
        )

    before = (fp(plain), fp(with_products))
    assert before[0] != before[1]
    dc_backup.save_backup(tmp_path, t, {"mode": "hybrid", "autonomy_h": 8.0})
    after = (fp(plain), fp(with_products))
    assert after[0] == before[0]
    assert after[1] != before[1]

"""
U2 WP4 — `compile.commercial_from_ledger` (+ C3): the guided tariff form and
the ledger compiled into IC's `CommercialConfig`.

Plan: docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md §1,
§3.1, WP2 (port of `test_tariff_bill.py`'s pricing, refusal and export
expectations), WP4.

The GS form (`models.study.Tariff`) stays the guided intake shape (owner
decision 8); `compile` is the only code that turns it into engine input. The
compiled tariff is rated by IC's `tariff_engine.rate` here, never by GS's
`BillCalculator` (which stays only as the WP0 oracle until WP10).
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pypsa
import pytest

from models.study import Tariff
from tests.golden import site_fixture as SF
from tests.u2_targets import DE, FAKE_REF, TOU, flat_resolver, golden_copy

JAN_FEB = pd.date_range("2030-01-01", "2030-02-28 23:00", freq="h")
YEAR = pd.date_range("2025-01-01", periods=8760, freq="h")
SEVEN = {"energy", "demand", "capacity", "fixed", "network", "export_credit", "taxes_levies"}



def _form(**over) -> Tariff:
    base = dict(
        tariff_id="t", name="toy", source="illustrative", currency="EUR",
        currency_year=2026, billing_period="month",
        energy_bands=[{"label": "flat", "price_per_mwh": 100.0, "applies": {}}],
        demand_charge=None, fixed_charge_per_period=0.0, network_charges=[],
        export={"price_per_mwh": None, "series_ref": None, "cap_mw": None},
    )
    base.update(over)
    return Tariff.model_validate(base)


def _C():
    from services.study import compile as C

    return C


def _defaults():
    from services.study import library as L

    return L.load_defaults()


def _intake(tariff_id: str = DE) -> dict:
    return {**SF.site_intake(), "tariff": {"tariff_id": tariff_id}}


def _ledger(tariff_id: str = DE, defaults=None):
    from services.study import library as L
    from services.study import questions as Q

    return L.seed_ledger(Q.BESS_AT_SITE, _intake(tariff_id), defaults or _defaults())


def _compiled(tariff_id: str = DE, *, ledger=None, export_series=FAKE_REF, defaults=None):
    defaults = defaults or _defaults()
    return _C().commercial_from_ledger(
        _intake(tariff_id), ledger or _ledger(tariff_id, defaults), defaults, YEAR,
        export_series=export_series)


def _items(compiled) -> dict:
    return {i.id: i for i in compiled.config.import_tariff.items}


def _rated_rate_per_mwh(tariff, idx, item_id: str) -> np.ndarray:
    """
    IC's own rate (EUR/MWh) of one item per snapshot, from `tariff_engine.rate`
    on a 1 MW constant import (the rate column of its interval lines).
    """
    from services.commercial.tariff_engine import rate

    disp = pd.DataFrame({"import_mw": 1.0, "export_mw": 0.0}, index=idx)
    res = rate(disp, tariff, step_hours=1.0, timezone=None)
    lines = res.lines[res.lines["tariff_item"] == item_id]
    return lines["rate"].to_numpy(dtype=float) * 1000.0


# ── §3.1 bands → one `energy` item, periods per band and hour run ─────────

def test_a_flat_band_is_one_energy_item_per_kwh_at_settlement_h():
    eng = _C().tariff_to_engine(_form(), snapshots=JAN_FEB)
    [item] = eng.tariff.items
    assert (item.id, item.kind, item.unit, item.settlement, item.direction) == (
        "energy", "energy", "per_kwh", "h", "cost")
    assert [p.rate for p in item.periods] == [pytest.approx(0.1)]
    assert eng.item_component == {"energy": "energy"}


def test_bands_keep_their_order_and_split_into_contiguous_hour_runs():
    """TOU off-peak `hours=[0..7, 20..23]` is two periods with one name and rate."""
    form = _form(energy_bands=[
        {"label": "peak", "price_per_mwh": 160.0,
         "applies": {"weekdays": [0, 1, 2, 3, 4], "hours": list(range(8, 20))}},
        {"label": "night", "price_per_mwh": 90.0,
         "applies": {"weekdays": [0, 1, 2, 3, 4], "hours": [*range(0, 8), *range(20, 24)]}},
        {"label": "weekend", "price_per_mwh": 90.0, "applies": {"weekdays": [5, 6]}},
    ])
    [item] = _C().tariff_to_engine(form, snapshots=JAN_FEB).tariff.items
    got = [(p.name, p.rate, p.weekdays, p.start_hour, p.end_hour) for p in item.periods]
    assert got == [("peak", pytest.approx(0.16), [0, 1, 2, 3, 4], 8, 20),
                   ("night", pytest.approx(0.09), [0, 1, 2, 3, 4], 0, 8),
                   ("night", pytest.approx(0.09), [0, 1, 2, 3, 4], 20, 24),
                   ("weekend", pytest.approx(0.09), [5, 6], None, None)]


@pytest.mark.parametrize("order", ["always_first", "peak_first"])
def test_first_matching_band_wins_in_the_engine_too(order):
    """Port of `test_first_matching_band_wins`: IC's first match equals GS's."""
    always = {"label": "always", "price_per_mwh": 50.0, "applies": {}}
    peak = {"label": "peak", "price_per_mwh": 999.0, "applies": {"hours": [12]}}
    form = _form(energy_bands=[always, peak] if order == "always_first" else [peak, always])
    eng = _C().tariff_to_engine(form, snapshots=JAN_FEB)
    got = _rated_rate_per_mwh(eng.tariff, JAN_FEB, "energy")
    want = (np.full(len(JAN_FEB), 50.0) if order == "always_first"
            else np.where(JAN_FEB.hour == 12, 999.0, 50.0))
    np.testing.assert_allclose(got, want, rtol=1e-12)


def test_bands_that_leave_an_hour_unpriced_are_refused():
    form = _form(energy_bands=[
        {"label": "day", "price_per_mwh": 100.0, "applies": {"hours": list(range(6, 22))}}])
    with pytest.raises(_C().CompileError) as exc:
        _C().tariff_to_engine(form, snapshots=JAN_FEB)
    assert exc.value.code == "tariff_unpriced_hours"


def test_a_non_flat_snapshot_axis_is_refused():
    """Port of `test_write_tariff_prices_refuses_a_non_datetime_index`."""
    with pytest.raises(_C().CompileError) as exc:
        _C().tariff_to_engine(_form(), snapshots=pd.RangeIndex(24))
    assert exc.value.code == "tariff_snapshots_not_flat"


@pytest.mark.parametrize("tariff_id", [DE, TOU])
def test_the_seeds_compiled_reproduce_gs_prices_on_every_hour(tariff_id):
    """
    WP4: band → periods reproduce GS's band price plus per-MWh network
    charges on 8,760 h of both seeds, rated by `tariff_engine.rate`.
    """
    from services.study import tariff as T

    compiled = _compiled(tariff_id)
    t = compiled.config.import_tariff
    gs = T.band_prices(YEAR, _defaults().tariffs[tariff_id].energy_bands)
    np.testing.assert_allclose(_rated_rate_per_mwh(t, YEAR, "energy"), gs.to_numpy(), rtol=1e-12)
    net = sum((_rated_rate_per_mwh(t, YEAR, i.id) for i in t.items
               if i.id.startswith("network:")), np.zeros(len(YEAR)))
    want = sum((nc.price for nc in _defaults().tariffs[tariff_id].network_charges
                if nc.basis == "per_mwh"), 0.0)
    np.testing.assert_allclose(net, want, rtol=1e-12)


# ── units and the other charges ──────────────────────────────────────────

def test_the_demand_charge_is_per_kw_month_on_import_divided_by_a_thousand():
    eng = _C().tariff_to_engine(_form(demand_charge={"price_per_mw_per_period": 9000.0}),
                                snapshots=JAN_FEB)
    d = {i.id: i for i in eng.tariff.items}["demand"]
    assert (d.kind, d.unit, d.measured_on, d.settlement) == ("demand", "per_kw_month", "import", "h")
    assert d.periods[0].rate == pytest.approx(9.0)
    assert eng.item_component["demand"] == "demand"


def test_a_year_billed_demand_charge_is_the_annual_measured_peak():
    """
    §3.1: a year-billed GS demand charge → IC `capacity` on `peak_import`,
    per kW-year, still the `demand` component (mapped by id).
    """
    eng = _C().tariff_to_engine(_form(billing_period="year",
                                      demand_charge={"price_per_mw_per_period": 9500.0}),
                                snapshots=JAN_FEB)
    d = {i.id: i for i in eng.tariff.items}["demand"]
    assert (d.kind, d.unit, d.measured_on) == ("capacity", "per_kw_year", "peak_import")
    assert d.periods[0].rate == pytest.approx(9.5)
    assert eng.item_component["demand"] == "demand"


def test_network_charges_are_network_items_by_basis():
    eng = _C().tariff_to_engine(_form(network_charges=[
        {"label": "levy", "price": 4.0, "basis": "per_mwh"},
        {"label": "meter", "price": 10.0, "basis": "per_period"},
        {"label": "annual", "price": 120.0, "basis": "per_year"}]), snapshots=JAN_FEB)
    items = {i.id: i for i in eng.tariff.items}
    assert (items["network:energy:0"].kind, items["network:energy:0"].unit) == ("energy", "per_kwh")
    assert items["network:energy:0"].periods[0].rate == pytest.approx(0.004)
    assert (items["network:fixed:1"].kind, items["network:fixed:1"].unit) == ("fixed", "per_month")
    assert items["network:fixed:1"].periods[0].rate == pytest.approx(10.0)
    assert items["network:fixed:2"].periods[0].rate == pytest.approx(10.0)  # 120 / 12
    assert "network_per_year_billed_monthly" in eng.notes
    assert {k: v for k, v in eng.item_component.items() if k.startswith("network:")} == {
        "network:energy:0": "network", "network:fixed:1": "network", "network:fixed:2": "network"}


def test_year_billed_fixed_charges_are_billed_monthly_at_a_twelfth():
    eng = _C().tariff_to_engine(_form(billing_period="year", fixed_charge_per_period=1200.0),
                                snapshots=JAN_FEB)
    f = {i.id: i for i in eng.tariff.items}["fixed"]
    assert (f.kind, f.unit, f.periods[0].rate) == ("fixed", "per_month", pytest.approx(100.0))


def test_a_contracted_capacity_charge_rates_on_the_poc_size():
    eng = _C().tariff_to_engine(
        _form(capacity_charge={"price_per_mw_per_year": 8760.0, "basis": "contracted"},
              connection_limit_mw=12.0), snapshots=JAN_FEB, connection_mw=12.0)
    c = {i.id: i for i in eng.tariff.items}["capacity"]
    assert (c.kind, c.unit, c.measured_on) == ("capacity", "per_kw_year", "import")
    assert c.periods[0].rate == pytest.approx(8.76)


def test_a_capacity_limit_other_than_the_connection_is_refused():
    with pytest.raises(_C().CompileError) as exc:
        _C().tariff_to_engine(
            _form(capacity_charge={"price_per_mw_per_year": 8760.0, "basis": "contracted"},
                  connection_limit_mw=12.0), snapshots=JAN_FEB, connection_mw=2.0)
    assert exc.value.code == "capacity_basis_mismatch"


def test_every_compiled_item_settles_hourly_and_maps_to_one_of_seven_components():
    eng = _C().tariff_to_engine(_form(
        demand_charge={"price_per_mw_per_period": 1.0}, fixed_charge_per_period=5.0,
        capacity_charge={"price_per_mw_per_year": 1.0, "basis": "contracted"},
        network_charges=[{"label": "levy", "price": 4.0, "basis": "per_mwh"}]),
        snapshots=JAN_FEB, connection_mw=None)
    assert {i.settlement for i in eng.tariff.items} == {"h"}
    assert set(eng.item_component) == {i.id for i in eng.tariff.items}
    assert set(eng.item_component.values()) <= SEVEN


# ── refusals kept (same codes) ────────────────────────────────────────────

@pytest.mark.parametrize("dc", [
    {"price_per_mw_per_period": 1.0, "basis": "annual_peak"},
    {"price_per_mw_per_period": 1.0, "basis": "ratchet", "ratchet": {"months": 11, "share": 0.8}},
])
def test_annual_peak_and_ratchet_bases_are_refused(dc):
    with pytest.raises(_C().CompileError) as exc:
        _C().tariff_to_engine(_form(demand_charge=dc), snapshots=JAN_FEB)
    assert exc.value.code == f"demand_charge_basis_{dc['basis']}"


def test_measured_capacity_charge_is_refused_like_an_annual_peak():
    with pytest.raises(_C().CompileError) as exc:
        _C().tariff_to_engine(_form(capacity_charge={"price_per_mw_per_year": 1.0,
                                                     "basis": "measured"}), snapshots=JAN_FEB)
    assert exc.value.code == "capacity_charge_measured_unsupported"


def test_an_unpriced_network_basis_is_refused():
    with pytest.raises(_C().CompileError) as exc:
        _C().tariff_to_engine(_form(network_charges=[{"label": "x", "price": 1.0,
                                                      "basis": "per_kw"}]), snapshots=JAN_FEB)
    assert exc.value.code == "network_charge_basis_unsupported"


def test_export_price_and_series_ref_together_are_refused():
    with pytest.raises(_C().CompileError) as exc:
        _C().commercial_from_form(_form(export={"price_per_mwh": 40.0, "series_ref": "spot"}),
                                  JAN_FEB)
    assert exc.value.code == "tariff_export_pricing"


def test_an_export_link_without_any_price_is_unpriced_not_refused():
    """
    Port of `test_export_link_without_any_export_price_is_refused_when_writing`:
    GS refused WRITING prices; IC prices export only through a series, so a
    tariff without one compiles with no `export_price_ref` (the export Link
    earns nothing), noted `export_unpriced` (delta recorded).
    """
    compiled = _C().commercial_from_form(_form(), JAN_FEB)
    assert compiled.config.export_price_ref is None
    assert compiled.config.export_link == "grid_export"
    assert "export_unpriced" in compiled.notes


def test_an_export_price_without_a_minted_series_is_noted_and_cannot_bind():
    compiled = _C().commercial_from_form(_form(export={"price_per_mwh": 40.0}), JAN_FEB)
    assert compiled.config.export_price_ref is None
    assert "export_series_not_minted" in compiled.notes
    n = pypsa.Network()
    n.set_snapshots(JAN_FEB)
    with pytest.raises(_C().CompileError) as exc:
        _C().bind_on_network(n, compiled, resolve_ref=flat_resolver(40.0, JAN_FEB))
    assert exc.value.code == "export_series_not_minted"


# ── the ledger's rows (demand price, energy level) and staleness ──────────

def test_the_ledgers_demand_price_replaces_the_tariffs_own():
    from services.study import ledger as LG

    led = LG.apply_user_row(_ledger(DE), "demand_charge_price", 7000.0, unit="EUR/MW/month",
                            changed_by="u")
    assert _items(_compiled(DE, ledger=led))["demand"].periods[0].rate == pytest.approx(7.0)


def test_the_energy_price_level_rescales_bands_around_their_weighted_mean():
    from services.study import ledger as LG
    from services.study import tariff as T

    led = LG.apply_user_row(_ledger(TOU), "energy_price_level", 1.5, unit="multiplier",
                            changed_by="u")
    compiled = _compiled(TOU, ledger=led)
    gs = T.tariff_from_ledger(_defaults().tariffs[TOU], led, YEAR)   # the WP0 rule
    got = _rated_rate_per_mwh(compiled.config.import_tariff, YEAR, "energy")
    np.testing.assert_allclose(got, T.band_prices(YEAR, gs.energy_bands).to_numpy(), rtol=1e-12)
    assert "energy_bands_scaled_by_energy_price_level" in compiled.tariff_meta["honesty_notes"]


def test_a_ledger_priced_on_another_tariff_is_refused_stale():
    with pytest.raises(_C().CompileError) as exc:
        _C().commercial_from_ledger(_intake(TOU), _ledger(DE), _defaults(), YEAR,
                                    export_series=FAKE_REF)
    assert exc.value.code == "ledger_tariff_stale"


# ── the compiled config ──────────────────────────────────────────────────

@pytest.mark.parametrize("tariff_id", [DE, TOU])
def test_the_compiled_config_names_the_fixed_constants_and_the_pack_validity(tariff_id):
    c = _compiled(tariff_id)
    cfg = c.config
    assert (cfg.poc_link, cfg.export_link, cfg.timezone, cfg.site_party) == (
        "grid_import", "grid_export", None, "site")
    assert cfg.import_tariff_ref is None and cfg.import_tariff_id == tariff_id
    assert cfg.import_tariff.jurisdiction == ("DE" if tariff_id == DE else "generic")
    assert cfg.import_tariff.valid_from == date(2020, 1, 1) and cfg.import_tariff.valid_to is None
    assert cfg.export_price_ref is not None and cfg.export_price_ref.id == FAKE_REF["id"]
    assert cfg.connection is None
    meta = c.tariff_meta
    assert (meta["currency"], meta["currency_year"], meta["illustrative"]) == ("EUR", 2020, True)
    assert set(c.item_component) == {i.id for i in cfg.import_tariff.items}
    assert set(c.item_component.values()) <= SEVEN


def test_a_supplied_tariffs_prose_note_is_flagged_never_dropped():
    """
    Compile half of the `test_proforma_golden` port (the case half is WP7):
    a prose note in the form becomes the code `tariff_has_uncoded_notes`.
    """
    form = _defaults().tariffs[DE].model_copy(update={"honesty_notes": ["Prose with 2 digits."]})
    c = _C().commercial_from_form(form, YEAR, export_series=FAKE_REF)
    assert "tariff_has_uncoded_notes" in c.tariff_meta["honesty_notes"]
    assert all(" " not in code for code in c.tariff_meta["honesty_notes"])


def test_the_digest_is_stable_and_moves_with_the_inputs():
    from services.study import ledger as LG

    a, b = _compiled(DE), _compiled(DE)
    assert a.digest == b.digest and len(a.digest) == 64
    led = LG.apply_user_row(_ledger(DE), "demand_charge_price", 7000.0, unit="EUR/MW/month",
                            changed_by="u")
    assert _compiled(DE, ledger=led).digest != a.digest
    assert _compiled(DE, export_series={**FAKE_REF, "version": 2}).digest != a.digest


def test_an_export_cap_becomes_the_connection_agreements_export_cap():
    """C3 / row 34: `cap_mw` lifts GS's `export_cap_unsupported` refusal."""
    c = _C().commercial_from_form(_form(export={"price_per_mwh": 40.0, "cap_mw": 0.5}), JAN_FEB,
                                  connection_mw=2.0, export_series=FAKE_REF)
    conn = c.config.connection
    assert (conn.kind, conn.import_cap_mw, conn.export_cap_mw) == ("firm", 2.0, 0.5)
    assert conn.available_from == date(2030, 1, 1)


@pytest.mark.live_solve
def test_the_lp_never_exports_above_the_cap():
    """
    WP4: an LP solved through `run_simulation` with the compiled cap keeps
    export ≤ cap in every snapshot (and would export more without it).
    """
    import queue
    import threading

    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation
    from tests.conftest import install_network_into_backend

    idx = pd.date_range("2030-06-01", periods=48, freq="h")

    def net():
        n = pypsa.Network()
        n.set_snapshots(idx)
        n.add("Bus", "grid")
        n.add("Bus", "site")
        n.add("Generator", "grid_supply", bus="grid", p_nom=50.0, p_min_pu=-1.0,
              eh_role="grid_supply")
        n.add("Link", "grid_import", bus0="grid", bus1="site", p_nom=2.0, eh_role="grid_import")
        n.add("Link", "grid_export", bus0="site", bus1="grid", p_nom=2.0, eh_role="grid_export")
        n.add("Load", "l", bus="site", p_set=0.2)
        pv = np.clip(np.sin((idx.hour - 6) / 12 * np.pi), 0, None)
        n.add("Generator", "pv", bus="site", p_nom=1.5, p_max_pu=pd.Series(pv, index=idx))
        return n

    def solve(cap):
        n = net()
        form = _form(export={"price_per_mwh": 40.0, "cap_mw": cap})
        c = _C().commercial_from_form(form, idx, connection_mw=2.0, export_series=FAKE_REF)
        c = _C().bind_on_network(n, c, resolve_ref=flat_resolver(40.0, idx))
        install_network_into_backend(n)
        cfg = SolverConfig(solver_name="highs", commercial=c.config.model_dump(mode="json"))
        live = PyPSAService.get_network()
        status, _ = run_simulation(cfg, live, PyPSAService.get_lock(), threading.Event(),
                                   queue.SimpleQueue(), state_update=lambda **k: None)
        assert status in ("ok", "optimal")
        return live.links_t.p0["grid_export"]

    assert float(solve(None).max()) > 0.5 + 1e-6
    assert float(solve(0.5).max()) <= 0.5 + 1e-6


# ── C3: the per-study export series (owner decision 2026-10-05) ──────────

def test_the_export_series_name_carries_base_and_study():
    import uuid

    base = uuid.UUID(int=7)
    name = _C().export_series_name(base, "a" * 32)
    assert name == f"decision-study:{base}:{'a' * 32}:export"
    assert len(name) <= 128


@pytest.fixture
def local_db(_auth_db, monkeypatch, tmp_path):
    import local_mode

    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    _engine, session_local = _auth_db
    with session_local() as db:
        local_mode.ensure_local_identity(db)
        db.commit()
    return session_local, local_mode.LOCAL_ORG_ID, tmp_path / "lib"


def test_the_export_series_is_minted_once_per_study_and_versioned_by_content(local_db):
    import uuid

    from services.library import series_store

    session_local, org, root = local_db
    base, sid = uuid.uuid4(), "b" * 32
    form = _form(export={"price_per_mwh": 40.0})
    with session_local() as db:
        def mint(t):
            return _C().mint_export_series(db, org, base_uuid=base, study_id=sid,
                                           study_name="Site A", tariff=t, snapshots=JAN_FEB,
                                           root=root)
        ref = mint(form)
        assert ref.id == _C().export_series_name(base, sid) and ref.version == 1
        assert ref.source == "decision_study"
        assert mint(form).version == 1                     # unchanged price: no new version
        assert mint(_form(export={"price_per_mwh": 41.0})).version == 2
        meta = series_store.series_meta(db, org, ref)
        assert meta["description"] == "Site A — export price"
        got = series_store.resolve(db, org, ref, root=root)
        assert np.allclose(got.to_numpy(), 40.0) and len(got) == len(JAN_FEB)
        assert mint(_form()) is None                       # no export price: nothing minted


def test_a_copied_studys_series_survives_the_origins_delete(local_db):
    """
    Owner decision: the base uuid is in the name, so a copy's series is its
    own; the delete (adapter-side WORKAROUND until `series_store.delete_series`
    exists) removes every version of the origin's and keeps the copy's, and a
    payload file the copy shares (same content) survives.
    """
    import uuid

    from services.library import series_store

    session_local, org, root = local_db
    origin, copy, sid = uuid.uuid4(), uuid.uuid4(), "c" * 32
    form = _form(export={"price_per_mwh": 40.0})
    with session_local() as db:
        a1 = _C().mint_export_series(db, org, base_uuid=origin, study_id=sid, study_name="A",
                                     tariff=form, snapshots=JAN_FEB, root=root)
        _C().mint_export_series(db, org, base_uuid=origin, study_id=sid, study_name="A",
                                tariff=_form(export={"price_per_mwh": 41.0}), snapshots=JAN_FEB,
                                root=root)
        b1 = _C().mint_export_series(db, org, base_uuid=copy, study_id=sid, study_name="A copy",
                                     tariff=form, snapshots=JAN_FEB, root=root)
        out = _C().delete_export_series(db, org, base_uuid=origin, study_id=sid, root=root)
        assert out == {"deleted_versions": 2, "kept": None}
        assert series_store.latest_ref(db, org, a1.id) is None
        assert np.allclose(series_store.resolve(db, org, b1, root=root).to_numpy(), 40.0)


def test_a_pinned_series_is_kept_and_reported(local_db, tmp_path):
    import uuid

    from services.library import bundle_pins, series_store

    session_local, org, root = local_db
    base, sid = uuid.uuid4(), "d" * 32
    with session_local() as db:
        ref = _C().mint_export_series(db, org, base_uuid=base, study_id=sid, study_name="A",
                                      tariff=_form(export={"price_per_mwh": 40.0}),
                                      snapshots=JAN_FEB, root=root)
        other = tmp_path / "expert_project"
        other.mkdir()
        bundle_pins.write_pins(other, [ref])
        out = _C().delete_export_series(db, org, base_uuid=base, study_id=sid, root=root,
                                        project_dirs=[other])
        assert out == {"deleted_versions": 0, "kept": "export_series_kept_in_use"}
        assert series_store.latest_ref(db, org, ref.id) is not None


# ── C6: bind on the fork's in-memory network; way (a) meter Links ─────────

def test_binding_writes_the_export_price_on_the_network_and_survives_netcdf(tmp_path):
    from services.pypsa_service import PyPSAService
    from services.study import forks

    n = golden_copy("none")
    c = _compiled(DE)
    bound = _C().bind_on_network(n, c, resolve_ref=flat_resolver(40.0, n.snapshots),
                                 project_dir=tmp_path)
    assert bound.config.export_price_ref.hash == FAKE_REF["hash"]
    assert np.allclose(n.links_t["ic_export_price"]["grid_export"].to_numpy(), 40.0)
    forks._write_network(tmp_path, n)
    back = pypsa.Network()
    PyPSAService.import_network_from_netcdf(back, tmp_path / "network.nc")
    assert np.allclose(back.links_t["ic_export_price"]["grid_export"].to_numpy(), 40.0)


def test_the_meter_links_are_typed_at_zero_cost_over_the_horizon():
    """
    Row 28 way (a), an adapter-side WORKAROUND until IC settles Q5's
    `single_owner` template: the PoC meter Links carry `overnight_cost = 0`
    and `lifetime = H`, which leaves the LP's link costs at 0.
    """
    from services.study import packs

    n = packs.build_site_network(_intake(DE), _ledger(DE), "bess_2h", library=_defaults())
    _C().type_meter_links(n, horizon_years=25)
    for link in ("grid_import", "grid_export"):
        assert n.links.at[link, "overnight_cost"] == 0.0
        assert n.links.at[link, "lifetime"] == 25.0
        assert n.links.at[link, "capital_cost"] == 0.0
        assert not bool(n.links.at[link, "p_nom_extendable"])

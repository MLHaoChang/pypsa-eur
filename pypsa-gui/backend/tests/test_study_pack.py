"""
The site question pack (MVP-1 S4; plan S4 "Files" and "Acceptance", review
v2 BC-1, BC-2, N1-v2, N2-v2, N3-v2; gate S2 and S3 carries).

`services/study/packs.py::build_site_network(intake, ledger, option_id)`.
Every figure asserted here is recomputed from the LEDGER in the test, so a
literal in the pack (or a cost read from anywhere but the ledger) goes red.
"""
from __future__ import annotations

import math
import queue
import threading

import numpy as np
import pandas as pd
import pytest

from models.energy_hub import ImportOverlaySpec
from services.adequacy.archetypes import select_import_links
from services.study import ledger as L
from services.study import library as lib
from services.study import packs as P
from services.study import questions as Q
from services.study import tariff as T
from services.validation_service import validate_for_run

NOW = pd.Timestamp("2026-09-29T10:00:00Z").to_pydatetime()


def _intake(**over):
    base = {
        "site": {"zone": "DE", "connection_mw": 2.0},
        "tariff": {"tariff_id": "de_industrial_illustrative"},
        "load": {"source": "sector_profile", "profile": "commercial_office",
                 "annual_mwh": 4000.0},
        "pv": {"enabled": True, "kind": "rooftop"},
    }
    base.update(over)
    return base


@pytest.fixture(scope="module")
def library():
    return lib.load_library()


@pytest.fixture(scope="module")
def ledger(library):
    return lib.seed_ledger(Q.BESS_AT_SITE, _intake(), library)


def _v(ledger, key):
    return {r.key: r.value for r in ledger.rows}[key]


def _crf(r, n):
    return r * (1 + r) ** n / ((1 + r) ** n - 1)


def _bound(intake, led, option_id, library=None):
    """The option as the runner builds it since U2 WP6: compiled and bound."""
    from tests.u2_targets import bound_option

    return bound_option(intake, led, option_id, library or lib.load_library())


def _prices(intake, led, option_id="none"):
    """
    The Link prices the engine materialises at solve time (U2 WP6, plan §2
    C2: the pack writes none): (import, export) EUR/MWh series.
    """
    from services.commercial.lp_bindings import materialise_poc_prices

    n, c = _bound(intake, led, option_id)
    assert "grid_import" not in n.links_t.marginal_cost.columns
    applied = materialise_poc_prices(n, c.config)
    try:
        mc = n.links_t.marginal_cost[["grid_import", "grid_export"]].copy()
    finally:
        applied.undo()
    return mc["grid_import"], mc["grid_export"]


# ── the battery: two annuities, RTE, upfront ─────────────────────────────

@pytest.mark.parametrize("option_id,hours", [("bess_1h", 1), ("bess_2h", 2),
                                             ("bess_4h", 4), ("bess_pv_2h", 2)])
def test_storage_capital_cost_is_the_two_annuity_formula(ledger, option_id, hours):
    n = P.build_site_network(_intake(), ledger, option_id)
    r = _v(ledger, "discount_rate")
    expected = (_crf(r, _v(ledger, "battery_inverter_lifetime_years"))
                * _v(ledger, "battery_inverter_eur_per_kw") * 1000.0
                + hours * _crf(r, _v(ledger, "battery_storage_lifetime_years"))
                * _v(ledger, "battery_storage_eur_per_kwh") * 1000.0)
    # The accessor PyPSA's LP reads, not only the static column.
    assert n.c["StorageUnit"].capital_cost["battery"] == pytest.approx(expected, rel=1e-12)
    assert n.storage_units.at["battery", "max_hours"] == hours
    # `overnight_cost` stays unset: it cannot carry two lifetimes.
    assert math.isnan(n.storage_units.at["battery", "overnight_cost"])
    fom = (_v(ledger, "battery_inverter_fom_pct_per_year") / 100.0
           * _v(ledger, "battery_inverter_eur_per_kw") * 1000.0)
    assert n.storage_units.at["battery", "fom_cost"] == pytest.approx(fom)
    meta = n.meta[P.PACK_META_KEY]
    assert meta["cost_basis"]["battery"] == "derived_from_two_annuities"
    # The upfront figures the pro forma books are the ledger's, x 1000.
    up = meta["upfront_eur_per_mw"]["battery"]
    assert up["inverter"] == pytest.approx(_v(ledger, "battery_inverter_eur_per_kw") * 1000)
    assert up["storage"] == pytest.approx(
        hours * _v(ledger, "battery_storage_eur_per_kwh") * 1000)
    assert up == P.battery_upfront_eur_per_mw(ledger, hours)


def test_costs_follow_the_ledger_not_literals(ledger):
    edited = L.apply_user_row(ledger, "battery_storage_eur_per_kwh", 100.0,
                              unit="EUR/kWh", changed_by="u", changed_at=NOW)
    edited = L.apply_user_row(edited, "discount_rate", 0.05, unit="per unit",
                              changed_by="u", changed_at=NOW)
    a = P.build_site_network(_intake(), ledger, "bess_2h").storage_units.at["battery", "capital_cost"]
    b = P.build_site_network(_intake(), edited, "bess_2h").storage_units.at["battery", "capital_cost"]
    expected = (_crf(0.05, 10) * _v(ledger, "battery_inverter_eur_per_kw") * 1000
                + 2 * _crf(0.05, 25) * 100.0 * 1000)
    assert b == pytest.approx(expected) and a != pytest.approx(b)


def test_round_trip_efficiency_is_reproduced_from_the_one_rte_row(ledger):
    n = P.build_site_network(_intake(), ledger, "bess_2h")
    su = n.storage_units.loc["battery"]
    assert su.efficiency_store == pytest.approx(su.efficiency_dispatch)
    assert su.efficiency_store * su.efficiency_dispatch == pytest.approx(
        _v(ledger, "battery_round_trip_efficiency"))
    edited = L.apply_user_row(ledger, "battery_round_trip_efficiency", 0.85,
                              unit="per unit", changed_by="u", changed_at=NOW)
    su = P.build_site_network(_intake(), edited, "bess_2h").storage_units.loc["battery"]
    assert su.efficiency_store * su.efficiency_dispatch == pytest.approx(0.85)
    assert bool(su.cyclic_state_of_charge) is True


def test_pv_uses_overnight_cost_lifetime_and_discount_rate(ledger):
    n = P.build_site_network(_intake(), ledger, "bess_pv_2h")
    pv = n.generators.loc["pv"]
    assert pv.overnight_cost == pytest.approx(_v(ledger, "pv_rooftop_eur_per_kw") * 1000)
    assert pv.lifetime == _v(ledger, "pv_rooftop_lifetime_years")
    assert pv.discount_rate == _v(ledger, "discount_rate")
    assert n.c["Generator"].capital_cost["pv"] == pytest.approx(
        pv.overnight_cost * _crf(pv.discount_rate, pv.lifetime))
    assert "synthetic_pv_profile" in n.meta[P.PACK_META_KEY]["honesty_notes"]


# ── bounds, options, snapshots ───────────────────────────────────────────

def test_every_extendable_asset_has_a_finite_bound_from_a_ledger_row(ledger):
    [row] = [r for r in ledger.rows if r.key == "sizing_limit_connection_multiple"]
    assert row.source and row.status == "default" and row.provenance == "library"
    n = P.build_site_network(_intake(), ledger, "bess_pv_2h")
    bound = 2.0 * row.value
    assert n.storage_units.at["battery", "p_nom_max"] == pytest.approx(bound)
    assert n.generators.at["pv", "p_nom_max"] == pytest.approx(bound)
    for df in (n.generators, n.storage_units, n.links):
        ext = df[df.get("p_nom_extendable", False) == True]  # noqa: E712
        assert np.isfinite(ext["p_nom_max"]).all()


def test_none_omits_the_battery_and_pv(ledger):
    n = P.build_site_network(_intake(), ledger, "none")
    assert n.storage_units.empty
    assert "pv" not in n.generators.index


def test_snapshots_are_one_flat_year_of_hours(ledger):
    n = P.build_site_network(_intake(), ledger, "none")
    assert isinstance(n.snapshots, pd.DatetimeIndex) and len(n.snapshots) == 8760
    assert (n.snapshot_weightings == 1.0).all().all()


def test_the_import_link_is_selected_by_role_and_the_site_bus_is_not_a_poc(ledger):
    n = P.build_site_network(_intake(), ledger, "bess_1h")
    assert select_import_links(n, ImportOverlaySpec()) == ["grid_import"]
    assert "eh_poc" not in n.buses.columns
    assert n.generators.at["grid", "eh_role"] == "grid_supply"
    assert n.generators.at["grid", "p_min_pu"] == -1.0
    assert bool(n.links.at["grid_import", "active"])
    assert n.links.at["grid_import", "p_nom"] == 2.0


@pytest.mark.parametrize("tariff_id", ["de_industrial_illustrative",
                                       "tou_reference_illustrative"])
def test_export_price_below_import_price_every_hour(library, tariff_id):
    intake = _intake(tariff={"tariff_id": tariff_id})
    led = lib.seed_ledger(Q.BESS_AT_SITE, intake, library)
    imp, exp = _prices(intake, led, "bess_2h")
    assert ((-exp) < imp).all()
    # Gate S3 [S2]: cross-hour cycling through the battery does not pay either.
    rte = _v(led, "battery_round_trip_efficiency")
    assert (-exp).max() * rte < imp.min()
    # F1 B6 (moved to the engine's preflight, plan §5.3): it stays silent here.
    n, c = _bound(intake, led, "bess_2h")
    issues = validate_for_run(n, P.option_solver_config(led, c))
    assert len(n.storage_units) and not [
        i for i in issues if "arbitrage" in i.code or "cycling" in i.code
        or "cross_interval" in i.code]


@pytest.mark.parametrize("option_id", ["none", "bess_1h", "bess_2h", "bess_4h", "bess_pv_2h"])
def test_validate_for_run_has_no_errors_for_every_option(library, ledger, option_id):
    n, c = _bound(_intake(), ledger, option_id, library)
    cfg = P.option_solver_config(ledger, c)
    issues = validate_for_run(n, cfg)
    errors = [i for i in issues if i.severity == "error"]
    assert errors == [], errors
    assert not [i for i in issues if i.code == "gen_zero_costs"]


def test_option_solver_config_is_explicit_and_from_the_ledger(library, ledger):
    _n, c = _bound(_intake(), ledger, "none", library)
    cfg = P.option_solver_config(ledger, c)
    assert cfg.mode == "lopf" and cfg.solve_strategy == "full"
    assert cfg.sclopf is False and cfg.multi_investment_periods is False
    assert cfg.discount_rate == _v(ledger, "discount_rate")
    assert cfg.default_lifetime == _v(ledger, "battery_storage_lifetime_years")
    # U2 WP6: the demand charge is the engine's monthly demand item, not GS's.
    assert cfg.demand_charge is None
    [d] = [i for i in cfg.commercial["import_tariff"]["items"] if i["id"] == "demand"]
    assert (d["kind"], d["unit"], d["settlement"]) == ("demand", "per_kw_month", "h")
    assert d["periods"][0]["rate"] * 1000.0 == pytest.approx(_v(ledger, "demand_charge_price"))
    assert cfg.commercial["poc_link"] == "grid_import"


def test_the_demand_charge_price_is_the_ledgers(library, ledger):
    edited = L.apply_user_row(ledger, "demand_charge_price", 7000.0,
                              unit="EUR/MW/month", changed_by="u", changed_at=NOW)
    t = P.effective_tariff(_intake(), edited, library)
    assert t.demand_charge.price_per_mw_per_period == 7000.0
    c = P.option_commercial(_intake(), edited, library)
    [d] = [i for i in P.option_solver_config(edited, c).commercial["import_tariff"]["items"]
           if i["id"] == "demand"]
    assert d["periods"][0]["rate"] == pytest.approx(7.0)


# ── energy_price_level: one meaning ──────────────────────────────────────

def test_energy_price_level_scales_bands_around_their_time_weighted_mean(library):
    intake = _intake(tariff={"tariff_id": "tou_reference_illustrative"})
    led = lib.seed_ledger(Q.BESS_AT_SITE, intake, library)
    [row] = [r for r in led.rows if r.key == "energy_price_level"]
    assert "time-weighted mean" in row.label and "time-weighted mean" in row.help
    assert "mean + value x (band - mean)" in row.technical_name
    base, _e = _prices(intake, led)
    led2 = L.apply_user_row(led, "energy_price_level", 2.0, unit="multiplier",
                            changed_by="u", changed_at=NOW)
    scaled, _e = _prices(intake, led2)
    assert scaled.mean() == pytest.approx(base.mean())            # level kept
    assert scaled.std() == pytest.approx(2.0 * base.std())        # spread doubled


def test_energy_price_level_is_not_applicable_on_a_flat_tariff(ledger):
    """
    Gate S4 [S5]: on a single-band tariff the row is null + not_applicable,
    an edit is refused (not stored and ignored), and the pack prices as 1.0.
    """
    [row] = [r for r in ledger.rows if r.key == "energy_price_level"]
    assert row.value is None and row.unavailable == {"value": "not_applicable"}
    with pytest.raises(L.LedgerEditError) as exc:
        L.apply_user_row(ledger, "energy_price_level", 1.5, unit="multiplier",
                         changed_by="u", changed_at=NOW)
    assert "single energy band" in str(exc.value)
    imp, _e = _prices(_intake(), ledger)
    assert np.allclose(imp.to_numpy(), 110.0 + 20.0)


# ── refusals ─────────────────────────────────────────────────────────────

def test_a_needs_attention_row_refuses_the_build(ledger):
    flagged = ledger.model_copy(update={
        "honesty_notes": (*ledger.honesty_notes,
                          "needs_attention:demand_charge_price:not_applicable")})
    with pytest.raises(P.PackError) as exc:
        P.build_site_network(_intake(), flagged, "bess_1h")
    assert exc.value.code == "ledger_needs_attention"
    assert "demand_charge_price" in str(exc.value)


def test_a_ledger_priced_on_another_tariff_refuses(ledger):
    with pytest.raises(P.PackError) as exc:
        P.build_site_network(_intake(tariff={"tariff_id": "tou_reference_illustrative"}),
                             ledger, "none")
    assert exc.value.code == "ledger_tariff_stale"


def test_a_measured_capacity_charge_refuses(library):
    custom = library.tariffs["de_industrial_illustrative"].model_dump()
    custom.update(tariff_id="mine", source="site bill",
                  capacity_charge={"price_per_mw_per_year": 50000.0, "basis": "measured"})
    intake = _intake(tariff={"custom": custom})
    led = lib.seed_ledger(Q.BESS_AT_SITE, intake, library)
    with pytest.raises(P.PackError) as exc:
        P.build_site_network(intake, led, "none")
    assert exc.value.code == "capacity_charge_measured_unsupported"


def test_incomplete_intake_names_the_missing_inputs(ledger):
    with pytest.raises(P.PackError) as exc:
        P.build_site_network({"tariff": {"tariff_id": "de_industrial_illustrative"}},
                             ledger, "none")
    assert exc.value.code == "intake_incomplete"
    for key in ("site", "connection_limit", "load"):
        assert key in str(exc.value)


def test_an_uploaded_load_is_used_as_given(library):
    series = [1.0 + (i % 24) / 24 for i in range(8760)]
    intake = _intake(load={"source": "upload", "series_mw": series})
    led = lib.seed_ledger(Q.BESS_AT_SITE, intake, library)
    n = P.build_site_network(intake, led, "none")
    assert n.loads_t.p_set["site_load"].tolist() == series
    assert not any(x.startswith("synthetic_load") for x in n.meta[P.PACK_META_KEY]["honesty_notes"])


def test_an_inactive_import_link_is_a_typed_refusal_not_a_traceback(library, ledger):
    """
    U2 WP6: the engine (not GS's wrapper) prices the PoC; an inactive one is
    refused, typed, when the option is bound — before a fork is written (IC
    itself fails the solve with a raw KeyError: engine ask).
    """
    from tests.u2_targets import FAKE_REF, flat_resolver

    n = P.build_site_network(_intake(), ledger, "bess_1h")
    n.links.loc["grid_import", "active"] = False
    c = P.option_commercial(_intake(), ledger, library, n.snapshots, export_series=FAKE_REF)
    with pytest.raises(P.PackError) as exc:
        P.bind_option(n, c, resolve_ref=flat_resolver(40.0, n.snapshots))
    assert exc.value.code == "import_link_inactive"


# ── one billing-period source: the bill's charge IS the bridge's ─────────

def test_the_bills_demand_charge_equals_the_bridges_on_a_solved_pack(library, ledger):
    """
    Gate S3 [S4]: the LP (via `demand_charge_config`) and the bill take the
    billing period from the one tariff object, and the objective bridge,
    reading the config the solve used, closes on the bill's figure.
    """
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition
    from services.solver_service import run_simulation

    from services.study import engine_adapter
    from tests.u2_targets import FAKE_REF, flat_resolver

    n = P.build_site_network(_intake(), ledger, "bess_1h")
    n.set_snapshots(n.snapshots[: 24 * 59])          # Jan + Feb, for speed
    n.snapshot_weightings.loc[:, :] = 1.0
    tariff = P.effective_tariff(_intake(), ledger, library)
    # U2 WP6: the engine's demand item, compiled and bound on the axis solved.
    c = P.option_commercial(_intake(), ledger, library, n.snapshots, export_series=FAKE_REF)
    c = P.bind_option(n, c, resolve_ref=flat_resolver(40.0, n.snapshots))
    cfg = P.option_solver_config(ledger, c)
    status, _cond = run_simulation(cfg, n, threading.RLock(), threading.Event(),
                                   queue.SimpleQueue())
    assert status in ("ok", "optimal"), status
    bill = T.BillCalculator().bill(n.links_t.p0["grid_import"], n.links_t.p0["grid_export"],
                                   tariff, n.snapshot_weightings)
    assert bill.billing_periods == ["2025-01", "2025-02"]
    # The charge the solve committed (the run record's figure) is the bill's,
    # and the bridge closes with it inside the Commercial component (§5.2).
    assert engine_adapter.demand_charge_eur(n, c) == pytest.approx(
        bill.by_component.demand, rel=1e-9)
    breakdown = compute_cost_breakdown(n, cfg)
    assert breakdown["commercial"]["demand_charge"] == pytest.approx(
        bill.by_component.demand, rel=1e-9)
    decomposition = compute_objective_decomposition(n, breakdown, cfg)
    assert abs(decomposition["residual_gap_pct"]) < 1e-3


@pytest.mark.parametrize("year", [2024, 2028])
def test_a_leap_year_is_refused(ledger, year):
    """BC-S4-4: 8760 hours from 1 January of a leap year stop on 30 December."""
    intake = _intake(site={"zone": "DE", "connection_mw": 2.0, "year": year})
    with pytest.raises(P.PackError) as exc:
        P.build_site_network(intake, ledger, "none")
    assert exc.value.code == "leap_year_unsupported"


# ── S8 (gate S4 [S6] carry): an uploaded load goes through a parser with
# timestamp and unit checks and the time-series QA, never "the last numeric
# column" taken on trust ──────────────────────────────────────────────────

def _csv(values, *, header="timestamp,load (MW)", year=2025, freq="h", timestamps=True):
    import pandas as pd

    idx = pd.date_range(f"{year}-01-01", periods=len(values), freq=freq)
    lines = [header] if header else []
    for t, v in zip(idx, values):
        lines.append(f"{t:%Y-%m-%d %H:%M},{v}" if timestamps else f"{v}")
    return ("\n".join(lines) + "\n").encode()


def _shape():
    return [1.0 + (i % 24) / 24 for i in range(8760)]


def test_an_hourly_csv_in_mw_is_parsed_as_given():
    up = P.parse_load_upload(_csv(_shape()), unit=None, year=2025)
    assert up.values.tolist() == pytest.approx(_shape())
    assert up.unit == "MW" and up.has_timestamps is True
    assert up.notes == [] and up.warnings == []


def test_a_kw_header_is_converted_to_mw_and_says_so():
    kw = [v * 1000 for v in _shape()]
    up = P.parse_load_upload(_csv(kw, header="time,Site load [kW]"), unit=None, year=2025)
    assert up.values.tolist() == pytest.approx(_shape())
    assert up.unit == "kW" and "load_upload_converted_from_kw" in up.notes


def test_a_file_that_names_no_unit_needs_one_from_the_intake():
    blob = _csv(_shape(), header="timestamp,load")
    with pytest.raises(P.PackError) as exc:
        P.parse_load_upload(blob, unit=None, year=2025)
    assert exc.value.code == "load_upload_unit_unknown"
    up = P.parse_load_upload(blob, unit="kW", year=2025)
    assert up.values.tolist() == pytest.approx([v / 1000 for v in _shape()])


def test_a_header_unit_that_contradicts_the_intake_is_refused():
    with pytest.raises(P.PackError) as exc:
        P.parse_load_upload(_csv(_shape()), unit="kW", year=2025)
    assert exc.value.code == "load_upload_unit_mismatch"


@pytest.mark.parametrize("blob_kw", [
    {"year": 2023},                   # another year than the study's
    {"freq": "15min"},                # not hourly
])
def test_timestamps_that_are_not_the_studys_hourly_year_are_refused(blob_kw):
    values = _shape() if blob_kw.get("freq") is None else [1.0] * 8760
    with pytest.raises(P.PackError) as exc:
        P.parse_load_upload(_csv(values, **blob_kw), unit=None, year=2025)
    assert exc.value.code == "load_upload_timestamps_invalid"


def test_a_duplicated_hour_is_refused():
    import pandas as pd

    idx = list(pd.date_range("2025-01-01", periods=8760, freq="h"))
    idx[5] = idx[4]
    blob = ("timestamp,load (MW)\n" + "\n".join(
        f"{t:%Y-%m-%d %H:%M},1.0" for t in idx) + "\n").encode()
    with pytest.raises(P.PackError) as exc:
        P.parse_load_upload(blob, unit=None, year=2025)
    assert exc.value.code == "load_upload_timestamps_invalid"


def test_a_single_column_without_timestamps_is_read_in_order_and_says_so():
    up = P.parse_load_upload(_csv(_shape(), header="MW", timestamps=False), unit=None, year=2025)
    assert up.has_timestamps is False
    assert up.values.tolist() == pytest.approx(_shape())
    assert "load_upload_without_timestamps" in up.notes


def test_the_timeseries_qa_findings_ride_along_as_codes_and_sentences():
    spiky = _shape()
    spiky[100] = 5000.0          # a misplaced decimal point
    up = P.parse_load_upload(_csv(spiky), unit=None, year=2025)
    assert "load_upload_qa_timeseries_spike" in up.notes
    (w,) = [w for w in up.warnings if w["code"] == "timeseries_spike"]
    assert "median" in w["message"]


def test_a_pack_built_from_an_upload_id_reads_the_parsed_mw_series(library):
    kw = [v * 1000 for v in _shape()]
    blobs = {"u1": _csv(kw, header="timestamp,load")}
    intake = _intake(load={"source": "upload", "upload_id": "u1", "unit": "kW"})
    led = lib.seed_ledger(Q.BESS_AT_SITE, intake, library)
    n = P.build_site_network(intake, led, "none", resolve_upload=blobs.__getitem__)
    assert n.loads_t.p_set["site_load"].tolist() == pytest.approx(_shape())
    assert "load_upload_converted_from_kw" in n.meta[P.PACK_META_KEY]["honesty_notes"]


# ── gate S8 BC-S8-6: a header naming any unit other than kW/MW (or kWh/MWh
# per hour) is refused, never overridden by the intake's answer ──────────

@pytest.mark.parametrize("header", ["timestamp,load (GW)", "timestamp,load (W)", "timestamp,load kVA",
                                   "timestamp,load [MVA]", "timestamp,energy (Wh)", "timestamp,load (GWh)"])
def test_a_header_naming_another_unit_is_refused_whatever_the_intake_says(header):
    for unit in ("MW", "kW", None):
        with pytest.raises(P.PackError) as exc:
            P.parse_load_upload(_csv(_shape(), header=header), unit=unit, year=2025)
        assert exc.value.code == "load_upload_unit_unsupported", (header, unit)


def test_kwh_and_mwh_per_hour_headers_still_read_as_kw_and_mw():
    assert P.parse_load_upload(_csv(_shape(), header="t,energy (MWh)"), unit=None, year=2025).unit == "MW"
    assert P.parse_load_upload(_csv(_shape(), header="t,kWh"), unit=None, year=2025).unit == "kW"


# ── gate S8 BC-S8-5: a draft's load is carried in the intake as CSV text
# (`load.csv_text`), never written into a user project before creation ───

def test_a_load_given_as_csv_text_is_read_like_an_upload(library):
    kw = [v * 1000 for v in _shape()]
    intake = _intake(load={"source": "upload", "csv_text": _csv(kw, header="t,load (kW)").decode()})
    led = lib.seed_ledger(Q.BESS_AT_SITE, intake, library)
    n = P.build_site_network(intake, led, "none")
    assert n.loads_t.p_set["site_load"].tolist() == pytest.approx(_shape())


# ── gate S8 re-verification BC-S8-v2-1: Excel for Mac's "CSV (Macintosh)"
# ends lines with a bare CR; it must read, not 500 on csv.Error ──────────

@pytest.mark.parametrize("eol", ["\r", "\r\n", "\n"])
def test_a_csv_with_any_line_ending_reads_the_same(eol):
    blob = _csv(_shape()).decode().replace("\r\n", "\n").replace("\n", eol).encode()
    got = P.parse_load_upload(blob, unit=None, year=2025)
    assert got.unit == "MW" and list(got.values) == pytest.approx(_shape())


def test_a_csv_the_reader_cannot_parse_is_a_typed_refusal():
    with pytest.raises(P.PackError) as exc:
        P.parse_load_upload(b'timestamp,load (MW)\n"2025-01-01 00:00,1\x00\n', unit=None, year=2025)
    assert exc.value.code in ("load_upload_invalid", "load_upload_timestamps_invalid")

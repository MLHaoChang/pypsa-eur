"""
URDB importer (Edge Investment Case P2 WP2.4b-i).

Plan: docs/superpowers/plans/2026-09-27-edge-investment-case-p2.md WP2.4b-i.
`urdb.urdb_to_tariff(urdb_response, name=…, cyclic_year=…)` maps a URDB rate
onto the `Tariff` model and returns `(tariff, refusals, notes)`. What it cannot
map is REFUSED with the field name, never dropped. The route
`POST /api/library/items/tariff/import_urdb` answers 422 with the refusals
unless `accept_partial=true`; a partial tariff records the refused fields in
`Tariff.unsupported_fields`, and the engine bills it with `tariff_incomplete`
and `total = None` (ADR-0001).
"""
from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from models.commercial import Tariff
from services.commercial.tariff_engine import rate
from services.library import urdb as U

ORACLES = Path(__file__).parent / "fixtures" / "investment_case" / "oracles"


def _json(name):
    return json.loads((ORACLES / name).read_text())


def _import(urdb, hand, **kw):
    return U.urdb_to_tariff(urdb, name=hand["name"], tariff_id=hand["id"],
                            jurisdiction=hand["jurisdiction"],
                            valid_from=date.fromisoformat(hand["valid_from"]), **kw)


def _dump(t: Tariff) -> dict:
    return t.model_dump(mode="json")


# ── the oracles, exactly ───────────────────────────────────────────────────


@pytest.mark.parametrize("stem,kw,notes", [
    ("r1_leap_year", {}, ["demandwindow_absent_assumed_15min",
                          "fixed_per_day_billed_on_covered_days"]),
    ("r2_tiered_tou_demand", {}, ["demandwindow_absent_assumed_15min"]),
    ("r3prime", {"cyclic_year": True}, ["cyclic_year_set_by_importer"]),
])
def test_the_importer_reproduces_the_hand_translated_oracles_exactly(stem, kw, notes):
    hand = _json(f"{stem}.tariff.json")
    tariff, refusals, got_notes = _import(_json(f"{stem}.urdb.json"), hand, **kw)
    assert refusals == []
    assert _dump(tariff) == _dump(Tariff.model_validate(hand))
    assert got_notes == notes


def test_schedules_given_as_json_strings_import_the_same():
    """REopt's scenario files have carried the 12×24 schedules as JSON strings."""
    urdb = _json("r1_leap_year.urdb.json")
    strings = {k: (json.dumps(v) if k.endswith("schedule") else v) for k, v in urdb.items()}
    hand = _json("r1_leap_year.tariff.json")
    assert _dump(_import(strings, hand)[0]) == _dump(_import(urdb, hand)[0])


def test_r1_bills_the_reopt_leap_year_cases():
    """The imported R1 rates both REopt testset branches (runtests.jl
    L4143–4210; REopt's `loads_kw[i]` is 1-based, so step i − 1). Energy and
    demand only: REopt's per-day fixed conversion is a documented deviation."""
    tariff, _, _ = _import(_json("r1_leap_year.urdb.json"), _json("r1_leap_year.tariff.json"))

    def bill(year, hours):
        idx = pd.date_range(f"{year}-01-01", periods=8760, freq="h")
        load = np.zeros(8760)
        for h in hours:
            load[h - 1] = 10.0 / 1000.0          # 10 kW in MW
        return rate(pd.DataFrame({"import_mw": load, "export_mw": 0.0}, index=idx), tariff,
                    step_hours=1.0, timezone=None)

    tou = 31 * 24 + 29 * 24 + 3 * 24 + 16
    for year, energy, demand in ((2023, 0.28 * 10, 18.05 * 10), (2024, 0.36 * 10, 28.05 * 10)):
        res = bill(year, [tou])
        assert res.per_item["energy"] == pytest.approx(energy, abs=0.005)
        assert res.per_item["demand"] + res.per_item["demand_tou"] == \
            pytest.approx(demand, abs=0.005)
    facility = [31 * 24 + 27 * 24 + 8, 31 * 24 + 28 * 24 + 8]   # Feb 28; Feb 29 or Mar 1
    for year, demand in ((2023, 2 * 18.05 * 10), (2024, 18.05 * 10)):
        res = bill(year, facility)
        assert res.per_item["demand"] + res.per_item["demand_tou"] == \
            pytest.approx(demand, abs=0.005)


# ── mapping rules ──────────────────────────────────────────────────────────


FLAT = [[0] * 24 for _ in range(12)]


def _base(**extra):
    return {"energyratestructure": [[{"rate": 0.1, "unit": "kWh"}]],
            "energyweekdayschedule": copy.deepcopy(FLAT),
            "energyweekendschedule": copy.deepcopy(FLAT), **extra}


def _imp(urdb, **kw):
    return U.urdb_to_tariff(urdb, name="t", tariff_id="t", valid_from=date(2030, 1, 1), **kw)


def _item(tariff, item_id):
    return next(i for i in tariff.items if i.id == item_id)


def test_rate_plus_adj_and_a_catch_all_energy_period():
    t, refusals, _ = _imp(_base(energyratestructure=[[{"rate": 0.1, "adj": 0.02, "unit": "kWh"}]]))
    assert refusals == []
    (p,) = _item(t, "energy").periods
    assert (p.name, p.rate, p.months, p.weekdays, p.start_hour) == ("0", pytest.approx(0.12),
                                                                   [], [], None)
    assert _item(t, "energy").settlement == "h"


def test_windowed_energy_tiers_carry_per_period_tier_rates():
    wd = [[0] * 8 + [1] * 12 + [0] * 4 for _ in range(12)]
    t, refusals, _ = _imp(_base(
        energyratestructure=[[{"rate": 0.1, "max": 1000, "unit": "kWh"}, {"rate": 0.15}],
                             [{"rate": 0.2, "max": 1000, "unit": "kWh"}, {"rate": 0.3}]],
        energyweekdayschedule=wd))
    assert refusals == []
    item = _item(t, "energy")
    assert [(x.threshold, x.rate) for x in item.tiers] == [(0.0, 0.0), (1000.0, 0.0)]
    assert {p.name: p.tier_rates for p in item.periods} == {"0": [0.1, 0.15], "1": [0.2, 0.3]}


def test_cumulative_tier_max_becomes_thresholds():
    t, refusals, _ = _imp(_base(energyratestructure=[[
        {"rate": 0.1, "max": 500}, {"rate": 0.12, "max": 1500}, {"rate": 0.2}]]))
    assert refusals == []
    assert [x.threshold for x in _item(t, "energy").tiers] == [0.0, 500.0, 1500.0]


@pytest.mark.parametrize("units,unit,rate", [
    ("$/month", "per_month", 30.0), ("$/day", "per_day", 30.0), ("$/year", "per_month", 2.5)])
def test_fixed_charge_units(units, unit, rate):
    t, refusals, _ = _imp(_base(fixedchargefirstmeter=30.0, fixedchargeunits=units))
    assert refusals == []
    fixed = _item(t, "fixed")
    assert (fixed.kind, fixed.unit, fixed.periods[0].rate) == ("fixed", unit, pytest.approx(rate))


def test_fixedmonthlycharge_takes_precedence():
    t, refusals, _ = _imp(_base(fixedmonthlycharge=12.0, fixedchargefirstmeter=1.0,
                                fixedchargeunits="$/day"))
    assert refusals == []
    fixed = _item(t, "fixed")
    assert (fixed.unit, fixed.periods[0].rate) == ("per_month", 12.0)


def _facility(**extra):
    return _base(flatdemandstructure=[[{"rate": 10.0}]], flatdemandmonths=[0] * 12,
                 demandwindow=30, **extra)


def test_ratchet_modes():
    t, refusals, notes = _imp(_facility(lookbackpercent=0.5, lookbackrange=3,
                                        lookbackmonths=[0] * 12))
    assert refusals == [] and notes == []
    r = _item(t, "demand").ratchet
    assert (r.lookback_months, r.share, r.months, r.cyclic_year) == (3, 0.5, None, False)
    assert _item(t, "demand").settlement == "30min"
    t, refusals, _ = _imp(_facility(lookbackpercent=0.8, lookbackrange=0,
                                    lookbackmonths=[1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0]))
    assert refusals == []
    r = _item(t, "demand").ratchet
    assert (r.lookback_months, r.months) == (None, [1, 7])
    t, _, _ = _imp(_facility(lookbackpercent=0.0, lookbackrange=6))
    assert _item(t, "demand").ratchet is None


def test_a_lookback_reading_free_facility_months_is_refused():
    """Review M1: REopt's ratchet reads every month's actual peak; the
    engine's reads charged months only — refused, never under-billed."""
    urdb = _base(flatdemandstructure=[[{"rate": 0.0}], [{"rate": 10.0}]],
                 flatdemandmonths=[0, 0, 0, 1, 1, 1, 1, 1, 1, 0, 0, 0], demandwindow=15,
                 lookbackpercent=0.8, lookbackrange=11)
    with pytest.raises(U.UrdbRefused) as exc:
        _imp(urdb, cyclic_year=True)
    assert [r["field"] for r in exc.value.refusals] == ["lookbackpercent"]
    # Designated months that are all charged are fine.
    ok = {**urdb, "lookbackrange": 0, "lookbackmonths": [0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0]}
    _, refusals, _ = _imp(ok)
    assert refusals == []


def test_a_refused_unit_drops_the_item_instead_of_misreading_it():
    """Review M2: a partial import misses a charge, never misstates it."""
    urdb = _facility(flatdemandunit="kVA",
                     energyratestructure=[[{"rate": 0.1, "max": 10, "unit": "kWh daily"},
                                           {"rate": 0.2}]])
    urdb["fixedmonthlycharge"] = 5.0
    t, refusals, _ = _imp(urdb, accept_partial=True)
    assert {r["field"] for r in refusals} == {"flatdemandunit", "energyratestructure.unit"}
    assert [i.id for i in t.items] == ["fixed"]


def test_cyclic_lookback_of_twelve_or_more_is_every_month_and_bad_flags_are_refused():
    """Review L2."""
    t, refusals, notes = _imp(_facility(lookbackpercent=0.5, lookbackrange=12),
                              cyclic_year=True)
    assert refusals == [] and "cyclic_lookbackrange_ge_12_as_all_months" in notes
    assert _item(t, "demand").ratchet.months == list(range(1, 13))
    with pytest.raises(U.UrdbRefused) as exc:
        _imp(_facility(lookbackpercent=0.5, lookbackmonths=[1, 0, 1]))
    assert [r["field"] for r in exc.value.refusals] == ["lookbackmonths"]


def test_an_enddate_before_the_given_valid_from_is_ignored_and_noted():
    """Review L3: an expired URDB rate imported for a later year."""
    t, _, notes = _imp(_base(enddate=1577836800))                  # 2020-01-01
    assert t.valid_to is None and "enddate_before_valid_from_ignored" in notes


def test_notes_disclose_the_fixed_charge_choices():
    """Review L5."""
    _, _, notes = _imp(_base(fixedmonthlycharge=12.0, fixedchargefirstmeter=1.0,
                             fixedchargeunits="$/day"))
    assert "fixedchargefirstmeter_ignored_fixedmonthlycharge_used" in notes
    _, _, notes = _imp(_base(fixedchargefirstmeter=1.0, fixedchargeunits="$/day"))
    assert "fixed_per_day_billed_on_covered_days" in notes


@pytest.mark.live_solve
def test_a_partial_tariff_says_so_on_the_site_bill():
    """Review L4: `tariff_incomplete` reaches `SiteBill.flags`."""
    import queue
    import threading

    from services.commercial import billing as B
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation
    from tests.fixtures.investment_case.edge_15min import build_edge_15min

    t, _, _ = _imp(_base(mincharge=5.0), accept_partial=True)
    commercial = {"poc_link": "import", "import_tariff": t.model_dump(mode="json")}
    n = build_edge_15min()
    PyPSAService.set_network(n)
    status, _ = run_simulation(SolverConfig(commercial=commercial), n, PyPSAService.get_lock(),
                               threading.Event(), queue.SimpleQueue(),
                               state_update=lambda **k: None)
    assert status in ("ok", "optimal")
    assert "tariff_incomplete:mincharge" in B.bill_site(n, commercial).flags


def test_tou_and_facility_demand_are_two_items():
    t, refusals, _ = _imp(_facility(demandratestructure=[[{"rate": 5.0, "unit": "kW"}]],
                                    demandweekdayschedule=FLAT, demandweekendschedule=FLAT))
    assert refusals == []
    assert [i.id for i in t.items] == ["energy", "demand_tou", "demand"]


# ── refusals: the field name, never dropped ────────────────────────────────


@pytest.mark.parametrize("extra,field", [
    ({"mincharge": 5.0, "minchargeunits": "$/month"}, "mincharge"),
    ({"annualmincharge": 100.0}, "annualmincharge"),
    ({"coincidentratestructure": [[{"rate": 3.0}]]}, "coincidentratestructure"),
    ({"demandunits": "kVA"}, "demandunits"),
    ({"energyratestructure": [[{"rate": 0.1, "unit": "kWh daily"}]]}, "energyratestructure.unit"),
    ({"energyratestructure": [[{"rate": 0.1, "sell": 0.05}]]}, "energyratestructure.sell"),
    ({"demandwindow": 5}, "demandwindow"),
    ({"lookbackpercent": 0.5, "lookbackrange": 6,
      "lookbackmonths": [1] + [0] * 11}, "lookbackrange/lookbackmonths"),
    ({"energyratestructure": [[{"rate": 0.1, "max": 100}, {"rate": 0.2}],
                              [{"rate": 0.1, "max": 200}, {"rate": 0.2}]],
      "energyweekendschedule": [[1] * 24 for _ in range(12)]}, "energyratestructure.max"),
    ({"energyratestructure": [[{"rate": 0.1, "max": 100}, {"rate": 0.2}], [{"rate": 0.3}]],
      "energyweekendschedule": [[1] * 24 for _ in range(12)]}, "energyratestructure.max"),
    ({"demandratchetpercentage": [0.8] * 12}, "demandratchetpercentage"),
    ({"fixedchargefirstmeter": 3.0, "fixedchargeunits": "$/kWh"}, "fixedchargeunits"),
])
def test_each_unsupported_field_is_refused_by_name(extra, field):
    urdb = _facility()     # a facility item stays importable when energy is refused
    urdb.update(extra)
    with pytest.raises(U.UrdbRefused) as exc:
        _imp(urdb)
    assert field in [r["field"] for r in exc.value.refusals]
    _, refusals, _ = _imp(urdb, accept_partial=True)
    assert field in [r["field"] for r in refusals], refusals
    assert all(r["reason"] for r in refusals)


def test_zero_or_empty_fields_are_not_refusals():
    _, refusals, _ = _imp(_base(mincharge=0, annualmincharge=None, coincidentratestructure=[],
                                demandratchetpercentage=[0] * 12, label="abc",
                                utility="Some Utility", startdate=1672531200))
    assert refusals == []


def test_startdate_sets_valid_from_when_none_is_given():
    t, _, _ = U.urdb_to_tariff(_base(startdate=1672531200), name="t")
    assert t.valid_from == date(2023, 1, 1)
    with pytest.raises(ValueError, match="valid_from"):
        U.urdb_to_tariff(_base(), name="t")


def test_a_partial_import_lists_what_was_refused_and_the_engine_says_it_is_incomplete():
    urdb = _base(mincharge=5.0)
    with pytest.raises(U.UrdbRefused) as exc:
        U.urdb_to_tariff(urdb, name="t", valid_from=date(2030, 1, 1))
    assert [r["field"] for r in exc.value.refusals] == ["mincharge"]
    t, refusals, _ = _imp(urdb, accept_partial=True)
    assert t.unsupported_fields == ["mincharge"]
    idx = pd.date_range("2030-01-01", periods=48, freq="h")
    res = rate(pd.DataFrame({"import_mw": 1.0, "export_mw": 0.0}, index=idx), t,
               step_hours=1.0, timezone=None)
    assert res.flags["_tariff"] == ["tariff_incomplete:mincharge"]
    assert res.total is None and res.total_supported is not None and not res.complete


def test_nothing_mappable_is_refused_even_when_partial():
    with pytest.raises(U.UrdbRefused):
        _imp({"mincharge": 5.0}, accept_partial=True)


# ── the route ──────────────────────────────────────────────────────────────


def test_the_route_refuses_with_422_unless_partial_is_accepted(client):
    urdb = _base(mincharge=5.0)
    body = {"urdb_response": urdb, "name": "us-rate", "valid_from": "2030-01-01"}
    r = client.post("/api/library/items/tariff/import_urdb", json=body)
    assert r.status_code == 422, r.text
    assert [x["field"] for x in r.json()["detail"]["refusals"]] == ["mincharge"]
    r = client.post("/api/library/items/tariff/import_urdb", json={**body, "accept_partial": True})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ref"]["id"] == "us-rate" and out["ref"]["version"] == 1
    assert out["unsupported_fields"] == ["mincharge"]
    got = client.get("/api/library/items/tariff/us-rate").json()
    assert got["payload"]["unsupported_fields"] == ["mincharge"]
    assert got["meta"]["source"] == "urdb"


def test_the_route_records_the_cyclic_year_disclosure_in_the_items_meta(client):
    body = {"urdb_response": _json("r3prime.urdb.json"), "name": "r3prime",
            "valid_from": "2022-01-01", "cyclic_year": True}
    r = client.post("/api/library/items/tariff/import_urdb", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["notes"] == ["cyclic_year_set_by_importer"]
    meta = client.get("/api/library/items/tariff/r3prime").json()["meta"]
    assert meta["notes"] == ["cyclic_year_set_by_importer"]

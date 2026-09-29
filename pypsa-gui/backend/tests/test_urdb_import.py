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
    ("r1_leap_year", {}, ["demandwindow_absent_assumed_15min"]),
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
    """The imported R1 rates the REopt testset's loads (energy and demand;
    REopt's per-day fixed conversion is a documented deviation)."""
    tariff, _, _ = _import(_json("r1_leap_year.urdb.json"), _json("r1_leap_year.tariff.json"))
    for year, energy, demand in ((2023, 0.28 * 10, 18.05 * 10), (2024, 0.36 * 10, 28.05 * 10)):
        idx = pd.date_range(f"{year}-01-01", periods=8760, freq="h")
        load = np.zeros(8760)
        load[31 * 24 + 29 * 24 + 3 * 24 + 16] = 10.0 / 1000.0          # MW
        res = rate(pd.DataFrame({"import_mw": load, "export_mw": 0.0}, index=idx), tariff,
                   step_hours=1.0, timezone=None)
        assert res.per_item["energy"] == pytest.approx(energy, abs=0.005)
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

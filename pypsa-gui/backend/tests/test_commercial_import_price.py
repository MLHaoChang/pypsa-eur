"""
A wholesale-indexed import price, `CommercialConfig.import_price_ref` (IPP renewables + BESS
ticket 07, spec §10 G7; gatekeeper plan 2026-10-20).

The mirror of `export_price_ref`: a Library series written on the import Link(s) as
`links_t["ic_import_price"]` (persisted input, like `ic_export_price`) and added to the import
side of the LP for one solve (transient, under `Applied.undo()`). The solve's committed
`ic_energy_price` carries it, so `energy_cost_rows` and `cost_breakdown` see it after a reload.
The ledger books it as its own stream, `energy_wholesale` (site → market, escalation class
`export`), apart from the tariff's `energy_import`, and the reconciliation still closes.
"""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from models.commercial import CommercialConfig
from services.commercial import hashing as H
from services.commercial import lp_bindings as LP
from services.commercial.preflight import commercial_findings
from tests.test_value_flow_reconciliation import (
    REF, VF, _checks, _commercial, _ledger, _network, _solve,
)

CHARGE = {"id": "network", "kind": "energy", "unit": "per_kwh",
          "periods": [{"name": "all", "rate": 0.01}]}          # 10 €/MWh, network only
TARIFF = {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2029-01-01",
          "items": [CHARGE]}


def _da(n) -> pd.Series:
    """A day-ahead shape: cheap at night (5 €/MWh), dear in the evening (120 €/MWh)."""
    hour = np.asarray(pd.DatetimeIndex(n.snapshots).hour)
    return pd.Series(np.where(hour < 6, 5.0, np.where(hour >= 17, 120.0, 40.0)),
                     index=n.snapshots, dtype=float)


def _priced_network():
    n = _network()
    n.generators.loc["grid_supply", "marginal_cost"] = 0.0      # the commodity is the series
    da = _da(n)
    assert LP.write_import_price(n, ["import"], da) == 0
    assert LP.write_export_price(n, "export", da) == 0
    return n, da


def _config(**extra):
    kw = dict(import_tariff=TARIFF, contracts=[], connection=None, import_price_ref=REF)
    kw.update(extra)
    return _commercial(copy.deepcopy(VF), **kw)


# ── model and hash ─────────────────────────────────────────────────────────


def test_a_config_without_the_field_keeps_its_hash():
    cfg = CommercialConfig.model_validate({"poc_link": "import"})
    assert ("CommercialConfig", "import_price_ref") in H.FIELDS_AFTER_V1
    assert H.FIELDS_AFTER_V1[("CommercialConfig", "import_price_ref")] is None
    assert "import_price_ref" not in H.canonical(cfg, version=1)


# ── the solve ──────────────────────────────────────────────────────────────


@pytest.mark.live_solve
def test_the_battery_charges_from_the_grid_at_day_ahead_and_the_rows_carry_it(reset_backend):
    n, da = _priced_network()
    n, cfg = _solve(n, _config())
    rows = LP.energy_cost_rows(n, cfg.commercial)
    w = n.snapshot_weightings.objective.to_numpy(dtype=float)
    imp = n.links_t.p0["import"].to_numpy(dtype=float)
    assert rows["energy_import"] == pytest.approx(float((w * imp * (da.to_numpy() + 10.0)).sum()),
                                                  rel=1e-9)
    # The battery charges in the cheap night and discharges in the dear evening.
    hour = np.asarray(pd.DatetimeIndex(n.snapshots).hour)
    bess = n.storage_units_t.p["bess"].to_numpy(dtype=float)        # + discharge, − charge
    assert (-bess[hour < 6]).clip(min=0).sum() > (-bess[hour >= 17]).clip(min=0).sum()
    assert bess[hour >= 17].clip(min=0).sum() > bess[hour < 6].clip(min=0).sum()
    # The user's Link cost is untouched on disk (transient).
    assert "import" not in n.links_t.marginal_cost.columns or \
        np.allclose(n.links_t.marginal_cost["import"], n.links.at["import", "marginal_cost"])
    assert LP.IMPORT_PRICE_ATTR in n.links_t and "import" in n.links_t[LP.IMPORT_PRICE_ATTR]


@pytest.mark.live_solve
def test_the_rows_are_identical_after_a_reload(reset_backend, tmp_path):
    import pypsa

    n, _da_ = _priced_network()
    n, cfg = _solve(n, _config())
    before = LP.energy_cost_rows(n, cfg.commercial)
    path = tmp_path / "n.nc"
    n.export_to_netcdf(path)
    again = pypsa.Network(path)
    assert LP.energy_cost_rows(again, cfg.commercial)["energy_import"] == pytest.approx(
        before["energy_import"], rel=1e-12)
    assert LP._import_price(again, LP._parse(cfg.commercial)) == pytest.approx(
        LP._import_price(n, LP._parse(cfg.commercial)))


@pytest.mark.live_solve
def test_the_ledger_books_wholesale_apart_and_still_reconciles(reset_backend):
    n, da = _priced_network()
    n, cfg = _solve(n, _config())
    inputs, vf, ledger, res = _ledger(n, cfg)
    p = inputs.periods[0]
    lines = [ln for ln in ledger.periods[p] if ln.source == "import_price"]
    assert len(lines) == 1
    ln = lines[0]
    assert ln.value_stream == "energy_wholesale" and (ln.payer, ln.payee) == ("site", "market")
    w = n.snapshot_weightings.objective.to_numpy(dtype=float)
    imp = n.links_t.p0["import"].to_numpy(dtype=float)
    assert ln.amount == pytest.approx(float((w * imp * da.to_numpy()).sum()), rel=1e-9)
    rec = _checks(res, p)["reconciliation"]
    assert rec["ok"] is True, rec


def test_energy_wholesale_escalates_with_the_market():
    from services.finance.cashflow import STREAM_CLASS

    assert STREAM_CLASS["energy_wholesale"] == "export"


# ── refusals and preflight ─────────────────────────────────────────────────


def test_a_two_way_import_link_is_refused():
    n, _da_ = _priced_network()
    n.links.loc["import", "p_min_pu"] = -1.0
    with pytest.raises(LP.CommercialBindingError):
        LP.validate_for_network(n, LP._parse(_config()))


def test_an_uncovered_import_price_is_not_written():
    n = _network()
    short = _da(n).iloc[:-4]
    assert LP.write_import_price(n, ["import"], short) > 0
    assert LP.IMPORT_PRICE_ATTR not in n.links_t or \
        "import" not in n.links_t[LP.IMPORT_PRICE_ATTR].columns


def test_the_arbitrage_preflight_counts_the_import_price():
    """Import DA + charge > export DA everywhere: no loop. Without the charge, and with
    an export feed-in item on top, exporting pays more than importing: a loop."""
    n, _da_ = _priced_network()
    codes = {c for _s, c, *_ in commercial_findings(n, _config())}
    assert "commercial.arbitrage_loop" not in codes
    feed = {"id": "feed", "kind": "energy", "unit": "per_kwh", "measured_on": "export",
            "direction": "revenue", "periods": [{"name": "all", "rate": 0.02}]}
    loop = _config(import_tariff={**TARIFF, "items": [CHARGE, feed]})
    assert "commercial.arbitrage_loop" in {c for _s, c, *_ in commercial_findings(n, loop)}


def test_before_the_series_is_written_preflight_is_never_silently_optimistic():
    """Gatekeeper (G7): the route validates before it writes the series. With the ref set and no
    `ic_import_price` yet, `validate_for_network` (no loop check) passes, and preflight states
    the price is not established instead of counting 0 loops."""
    n = _network()
    n.generators.loc["grid_supply", "marginal_cost"] = 0.0
    assert LP.write_export_price(n, "export", _da(n)) == 0         # export priced, import not yet
    cfg = _config()
    LP.validate_for_network(n, LP._parse(cfg))                      # no refusal, no loop figure
    found = commercial_findings(n, cfg)
    assert any(s == "error" and "import price is not established" in m
               for s, _c, _cls, _name, m in found), found

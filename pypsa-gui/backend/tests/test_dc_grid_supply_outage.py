"""DC 01 / DC01-OUTAGE-1: the data-centre templates' grid supply carries
generic, public, *assumed* outage data, so the reserve margin can price it
(the front's margin preflight no longer refuses), and the ledger says so."""
from __future__ import annotations

import json
import math
import pathlib
import threading

import numpy as np
import pytest

from services.solver_service import SolverConfig
from project_templates import eh_templates as T
from services.adequacy import front as fr

DC = ("eh_datacenter", "eh_datacenter_staged")
_BUILD = {"eh_datacenter": T.build_eh_datacenter,
          "eh_datacenter_staged": T.build_eh_datacenter_staged}
_HERE = pathlib.Path(T.__file__).parent


def _cfg():
    return SolverConfig(solver_name="highs", voll=5000.0)


def test_the_derivation_is_the_cited_median_year():
    # CEER-ECRB 7th Benchmarking Report (2022), Table 2-18, 2011-2018.
    assert T.GRID_SUPPLY_SAIDI_MIN == pytest.approx(3.945)
    assert T.GRID_SUPPLY_SAIFI == pytest.approx(0.135)
    assert T.GRID_SUPPLY_FOR == pytest.approx(3.945 / (60 * 8760), rel=1e-4)  # rounded to 1e-9
    assert T.GRID_SUPPLY_FOR == 7.506e-06
    assert T.GRID_SUPPLY_MTTR_H == 0.487
    assert 0.0 < T.GRID_SUPPLY_FOR < 1.0 and T.GRID_SUPPLY_MTTR_H > 0.0


@pytest.mark.parametrize("tid", DC)
def test_the_model_carries_the_outage_data(tid):
    g = _BUILD[tid]().generators.loc["grid_supply"]
    assert g["outage_rate_value"] == T.GRID_SUPPLY_FOR
    assert g["mttr_hours"] == T.GRID_SUPPLY_MTTR_H


@pytest.mark.parametrize("tid", DC)
def test_the_ledger_says_assumed_and_equals_the_model(tid):
    g = _BUILD[tid]().generators.loc["grid_supply"]
    for meta in (T.TEMPLATE_META[tid],
                 json.loads((_HERE / tid / T.META_FILE).read_text())):
        rows = {r["field"]: r for r in meta["ledger"]
                if r.get("entity") == "grid_supply"}
        assert set(rows) == {"outage_rate_value", "mttr_hours"}
        for field, row in rows.items():
            assert row["source"] == "assumed"
            assert row["component"] == "Generator"
            assert row["section"] == "connection"
            assert row["value"] == g[field]
            assert "CEER" in row["note"] and "not this site's" in row["note"]


def test_the_grid_supply_rows_are_on_the_dc_templates_only():
    for tid, meta in T.TEMPLATE_META.items():
        has = any(r.get("entity") == "grid_supply" for r in meta.get("ledger") or [])
        assert has is (tid in DC), tid


@pytest.mark.parametrize("tid", DC)
def test_the_margin_preflight_prices_the_grid_supply(tid):
    fr._margin_preflight(_BUILD[tid](), threading.Lock(), _cfg(), 0.1, "test")


@pytest.mark.parametrize("tid", DC)
def test_an_old_network_without_the_data_still_gets_the_named_refusal(tid):
    # A project made before this change is not migrated: its grid supply has
    # no outage data, and the preflight refuses it by name, as before.
    n = _BUILD[tid]()
    n.generators.loc["grid_supply", ["outage_rate_value", "mttr_hours"]] = np.nan
    with pytest.raises(fr.FrontConfigError, match=r"cannot price .*grid_supply"):
        fr._margin_preflight(n, threading.Lock(), _cfg(), 0.1, "test")
    assert math.isnan(n.generators.at["grid_supply", "outage_rate_value"])

"""
The IPP example's two observed day-ahead price years (IPP renewables + BESS GS-4; owner
Q-GS4 = A, 2026-10-21): DE-LU 2024 from SMARD and DK1 2024 from Energinet, shipped beside the
illustrative shapes, never replacing them (the template's default stays illustrative).

The sources are fetched once (`project_templates/fetch_ipp_market_series.py`) and pinned here by
hash. They are re-dated onto the template's 2030 calendar (hour of year; 29 Feb dropped; UTC
kept, so weekdays shift by one day) and taken as 2030 real money: an assumption the ledger
states apart from the observed values. The price–weather correlation is not preserved: the
prices are 2024's, the profiles synthetic.
"""
from __future__ import annotations

import hashlib
import json
import queue
import threading

import numpy as np
import pandas as pd
import pytest

import project_templates.eh_templates as T
from tests.test_eh_ipp import TID, TPL_DIR, M, _create

SOURCES = TPL_DIR / TID / "sources"
#: The committed sources' sha256 (printed by the fetch script).
PINS = {
    "smard_de_lu_2024.csv": "97c56c7e87ab3e132f09085106658fb8336014075d2f04069c41773a6c2e0ab8",
    "energinet_dk1_2024.csv": "f90ea159c0145f45aeece4276f9d0b64684539a1fd2e37d9160361f85029a37f",
}


def _source(file: str) -> pd.Series:
    df = pd.read_csv(SOURCES / file)
    idx = pd.DatetimeIndex(pd.to_datetime(df["timestamp"], utc=True)).tz_convert(None)
    return pd.Series(df["value"].to_numpy(dtype=float), index=idx)


def _shipped() -> dict:
    from services.library import template_series as TS

    return {s.name: s for s in TS.read(TPL_DIR / TID)}


@pytest.mark.parametrize("name", sorted(M.OBSERVED))
def test_the_re_dated_series_is_the_source_hour_for_hour(name):
    """Every 2024 hour but 29 Feb's, in order, on the 2030 snapshots; the values unchanged."""
    src = _source(M.OBSERVED[name]["source_file"])
    kept = src[~((src.index.month == 2) & (src.index.day == 29))]
    out = M.observed_series(name)
    assert out.index.equals(M.snapshots())
    np.testing.assert_array_equal(out.to_numpy(), kept.to_numpy())
    # Hour of year and UTC hour kept: 1 Mar 2024 00:00 lands on 1 Mar 2030 00:00.
    assert out[pd.Timestamp("2030-03-01 00:00")] == src[pd.Timestamp("2024-03-01 00:00")]
    assert out[pd.Timestamp("2030-12-31 23:00")] == src[pd.Timestamp("2024-12-31 23:00")]
    shipped = _shipped()[name].series
    np.testing.assert_array_equal(shipped.to_numpy(), out.to_numpy())
    assert shipped.index.equals(out.index)


@pytest.mark.parametrize("file", sorted(PINS))
def test_the_sources_are_the_fetched_files_complete(file):
    assert hashlib.sha256((SOURCES / file).read_bytes()).hexdigest() == PINS[file]
    s = _source(file)
    assert len(s) == 8784 and not s.isna().any()
    assert s.index.equals(pd.date_range("2024-01-01", periods=8784, freq="h"))


def test_the_observed_series_carry_their_attribution_and_fit_the_finance():
    from services.library import template_series as TS

    shipped = _shipped()
    fin = T.solver_config(TID)["finance"]
    for name, spec in M.OBSERVED.items():
        meta = shipped[name].meta
        assert spec["attribution"] in meta["source"] and "CC BY 4.0" in meta["source"]
        assert "illustrative" not in meta["notes"]
        assert TS.note_values(meta) == {"currency": "EUR", "price_basis": "real",
                                        "currency_year": "2030", "region": spec["region"],
                                        "source_time_basis": "UTC"}
        for words in ("observed 2024", "data changed", "29 feb dropped", "weekdays shift",
                      "2030 real money (assumed)", "price–weather correlation not preserved"):
            assert words in meta["description"].lower(), (name, words)
        assert shipped[name].targets == ()
    assert TS.basis_conflicts(list(shipped.values()), fin) == []


def test_the_default_stays_illustrative():
    meta = json.loads((TPL_DIR / TID / "price_series" / "meta.json").read_text())
    filled = {s["name"] for s in meta["series"] if s["targets"]}
    assert filled == {M.SERIES["base"]}


def test_the_ledger_states_the_observed_values_and_the_re_dating_apart():
    rows = {r["item"]: r for r in T.TEMPLATE_META[TID]["ledger"]}
    for name, spec in M.OBSERVED.items():
        obs = rows[f"day-ahead price, observed ({spec['region']} 2024)"]
        assert obs["source"] == "observed" and spec["attribution"] in obs["note"]
        assert name in obs["note"]
    red = rows["observed prices re-dated to 2030"]
    assert red["source"] == "assumed"
    assert "2030 real money" in red["note"]
    assert "price–weather correlation not preserved: prices are 2024, the profiles are " \
           "synthetic" in red["note"]


@pytest.mark.live_solve
def test_create_mints_all_five_and_an_observed_one_binds_and_solves(client, tmp_projects_dir,
                                                                    bound_ctx):
    """Created, the project's Library holds the three shapes and the two observed years; the
    export price swapped to the SMARD year (the 7a picker) binds on the export Link, and the
    solve prices the export with it."""
    import routers.simulation as sim_router
    from services.commercial import lp_bindings as LP
    from services.pypsa_service import PyPSAService
    from services.solver_service import run_simulation
    from tests.test_eh_ipp import _edit_commercial

    _create(client)
    listed = {s["id"]: s for s in client.get("/api/library/series").json()}
    assert set(M.SERIES.values()) | set(M.OBSERVED) <= set(listed)

    def change(com):
        com["export_price_ref"] = listed["ipp_da_smard_2024"]
        return lambda c: c["export_price_ref"]["id"]
    assert _edit_commercial(change)(client) == "ipp_da_smard_2024"
    want = M.observed_series("ipp_da_smard_2024").to_numpy()
    with bound_ctx(client):
        n = PyPSAService.get_network()
        np.testing.assert_allclose(n.links_t[LP.EXPORT_PRICE_ATTR]["grid_export"].to_numpy(), want)
        cfg = sim_router._state["solver_config"]
        status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                      queue.SimpleQueue(), state_update=lambda **k: None)
        assert status in ("ok", "optimal"), (status, cond)
        np.testing.assert_allclose(n.links_t[LP.EXPORT_PRICE_ATTR]["grid_export"].to_numpy(),
                                   want)
        # The negative hours of 2024 reach the plants: they curtail in some of them.
        neg = want < 0
        assert neg.sum() >= 400
        assert float(n.links_t.p0["grid_export"].to_numpy()[neg].max()) < 70.0

"""
P8(a) — Class-C ``kind=profiles`` runner + synthetic fixtures.

Plan: docs/superpowers/plans/2026-09-14-eh-reference-design-gaps.md Phase 8
Design: stress.py — profiles are whole-scenario profile swaps (not parametric
multipliers); occurrence_basis ``scenario:profiles``; real climate packs
remain optional behind data availability.

Synthetic packs live under tests/fixtures/eh_class_c/ and are also loadable
via ``profile_pack`` for in-process demos.
"""
from __future__ import annotations

import pathlib
import queue

import pandas as pd
import pypsa
import pytest

from services.adequacy import stress as ST
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig

WEIGHT = 3.0
N = 2
VOLL = 3000.0
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "eh_class_c"


def _network_with_wind() -> pypsa.Network:
    """Base: load 100, gas 50, wind 40 @ p_max_pu=1 → short 10 → EUE 60."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=N, freq="h"))
    n.snapshot_weightings.loc[:, :] = WEIGHT
    n.add("Carrier", "gas")
    n.add("Carrier", "wind")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "g", bus="b", carrier="gas", p_nom=50.0,
          marginal_cost=10.0)
    n.add("Generator", "wind1", bus="b", carrier="wind", p_nom=40.0)
    n.generators_t.p_max_pu = pd.DataFrame(
        {"wind1": [1.0, 1.0]}, index=n.snapshots)
    return n


def _profiles_scenario(**kw) -> dict:
    """Inline synthetic dunkelflaute: load 130, wind 0.25.

    Base short 10 → EUE 60; snap short 70 → EUE 420; ΔEUE = 360.
    """
    base = {
        "id": "synth_dunkelflaute",
        "name": "Synthetic dunkelflaute",
        "kind": "profiles",
        "frequency_per_year": 0.05,
        "loads_p_set": {"l": [130.0, 130.0]},
        "generators_p_max_pu": {"wind1": [0.25, 0.25]},
    }
    base.update(kw)
    return base


def test_profiles_incomplete_without_series_is_fail_closed():
    """Bare profiles (no pack / series) must not pretend to solve."""
    scen = [{"id": "s0", "name": "s0", "kind": "profiles",
             "frequency_per_year": 1.0},
            {"id": "s1", "name": "s1", "kind": "profiles",
             "frequency_per_year": 1.0}]
    rows, restore = ST.run_class_c_sweep(object(), object(), SolverConfig(), scen)
    assert [r["id"] for r in rows] == ["scenario:s0", "scenario:s1"]
    assert all(r["status"] == "profiles_incomplete" for r in rows)
    assert restore == {"base_restored": None, "base_restore_status": None,
                       "aborted": False}


def test_inline_profiles_prices_dunkelflaute():
    n = _network_with_wind()
    PyPSAService.set_network(n)
    rows, restore = ST.run_class_c_sweep(
        n, PyPSAService.get_lock(), SolverConfig(voll=VOLL),
        [_profiles_scenario()], log_queue=queue.SimpleQueue())
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] in ("ok", "optimal")
    # Base short 10 → EUE 60; snap: 130 − 50 − 0.25·40 = 70 short → EUE 420;
    # ΔEUE = 360.
    assert r["delta_eue_mwh"] == pytest.approx(360.0, rel=1e-3)
    fm = r["failure_mode"]
    assert fm["failure_class"] == "C"
    assert fm["occurrence_basis"] == "scenario:profiles"
    assert fm["occurrence_per_year"] == pytest.approx(0.05)
    assert fm["criticality_eur_per_year"] == pytest.approx(
        0.05 * 360.0 * VOLL, rel=1e-3)
    # Restored.
    assert float(n.loads.at["l", "p_set"]) == pytest.approx(100.0)
    assert float(n.generators_t.p_max_pu["wind1"].min()) == pytest.approx(1.0)
    assert restore["aborted"] is False


def test_synthetic_pack_resolves_and_ranks():
    """Multi-year synthetic fixture: two packs rank by ΔEUE × frequency."""
    from services.adequacy.stress import load_synthetic_profile_pack

    mild = load_synthetic_profile_pack("synth_mild_snap")
    deep = load_synthetic_profile_pack("synth_dunkelflaute")
    assert mild["kind"] == "profiles"
    assert deep["loads_p_set"]["l"] == [130.0, 130.0]

    n = _network_with_wind()
    PyPSAService.set_network(n)
    scenarios = [
        {**mild, "id": "year_mild", "frequency_per_year": 0.2},
        {**deep, "id": "year_deep", "frequency_per_year": 0.05},
    ]
    rows, _restore = ST.run_class_c_sweep(
        n, PyPSAService.get_lock(), SolverConfig(voll=VOLL),
        scenarios, log_queue=queue.SimpleQueue())
    runnable = [r for r in rows if r.get("failure_mode")]
    assert len(runnable) == 2
    # Ranked by ΔEUE desc (assembler sort) — deep shortfall first.
    assert runnable[0]["id"] == "scenario:year_deep"
    assert all(r["failure_mode"]["occurrence_basis"] == "scenario:profiles"
               for r in runnable)


def test_profiles_abort_partial_like_parametric(monkeypatch):
    """Aborted sweep keeps reached profile rows; restore flags aborted."""
    from services.adequacy import sweep as SW

    scen = [
        _profiles_scenario(id="a"),
        _profiles_scenario(id="b", name="b"),
    ]
    monkeypatch.setattr(
        SW, "run_contingency_sweep",
        lambda *a, **k: {
            "base": {"eue_mwh": 0.0, "status": "ok"},
            "contingencies": {
                "scenario:a": {
                    "status": "ok", "eue_mwh": 10.0, "delta_eue_mwh": 10.0,
                    "meta": {"name": "a", "frequency_per_year": 0.05},
                },
            },
            "aborted": True, "base_restored": True, "base_restore_status": "ok",
        })
    rows, restore = ST.run_class_c_sweep(
        object(), object(), SolverConfig(voll=VOLL), scen)
    assert [r["id"] for r in rows] == ["scenario:a"]
    assert rows[0]["failure_mode"]["occurrence_basis"] == "scenario:profiles"
    assert restore["aborted"] is True


def test_registry_accepts_inline_profiles(tmp_path):
    ST.save_scenarios(tmp_path, [_profiles_scenario()])
    loaded = ST.load_scenarios(tmp_path)
    assert loaded[0]["kind"] == "profiles"
    assert loaded[0]["loads_p_set"]["l"] == [130.0, 130.0]


def test_registry_rejects_bad_profile_lengths(tmp_path):
    with pytest.raises(ST.StressValidationError, match="length"):
        ST.save_scenarios(tmp_path, [_profiles_scenario(
            loads_p_set={"l": [1.0]})])  # length 1, inconsistent with twin series

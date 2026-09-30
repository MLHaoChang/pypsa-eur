"""P29 (B3, guided-mode deferred spec §4.3): every FMEA row says why it
costs €0.

``worksheet.zero_reason`` is the one rule; the three engines (COPT class A,
the class-B link sweep, the class-C stress sweep) and the COPT block merge
set ``failure_mode["zero_reason"]``, and ``per_mode`` forwards it through
the ``failure_mode`` spread with no change of its own. Additive only: no
number changes, and the golden / range suites stay as they were.
"""
from __future__ import annotations

import contextlib
import math
import queue

import pandas as pd
import pypsa
import pytest

from services.adequacy import copt as C
from services.adequacy import stress as ST
from services.adequacy import sweep as S
from services.adequacy.copt_endpoint import build_copt_payload, build_fmea_modes_payload
from services.adequacy.worksheet import zero_reason
from services.solver_service import SolverConfig

REASONS = {"no_shortfall", "no_outage_data", "unpriced", "out_of_scope", None}


# ── the helper ──────────────────────────────────────────────────────────────

def _zr(**kw):
    base = dict(severity_eur=0.0, delta_eue_mwh=1.0, occurrence_per_year=2.0,
                in_metric_scope=True, voll=5000.0)
    base.update(kw)
    return zero_reason(**base)


def test_a_priced_row_has_no_reason():
    assert _zr(severity_eur=12.5) is None


def test_out_of_scope():
    assert _zr(in_metric_scope=False, delta_eue_mwh=0.0, occurrence_per_year=0.0) == "out_of_scope"


@pytest.mark.parametrize("occ", [0.0, -1.0, math.nan, math.inf])
def test_no_outage_data(occ):
    assert _zr(occurrence_per_year=occ) == "no_outage_data"


def test_no_outage_data_is_tested_before_no_shortfall():
    # The mttr-0 unit: no occurrence AND no measured shortfall — the missing
    # data is the reason, not "the site copes".
    assert _zr(occurrence_per_year=0.0, delta_eue_mwh=0.0) == "no_outage_data"


def test_unpriced():
    assert _zr(voll=0.0) == "unpriced"
    assert _zr(voll=0.0, delta_eue_mwh=0.0) == "unpriced"


def test_no_shortfall():
    assert _zr(delta_eue_mwh=0.0) == "no_shortfall"


def test_a_zero_row_with_every_input_present_has_no_reason():
    # Not reachable from the engines (severity would be > 0); the helper
    # does not invent a reason for it.
    assert _zr() is None


# ── class A (COPT) on a constructed network ─────────────────────────────────

def _copt_network(*, grid: bool, mttr2: float = 24.0) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=24, freq="h"))
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=80.0)
    if grid:
        # Never fails: covers the load alone, so the gensets are redundant.
        n.add("Generator", "grid", bus="b", carrier="gas", p_nom=200.0,
              outage_rate_value=0.0, outage_rate_basis="FOR", mttr_hours=24.0)
    n.add("Generator", "genset_1", bus="b", carrier="gas", p_nom=100.0,
          outage_rate_value=0.05, outage_rate_basis="FOR", mttr_hours=24.0)
    n.add("Generator", "genset_2", bus="b", carrier="gas", p_nom=100.0,
          outage_rate_value=0.05, outage_rate_basis="FOR", mttr_hours=mttr2)
    return n


def _per_mode(n, voll):
    out = build_copt_payload(n, SolverConfig(voll=voll), get_lock=contextlib.nullcontext)
    return {r["name"]: r for r in out["per_mode"]}


def test_copt_a_redundant_genset_has_no_shortfall():
    rows = _per_mode(_copt_network(grid=True), 5000.0)
    assert rows["genset_1"]["severity_eur"] == 0.0
    assert rows["genset_1"]["zero_reason"] == "no_shortfall"


def test_copt_a_unit_with_mttr_zero_has_no_outage_data():
    rows = _per_mode(_copt_network(grid=False, mttr2=0.0), 5000.0)
    assert rows["genset_2"]["delta_eue_mwh"] > 0
    assert rows["genset_2"]["zero_reason"] == "no_outage_data"
    # Its sibling is priced and carries no reason.
    assert rows["genset_1"]["severity_eur"] > 0
    assert rows["genset_1"]["zero_reason"] is None


def test_copt_voll_zero_is_unpriced():
    rows = _per_mode(_copt_network(grid=False), 0.0)
    for name in ("genset_1", "genset_2"):
        assert rows[name]["delta_eue_mwh"] > 0
        assert rows[name]["zero_reason"] == "unpriced"


def test_copt_every_row_carries_the_key():
    for n, voll in ((_copt_network(grid=True), 5000.0),
                    (_copt_network(grid=False, mttr2=0.0), 5000.0),
                    (_copt_network(grid=False), 0.0)):
        for r in _per_mode(n, voll).values():
            assert "zero_reason" in r
            assert r["zero_reason"] in REASONS


def test_copt_block_merge_rederives_the_reason():
    # Two periods: the merge re-sums criticality and recomputes severity, and
    # re-derives the reason from the merged numbers.
    units = [C.CoptUnit("g_zero_mttr", 100.0, 0.1, mttr_hours=0.0),
             C.CoptUnit("g_ok", 100.0, 0.1, mttr_hours=24.0)]
    idx = pd.MultiIndex.from_product(
        [[2030, 2040], pd.date_range("2030-01-01", periods=4, freq="h")],
        names=["period", "timestep"])
    res = pd.Series(150.0, index=idx)
    w = pd.Series(1.0, index=idx)
    an = C.screening_analysis(units, res, weights=w, voll=1000.0)
    by = {r["name"]: r["failure_mode"] for r in an["rows"]}
    assert by["g_zero_mttr"]["zero_reason"] == "no_outage_data"
    assert by["g_ok"]["severity_eur"] > 0 and by["g_ok"]["zero_reason"] is None
    an0 = C.screening_analysis(units, res, weights=w, voll=0.0)
    by0 = {r["name"]: r["failure_mode"] for r in an0["rows"]}
    assert by0["g_ok"]["zero_reason"] == "unpriced"
    assert by0["g_zero_mttr"]["zero_reason"] == "no_outage_data"


# ── class B (link sweep) and class C (stress) — the driver stubbed ─────────

def _sweep_network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Carrier", "gas")
    n.add("Carrier", "H2")
    n.add("Bus", "bus_gen", carrier="AC")
    n.add("Bus", "bus_load", carrier="AC")
    n.add("Bus", "h2", carrier="H2")
    n.add("Load", "l", bus="bus_load", p_set=50.0)
    n.add("Generator", "big_cheap", bus="bus_gen", carrier="gas", p_nom=200.0)
    n.add("Link", "tie", bus0="bus_gen", bus1="bus_load", p_nom=100.0,
          outage_rate_value=0.02, outage_rate_basis="FOR", mttr_hours=24.0)
    n.add("Link", "tie_spare", bus0="bus_gen", bus1="bus_load", p_nom=100.0,
          outage_rate_value=0.02, outage_rate_basis="FOR", mttr_hours=24.0)
    n.add("Link", "electrolyser", bus0="bus_gen", bus1="h2", p_nom=80.0,
          carrier="H2", efficiency=0.7,
          outage_rate_value=0.05, outage_rate_basis="FOR", mttr_hours=24.0)
    return n


def _stub_driver(monkeypatch, deltas: dict[str, float]):
    def fake(network, lock, cfg, contingencies, **_kw):
        return {"base": {"eue_mwh": 0.0, "status": "ok"},
                "contingencies": {c["id"]: {"delta_eue_mwh": deltas[c["id"]],
                                            "status": "ok"} for c in contingencies},
                "base_restored": True, "base_restore_status": "optimal",
                "aborted": False}
    monkeypatch.setattr(S, "run_contingency_sweep", fake)


def test_sweep_out_of_scope_link(monkeypatch):
    _stub_driver(monkeypatch, {"link:tie:forced_outage": 300.0,
                               "link:tie_spare:forced_outage": 0.0,
                               "link:electrolyser:forced_outage": 12.0})
    rows, _ = S.run_class_b_sweep(_sweep_network(), contextlib.nullcontext(),
                                  SolverConfig(voll=3000.0),
                                  log_queue=queue.SimpleQueue())
    fm = {r["id"]: r["failure_mode"] for r in rows}
    assert fm["link:electrolyser:forced_outage"]["zero_reason"] == "out_of_scope"
    assert fm["link:tie_spare:forced_outage"]["zero_reason"] == "no_shortfall"
    assert fm["link:tie:forced_outage"]["severity_eur"] > 0
    assert fm["link:tie:forced_outage"]["zero_reason"] is None


def test_stress_scenario_with_no_shortfall(monkeypatch):
    scen = [
        {"id": "mild", "name": "Mild week", "kind": "parametric",
         "frequency_per_year": 0.5, "electrical_load_multiplier": 1.1},
        {"id": "cold", "name": "Cold snap", "kind": "parametric",
         "frequency_per_year": 0.2, "electrical_load_multiplier": 1.5},
    ]
    _stub_driver(monkeypatch, {"scenario:mild": 0.0, "scenario:cold": 20.0})
    rows, _ = ST.run_class_c_sweep(_sweep_network(), contextlib.nullcontext(),
                                   SolverConfig(voll=3000.0), scen,
                                   log_queue=queue.SimpleQueue())
    fm = {r["id"]: r["failure_mode"] for r in rows}
    assert fm["scenario:mild"]["zero_reason"] == "no_shortfall"
    # A shortfall is priced per event: severity > 0 → no reason.
    assert fm["scenario:cold"]["severity_eur"] > 0
    assert fm["scenario:cold"]["zero_reason"] is None


def test_per_mode_forwards_the_sweep_rows_key():
    n = _copt_network(grid=True)
    record = {"status": "done", "rows": [
        {"id": "link:x:forced_outage", "delta_eue_mwh": 0.0, "failure_mode": {
            "mode_id": "link:x:forced_outage", "component_class": "Link",
            "name": "x", "failure_class": "B", "occurrence_per_year": 1.0,
            "occurrence_basis": "FOR", "severity_eur": 0.0,
            "criticality_eur_per_year": 0.0, "in_metric_scope": False,
            "engine": "lp_proxy", "fidelity": "deterministic_scenario",
            "zero_reason": "out_of_scope"}}]}
    out = build_fmea_modes_payload(n, SolverConfig(voll=5000.0), sweep_record=record,
                                   get_lock=contextlib.nullcontext)
    by = {r["mode_id"]: r for r in out["per_mode"]}
    assert by["link:x:forced_outage"]["zero_reason"] == "out_of_scope"
    assert by["generator:genset_1:forced_outage"]["zero_reason"] == "no_shortfall"


def test_the_contract_model_still_validates_a_row_with_the_key():
    from models.adequacy import FailureModeResult
    rows = _per_mode(_copt_network(grid=True), 5000.0)
    fm = {k: v for k, v in rows["genset_1"].items() if k not in ("delta_eue_mwh", "note")}
    FailureModeResult.model_validate(fm)


def test_the_zonal_screen_merge_rederives_the_reason_too():
    # Beyond the spec's anchors: the Energy Hub's two-area screening has its
    # own block merge (eh_stages._screen) and a common-mode row; both carry
    # the key, re-derived from the merged numbers.
    from tests.test_energy_hub_common_mode import _freeze, _weak
    cm = _freeze(_weak(rate=0.05, mttr=24.0))
    assert cm.copt_rows
    for r in cm.copt_rows:
        fm = r["failure_mode"]
        assert "zero_reason" in fm, fm["name"]
        assert fm["zero_reason"] == zero_reason(
            severity_eur=fm["severity_eur"], delta_eue_mwh=r["delta_eue_mwh"],
            occurrence_per_year=fm["occurrence_per_year"],
            in_metric_scope=fm["in_metric_scope"], voll=cm.voll)

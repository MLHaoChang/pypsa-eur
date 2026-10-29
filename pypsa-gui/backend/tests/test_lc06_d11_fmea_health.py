"""LC-06 S11, LC-D11: FMEA failure modes and asset health through the copilot's tools.

The seam: ``update_solver_config`` (VoLL), ``get_adequacy_results('fmea_modes')``
(class A, the COPT leave-one-out), ``run_fmea_sweep`` then
``get_adequacy_results('fmea_sweep')`` (class B, one Link outage re-solved),
``record_asset_health`` and ``get_asset_health`` (the provenance ledger).

Class A, on the ``test_copt_assurance`` classical fleet: thermal1 60 MW
(EFORd 0.1, MTTR 50 h), thermal2 40 MW (0.2, 40 h), residual 70 then 100 MW,
two snapshots weighted 3 h. Capacity states: 100 MW p 0.72, 60 MW p 0.18,
40 MW p 0.08, 0 MW p 0.02.

* Fleet as is: EUE = 3·(10·0.18 + 30·0.08 + 70·0.02) + 3·(40·0.18 + 60·0.08 + 100·0.02)
  = 3·5.6 + 3·14 = 58.8 MWh.
* thermal1 never out (60 MW firm; 100 MW p 0.8, 60 MW p 0.2):
  EUE = 3·(10·0.2) + 3·(40·0.2) = 30 MWh, so ΔEUE = 28.8 MWh.
* thermal2 never out (100 MW p 0.9, 40 MW p 0.1):
  EUE = 3·(30·0.1) + 3·(60·0.1) = 27 MWh, so ΔEUE = 31.8 MWh.
* Occurrence 8760·q/MTTR: 8760·0.1/50 = 17.52 /yr and 8760·0.2/40 = 43.8 /yr.
* Criticality is per year, like occurrence (ticket 30): the horizon's ΔEUE
  scaled to a year by 8760/Σw = 8760/6 = 1460, times VoLL. thermal1:
  28.8·1460·1000 = 42 048 000 EUR/yr, severity 42 048 000/17.52 = 2 400 000 EUR.
  thermal2: 31.8·1460·1000 = 46 428 000 EUR/yr, severity 1 060 000 EUR.

Class B: bus B1 with 200 MW firm and 100 MW load, and bus B2 with 5 MW load
fed only by the 10 MW tie (FOR 0.02, MTTR 24 h). The base serves all the load.
With the tie out, B2 sheds 5 MW in both snapshots: ΔEUE = 5·2·3 = 30 MWh.
Criticality q·ΔEUE·(8760/6)·VoLL = 0.02·30·1460·1000 = 876 000 EUR/yr over
occurrence 8760·0.02/24 = 7.3 /yr: severity 120 000 EUR.

Tolerance: ``rel=1e-9``. COPT is exact arithmetic on four states, and the
LP's shed load is a bound that HiGHS returns to its precision. The mutations
move every figure by over 1 %.
"""
# ruff: noqa: F811 -- shared public harness fixtures
import json
import time

import pandas as pd
import pytest
from fastapi import HTTPException

from services import chat_tools
from tests.test_copt_assurance import classical, fleet  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401

VOLL = 1000.0
REL = 1e-9
P = "Tool Baseline"


def _modes():
    chat_tools.update_solver_config({"voll": VOLL})
    out = chat_tools.get_adequacy_results("fmea_modes")
    assert out["voll_eur_per_mwh"] == VOLL
    return {m["name"]: m for m in out["per_mode"]}


# ── class A ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name, delta_eue, occurrence", [
    ("thermal1", 58.8 - 30.0, 8760 * 0.1 / 50),
    ("thermal2", 58.8 - 27.0, 8760 * 0.2 / 40),
], ids=["thermal1", "thermal2"])
def test_class_a_prices_each_units_outages_by_hand(classical, name, delta_eue, occurrence):
    modes = _modes()
    assert set(modes) == {"thermal1", "thermal2"}       # the wind profile has no outage rate
    m = modes[name]
    assert m["failure_class"] == "A" and m["engine"] == "copt"
    assert m["mode_id"] == f"generator:{name}:forced_outage"
    assert m["delta_eue_mwh"] == pytest.approx(delta_eue, rel=REL)
    assert m["occurrence_per_year"] == pytest.approx(occurrence, rel=REL)
    assert m["severity_eur"] * m["occurrence_per_year"] == pytest.approx(m["criticality_eur_per_year"], rel=REL)


@pytest.mark.parametrize("name, criticality, severity", [
    ("thermal1", 28.8 * 1460 * VOLL, 28.8 * 1460 * VOLL / 17.52),
    ("thermal2", 31.8 * 1460 * VOLL, 31.8 * 1460 * VOLL / 43.8),
], ids=["thermal1", "thermal2"])
def test_class_a_criticality_is_per_year(classical, name, criticality, severity):
    m = _modes()[name]
    assert m["criticality_eur_per_year"] == pytest.approx(criticality, rel=REL)
    assert m["severity_eur"] == pytest.approx(severity, rel=REL)
    assert m["criticality_basis"] == "annualised"
    assert m["horizon_years"] == pytest.approx(6 / 8760, rel=REL)


def test_class_a_criticality_on_a_year_is_the_horizons(classical):
    """Mutation: each snapshot weighted 4380 h, so the horizon is a year and
    nothing is scaled. ΔEUE = 28.8/3·4380 = 42 048 MWh; criticality 42 048·1000."""
    classical.network.snapshot_weightings.loc[:, :] = 4380.0
    m = _modes()["thermal1"]
    assert m["delta_eue_mwh"] == pytest.approx(28.8 / 3 * 4380, rel=REL)
    assert m["criticality_eur_per_year"] == pytest.approx(28.8 / 3 * 4380 * VOLL, rel=REL)
    assert m["horizon_years"] == pytest.approx(1.0, rel=REL)


def test_class_a_moves_with_one_units_rate(classical):
    """Mutation: thermal2 at EFORd 0.1 instead of 0.2. States 100 MW p 0.81, 60 MW
    p 0.09, 40 MW p 0.09, 0 MW p 0.01; EUE = 3·(0.9 + 2.7 + 0.7) + 3·(3.6 + 5.4 + 1)
    = 42.9. thermal2 firm: 100 MW p 0.9, 40 MW p 0.1, EUE 27 (as before), ΔEUE 15.9."""
    classical.network.generators.at["thermal2", "outage_rate_value"] = 0.1
    m = _modes()["thermal2"]
    assert m["delta_eue_mwh"] == pytest.approx(42.9 - 27.0, rel=REL)
    assert m["occurrence_per_year"] == pytest.approx(8760 * 0.1 / 40, rel=REL)


# ── class B ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tied(baseline):
    ctx = baseline[0]
    n = ctx.network
    n.snapshot_weightings.loc[:, :] = 3.0
    n.generators.at["G1", "p_nom"] = 200.0
    n.loads_t.p_set = pd.DataFrame({"L1": [100.0, 100.0]}, index=n.snapshots)
    n.add("Bus", "B2", v_nom=110)
    n.add("Load", "L2", bus="B2", p_set=5.0)
    n.add("Link", "tie", bus0="B1", bus1="B2", p_nom=10.0,
          outage_rate_value=0.02, outage_rate_basis="FOR", mttr_hours=24.0)
    return ctx


def _sweep(n):
    chat_tools.update_solver_config({"voll": VOLL})
    started = chat_tools.run_fmea_sweep()
    assert started["status"] == "running", started
    deadline = time.time() + 300
    while time.time() < deadline:
        done = chat_tools.get_adequacy_results("fmea_sweep")
        if done.get("status") not in ("running", "queued"):
            break
        time.sleep(0.2)
    assert done["status"] == "done", done
    assert done.get("base_restored", True) is not False
    return {r["id"]: r for r in done["rows"]}


def test_class_b_one_link_outage_sheds_its_load(tied):
    rows = _sweep(tied.network)
    (row,) = rows.values()
    fm = row["failure_mode"]
    assert fm["failure_class"] == "B" and fm["component_class"] == "Link"
    assert row["delta_eue_mwh"] == pytest.approx(5.0 * 2 * 3.0, rel=REL)          # 30 MWh, not 0
    assert fm["occurrence_per_year"] == pytest.approx(8760 * 0.02 / 24, rel=REL)
    assert tied.network.links.at["tie", "p_nom"] == 10.0                          # the outage was undone


def test_class_b_criticality_is_per_year(tied):
    (row,) = _sweep(tied.network).values()
    fm = row["failure_mode"]
    assert fm["criticality_eur_per_year"] == pytest.approx(0.02 * 30.0 * 1460 * VOLL, rel=REL)
    assert fm["severity_eur"] == pytest.approx(0.02 * 30.0 * 1460 * VOLL / 7.3, rel=REL)
    assert fm["criticality_basis"] == "annualised"
    assert fm["horizon_years"] == pytest.approx(6 / 8760, rel=REL)


def test_class_b_moves_with_the_load_behind_the_tie(tied):
    """Mutation: 4 MW behind the tie sheds 4·2·3 = 24 MWh."""
    tied.network.loads.at["L2", "p_set"] = 4.0
    (row,) = _sweep(tied.network).values()
    assert row["delta_eue_mwh"] == pytest.approx(24.0, rel=REL)


# ── asset health ────────────────────────────────────────────────────────────


def entry(name, rate, mttr, confidence="medium", **kw):
    return {"component": "generators", "name": name, "outage_rate_value": rate, "outage_rate_basis": "EFORd",
            "mttr_hours": mttr, "method": "expert_judgement", "source_ref": "demo sheet",
            "measured_at": "2026-01-01", "confidence": confidence, **kw}


def test_health_reads_back_from_storage_and_reconciles_with_the_network(classical, baseline):
    root = baseline[2]
    entries = [entry("thermal1", 0.1, 50.0), entry("thermal2", 0.25, 40.0, "low"), entry("ghost", 0.05, 10.0)]
    chat_tools.record_asset_health(P, entries)
    stored = json.loads((root / "asset_health.json").read_text())
    got = chat_tools.get_asset_health(P)                     # a fresh read through the route
    want = [{**e, "note": None} for e in entries]
    assert stored["entries"] == want and got["entries"] == want
    assert got["version"] == stored["version"] == 1
    prov = got["provenance"]
    assert prov["counts"] == {"sourced": 1, "unsourced": 0, "drifted": 1, "orphaned": 1}
    assert [s["name"] for s in prov["sourced"]] == ["thermal1"]
    assert prov["sourced"][0]["method"] == "expert_judgement" and prov["sourced"][0]["confidence"] == "medium"
    (drift,) = prov["drifted"]
    assert (drift["name"], drift["recorded"], drift["on_network"]) == ("thermal2", 0.25, 0.2)
    assert [o["name"] for o in prov["orphaned"]] == ["ghost"]


@pytest.mark.parametrize("field, bad", [
    ("outage_rate_value", 1.0),
    ("mttr_hours", 0.0),
    ("measured_at", "01/01/2026"),
    ("confidence", "certain"),
])
def test_an_invalid_record_names_its_field_and_stores_nothing(classical, baseline, field, bad):
    root = baseline[2]
    chat_tools.record_asset_health(P, [entry("thermal1", 0.1, 50.0)])
    before = (root / "asset_health.json").read_bytes()
    with pytest.raises(HTTPException) as exc:
        chat_tools.record_asset_health(P, [entry("thermal1", 0.1, 50.0), {**entry("thermal2", 0.2, 40.0), field: bad}])
    assert exc.value.status_code == 422
    assert field in str(exc.value.detail), exc.value.detail
    assert (root / "asset_health.json").read_bytes() == before

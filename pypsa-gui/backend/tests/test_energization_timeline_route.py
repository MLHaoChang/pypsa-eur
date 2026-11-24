# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""DC template review 06: `GET /api/projects/{name}/flow/energization_timeline`
reads, per model period, the DC's peak facility demand against the energized
grid import and the committed on-site firm capacity, with the shortfall plain.
It reads the open network, so a saved energization edit shows on the next read
without a run (no re-solve)."""

from __future__ import annotations

import pytest

LINK = "grid_import"


def _create(client, tid, name, spec=True):
    r = client.post(
        f"/api/projects/from_template/{tid}",
        params={"name": name},
        json={"dc_load": {}} if spec else None,
    )
    assert r.status_code == 200, r.text
    return r.json().get("name", name)


def _get(client, name):
    return client.get(f"/api/projects/{name}/flow/energization_timeline")


def test_timeline_reads_demand_grid_and_onsite_per_period(client, tmp_projects_dir):
    name = _create(client, "eh_datacenter_staged", "tl06")
    r = _get(client, name)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["basis"] == "inputs" and body["unit"] == "MW"
    assert body["basis_note"]
    rows = {p["period"]: p for p in body["periods"]}
    assert list(rows) == ["2027", "2030", "2035"]
    assert [rows[k]["grid_mw"] for k in rows] == [15.0, 55.0, 55.0]
    for p in rows.values():
        assert p["demand_mw"] > 0
        onsite = sum(k["mw"] for k in p["onsite"])
        assert p["onsite_mw"] == pytest.approx(onsite)
        gap = p["demand_mw"] - p["grid_mw"] - p["onsite_mw"]
        assert p["shortfall_mw"] == pytest.approx(max(gap, 0.0))
    assert body["time_to_power"]["status"] in ("available", "not_available")


def test_timeline_redraws_from_a_saved_energization_edit(client, tmp_projects_dir):
    name = _create(client, "eh_datacenter_staged", "tl06b")
    before = {p["period"]: p for p in _get(client, name).json()["periods"]}
    r = client.put(
        f"/api/projects/{name}/flow/energization",
        json={"link": LINK, "period": 2030, "mw": 30},
    )
    assert r.status_code == 200, r.text
    after = {p["period"]: p for p in _get(client, name).json()["periods"]}
    assert after["2030"]["grid_mw"] == 30.0
    assert after["2030"]["shortfall_mw"] >= before["2030"]["shortfall_mw"]
    assert after["2027"] == before["2027"]


def test_timeline_refuses_a_project_with_no_dc_load(client, tmp_projects_dir):
    name = _create(client, "eh_h2_hub", "tl06c", spec=False)
    r = _get(client, name)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == "energization_timeline_unavailable"


# ── the on-site firm rule and the run overlay, on a hand-built network ──────


def _net():
    import pandas as pd
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "b")
    n.add("Load", "l", bus="b", p_set=[10.0, 30.0, 20.0, 10.0])
    return n


def test_onsite_firm_counts_only_committed_fuel_units_and_batteries():
    from services import dc_energization_timeline as T

    n = _net()
    n.add("Generator", "gas1", bus="b", carrier="gas", p_nom=8.0)
    n.add("Generator", "gas2", bus="b", carrier="gas", p_nom=4.0)
    n.add("Generator", "gas_new", bus="b", carrier="gas", p_nom=5.0, p_nom_extendable=True)
    n.add("Generator", "pv", bus="b", carrier="solar", p_nom=9.0)
    n.add("Generator", "grid", bus="b", carrier="grid", p_nom=50.0)
    n.add("Generator", "zero", bus="b", carrier="gas", p_nom=0.0)
    n.add("Generator", "__voll_l", bus="b", carrier="load_shedding", p_nom=99.0)
    n.add("Generator", "late", bus="b", carrier="gas", p_nom=7.0, build_year=2040)
    n.add("Generator", "off", bus="b", carrier="gas", p_nom=6.0, active=False)
    n.add("StorageUnit", "bess", bus="b", carrier="battery", p_nom=3.0, max_hours=2)
    n.add("StorageUnit", "bess_new", bus="b", carrier="battery", p_nom=1.0, p_nom_extendable=True)
    out = {k["kind"]: k for k in T.onsite_firm(n, 2030)}
    assert out["genset"]["mw"] == 12.0 and sorted(out["genset"]["units"]) == ["gas1", "gas2"]
    assert out["battery"]["mw"] == 3.0 and out["battery"]["units"] == ["bess"]
    assert {k["kind"] for k in T.onsite_firm(_net(), 2030)} == set()


def test_timeline_shortfall_and_period_peak_on_a_built_network():
    from services import dc_energization_timeline as T

    n = _net()
    n.add("Generator", "gas1", bus="b", carrier="gas", p_nom=8.0)
    out = T.timeline(n, {})
    (p,) = out["periods"]
    assert p["demand_mw"] == 30.0 and p["onsite_mw"] == 8.0
    assert out["basis"] == "inputs" and out["run_state"] is None
    assert "run" not in p


def test_run_overlay_reports_shed_and_curtailment_in_mwh():
    import numpy as np
    import pandas as pd

    from services import dc_energization_timeline as T

    n = _net()
    n.add("Generator", "gas1", bus="b", carrier="gas", p_nom=25.0)
    n.add("Generator", "pv", bus="b", carrier="solar", p_nom=5.0)
    n.add("Generator", "__voll_l", bus="b", carrier="load_shedding", p_nom=99.0)
    n.generators["p_nom_opt"] = n.generators["p_nom"]
    idx = n.snapshots
    n.generators_t.p = pd.DataFrame(
        {"gas1": [10.0, 25.0, 20.0, 10.0], "pv": [0.0, 0.0, 0.0, 0.0],
         "__voll_l": [0.0, 5.0, 0.0, 0.0]}, index=idx)
    (row,) = T._overlay(n, T._period_masks(n))
    assert row["shed_mwh"] == 5.0
    assert row["served_mwh"] == 70.0 - 5.0
    assert row["curtailed_mwh"] == 20.0  # pv could give 5 MW in 4 h and gave 0
    assert np.isclose(row["served_mwh"] + row["shed_mwh"], 70.0)

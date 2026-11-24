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


def test_timeline_of_a_project_that_is_not_open_is_refused(client, tmp_projects_dir):
    first = _create(client, "eh_datacenter_staged", "tl06d")
    _create(client, "eh_datacenter_staged", "tl06e")  # opens the second
    r = _get(client, first)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error_kind"] == "flow_project_not_open"


def test_timeline_of_a_single_period_network_has_one_all_period(
    client, tmp_projects_dir
):
    name = _create(client, "eh_datacenter", "tl06f")
    body = _get(client, name).json()
    assert [p["period"] for p in body["periods"]] == ["all"]
    assert body["periods"][0]["demand_mw"] > 0

# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
ui-modern 05 slice 2: what sections 2, 4, 5 and 6 hold (`holds` on the flow rows).

The list is display only. It comes from the same set the counts use, carries
raw values (a missing one is null, never 0), and no state reads it.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pypsa
import pytest

from services import flow_service as S
from tests.test_flow_filing_complete import TEMPLATES, _create

HOLDING = ("carriers", "loads", "assets", "profiles")


def _flow(client, tid: str, name: str = "holds") -> dict:
    return client.get(f"/api/projects/{_create(client, tid, name)}/flow").json()


def _section(flow: dict, sid: str) -> dict:
    return next(s for s in flow["sections"] if s["section"] == sid)


@pytest.mark.parametrize("tid", TEMPLATES)
def test_a_section_lists_exactly_what_its_heading_counts(client, tmp_projects_dir, tid):
    flow = _flow(client, tid)
    assert {s["section"] for s in flow["sections"] if "holds" in s} == set(HOLDING)
    for sid, noun in (("assets", "asset"), ("loads", "load")):
        row = _section(flow, sid)
        # A "not needed" section says why instead of a count; its list is still whole.
        if row["state"] != "not_needed":
            assert row["summary"] == S._count(len(row["holds"]), noun)
        names = [(h["component"], h["entity"]) for h in row["holds"]]
        assert len(names) == len(set(names))
    for h in _section(flow, "assets")["holds"]:
        assert h["field"] in ("p_nom", "e_nom") and h["unit"] in ("MW", "MWh")
        assert h["value"] is None or math.isfinite(h["value"])
    for h in _section(flow, "profiles")["holds"]:
        # The attribute's name only: no series value rides along.
        assert h["field"] in S._PROFILE_FIELDS and h["value"] is None and h["unit"] is None


@pytest.mark.parametrize("tid", TEMPLATES)
def test_the_list_decides_nothing(client, tmp_projects_dir, monkeypatch, tid):
    """States, verdicts, blockers, notes and items are the same with no list."""
    name = _create(client, tid, "inert")
    url = f"/api/projects/{name}/flow"
    with_list = client.get(url).json()
    monkeypatch.setattr(S, "_holds", lambda *a, **k: {})
    S._readiness_cache.clear()
    without = client.get(url).json()
    assert not any("holds" in s for s in without["sections"])
    for s in with_list["sections"]:
        s.pop("holds", None)
    assert with_list == without


def _hand_built() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(4))
    for bus in ("grid", "site", "h2"):
        n.add("Bus", bus)
    n.add("Generator", "market", bus="grid", p_nom=100.0)          # behind the fence
    n.add("Generator", "pv", bus="site", p_nom=5.0, carrier="solar",
          p_max_pu=[0.0, 0.5, 1.0, 0.2])
    n.add("Generator", "candidate", bus="site", p_nom=2.0, p_nom_extendable=True)
    n.add("Generator", "broken", bus="site")
    n.add("StorageUnit", "bess", bus="site", p_nom=3.0)
    n.add("Store", "tank", bus="h2", e_nom=40.0)
    n.add("Link", "grid_import", bus0="grid", bus1="site", p_nom=10.0)   # the import link
    n.add("Link", "export_tie", bus0="site", bus1="grid", p_nom=4.0)     # in `connection`
    n.add("Link", "electrolyser", bus0="site", bus1="h2", p_nom=1.5)
    n.add("Load", "fixed", bus="site", p_set=2.0)
    n.add("Load", "shaped", bus="site", p_set=[1.0, 7.5, 3.0, 2.0])
    n.generators.at["broken", "p_nom"] = np.nan
    return n


def test_the_list_and_the_count_share_one_set():
    n = _hand_built()
    assets = S._site_assets(n, ["grid_import"], {"grid_import", "export_tie"})
    assert assets == [("Generator", "pv"), ("Generator", "candidate"), ("Generator", "broken"),
                      ("StorageUnit", "bess"), ("Store", "tank"), ("Link", "electrolyser")]
    holds = S._holds(n, ["solar"], assets)
    assert [(h["component"], h["entity"]) for h in holds["assets"]] == assets
    by = {h["entity"]: h for h in holds["assets"]}
    assert by["tank"] | {"carrier": None} == {
        "component": "Store", "entity": "tank", "carrier": None, "field": "e_nom",
        "value": 40.0, "unit": "MWh", "extendable": False}
    assert by["pv"]["carrier"] == "solar" and by["pv"]["value"] == 5.0
    # The size as entered, flagged; never a solved one.
    assert by["candidate"]["value"] == 2.0 and by["candidate"]["extendable"] is True
    # A missing size is null, never 0.
    assert by["broken"]["value"] is None
    loads = {h["entity"]: h["value"] for h in holds["loads"]}
    assert loads == {"fixed": 2.0, "shaped": 7.5}
    assert [(h["component"], h["entity"], h["field"]) for h in holds["profiles"]] == [
        ("Load", "shaped", "p_set"), ("Generator", "pv", "p_max_pu")]
    assert holds["carriers"] == [{"component": "Carrier", "entity": "solar", "carrier": None,
                                  "field": None, "value": None, "unit": None,
                                  "extendable": False}]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None, "", "n/a"])
def test_a_value_that_is_not_a_finite_number_is_null(bad):
    assert S._number(bad) is None
    assert S._number(0) == 0.0 and S._number("3.5") == 3.5


def test_a_multi_period_axis_and_an_empty_frame_list_names_only(client, tmp_projects_dir):
    flow = _flow(client, "eh_microgrid_staged", "staged")
    profiles = _section(flow, "profiles")["holds"]
    assert profiles and all(set(h) == {"component", "entity", "carrier", "field", "value",
                                       "unit", "extendable"} for h in profiles)
    n = _hand_built()
    n.loads_t.p_set = n.loads_t.p_set.iloc[0:0]                      # an empty frame
    assert {h["entity"]: h["value"] for h in S._holds(n, [], [])["loads"]} == {
        "fixed": 2.0, "shaped": None}


def test_the_assistants_copy_of_the_flow_leaves_the_lists_out(monkeypatch):
    """The lists grow with the network, so the tool result never carries them."""
    from services import chat_tools

    flow = {"run_ready": True, "sections": [
        {"section": "assets", "summary": "1 asset", "items": [], "holds": [{"entity": "pv"}]},
        {"section": "results", "summary": None, "items": []}]}
    monkeypatch.setattr(chat_tools, "_authorized_project", lambda name: name)
    monkeypatch.setattr(chat_tools, "_route", lambda handler, **kw: flow)
    assert chat_tools.get_project_flow("p") == {"run_ready": True, "sections": [
        {"section": "assets", "summary": "1 asset", "items": []},
        {"section": "results", "summary": None, "items": []}]}


def test_reading_the_flow_leaves_the_input_fingerprint_alone(client, tmp_projects_dir):
    from services import workflow_tools
    from services.pypsa_service import PyPSAService

    name = _create(client, "eh_microgrid", "fingerprint")
    before = workflow_tools._network_input_fingerprint(PyPSAService.get_active_context())
    assert client.get(f"/api/projects/{name}/flow").status_code == 200
    assert workflow_tools._network_input_fingerprint(PyPSAService.get_active_context()) == before

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

import threading

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
        # No shipped template reaches the cap: the list is whole.
        assert row["holds_total"] == len(row["holds"])
        names = [(h["component"], h["entity"]) for h in row["holds"]]
        assert len(names) == len(set(names))
    for h in _section(flow, "assets")["holds"]:
        assert h["field"] in ("p_nom", "e_nom")
        assert (
            h["unit"] in ({"p_nom": ("MW", "t/h"), "e_nom": ("MWh", "t")}[h["field"]])
        )
        assert h["value"] is None or math.isfinite(h["value"])
    for h in _section(flow, "profiles")["holds"]:
        # The attribute's name only: no series value rides along.
        assert (
            h["field"] in S._PROFILE_FIELDS and h["value"] is None and h["unit"] is None
        )


@pytest.mark.parametrize("tid", TEMPLATES)
def test_the_list_decides_nothing(client, tmp_projects_dir, monkeypatch, tid):
    """States, verdicts, blockers, notes and items are the same with no list."""
    name = _create(client, tid, "inert")
    url = f"/api/projects/{name}/flow"
    with_list = client.get(url).json()
    monkeypatch.setattr(S, "_holds", lambda *a, **k: ({}, {}))
    S._readiness_cache.clear()
    without = client.get(url).json()
    assert not any("holds" in s for s in without["sections"])
    for s in with_list["sections"]:
        s.pop("holds", None)
        s.pop("holds_total", None)
    assert with_list == without


def _hand_built() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(4))
    for bus in ("grid", "site", "h2"):
        n.add("Bus", bus)
    n.add("Generator", "market", bus="grid", p_nom=100.0)  # behind the fence
    n.add(
        "Generator",
        "pv",
        bus="site",
        p_nom=5.0,
        carrier="solar",
        p_max_pu=[0.0, 0.5, 1.0, 0.2],
    )
    n.add("Generator", "candidate", bus="site", p_nom=2.0, p_nom_extendable=True)
    n.add("Generator", "broken", bus="site")
    n.add("StorageUnit", "bess", bus="site", p_nom=3.0)
    n.add("Store", "tank", bus="h2", e_nom=40.0)
    n.add("Store", "grid_import", bus="h2", e_nom=1.0)  # a store, whatever its name
    n.add(
        "Link", "grid_import", bus0="grid", bus1="site", p_nom=10.0
    )  # the import link
    n.add("Link", "export_tie", bus0="site", bus1="grid", p_nom=4.0)  # in `connection`
    n.add("Link", "electrolyser", bus0="site", bus1="h2", p_nom=1.5)
    n.add("Load", "fixed", bus="site", p_set=2.0, carrier="AC")
    n.add("Load", "shaped", bus="site", p_set=[1.0, 7.5, 3.0, 2.0])
    n.generators.at["broken", "p_nom"] = np.nan
    return n


def test_the_list_and_the_count_share_one_set():
    n = _hand_built()
    assets = S._site_assets(n, ["grid_import"], {"grid_import", "export_tie"})
    assert assets == [
        ("Generator", "pv"),
        ("Generator", "candidate"),
        ("Generator", "broken"),
        ("StorageUnit", "bess"),
        ("Store", "tank"),
        ("Store", "grid_import"),
        ("Link", "electrolyser"),
    ]
    holds, totals = S._holds(n, ["solar"], assets)
    assert totals == {"carriers": 1, "loads": 2, "assets": len(assets), "profiles": 2}
    assert [(h["component"], h["entity"]) for h in holds["assets"]] == assets
    by = {h["entity"]: h for h in holds["assets"]}
    # No carrier is null, not an empty string.
    assert by["tank"] == {
        "component": "Store",
        "entity": "tank",
        "carrier": None,
        "field": "e_nom",
        "value": 40.0,
        "unit": "MWh",
        "extendable": False,
        "series": False,
    }
    assert by["pv"]["carrier"] == "solar" and by["pv"]["value"] == 5.0
    # The size as entered, flagged; never a solved one.
    assert by["candidate"]["value"] == 2.0 and by["candidate"]["extendable"] is True
    # A missing size is null, never 0.
    assert by["broken"]["value"] is None
    loads = {
        h["entity"]: (h["value"], h["carrier"], h["series"], h["unit"])
        for h in holds["loads"]
    }
    assert loads == {
        "fixed": (2.0, "AC", False, "MW"),
        "shaped": (7.5, None, True, "MW"),
    }
    assert [(h["component"], h["entity"], h["field"]) for h in holds["profiles"]] == [
        ("Load", "shaped", "p_set"),
        ("Generator", "pv", "p_max_pu"),
    ]
    assert holds["carriers"] == [
        {
            "component": "Carrier",
            "entity": "solar",
            "carrier": None,
            "field": None,
            "value": None,
            "unit": None,
            "extendable": False,
            "series": False,
        }
    ]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None, "", "n/a"])
def test_a_value_that_is_not_a_finite_number_is_null(bad):
    assert S._number(bad) is None
    assert S._number(0) == 0.0 and S._number("3.5") == 3.5


@pytest.mark.parametrize(
    "flag, expected",
    [(float("nan"), False), (None, False), (False, False), (True, True)],
)
def test_a_blank_extendable_flag_is_not_extendable(flag, expected):
    assert S._held("Generator", "g", extendable=flag)["extendable"] is expected


def test_a_multi_period_axis_and_an_empty_frame_list_names_only(
    client, tmp_projects_dir
):
    flow = _flow(client, "eh_microgrid_staged", "staged")
    profiles = _section(flow, "profiles")["holds"]
    assert profiles and all(
        set(h)
        == {
            "component",
            "entity",
            "carrier",
            "field",
            "value",
            "unit",
            "extendable",
            "series",
        }
        for h in profiles
    )
    n = _hand_built()
    n.loads_t.p_set = n.loads_t.p_set.iloc[0:0]  # an empty frame
    assert {h["entity"]: h["value"] for h in S._holds(n, [], [])[0]["loads"]} == {
        "fixed": 2.0,
        "shaped": None,
    }


def test_the_assistants_copy_of_the_flow_leaves_the_lists_out(monkeypatch):
    """The lists grow with the network, so the tool result never carries them."""
    from services import chat_tools

    flow = {
        "run_ready": True,
        "sections": [
            {
                "section": "assets",
                "summary": "1 asset",
                "items": [],
                "holds": [{"entity": "pv"}],
            },
            {"section": "results", "summary": None, "items": []},
        ],
    }
    monkeypatch.setattr(chat_tools, "_authorized_project", lambda name: name)
    monkeypatch.setattr(chat_tools, "_route", lambda handler, **kw: flow)
    assert chat_tools.get_project_flow("p") == {
        "run_ready": True,
        "sections": [
            {"section": "assets", "summary": "1 asset", "items": []},
            {"section": "results", "summary": None, "items": []},
        ],
    }


def _input_state(client, session_ctx, bound_ctx) -> tuple:
    """What a run and the investment case key on, read from the session's own
    context (the test thread's foreground context is another, empty one)."""
    from routers import results
    from services import dirty_state, workflow_tools

    ctx = session_ctx(client)
    n = ctx.network
    with bound_ctx(client):
        case = results._ic_assumptions(
            ctx.solver_state["solver_config"], n, lock=threading.RLock()
        )[0]
    sizes = [
        tuple(getattr(n, t)[c].astype(str))
        for _k, t, c, _s in S._ASSET_TABLES
        if len(getattr(n, t))
    ]
    return (
        workflow_tools._network_input_fingerprint(ctx),
        dirty_state.revision(ctx),
        case,
        sizes,
    )


def test_reading_the_flow_leaves_the_inputs_alone(
    client, tmp_projects_dir, session_ctx, bound_ctx, monkeypatch
):
    name = _create(client, "eh_microgrid", "fingerprint")
    assert len(
        session_ctx(client).network.generators
    )  # the real network, not an empty one
    before = _input_state(client, session_ctx, bound_ctx)
    assert client.get(f"/api/projects/{name}/flow").status_code == 200
    assert _input_state(client, session_ctx, bound_ctx) == before

    # The check sees a list that writes: the same read is then not inert.
    real = S._holds

    def writing(n, *a, **k):
        n.generators["p_nom"] = n.generators["p_nom"] + 1.0
        return real(n, *a, **k)

    monkeypatch.setattr(S, "_holds", writing)
    S._readiness_cache.clear()
    assert client.get(f"/api/projects/{name}/flow").status_code == 200
    assert _input_state(client, session_ctx, bound_ctx) != before


def test_a_size_on_a_tonne_bus_is_in_tonnes(client, tmp_projects_dir, session_ctx):
    flow = _flow(client, "eh_concentrator_staged", "tonnes")
    n = session_ctx(client).network
    tonne = {str(b) for b in n.buses.index[n.buses["unit"] == "t"]}
    assert tonne
    seen = 0
    for h in _section(flow, "assets")["holds"]:
        table = {k: t for k, t, _f, _s in S._ASSET_TABLES}[h["component"]]
        bus = getattr(n, table).at[
            h["entity"], "bus0" if h["component"] == "Link" else "bus"
        ]
        if str(bus) in tonne:
            seen += 1
            assert h["unit"] == ("t" if h["component"] == "Store" else "t/h"), h
        else:
            assert h["unit"] in ("MW", "MWh"), h
    assert seen
    by = {h["entity"]: h["unit"] for h in _section(flow, "assets")["holds"]}
    assert by["mine"] == "t/h" and by["coarse_ore_stockpile"] == "t"
    loads = {h["entity"]: h["unit"] for h in _section(flow, "loads")["holds"]}
    assert loads["shipments"] == "t/h"


def test_a_bus_of_unknown_unit_gives_no_unit(monkeypatch):
    from services import process_products

    n = _hand_built()
    assert S._size_unit(n, "site", stock=False) == "MW"
    assert S._size_unit(n, "site", stock=True) == "MWh"
    monkeypatch.setattr(process_products, "bus_unit", lambda n, bus: "kg")
    assert S._size_unit(n, "site", stock=False) is None


def test_a_long_list_stops_at_the_cap_and_carries_the_true_total(monkeypatch):
    monkeypatch.setattr(S, "HOLDS_CAP", 3)
    n = _hand_built()
    n.loads_t.p_set["fixed"] = 1.0  # a second load series
    assets = S._site_assets(n, ["grid_import"], {"grid_import", "export_tie"})
    carriers = ["a", "b", "c", "d"]
    holds, totals = S._holds(n, carriers, assets)
    assert totals == {"carriers": 4, "loads": 2, "assets": 7, "profiles": 3}
    assert {k: len(v) for k, v in holds.items()} == {
        "carriers": 3,
        "loads": 2,
        "assets": 3,
        "profiles": 3,
    }
    # The first rows, in the order the uncapped list has them.
    assert [(h["component"], h["entity"]) for h in holds["assets"]] == assets[:3]
    # One frame wider than the cap, and more loads than the cap: the totals stay whole.
    monkeypatch.setattr(S, "HOLDS_CAP", 1)
    holds, totals = S._holds(n, carriers, assets)
    assert totals == {"carriers": 4, "loads": 2, "assets": 7, "profiles": 3}
    assert {k: len(v) for k, v in holds.items()} == {
        "carriers": 1,
        "loads": 1,
        "assets": 1,
        "profiles": 1,
    }
    assert [h["entity"] for h in holds["loads"]] == [str(n.loads.index[0])]
    monkeypatch.setattr(S, "HOLDS_CAP", 2)
    assert len(S._holds(n, carriers, assets)[0]["profiles"]) == 2
    assert S.HOLDS_CAP == 2 and S._holds(n, carriers, assets)[1]["profiles"] == 3


def test_the_route_keeps_the_true_total_beside_a_cut_list(
    client, tmp_projects_dir, monkeypatch
):
    name = _create(client, "eh_microgrid_staged", "cut")
    url = f"/api/projects/{name}/flow"
    whole = client.get(url).json()
    monkeypatch.setattr(S, "HOLDS_CAP", 2)
    S._readiness_cache.clear()
    cut = client.get(url).json()
    for sid in HOLDING:
        full, row = _section(whole, sid), _section(cut, sid)
        assert row["holds_total"] == full["holds_total"] == len(full["holds"])
        assert row["holds"] == full["holds"][:2]
    assets = _section(cut, "assets")
    assert assets["holds_total"] > 2 and len(assets["holds"]) == 2
    # The heading counts every asset, not the rows sent.
    assert assets["summary"] == S._count(assets["holds_total"], "asset")
    loads = _section(cut, "loads")
    if loads["state"] != "not_needed":
        assert loads["summary"] == S._count(loads["holds_total"], "load")


def test_the_cap_as_shipped():
    assert S.HOLDS_CAP == 200


def test_a_peak_is_the_value_furthest_from_zero():
    assert S._peak([-5.0, -1.0, -2.0]) == -5.0
    assert S._peak([1.0, 7.5, float("nan")]) == 7.5
    assert S._peak([float("nan"), float("inf")]) is None and S._peak([]) is None
    # A tie goes to the positive value, whichever comes first.
    assert S._peak([-5.0, 5.0]) == 5.0 and S._peak([5.0, -5.0]) == 5.0


def test_a_load_shows_the_peak_of_its_series():
    n = _hand_built()
    n.loads_t.p_set["shaped"] = [-5.0, -1.0, -2.0, -3.0]
    loads = {h["entity"]: h["value"] for h in S._holds(n, [], [])[0]["loads"]}
    assert loads["shaped"] == -5.0


def test_an_unreadable_bus_gives_no_unit(monkeypatch):
    from services import process_products

    def broken(n, bus):
        raise KeyError(bus)

    monkeypatch.setattr(process_products, "bus_unit", broken)
    n = _hand_built()
    assert S._size_unit(n, "site", stock=False) is None
    assert S._size_unit(n, "site", stock=True) is None
    held = S._holds(n, [], [("Generator", "pv")])[0]["assets"]
    assert {h["unit"] for h in held} == {None}


def test_a_float_extendable_column_reads_as_the_engine_reads_it():
    n = _hand_built()
    n.generators["p_nom_extendable"] = n.generators["p_nom_extendable"].astype(float)
    assert n.generators["p_nom_extendable"].dtype == float
    assets = S._site_assets(n, ["grid_import"], {"grid_import", "export_tie"})
    by = {h["entity"]: h["extendable"] for h in S._holds(n, [], assets)[0]["assets"]}
    assert by["candidate"] is True and by["pv"] is False


def test_an_unset_fixed_load_is_told_apart_from_an_empty_series():
    n = _hand_built()
    n.loads.at["fixed", "p_set"] = np.nan
    n.loads_t.p_set["shaped"] = np.nan
    loads = {
        h["entity"]: (h["value"], h["series"]) for h in S._holds(n, [], [])[0]["loads"]
    }
    assert loads == {"fixed": (None, False), "shaped": (None, True)}


@pytest.mark.parametrize(
    "flag, expected",
    [
        ("True", True),
        ("true", True),
        ("1", True),
        ("yes", True),
        (np.True_, True),
        ("1.0", True),
        (1.0, True),
        (np.float64(1.0), True),
        (0.0, False),
        (np.float64(0.0), False),
        ("False", False),
        ("", False),
        ("0", False),
        (np.False_, False),
    ],
)
def test_an_extendable_flag_stored_as_text_counts(flag, expected):
    assert S._held("Generator", "g", extendable=flag)["extendable"] is expected


@pytest.mark.parametrize("blank", ["nan", "", None, float("nan")])
def test_a_blank_carrier_is_null(blank):
    assert S._held("Generator", "g", carrier=blank)["carrier"] is None
    assert S._held("Generator", "g", carrier="gas")["carrier"] == "gas"

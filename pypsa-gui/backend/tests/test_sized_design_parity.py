"""
Issue template-e2e 20: the size of what the study chose to build, the same in
Guided, Expert and the report, for every template (GK A 33 S1-S7).

* The report's sized-assets table lists every asset the study sized, one row
  each, with its period on a staged template.
* Installed capacity is the site's own generation. The grid supply behind the
  connection (a 100-1,000 MW placeholder price source) and a product's buy-in
  or shortfall are never in it. The connection's limit is
  ``flow_service.import_capacity``'s, and the peak import is checked against
  it per period.
* The report's Built cell for each asset is the string Guided and Expert show
  (``SizedDesignTable.sizedBuilt``), checked against the fixture the frontend
  test reads (``sized_design_parity.json``).
* A report stored before this change renders, and hashes, as it did.
"""

from __future__ import annotations

import copy
import json
import math
import pathlib
import sys

import pandas as pd
import pypsa
import pytest

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND / "project_templates"))

import eh_site as S  # noqa: E402
import eh_templates as T  # noqa: E402

from services.adequacy import eh_report as R  # noqa: E402
from services.reports import evidence as E  # noqa: E402

FIXTURE = (
    _BACKEND.parent
    / "frontend"
    / "src"
    / "pages"
    / "results"
    / "__fixtures__"
    / "sized_design_parity.json"
)
TEMPLATES = [*T.BUILDERS, "eh_site"]
_TABLES = (("generators", "p"), ("storage_units", "p"), ("links", "p"), ("stores", "e"))
GRID_ROW = "Grid connection (import limit, not counted)"
# The literal, so a widened constant fails the boundary tests (GK A 33).
TOL = 1e-6


def _build(tid: str):
    return S.build_eh_site() if tid == "eh_site" else T.BUILDERS[tid]()


def _fake_solve(n):
    """A solve's sizes without a solver: an extendable asset is built at half
    its cap (3.25 where it has none), a fixed one at its size; the network
    reads as solved."""
    for table, stem in _TABLES:
        df = getattr(n, table)
        if df.empty:
            continue
        ext = df[f"{stem}_nom_extendable"].fillna(False).astype(bool)
        cap = df[f"{stem}_nom_max"].where(lambda s: s.apply(math.isfinite), 6.5)
        df[f"{stem}_nom_opt"] = (cap / 2).where(ext, df[f"{stem}_nom"])
    n._objective = 1.0
    return n


def _grid_import(n):
    role = n.links.get("eh_role")
    return n.links[role == "grid_import"] if role is not None else n.links.iloc[:0]


def _payload(tid: str) -> tuple[pypsa.Network, dict]:
    n = _fake_solve(_build(tid))
    return n, R.sizing_summary_from_network(n)


def _with_import(n, flow: pd.Series):
    n.links_t.p0 = pd.DataFrame({"grid_import": flow})
    return R.sizing_summary_from_network(n)


# ── every template ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("tid", TEMPLATES)
def test_report_lists_every_sized_asset_per_period(tid):
    n, payload = _payload(tid)
    extendable = {
        f"{c}:{name}"
        for c, table, stem in R._SIZED
        for name in getattr(n, table).index[
            getattr(n, table)[f"{stem}_nom_extendable"].fillna(False).astype(bool)
        ]
        if not (
            c == "Generator"
            and R._is_slack(name, str(n.generators.at[name, "carrier"]))
        )
    }
    assert {f"{a['component']}:{a['name']}" for a in payload["assets"]} == extendable
    table = E._t_sized_assets(payload)
    staged = len(n.investment_periods) > 0
    assert (table.columns[0] == "Period") is staged
    assert len(table.rows) == len(payload["assets"])
    if staged:
        for a in payload["assets"]:
            assert a["stage"] == int(
                n.components[a["component"]].static.at[a["name"], "build_year"]
            )
        assert [int(r[0]) for r in table.rows] == sorted(int(r[0]) for r in table.rows)
    else:
        assert all("stage" not in a for a in payload["assets"])


@pytest.mark.parametrize("tid", TEMPLATES)
def test_grid_supply_and_product_slacks_are_not_installed_capacity(tid):
    n, payload = _payload(tid)
    gi = _grid_import(n)
    assert not gi.empty, "every template has a grid connection"
    supply = set(n.generators.index[n.generators.bus.isin(gi.bus0)])
    roles = n.generators.get("process_role")
    slacks = (
        set(roles.index[roles.isin(("buy_in", "shortfall"))])
        if roles is not None
        else set()
    )
    counted = {
        name
        for name in n.generators.index
        if name not in supply | slacks
        and not R._is_slack(name, str(n.generators.at[name, "carrier"]))
    }
    assert payload["total_p_nom_mw"] == pytest.approx(
        float(n.generators.loc[sorted(counted), "p_nom_opt"].sum())
    )
    assert payload["total_p_nom_mw"] == pytest.approx(
        sum(payload["by_carrier"].values())
    )
    assert payload["grid_connection_mw"] == pytest.approx(float(gi.p_nom.sum()))
    assert payload["grid_supply_mw"] == pytest.approx(
        float(n.generators.loc[sorted(supply), "p_nom_opt"].sum())
    )
    assert payload["grid_supply_counted"] == []
    assert {s["name"] for s in payload["product_slacks"]} == slacks
    rows = dict(E._t_sizing(payload).rows)
    assert rows[GRID_ROW] == f"{payload['grid_connection_mw']:,.1f} MW"
    assert "Not counted: the grid supply" in E._t_sizing(payload).caption


# ── the connection (S1, S2, S5) ─────────────────────────────────────────────


def _site(*, extendable=False, with_link=True, grid_load=False, poc=True, export=None):
    """A template-free site: a grid bus with a 500 MW supply, a 40 MW import
    Link into the site bus, a site load and a site generator; ``export`` adds
    a site-to-grid Link with that ``p_min_pu``."""
    n = pypsa.Network()
    n.set_snapshots(range(3))
    n.add("Bus", "grid", carrier="AC")
    n.add("Bus", "site", carrier="AC")
    if poc:
        n.buses["eh_poc"] = [True, False]
    n.add("Generator", "grid_supply", bus="grid", carrier="grid", p_nom=500.0)
    n.add("Generator", "pv", bus="site", carrier="solar", p_nom=4.0)
    n.add("Load", "it", bus="site", p_set=30.0)
    if with_link:
        n.add(
            "Link",
            "grid_import",
            bus0="grid",
            bus1="site",
            p_nom=40.0,
            p_nom_extendable=extendable,
        )
        n.links["eh_role"] = ["grid_import"]
    if export is not None:
        n.add(
            "Link", "grid_export", bus0="site", bus1="grid", p_nom=70.0, p_min_pu=export
        )
    if grid_load:
        n.add("Load", "aux", bus="grid", p_set=1.0)
    return n


def test_connection_limit_is_import_capacitys_and_supply_leaves_the_total():
    p = R.sizing_summary_from_network(_site())
    assert p["grid_connection_mw"] == 40.0 and p["grid_connection_refusal"] is None
    assert p["by_carrier"] == {"solar": 4.0} and p["grid_supply_mw"] == 500.0


def test_an_extendable_import_link_is_no_limit_never_a_number_or_within():
    n = _site(extendable=True)
    n.links.loc["grid_import", "p_nom_opt"] = 0.0
    n._objective = 1.0
    n.links_t.p0 = pd.DataFrame({"grid_import": [0.0, 0.0, 0.0]}, index=n.snapshots)
    p = R.sizing_summary_from_network(n)
    assert p["grid_connection_mw"] is None and p["connection_check"] is None
    assert "sized by the optimisation" in p["grid_connection_refusal"]
    assert dict(E._t_sizing(p).rows)[GRID_ROW].startswith(
        "Not established: The grid import link"
    )


def test_a_grid_generator_beside_site_equipment_is_counted_with_the_reason():
    p = R.sizing_summary_from_network(_site(grid_load=True))
    assert p["by_carrier"] == {"grid": 500.0, "solar": 4.0}
    assert p["grid_supply_counted"] == [
        "grid_supply is counted: bus grid also holds load aux"
    ]
    assert ["Grid supply counted", p["grid_supply_counted"][0]] in E._t_sizing(p).rows


def test_an_export_only_link_leaves_the_grid_bus_the_grids():
    """The IPP's export Link delivers into the grid bus and never draws from
    it: the supply there is still the grid's."""
    assert R.sizing_summary_from_network(_site(export=0.0))["by_carrier"] == {
        "solar": 4.0
    }


def test_a_two_way_link_on_the_grid_bus_is_site_side():
    p = R.sizing_summary_from_network(_site(export=-1.0))
    assert p["by_carrier"] == {"grid": 500.0, "solar": 4.0}
    assert p["grid_supply_counted"] == [
        "grid_supply is counted: bus grid also holds link grid_export"
    ]


def test_a_link_with_the_grid_bus_on_another_port_is_site_side():
    """A multi-port Link with the grid bus on bus2 can draw from it."""
    n = _site(export=0.0)
    n.add("Bus", "heat")
    n.links["bus2"] = ""
    n.links.loc["grid_export", ["bus1", "bus2"]] = ["heat", "grid"]
    n.links["efficiency2"] = 0.0
    n.links.loc["grid_export", "efficiency2"] = -0.1
    p = R.sizing_summary_from_network(n)
    assert p["grid_supply_counted"] == [
        "grid_supply is counted: bus grid also holds link grid_export"
    ]


def test_an_export_link_with_one_negative_p_min_pu_step_is_site_side():
    n = _site(export=0.0)
    n.links_t.p_min_pu = pd.DataFrame(
        {"grid_export": [0.0, -0.2, 0.0]}, index=n.snapshots
    )
    assert R.sizing_summary_from_network(n)["by_carrier"] == {
        "grid": 500.0,
        "solar": 4.0,
    }


def test_an_export_link_with_no_efficiency_is_site_side():
    n = _site(export=0.0)
    n.links.loc["grid_export", "efficiency"] = 0.0
    assert R.sizing_summary_from_network(n)["by_carrier"] == {
        "grid": 500.0,
        "solar": 4.0,
    }


def test_without_a_request_a_carrier_pick_is_refused():
    """Neither eh_role nor eh_poc marks the Link: only the carrier rule (3)
    would pick it, and with no request its pack is a placeholder (GK A 33)."""
    n = _site(poc=False)
    n.links["eh_role"] = [""]
    n.links.loc["grid_import", "carrier"] = "AC"
    p = R.sizing_summary_from_network(n)
    assert p["grid_connection_mw"] is None and p["connection_check"] is None
    assert p["grid_connection_refusal"] == R.NO_REQUEST_REFUSAL
    # The study's own request picks it as the study does.
    assert (
        R.sizing_summary_from_network(n, {"archetype": "weak_flexible"})[
            "grid_connection_mw"
        ]
        == 40.0
    )


def test_capacity_mix_figure_loses_the_grid_bar():
    """An intended change: the figure plots the site's own generation only."""
    from services.reports.figures import _capacity_rows

    _n, p = _payload("eh_datacenter")
    assert [name for name, _mw in _capacity_rows(p["by_carrier"])] == ["gas", "solar"]


def test_a_grid_generator_at_the_poc_with_no_link_is_unbounded():
    p = R.sizing_summary_from_network(_site(with_link=False))
    assert p["by_carrier"] == {"solar": 4.0}
    assert p["grid_connection_mw"] is None and p["connection_check"] == "unbounded"
    assert dict(E._t_sizing(p).rows)[GRID_ROW].startswith("unbounded connection")


def test_a_link_on_the_poc_by_another_port_is_no_unbounded_poc():
    """A Link touching the PoC on bus2 is a Link there: not "unbounded"."""
    n = _site(with_link=False)
    n.add("Bus", "heat")
    n.add("Link", "chp", bus0="site", bus1="heat", p_nom=5.0)
    n.links["bus2"] = ["grid"]
    p = R.sizing_summary_from_network(n)
    assert p["connection_check"] != "unbounded"
    assert p["grid_supply_counted"] == [
        "grid_supply is counted: bus grid is marked as the point of connection, "
        "but no grid import link leaves it"
    ]


def test_a_generator_on_an_unmarked_bus_with_no_link_is_installed():
    p = R.sizing_summary_from_network(_site(with_link=False, poc=False))
    assert p["by_carrier"] == {"grid": 500.0, "solar": 4.0}
    assert p["connection_check"] is None


@pytest.mark.parametrize(
    "over,verdict",
    [(0.0, "within"), (TOL, "within"), (2 * TOL, "exceeds"), (1.0, "exceeds")],
)
def test_peak_import_against_the_limit_at_the_tolerance(over, verdict):
    n = _site()
    n._objective = 1.0
    p = _with_import(n, pd.Series([10.0, 40.0 + over, -5.0], index=n.snapshots))
    assert p["grid_import_peak_mw"] == pytest.approx(40.0 + over, abs=1e-12)
    assert p["connection_check"] == verdict
    row = dict(E._t_sizing(p).rows)[GRID_ROW]
    assert ("exceeds the limit" in row) is (verdict == "exceeds")
    assert ("within the limit" in row) is (verdict == "within")


def test_export_is_not_import():
    n = _site()
    n._objective = 1.0
    p = _with_import(n, pd.Series([-90.0, 12.0, -60.0], index=n.snapshots))
    assert p["grid_import_peak_mw"] == 12.0 and p["connection_check"] == "within"


def test_multi_period_peak_is_checked_per_period():
    n = _fake_solve(_build("eh_datacenter_staged"))
    flow = pd.Series(10.0, index=n.snapshots)
    flow.loc[2030] = 55.0 + 1.0  # over the 55 MW limit in 2030 only
    p = _with_import(n, flow)
    assert p["grid_import_peak_by_period"] == {"2027": 10.0, "2030": 56.0, "2035": 10.0}
    assert p["connection_check"] == "exceeds"
    assert dict(E._t_sizing(p).rows)[GRID_ROW].endswith(
        "peak import 56.0 MW · exceeds the limit by 1.0 MW · in 2030"
    )


def test_unsolved_network_has_no_peak_or_check():
    p = R.sizing_summary_from_network(_build("eh_datacenter"))
    assert p["grid_import_peak_mw"] is None and p["connection_check"] is None


# ── product slacks (S3) ─────────────────────────────────────────────────────


def test_a_shortfall_that_delivered_is_its_own_row_never_in_the_total():
    n = _fake_solve(_build("eh_ammonia_staged"))
    p_set = pd.DataFrame(0.0, index=n.snapshots, columns=["__lost_nh3"])
    p_set.iloc[0, 0] = 5.0
    n.generators_t.p = p_set
    p = R.sizing_summary_from_network(n)
    assert (
        "NH3 shortfall" not in p["by_carrier"] and "NH3 import" not in p["by_carrier"]
    )
    lost = next(s for s in p["product_slacks"] if s["name"] == "__lost_nh3")
    assert lost["energy"] == pytest.approx(
        5.0 * float(n.snapshot_weightings.generators.iloc[0])
    )
    rows = dict(E._t_sizing(p).rows)
    assert rows["Shortfall, NH3 shortfall (not counted)"].startswith("32.3 MW_NH3 · ")
    assert not any(k.startswith("Bought in") for k in rows)  # bought nothing: no row


# ── unit and stage (S6) ─────────────────────────────────────────────────────


def test_unit_follows_the_bus_and_stage_only_on_a_staged_network():
    _n, staged = _payload("eh_concentrator_staged")
    by = {a["name"]: a for a in staged["assets"]}
    assert by["coarse_ore_stockpile_new_2027"]["unit"] == "t"
    assert by["pv_new_2027"]["unit"] == "MW" and by["pv_new_2027"]["stage"] == 2027
    _n, single = _payload("eh_datacenter")
    assert all(a["unit"] == "MW" and "stage" not in a for a in single["assets"])


# ── stored format (S4) ──────────────────────────────────────────────────────

#: A sizing payload as stored before this change (it counted the grid).
OLD = {
    "sections": {
        "sizing": {
            "status": "ok",
            "note": None,
            "payload": {
                "by_carrier": {"gas": 40.0, "grid": 500.0, "solar": 4.0},
                "total_p_nom_mw": 544.0,
                "assets": [
                    {
                        "component": "Generator",
                        "name": "genset_new",
                        "carrier": "gas",
                        "built_mw": 36.67,
                        "min_mw": 0.0,
                        "max_mw": 40.0,
                    }
                ],
                "assets_solved": True,
            },
        }
    }
}


def test_an_old_stored_report_hashes_and_renders_as_on_master():
    """sha256 of the evidence master ae6ea750a collects for OLD: no sized-assets
    table, no connection row, the old caption, so a stored report stays fresh.
    guided-results 04 renamed two of its rows on purpose ("MC LOLE (h/yr)" → "Expected
    shortfall (h/yr)", "LCOE" → "Cost per MWh served (study)"); the hash moved with them
    and nothing else did, so such a report asks to be redrafted once."""
    ev = E.collect_evidence(study_report=None, eh_report=OLD, worksheet=None)
    assert (
        E.evidence_hash(ev)
        == "bd86cc3cd9554d65e48cb5133a2032da18d98ff0dfc2a2abe449c2dbccb8a2d5"
    )
    assert "sized_assets" not in ev.tables
    assert (
        ev.tables["sizing"].caption
        == "Installed capacity of the least-cost plan by carrier"
    )


#: A sizing payload as stored since #1786 (it carries ``grid_connection_mw``).
POST_1786 = {
    "sections": {
        "sizing": {
            "status": "ok",
            "note": None,
            "payload": {
                "by_carrier": {"gas": 40.0, "solar": 4.0},
                "total_p_nom_mw": 44.0,
                "grid_connection_mw": 40.0,
                "grid_connection_refusal": None,
                "grid_supply_mw": 500.0,
                "grid_supply_counted": [],
                "grid_import_peak_mw": 41.0,
                "grid_import_peak_by_period": None,
                "connection_check": "exceeds",
                "product_slacks": [
                    {
                        "name": "__lost_nh3",
                        "process_role": "shortfall",
                        "carrier": "NH3 shortfall",
                        "p_nom": 32.3,
                        "energy": 52.1,
                        "unit": "MW_NH3",
                        "energy_unit": "MWh_NH3",
                    }
                ],
                "assets": [
                    {
                        "component": "Generator",
                        "name": "genset_new",
                        "carrier": "gas",
                        "built_mw": 36.67,
                        "min_mw": 0.0,
                        "max_mw": 40.0,
                        "unit": "MW",
                    }
                ],
                "assets_solved": True,
            },
        }
    }
}


def test_a_post_1786_report_moves_its_hash_once_with_the_reworded_rows():
    """GK A 40 P5: the reworded rows (UX 34) move the hash of a report stored
    since #1786, as guided-results 04's ruled renames did, so the report reads
    "study_changed" and asks to be redrafted once (routers/reports' stale
    path); collecting its evidence raises nothing. master d195f4f6 hashed it
    e75ecc0f... ; the pre-key OLD pin above is unchanged."""
    ev = E.collect_evidence(
        study_report=None, eh_report=copy.deepcopy(POST_1786), worksheet=None
    )
    now = E.evidence_hash(ev)
    assert now != "e75ecc0f236eaaf5f859820149689ba9207d4d3542d24fd443b009f98f99d309"
    assert now == "5ee532249b25f87678098a29ef08b11b3104a854cd2b25aed2eb78a19a4913df"


def _cell(**kw) -> str:
    p = {"grid_connection_mw": 40.0, "connection_check": "exceeds", **kw}
    return E._connection_cell(p)


@pytest.mark.parametrize(
    "excess,words",
    [
        (1.1 * TOL, "by less than 0.1 MW"),
        (0.049, "by less than 0.1 MW"),
        (0.05, "by 0.1 MW"),
        (2.8, "by 2.8 MW"),
    ],
)
def test_the_excess_rounds_in_words_only(excess, words):
    """GK A 40 P3: the verdict is the payload's; "less than 0.1 MW" exactly for
    an excess below 0.05, the 1e-6 tolerance still reads as an excess."""
    cell = _cell(grid_import_peak_mw=40.0 + excess)
    assert (
        cell
        == f"40.0 MW · peak import {F_fmt(40.0 + excess)} · exceeds the limit {words}"
    )


def F_fmt(v: float) -> str:
    return f"{v:,.1f} MW"


def test_every_period_over_the_limit_is_named():
    by = {
        "2027": 40.0 + 0.9 * TOL,
        "2030": 41.0,
        "2035": 40.0 + 1.1 * TOL,
        "2040": 45.0,
    }
    assert _cell(grid_import_peak_mw=45.0, grid_import_peak_by_period=by).endswith(
        "exceeds the limit by 5.0 MW · in 2030, 2035 and 2040"
    )
    by2 = {"2030": 41.0, "2035": 39.0}
    assert _cell(grid_import_peak_mw=41.0, grid_import_peak_by_period=by2).endswith(
        "· in 2030"
    )
    by3 = {"2030": 41.0, "2035": 42.0}
    assert _cell(grid_import_peak_mw=42.0, grid_import_peak_by_period=by3).endswith(
        "· in 2030 and 2035"
    )


def test_slack_units_are_the_payloads_and_the_horizon_is_named():
    """GK A 40 P3, UX 34: the units come from the payload (a tonne bus reads
    t/h · t), and the energy names its horizon."""
    slack = {
        "name": "__lost_ore",
        "process_role": "shortfall",
        "carrier": "concentrate",
        "p_nom": 12.0,
        "energy": 3.5,
        "unit": "t/h",
        "energy_unit": "t",
    }
    rows = E._slack_rows({"product_slacks": [slack], "horizon": "2027–2035"})
    assert rows == [
        [
            "Shortfall, concentrate (not counted)",
            "12.0 t/h · 3.5 t over the horizon (2027–2035)",
        ]
    ]
    assert (
        E._slack_rows({"product_slacks": [slack]})[0][1]
        == "12.0 t/h · 3.5 t over the horizon"
    )


#: Each template's horizon in words, written out (GK A 41 H1): a staged plan
#: names its first and last period, a single-period one its weighted hours.
HORIZONS = {
    "eh_datacenter": "8,760 h",
    "eh_datacenter_staged": "2027–2035",
    "eh_h2_hub": "8,760 h",
    "eh_microgrid": "8,760 h",
    "eh_h2_hub_staged": "2027–2035",
    "eh_microgrid_staged": "2027–2035",
    "eh_ammonia_staged": "2027–2035",
    "eh_concentrator_staged": "2027–2035",
    "eh_ipp_hybrid": "8,760 h",
    "eh_site": "2027–2035",
}


def test_every_template_has_a_written_horizon():
    assert set(HORIZONS) == set(TEMPLATES)


@pytest.mark.parametrize("tid", TEMPLATES)
def test_the_horizon_is_the_networks(tid):
    assert R.sizing_summary_from_network(_build(tid))["horizon"] == HORIZONS[tid]


def test_the_no_request_refusal_names_no_internal_key_and_says_what_to_do():
    """GK A 40 P4: plain words, a step the GUI offers (the link's role)."""
    assert "eh_" not in R.NO_REQUEST_REFUSAL
    assert R.NO_REQUEST_REFUSAL.endswith("set the grid link's role to Grid import.")
    assert E._connection_cell(
        {"grid_connection_mw": None, "grid_connection_refusal": R.NO_REQUEST_REFUSAL}
    ) == (f"Not established: {R.NO_REQUEST_REFUSAL}")


# ── the UI's figures (fixture) ──────────────────────────────────────────────


#: Rows no template has (a zero, a missing size, a bare minimum, a tonne
#: store...): the fixture's ``_edge`` case, which the frontend test renders too.
EDGE_ASSETS = [
    {
        "component": "Generator",
        "name": "off",
        "carrier": "solar",
        "built_mw": 0.004,
        "min_mw": 0.0,
        "max_mw": None,
        "unit": "MW",
    },
    {
        "component": "Generator",
        "name": "unsolved",
        "carrier": None,
        "built_mw": None,
        "min_mw": 2.0,
        "max_mw": None,
        "unit": "MW",
    },
    {
        "component": "Link",
        "name": "fixed_floor",
        "carrier": "AC",
        "built_mw": 0.005,
        "min_mw": 1.5,
        "max_mw": 9.999,
        "unit": "MW",
    },
    {
        "component": "StorageUnit",
        "name": "bess_bare",
        "carrier": "battery",
        "built_mw": 1234.567,
        "min_mw": None,
        "max_mw": 2000.0,
        "unit": "MW",
    },
    {
        "component": "StorageUnit",
        "name": "tank_th",
        "carrier": "nh3",
        "built_mw": 3.0,
        "min_mw": 0.0,
        "max_mw": 5.0,
        "unit": "t/h",
        "max_hours": None,
        "built_mwh": 72.0,
    },
    {
        "component": "Store",
        "name": "silo",
        "carrier": "ore",
        "built_mwh": 0.0,
        "min_mwh": 0.0,
        "max_mwh": 400.0,
        "min_mw": 999.0,
        "max_mw": 1.0,
        "unit": "t",
    },
    {
        "component": "Store",
        "name": "silo_unsolved",
        "carrier": "ore",
        "built_mwh": None,
        "built_mw": 5.0,
        "min_mwh": 10.0,
        "max_mwh": None,
        "unit": "t",
    },
    {
        "component": "Store",
        "name": "h2_cav",
        "carrier": "H2",
        "built_mwh": -0.0001,
        "built_mw": 3.0,
        "unit": "MWh",
    },
]


def _cells(assets: list[dict]) -> tuple[dict, dict]:
    rows = E._t_sized_assets({"assets": assets}).rows
    return {r[-4]: r[-2] for r in rows}, {r[-4]: r[-1] for r in rows}


def test_fixture_covers_every_template():
    assert set(json.loads(FIXTURE.read_text())) == set(TEMPLATES) | {"_edge"}


def test_edge_rows_render_as_the_ui_fixture_pins_them():
    case = json.loads(FIXTURE.read_text())["_edge"]
    assert case["assets"] == EDGE_ASSETS
    assert _cells(EDGE_ASSETS) == (case["built"], case["range"])
    assert case["built"] == {
        "off": "none built",
        "unsolved": "not reported",
        "fixed_floor": "0.01 MW",
        "bess_bare": "1,234.57 MW",
        "tank_th": "3 t/h · 72 t",
        "silo": "none built",
        "silo_unsolved": "not reported",
        "h2_cav": "none built",
    }
    assert case["range"] == {
        "off": "no limit",
        "unsolved": "at least 2 MW",
        "fixed_floor": "1.5–10 MW",
        "bess_bare": "up to 2,000 MW",
        "tank_th": "0–5 t/h",
        "silo": "0–400 t",
        "silo_unsolved": "at least 10 t",
        "h2_cav": "no limit",
    }


@pytest.mark.parametrize("tid", TEMPLATES)
def test_report_built_cell_matches_the_ui_fixture(tid):
    """The report's Built cell is the string the frontend test pins for
    Guided and Expert (``SizedDesignTable.parity.test.tsx``)."""
    case = json.loads(FIXTURE.read_text())[tid]
    assert _cells(case["assets"]) == (case["built"], case["range"])
    # The fixture is this template's own rows (rebuild it if a template changes).
    _n, payload = _payload(tid)
    assert payload["assets"] == case["assets"]


def _write_fixture() -> None:
    """Rebuild the fixture from the templates (``python -m
    tests.test_sized_design_parity`` from ``pypsa-gui/backend``)."""
    out = {}
    for tid in TEMPLATES:
        _n, payload = _payload(tid)
        built, rng = _cells(payload["assets"])
        out[tid] = {"assets": payload["assets"], "built": built, "range": rng}
    built, rng = _cells(EDGE_ASSETS)
    out["_edge"] = {"assets": EDGE_ASSETS, "built": built, "range": rng}
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    _write_fixture()


# ── guards the mutants named ────────────────────────────────────────────────


def test_an_unsolved_network_with_stale_results_has_no_peak_or_energy():
    """Results left from an earlier solve never read as this one's."""
    n = _fake_solve(_build("eh_ammonia_staged"))
    n.links_t.p0 = pd.DataFrame({"grid_import": 500.0}, index=n.snapshots)
    n.generators_t.p = pd.DataFrame({"__lost_nh3": 5.0}, index=n.snapshots)
    n._objective = None
    p = R.sizing_summary_from_network(n)
    assert p["grid_import_peak_mw"] is None and p["connection_check"] is None
    assert all(s["energy"] is None for s in p["product_slacks"])


def test_a_size_that_is_not_a_number_reads_its_fixed_size_or_zero():
    n = _site()
    n.generators.loc["pv", "p_nom_opt"] = float("nan")
    n.add("Generator", "wind", bus="site", carrier="wind")
    n.generators.loc["wind", ["p_nom", "p_nom_opt"]] = [float("nan"), float("nan")]
    p = R.sizing_summary_from_network(n)
    assert p["by_carrier"] == {"solar": 4.0, "wind": 0.0}


def test_a_link_with_the_grid_bus_on_bus1_and_another_port_is_site_side():
    n = _site(export=0.0)
    n.links["bus2"] = ""
    n.links.loc["grid_export", "bus2"] = "grid"
    assert R.sizing_summary_from_network(n)["by_carrier"] == {
        "grid": 500.0,
        "solar": 4.0,
    }


# ── guards the second mutant run named ──────────────────────────────────────


def test_a_single_period_peak_has_no_per_period_split():
    n = _site()
    n._objective = 1.0
    p = _with_import(n, pd.Series([10.0, 30.0, 5.0], index=n.snapshots))
    assert p["grid_import_peak_mw"] == 30.0 and p["grid_import_peak_by_period"] is None


def test_slacks_on_the_grid_bus_are_neither_supply_nor_installed():
    n = _site()
    n.add("Generator", "__voll_grid", bus="grid", carrier="voll", p_nom=900.0)
    n.add("Generator", "__buy_power", bus="grid", carrier="grid", p_nom=700.0)
    n.add("Generator", "shed", bus="site", carrier="load_shedding", p_nom=60.0)
    n.generators["process_role"] = ""
    n.generators.loc["__buy_power", "process_role"] = "buy_in"
    p = R.sizing_summary_from_network(n)
    assert p["grid_supply_mw"] == 500.0 and p["by_carrier"] == {"solar": 4.0}


def test_a_line_named_like_the_import_link_is_site_side():
    n = _site()
    n.add("Line", "grid_import", bus0="grid", bus1="site", x=0.1, s_nom=10.0)
    assert R.sizing_summary_from_network(n)["by_carrier"] == {
        "grid": 500.0,
        "solar": 4.0,
    }


def test_no_import_link_keeps_import_capacitys_own_refusal():
    p = R.sizing_summary_from_network(_site(with_link=False, poc=False))
    assert p["grid_connection_refusal"] not in (None, R.NO_REQUEST_REFUSAL)


def test_a_poc_with_no_generator_and_no_link_is_not_unbounded():
    n = _site(with_link=False)
    n.remove("Generator", "grid_supply")
    assert R.sizing_summary_from_network(n)["connection_check"] != "unbounded"


def test_a_link_elsewhere_leaves_an_unlinked_poc_unbounded():
    n = _site(with_link=False)
    n.add("Bus", "heat")
    n.add("Link", "boiler", bus0="site", bus1="heat", p_nom=5.0)
    assert R.sizing_summary_from_network(n)["connection_check"] == "unbounded"


def test_a_zero_minimum_is_no_limit_and_an_asset_without_carrier_is_its_kind():
    a = {
        "name": "g",
        "component": "Generator",
        "carrier": None,
        "min_mw": 0.0,
        "max_mw": None,
        "p_nom_opt": 1.0,
    }
    assert E._sized_range(a) == "no limit"
    assert E._t_sized_assets({"assets": [a]}).rows[0][1] == "Generator"


def test_a_slack_that_delivered_nothing_has_no_row():
    _n, p = _payload("eh_datacenter")
    p["product_slacks"] = [
        {
            "name": "__lost_nh3",
            "process_role": "shortfall",
            "carrier": "NH3",
            "p_nom": 30.0,
            "energy": 1e-10,
            "unit": "t/h",
            "energy_unit": "t",
        }
    ]
    assert not any(r[0].startswith("Shortfall") for r in E._t_sizing(p).rows)


def test_a_horizon_with_no_import_peaks_at_zero():
    """Export only: the peak import is 0, never a negative "import" (GK A 33)."""
    n = _site()
    n._objective = 1.0
    p = _with_import(n, pd.Series([-90.0, -12.0, -60.0], index=n.snapshots))
    assert p["grid_import_peak_mw"] == 0.0 and p["connection_check"] == "within"
    assert "peak import 0.0 MW" in dict(E._t_sizing(p).rows)[GRID_ROW]


def test_a_period_with_no_import_peaks_at_zero():
    n = _fake_solve(_build("eh_datacenter_staged"))
    flow = pd.Series(10.0, index=n.snapshots)
    flow.loc[2030] = -20.0
    p = _with_import(n, flow)
    assert p["grid_import_peak_by_period"] == {"2027": 10.0, "2030": 0.0, "2035": 10.0}


# ── guards the PR A mutants named (GK A 40 P6) ──────────────────────────────


def test_a_peak_with_no_verdict_or_a_verdict_with_no_peak_shows_the_limit_alone():
    assert _cell(grid_import_peak_mw=30.0, connection_check=None) == "40.0 MW"
    assert _cell(grid_import_peak_mw=None, connection_check="within") == "40.0 MW"


def test_a_period_exactly_at_the_tolerance_is_not_named():
    by = {"2030": 40.0 + TOL, "2035": 41.0}
    assert _cell(grid_import_peak_mw=41.0, grid_import_peak_by_period=by).endswith(
        "· in 2035"
    )


def test_a_refusal_with_no_words_still_says_why():
    assert E._connection_cell({"grid_connection_mw": None}) == (
        "Not established: the network sets no connection limit."
    )


def test_one_period_and_no_snapshots_name_their_horizon():
    n = pypsa.Network()
    n.set_snapshots(pd.MultiIndex.from_product([[2030], range(3)]))
    n.investment_periods = [2030]
    assert R._horizon_words(n) == "2030"

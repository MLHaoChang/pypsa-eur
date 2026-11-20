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

FIXTURE = (_BACKEND.parent / "frontend" / "src" / "pages" / "results" / "__fixtures__"
           / "sized_design_parity.json")
TEMPLATES = [*T.BUILDERS, "eh_site"]
_TABLES = (("generators", "p"), ("storage_units", "p"), ("links", "p"), ("stores", "e"))
GRID_ROW = "Grid connection (import limit, not counted)"
TOL = R.CONNECTION_TOL_MW


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
    extendable = {f"{c}:{name}" for c, table, stem in R._SIZED
                  for name in getattr(n, table).index[
                      getattr(n, table)[f"{stem}_nom_extendable"].fillna(False).astype(bool)]
                  if not (c == "Generator" and R._is_slack(name, str(n.generators.at[name, "carrier"])))}
    assert {f"{a['component']}:{a['name']}" for a in payload["assets"]} == extendable
    table = E._t_sized_assets(payload)
    staged = len(n.investment_periods) > 0
    assert (table.columns[0] == "Period") is staged
    assert len(table.rows) == len(payload["assets"])
    if staged:
        for a in payload["assets"]:
            assert a["stage"] == int(n.components[a["component"]].static.at[a["name"], "build_year"])
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
    slacks = set(roles.index[roles.isin(("buy_in", "shortfall"))]) if roles is not None else set()
    counted = {name for name in n.generators.index if name not in supply | slacks
               and not R._is_slack(name, str(n.generators.at[name, "carrier"]))}
    assert payload["total_p_nom_mw"] == pytest.approx(
        float(n.generators.loc[sorted(counted), "p_nom_opt"].sum()))
    assert payload["total_p_nom_mw"] == pytest.approx(sum(payload["by_carrier"].values()))
    assert payload["grid_connection_mw"] == pytest.approx(float(gi.p_nom.sum()))
    assert payload["grid_supply_mw"] == pytest.approx(
        float(n.generators.loc[sorted(supply), "p_nom_opt"].sum()))
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
        n.add("Link", "grid_import", bus0="grid", bus1="site", p_nom=40.0,
              p_nom_extendable=extendable)
        n.links["eh_role"] = ["grid_import"]
    if export is not None:
        n.add("Link", "grid_export", bus0="site", bus1="grid", p_nom=70.0, p_min_pu=export)
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
    assert dict(E._t_sizing(p).rows)[GRID_ROW].startswith("not established: The grid import link")


def test_a_grid_generator_beside_site_equipment_is_counted_with_the_reason():
    p = R.sizing_summary_from_network(_site(grid_load=True))
    assert p["by_carrier"] == {"grid": 500.0, "solar": 4.0}
    assert p["grid_supply_counted"] == ["grid_supply is counted: bus grid also holds load aux"]
    assert ["Grid supply counted", p["grid_supply_counted"][0]] in E._t_sizing(p).rows


def test_an_export_only_link_leaves_the_grid_bus_the_grids():
    """The IPP's export Link delivers into the grid bus and never draws from
    it: the supply there is still the grid's."""
    assert R.sizing_summary_from_network(_site(export=0.0))["by_carrier"] == {"solar": 4.0}


def test_a_two_way_link_on_the_grid_bus_is_site_side():
    p = R.sizing_summary_from_network(_site(export=-1.0))
    assert p["by_carrier"] == {"grid": 500.0, "solar": 4.0}
    assert p["grid_supply_counted"] == ["grid_supply is counted: bus grid also holds link grid_export"]


def test_a_grid_generator_at_the_poc_with_no_link_is_unbounded():
    p = R.sizing_summary_from_network(_site(with_link=False))
    assert p["by_carrier"] == {"solar": 4.0}
    assert p["grid_connection_mw"] is None and p["connection_check"] == "unbounded"
    assert dict(E._t_sizing(p).rows)[GRID_ROW].startswith("unbounded connection")


def test_a_generator_on_an_unmarked_bus_with_no_link_is_installed():
    p = R.sizing_summary_from_network(_site(with_link=False, poc=False))
    assert p["by_carrier"] == {"grid": 500.0, "solar": 4.0}
    assert p["connection_check"] is None


@pytest.mark.parametrize("over,verdict", [(0.0, "within"), (TOL, "within"),
                                          (2 * TOL, "exceeds"), (1.0, "exceeds")])
def test_peak_import_against_the_limit_at_the_tolerance(over, verdict):
    n = _site()
    n._objective = 1.0
    p = _with_import(n, pd.Series([10.0, 40.0 + over, -5.0], index=n.snapshots))
    assert p["grid_import_peak_mw"] == pytest.approx(40.0 + over, abs=1e-12)
    assert p["connection_check"] == verdict
    row = dict(E._t_sizing(p).rows)[GRID_ROW]
    assert ("EXCEEDS the limit" in row) is (verdict == "exceeds")


def test_export_is_not_import():
    n = _site()
    n._objective = 1.0
    p = _with_import(n, pd.Series([-90.0, 12.0, -60.0], index=n.snapshots))
    assert p["grid_import_peak_mw"] == 12.0 and p["connection_check"] == "within"


def test_multi_period_peak_is_checked_per_period():
    n = _fake_solve(_build("eh_datacenter_staged"))
    flow = pd.Series(10.0, index=n.snapshots)
    flow.loc[2030] = 55.0 + 1.0           # over the 55 MW limit in 2030 only
    p = _with_import(n, flow)
    assert p["grid_import_peak_by_period"] == {"2027": 10.0, "2030": 56.0, "2035": 10.0}
    assert p["connection_check"] == "exceeds"
    assert dict(E._t_sizing(p).rows)[GRID_ROW].endswith("EXCEEDS the limit in 2030")


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
    assert "NH3 shortfall" not in p["by_carrier"] and "NH3 import" not in p["by_carrier"]
    lost = next(s for s in p["product_slacks"] if s["name"] == "__lost_nh3")
    assert lost["energy"] == pytest.approx(5.0 * float(n.snapshot_weightings.generators.iloc[0]))
    rows = dict(E._t_sizing(p).rows)
    assert rows["Shortfall, NH3 shortfall (not counted)"].startswith("32.3 MW; ")
    assert not any(k.startswith("Bought in") for k in rows)   # bought nothing: no row


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
OLD = {"sections": {"sizing": {"status": "ok", "note": None, "payload": {
    "by_carrier": {"gas": 40.0, "grid": 500.0, "solar": 4.0}, "total_p_nom_mw": 544.0,
    "assets": [{"component": "Generator", "name": "genset_new", "carrier": "gas",
                "built_mw": 36.67, "min_mw": 0.0, "max_mw": 40.0}],
    "assets_solved": True}}}}


def test_an_old_stored_report_hashes_and_renders_as_on_master():
    """sha256 of the evidence master ae6ea750a collects for OLD: no sized-assets
    table, no connection row, the old caption, so a stored report stays fresh."""
    ev = E.collect_evidence(study_report=None, eh_report=OLD, worksheet=None)
    assert E.evidence_hash(ev) == "cb5607f9d1c0bc438a55a3ff66a4d67a4f599e5ea6ab8d85d98ff3e2c5a949bf"
    assert "sized_assets" not in ev.tables
    assert ev.tables["sizing"].caption == "Installed capacity of the least-cost plan by carrier"


# ── the UI's figures (fixture) ──────────────────────────────────────────────


def test_fixture_covers_every_template():
    assert set(json.loads(FIXTURE.read_text())) == set(TEMPLATES)


@pytest.mark.parametrize("tid", TEMPLATES)
def test_report_built_cell_matches_the_ui_fixture(tid):
    """The report's Built cell is the string the frontend test pins for
    Guided and Expert (``SizedDesignTable.parity.test.tsx``)."""
    case = json.loads(FIXTURE.read_text())[tid]
    built = {r[-4]: r[-2] for r in E._t_sized_assets({"assets": case["assets"]}).rows}
    assert built == case["built"]
    # The fixture is this template's own rows (rebuild it if a template changes).
    _n, payload = _payload(tid)
    assert payload["assets"] == case["assets"]


def _write_fixture() -> None:
    """Rebuild the fixture from the templates (``python -m
    tests.test_sized_design_parity`` from ``pypsa-gui/backend``)."""
    out = {}
    for tid in TEMPLATES:
        _n, payload = _payload(tid)
        out[tid] = {"assets": payload["assets"],
                    "built": {r[-4]: r[-2] for r in E._t_sized_assets(payload).rows}}
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    _write_fixture()

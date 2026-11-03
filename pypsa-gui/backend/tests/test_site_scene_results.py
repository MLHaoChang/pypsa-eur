# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""
SC-03: native results on scene buildings and process groups (V22–V28, V57,
V87–V88), under the accepted D07 registry (`.scratch/site-scene-generation/
d07/plan.md`, owner decision D07-ACCEPT-1, GK A conditions 1–12 in
`issues/04-result-projection.md` ## Comments).

Every expected number below is a hand value derived from the fixture's literal
inputs (condition 12), never read back from the code under test. The HTTP tests
run a real solve through `/api/simulation/run`; the service tests solve with
`n.optimize` directly and pass the gate in, so parsing, signs and the group rule
are pinned on networks the app's project routes do not build (RangeIndex,
multi-period, reversed links).

Known hour: snapshot 3 (2030-01-01T03:00). NH3 offtake 4 MW → haber H2 in
4 / 0.8 = 5 MW, elec in 0.05 × 5 = 0.25 MW, N2 in 0.2 × 5 = 1 t/h; electrolyser
elec in 5 / 0.5 = 10 MW, heat out 0.1 × 10 = 1 MW; grid 3 + 10 + 0.25 = 13.25 MW.
"""
from __future__ import annotations

import copy
import math

import pandas as pd
import pypsa
import pytest

from services import site_service as ss
from services.site_scene import results as R
from services.site_scene.contract import SceneError
from tests.test_run_separability import _wait_idle
from tests.test_site_scene_associations import HALL, SHED

NAME = "d07"
SITE = {"id": "campus", "name": "Campus", "buses": ["elec"], "origin": {"lng": 11.58, "lat": 48.14},
        "boundary": [[11.579, 48.139], [11.581, 48.139], [11.581, 48.141]], "placements": {}}
OFFICE = {"id": "office", "center": [80, -40], "width": 10, "depth": 10, "height": 6, "heading": 0, "unit": "m"}
A, B, C = "greenfield:hall", "greenfield:shed", "greenfield:office"
T3 = "2030-01-01T03:00:00"
NH3_T_PER_MWH = 1 / 5.2


def oracle_network(freq="h", weight=1.0, *, store_weight=None, snapshots=None) -> pypsa.Network:
    """The D07 oracle fixture (`d07/evidence/oracle.py`), plus an idle tank and buffer that never move."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq=freq) if snapshots is None else snapshots)
    n.snapshot_weightings.loc[:, :] = weight
    if store_weight is not None:
        n.snapshot_weightings["stores"] = store_weight
    for c in ("AC", "heat", "H2", "NH3", "N2"):
        n.add("Carrier", c)
    n.add("Bus", "elec", carrier="AC")
    n.add("Bus", "heat", carrier="heat")
    n.add("Bus", "h2", carrier="H2")
    n.add("Bus", "nh3", carrier="NH3", unit="MWh")
    n.add("Bus", "n2", carrier="N2", unit="t")
    n.buses["mwh_per_t"] = float("nan")
    n.buses.loc["nh3", "mwh_per_t"] = 5.2
    n.add("Generator", "grid", bus="elec", p_nom=100, marginal_cost=10)
    n.add("Generator", "boiler", bus="heat", p_nom=100, marginal_cost=50)
    n.add("Generator", "n2_supply", bus="n2", p_nom=100, marginal_cost=1)
    n.add("Load", "office", bus="elec", p_set=[2, 3, 4, 3])
    n.add("Load", "heat_demand", bus="heat", p_set=[5, 5, 5, 5])
    n.add("Load", "nh3_offtake", bus="nh3", p_set=[8, 8, 0, 4])
    n.add("Link", "electrolyser", bus0="elec", bus1="h2", efficiency=0.5, bus2="heat", efficiency2=0.1, p_nom=100)
    n.add("Link", "haber", bus0="h2", bus1="nh3", efficiency=0.8, bus2="elec", efficiency2=-0.05,
          bus3="n2", efficiency3=-0.2, p_nom=100)
    n.links["eh_process"] = True
    # Pinned idle: level 5 MWh and 1 t for every snapshot, rate 0 (no effect on the dispatch above).
    n.add("Store", "nh3_tank", bus="nh3", e_nom=10, e_initial=5, e_min_pu=0.5, e_max_pu=0.5)
    n.add("StorageUnit", "n2_buffer", bus="n2", p_nom=1, max_hours=2, state_of_charge_initial=1,
          cyclic_state_of_charge=False, p_min_pu=0, p_max_pu=0)
    return n


# ── HTTP fixture: a solved project with an adopted Site ─────────────────────

def _url(tail=""):
    return f"/api/projects/{NAME}/sites/campus/scene{tail}"


def _propose(client, action, inputs, *, ok=True):
    base = client.get(_url()).json()["revision"]
    r = client.post(_url("/proposals"), json={"action": action, "base_revision": base, "inputs": inputs})
    if ok:
        assert r.status_code == 200, r.text
        return r.json()
    return r


def _apply(client, proposal):
    r = client.post(_url(f"/proposals/{proposal['id']}/apply"),
                    json={"expected_revision": proposal["base_revision"], "input_hash": proposal["input_hash"]})
    assert r.status_code == 200, r.text
    return r.json()


def _results(client, **params):
    r = client.get(_url("/results"), params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _solve(client):
    assert client.post("/api/simulation/run").status_code == 200
    st = _wait_idle(client)
    assert st["status"] == "completed", st


def _bind(client, associations, groups=()):
    _apply(client, _propose(client, "bind_targets", {"associations": list(associations), "groups": list(groups)}))


PLANT = {"id": "plant", "label": "Ammonia plant", "members": [A, B]}


@pytest.fixture
def project(client, install_network, project_storage_dir):
    install_network(oracle_network(), name=NAME)
    assert client.post(f"/api/projects/{NAME}", params={"force": True, "rebind": True}).status_code == 200
    path = project_storage_dir(NAME)
    assert client.put(f"/api/projects/{NAME}/sites", json={"version": 1, "sites": [copy.deepcopy(SITE)]}).status_code == 200
    from services.site_scene import store
    digest = store.legacy_site_digest(ss.read_sites(path), "campus")
    assert client.post(_url("/adopt"), json={"expected_digest": digest}).status_code == 200
    _apply(client, _propose(client, "generate_native", {"greenfield": [HALL, SHED, OFFICE]}))
    _bind(client, [
        {"feature": A, "target": {"class": "Link", "name": "electrolyser"}},
        {"feature": B, "target": {"class": "Link", "name": "haber"}},
        {"feature": B, "target": {"class": "Store", "name": "nh3_tank"}},
        {"feature": B, "target": {"class": "StorageUnit", "name": "n2_buffer"}},
        {"feature": C, "target": {"class": "Load", "name": "office"}},
    ], [PLANT])
    return path


@pytest.fixture
def solved(client, project):
    _solve(client)
    return project


def _target(payload, feature, name):
    return next(t for t in payload["features"][feature]["targets"] if t["name"] == name)


def _port(target, i):
    return next(p for p in target["ports"] if p["port"] == i)


def q(value, unit):
    return {"value": value, "available": True, "reason": None, "unit": unit}


def gone(reason, unit):
    return {"value": None, "available": False, "reason": reason, "unit": unit}


def _approx(leaf, value, unit):
    assert leaf["available"] is True and leaf["reason"] is None and leaf["unit"] == unit, leaf
    assert leaf["value"] == pytest.approx(value, abs=1e-9), leaf


# ── V22 / V23 / V88: exact native ports, signs and units at the known hour ──

def test_the_known_hour_shows_native_ports_signs_and_units(client, solved):
    got = _results(client, snapshot=T3)
    assert got["run"]["current"] is True and got["run"]["reason"] is None and len(got["run"]["digest"]) == 32
    assert got["snapshot"] == {"index": 3, "label": T3, "period": None, "weight_generators": q(1.0, "h"),
                               "period_years": q(1.0, "a")}
    haber = _target(got, B, "haber")
    assert [p["port"] for p in haber["ports"]] == [0, 1, 2, 3]
    _approx(_port(haber, 0)["rate"], 5.0, "MW")
    _approx(_port(haber, 1)["rate"], -4.0, "MW")
    _approx(_port(haber, 1)["rate_t"], -4.0 * NH3_T_PER_MWH, "t/h")
    _approx(_port(haber, 2)["rate"], 0.25, "MW")
    _approx(_port(haber, 3)["rate"], 1.0, "t/h")          # material is never labelled MW
    assert "rate_t" not in _port(haber, 3)
    assert [(p["bus"], p["carrier"], p["role"], p["reversed"], p["sign"]) for p in haber["ports"]] == [
        ("h2", "H2", "input", False, "withdrawal_positive"), ("nh3", "NH3", "output", False, "withdrawal_positive"),
        ("elec", "AC", "input", False, "withdrawal_positive"), ("n2", "N2", "input", False, "withdrawal_positive")]
    electrolyser = _target(got, A, "electrolyser")
    # PyPSA reports p3 = 0.0 for a link with no bus3: that port is absent, not a zero.
    assert [p["port"] for p in electrolyser["ports"]] == [0, 1, 2]
    _approx(_port(electrolyser, 0)["rate"], 10.0, "MW")
    _approx(_port(electrolyser, 1)["rate"], -5.0, "MW")
    _approx(_port(electrolyser, 2)["rate"], -1.0, "MW")
    office = _target(got, C, "office")
    assert office["ports"][0]["sign"] == "withdrawal_positive"
    _approx(office["ports"][0]["rate"], 3.0, "MW")


def test_a_storage_unit_on_a_tonne_bus_counts_in_tonnes(client, solved):
    """GK A condition 2: a StorageUnit's rate and level take its bus unit, as a Store's do."""
    got = _results(client, snapshot=T3)
    buffer = _target(got, B, "n2_buffer")
    _approx(buffer["ports"][0]["rate"], 0.0, "t/h")
    assert buffer["ports"][0]["sign"] == "injection_positive"
    _approx(buffer["level"], 1.0, "t")
    tank = _target(got, B, "nh3_tank")
    _approx(tank["ports"][0]["rate"], 0.0, "MW")
    _approx(tank["level"], 5.0, "MWh")


# ── V25: group boundary; internal transfers excluded ─────────────────────────

def test_the_group_boundary_excludes_the_internal_h2_transfer(client, solved):
    group = _results(client, snapshot=T3)["groups"]["plant"]
    assert group["internal_buses"] == ["h2"]
    rows = {r["bus"]: r for r in group["boundary"]}
    assert set(rows) == {"elec", "heat", "nh3", "n2"}
    _approx(rows["elec"]["net_import"], 10.25, "MW")
    _approx(rows["heat"]["net_import"], -1.0, "MW")
    _approx(rows["n2"]["net_import"], 1.0, "t/h")
    _approx(rows["nh3"]["net_import"], -4.0, "MW")       # the idle tank is a member: rate 0
    assert group["total"] == gone("mixed_units", None)
    totals = {(t["carrier"], t["net_import"]["unit"]): t["net_import"]["value"] for t in group["totals"]}
    assert totals == pytest.approx({("AC", "MW"): 10.25, ("heat", "MW"): -1.0, ("N2", "t/h"): 1.0,
                                    ("NH3", "MW"): -4.0})


# ── V57: exact null and zero shapes ─────────────────────────────────────────

def test_a_resolved_zero_is_available_and_a_missing_factor_is_null(client, solved, bound_ctx):
    t2 = _results(client, snapshot="2030-01-01T02:00:00")
    assert _port(_target(t2, B, "haber"), 1)["rate"] == q(0.0, "MW")
    with bound_ctx(client) as ctx:
        ctx.network.buses.loc["nh3", "mwh_per_t"] = float("nan")          # not a model input
    got = _results(client, snapshot=T3)
    assert got["run"]["current"] is True
    assert _port(_target(got, B, "haber"), 1)["rate_t"] == gone("conversion_factor_missing", "t/h")


# ── V24 / V87: instantaneous rate versus weighted totals ────────────────────

def test_the_window_total_is_weighted_and_the_rate_is_not(client, solved):
    got = _results(client, snapshot=T3, window_from="2030-01-01T00:00:00", window_to=T3)
    port = _port(_target(got, A, "electrolyser"), 0)
    _approx(port["rate"], 10.0, "MW")
    _approx(port["window_energy"], 20 + 20 + 0 + 10, "MWh")
    _approx(_port(_target(got, B, "haber"), 3)["window_energy"], 2 + 2 + 0 + 1, "t")
    assert "window_level" not in _target(got, B, "nh3_tank") and "window_energy" not in {
        k for k in _target(got, B, "nh3_tank") if k != "ports"}
    assert got["window"] == {"from": "2030-01-01T00:00:00", "to": T3, "snapshots": 4, "basis": "generators"}
    rows = {r["bus"]: r for r in got["groups"]["plant"]["boundary"]}
    _approx(rows["elec"]["window_energy"], 20.5 + 20.5 + 0 + 10.25, "MWh")


# ── V26: freshness, one gate and its labels ─────────────────────────────────

def _all_unavailable(got, reason):
    assert got["run"] == {"digest": None, "current": False, "reason": reason}
    for feature in got["features"].values():
        for t in feature["targets"]:
            for p in t["ports"]:
                assert p["rate"]["available"] is False and p["rate"]["reason"] == reason
    assert all(g["boundary"] == [] and g["total"]["reason"] == reason for g in got["groups"].values())


def test_before_any_solve_the_numbers_are_unavailable(client, project):
    _all_unavailable(_results(client, snapshot=T3), "run_not_solved")


def test_an_input_edit_after_the_solve_makes_every_number_unavailable(client, solved, bound_ctx):
    with bound_ctx(client) as ctx:
        ctx.network.loads_t.p_set.loc[:, "office"] = 9.0
    _all_unavailable(_results(client, snapshot=T3), "results_not_current")


@pytest.mark.parametrize("status,condition,reason", [
    ("aborted", "user_aborted:native_solve", "run_cancelled"),
    ("interrupted", None, "run_cancelled"),
    ("failed", "infeasible", "run_failed"),
    ("error", "validation_failed", "run_failed"),
    ("completed", "infeasible", "run_failed"),
    ("running", None, "run_running"),
])
def test_a_cancelled_failed_or_running_solve_labels_the_gate(client, solved, bound_ctx, status, condition, reason):
    with bound_ctx(client) as ctx:
        ctx.solver_state.update(status=status, condition=condition)
    _all_unavailable(_results(client, snapshot=T3), reason)


def test_after_an_ac_power_flow_the_scene_numbers_are_unavailable(client, solved, bound_ctx):
    """Owner choice C: the same guard as every result surface (`solve_matches_inputs`)."""
    with bound_ctx(client) as ctx:
        ctx.solver_state["ac_pf_results"] = {"buses_t.v_mag_pu": None}
    _all_unavailable(_results(client, snapshot=T3), "results_not_current")


def test_the_gate_itself_decides_availability(client, solved, monkeypatch):
    """GK A condition 6: a mutant that skips `solve_matches_inputs` must fail this test."""
    from harness import calculations
    monkeypatch.setattr(calculations, "solve_matches_inputs", lambda ctx: False)
    _all_unavailable(_results(client, snapshot=T3), "results_not_current")


def test_a_changed_run_is_refused_per_quantity(client, solved, bound_ctx):
    digest = _results(client, snapshot=T3)["run"]["digest"]
    from services.dispatch_status import dispatch_digest
    with bound_ctx(client) as ctx:
        assert digest == dispatch_digest(ctx.network)
    assert _results(client, snapshot=T3, run=digest)["run"]["current"] is True
    _all_unavailable(_results(client, snapshot=T3, run="0" * 32), "run_changed")


def test_another_open_project_leaves_this_scene_unavailable(client, solved, install_network):
    install_network(oracle_network(), name="d07_other")
    assert client.post("/api/projects/d07_other", params={"force": True, "rebind": True}).status_code == 200
    _all_unavailable(_results(client, snapshot=T3), "project_not_open")


def test_a_snapshot_outside_the_run_is_unavailable_not_nearest(client, solved):
    got = _results(client, snapshot="2030-01-01T03:30:00")
    assert got["snapshot"] is None
    assert _port(_target(got, A, "electrolyser"), 0)["rate"] == gone("snapshot_not_in_run", "MW")
    got = _results(client, snapshot=T3, window_from="2031-01-01T00:00:00", window_to="2031-01-02T00:00:00")
    assert _port(_target(got, A, "electrolyser"), 0)["window_energy"] == gone("window_out_of_range", "MWh")


@pytest.mark.parametrize("params", [
    {"snapshot": "2030-01-01T03:00:00+00:00"},        # tz-aware against a naive index
    {"snapshot": "not a time"},
    {},
    {"snapshot": T3, "window_from": T3},                # half a window
])
def test_a_malformed_query_is_invalid_input_never_a_500(client, solved, params):
    r = client.get(_url("/results"), params=params)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == "invalid_input"


# ── V28: geometry keeps the run; the read changes nothing ───────────────────

def test_a_geometry_edit_keeps_the_run_current(client, solved):
    before = _results(client, snapshot=T3)
    moved = {**HALL, "center": [35, -65], "heading": 15}
    _apply(client, _propose(client, "edit_buildings", {"greenfield": [moved, SHED, OFFICE]}))
    after = _results(client, snapshot=T3)
    assert after["run"] == before["run"] and after["scene_revision"] > before["scene_revision"]
    assert after["features"] == before["features"] and after["groups"] == before["groups"]


def test_the_read_writes_nothing(client, solved, bound_ctx):
    with bound_ctx(client) as ctx:
        n = ctx.network
        meta, state, revision = copy.deepcopy(dict(n.meta)), dict(ctx.solver_state), ctx.network_revision
    scene = client.get(_url()).json()
    _results(client, snapshot=T3, window_from="2030-01-01T00:00:00", window_to=T3)
    with bound_ctx(client) as ctx:
        assert ctx.network is n and dict(n.meta) == meta and ctx.network_revision == revision
        assert set(ctx.solver_state) == set(state) and ctx.solver_state["status"] == state["status"]
    assert client.get(_url()).json()["revision"] == scene["revision"]


# ── V15 / V16: explicit allocation ──────────────────────────────────────────

def test_an_explicit_share_is_labelled_allocated_and_conserves_the_native_value(client, solved):
    _bind(client, [{"feature": A, "target": {"class": "Load", "name": "office"}, "weight": 0.4},
                   {"feature": C, "target": {"class": "Load", "name": "office"}, "weight": 0.6}], [PLANT])
    got = _results(client, snapshot=T3)
    hall, office = _target(got, A, "office"), _target(got, C, "office")
    assert hall["allocation"] == {"basis": "explicit", "weight": 0.4}
    _approx(hall["ports"][0]["rate"], 3.0, "MW")               # the native value, unchanged
    _approx(hall["ports"][0]["allocated"], 1.2, "MW")
    windowed = _results(client, snapshot=T3, window_from="2030-01-01T00:00:00", window_to=T3)
    _approx(_target(windowed, A, "office")["ports"][0]["window_allocated"], 0.4 * (2 + 3 + 4 + 3), "MWh")
    _approx(office["ports"][0]["allocated"], 1.8, "MW")
    # The plant holds only the hall's share of the office load: no boundary is claimed for it.
    group = got["groups"]["plant"]
    assert group["boundary"] == [] and group["total"]["reason"] == "group_partial_allocation"
    assert group["unavailable_member"] == "Load:office"


def test_shares_above_one_are_refused(client, solved):
    r = _propose(client, "bind_targets", {"associations": [
        {"feature": A, "target": {"class": "Load", "name": "office"}, "weight": 0.7},
        {"feature": C, "target": {"class": "Load", "name": "office"}, "weight": 0.6}], "groups": []}, ok=False)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_allocation"


# ── Service seam: networks the project routes do not build ──────────────────

GATE_OK = {"current": True, "reason": None, "digest": "d" * 32}


def _scene(*assoc, groups=()):
    return {"revision": 1, "buildings": [{"id": f} for f in {a[0] for a in assoc}],
            "associations": [{"id": f"a{i}", "feature": f, "class": c, "uid": f"u-{c}-{nm}", "name_at_bind": nm,
                              "weight": w} for i, (f, c, nm, w) in enumerate(assoc)],
            "groups": list(groups), "site": {"placements": {}}}


def _uids(n, scene):
    from services import component_identity
    for a in scene["associations"]:
        component_identity.keep(n, a["class"], a["name_at_bind"], a["uid"])


def _project(n, scene, **query):
    _uids(n, scene)
    return R.project(scene, n, gate=GATE_OK, query=R.parse_query(n, **query))


def _solved(n):
    n.optimize(solver_name="highs", solver_options={"output_flag": False}, include_objective_constant=False)
    return n


@pytest.mark.parametrize("freq,weight,energy", [("h", 1.0, 50.0), ("15min", 0.25, 12.5), ("h", 10.0, 500.0)])
def test_cadence_and_representative_weights_change_energy_not_rate(freq, weight, energy):
    n = _solved(oracle_network(freq, weight))
    sns = [str(s.isoformat()) for s in n.snapshots]
    got = _project(n, _scene(("hall", "Link", "electrolyser", None)),
                   snapshot=sns[3], window_from=sns[0], window_to=sns[3])
    port = _port(got["features"]["hall"]["targets"][0], 0)
    _approx(port["rate"], 10.0, "MW")
    _approx(port["window_energy"], energy, "MWh")
    assert got["snapshot"]["weight_generators"] == q(weight, "h")


def test_window_energy_uses_the_generators_weighting_never_stores():
    """GK A condition 8: generators 1 h, stores 7 h; a level is never window-summed."""
    n = _solved(oracle_network(store_weight=7.0))
    got = _project(n, _scene(("b", "Store", "nh3_tank", None), ("b", "Link", "electrolyser", None)),
                   snapshot=T3, window_from="2030-01-01T00:00:00", window_to=T3)
    tank, link = got["features"]["b"]["targets"]
    _approx(_port(link, 0)["window_energy"], 50.0, "MWh")
    assert set(tank) == {"class", "uid", "name", "allocation", "ports", "level"}
    _approx(tank["level"], 5.0, "MWh")


def test_a_range_index_network_takes_its_integer_label():
    n = _solved(oracle_network(snapshots=pd.RangeIndex(4)))
    got = _project(n, _scene(("hall", "Link", "electrolyser", None)), snapshot="3")
    assert got["snapshot"]["label"] == 3
    _approx(_port(got["features"]["hall"]["targets"][0], 0)["rate"], 10.0, "MW")
    with pytest.raises(SceneError) as e:
        R.parse_query(n, snapshot="3.5")
    assert e.value.kind == "invalid_input"


def _multi_period():
    n = pypsa.Network()
    n.set_snapshots(pd.MultiIndex.from_product([[2030, 2040], pd.date_range("2030-01-01", periods=2, freq="h")]))
    n.investment_periods = [2030, 2040]
    n.investment_period_weightings["years"] = [10, 5]
    n.add("Bus", "elec", carrier="AC")
    n.add("Generator", "grid", bus="elec", p_nom=100, marginal_cost=10)
    n.add("Load", "office", bus="elec", p_set=[2, 3, 6, 7])
    return _solved_mp(n)


def _solved_mp(n):
    n.optimize(solver_name="highs", multi_investment_periods=True, solver_options={"output_flag": False},
               include_objective_constant=False)
    return n


def test_a_multi_period_network_needs_its_period_and_applies_its_years():
    n = _multi_period()
    with pytest.raises(SceneError) as e:
        R.parse_query(n, snapshot="2030-01-01T01:00:00")
    assert e.value.kind == "invalid_input"
    got = _project(n, _scene(("c", "Load", "office", None)), snapshot="2030-01-01T01:00:00", period="2040",
                   window_from="2030-01-01T00:00:00", window_to="2030-01-01T01:00:00")
    assert got["snapshot"]["period"] == 2040 and got["snapshot"]["period_years"] == q(5.0, "a")
    port = got["features"]["c"]["targets"][0]["ports"][0]
    _approx(port["rate"], 7.0, "MW")
    _approx(port["window_energy"], (6 + 7) * 1.0 * 5, "MWh")      # the 2040 period's own years only


def test_a_link_flowing_against_its_nominal_role_is_marked_reversed():
    """GK A condition 3: roles come from the parameters; the sign of the value marks a reversal."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=1, freq="h"))
    n.add("Bus", "a", carrier="AC")
    n.add("Bus", "b", carrier="AC")
    n.add("Generator", "g", bus="a", p_nom=10, marginal_cost=1)
    n.add("Load", "l", bus="b", p_set=4)
    n.add("Link", "tie", bus0="b", bus1="a", p_nom=10, p_min_pu=-1)    # nominally b → a; must run a → b
    n.add("Link", "loop", bus0="a", bus1="b", bus2="a", efficiency=1.0, efficiency2=-0.5, p_nom=0)
    _solved(n)
    got = _project(n, _scene(("x", "Link", "tie", None)), snapshot="2030-01-01T00:00:00")
    tie = got["features"]["x"]["targets"][0]
    assert [(p["role"], p["reversed"]) for p in tie["ports"]] == [("input", True), ("output", True)]
    _approx(_port(tie, 0)["rate"], -4.0, "MW")


def test_two_ports_on_one_bus_are_summed_in_the_group():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=1, freq="h"))
    n.add("Bus", "a", carrier="AC")
    n.add("Bus", "heat", carrier="heat")
    n.add("Generator", "g", bus="a", p_nom=10, marginal_cost=1)
    n.add("Load", "h", bus="heat", p_set=2)
    # 2 MW heat out needs 2 MW at bus0 plus 0.5 × 2 = 1 MW at bus2, both on bus a.
    n.add("Link", "hp", bus0="a", bus1="heat", bus2="a", efficiency=1.0, efficiency2=-0.5, p_nom=10)
    _solved(n)
    got = _project(n, _scene(("x", "Link", "hp", None), groups=[{"id": "g1", "label": "G", "members": ["x"]}]),
                   snapshot="2030-01-01T00:00:00")
    rows = {r["bus"]: r for r in got["groups"]["g1"]["boundary"]}
    _approx(rows["a"]["net_import"], 3.0, "MW")
    _approx(rows["heat"]["net_import"], -2.0, "MW")


def test_a_non_member_port_on_a_bus_makes_it_a_boundary_bus():
    """GK A condition 9: a non-member Link's bus3 on h2 means h2 is no longer internal to the plant."""
    n = oracle_network()
    n.add("Link", "probe", bus0="elec", bus1="heat", bus3="h2", efficiency3=-0.0, p_nom=0)
    _solved(n)
    got = _project(n, _scene(("hall", "Link", "electrolyser", None), ("shed", "Link", "haber", None),
                             groups=[{"id": "plant", "label": "P", "members": ["hall", "shed"]}]), snapshot=T3)
    group = got["groups"]["plant"]
    assert group["internal_buses"] == []
    _approx({r["bus"]: r for r in group["boundary"]}["h2"]["net_import"], 0.0, "MW")


@pytest.mark.parametrize("unit,expected", [("", "MW"), ("None", "MW"), (float("nan"), "MW"), ("MW", "MW"),
                                           ("MWh", "MW"), ("t", "t/h"), ("kg", None), ("GJ", None)])
def test_a_bus_unit_maps_to_a_rate_unit_or_unknown(unit, expected):
    """GK A condition 1: the raw declared unit is read, not only `bus_unit`'s t/MWh view."""
    n = oracle_network()
    n.buses.loc["heat", "unit"] = unit
    _solved(n)
    got = _project(n, _scene(("hall", "Link", "electrolyser", None)), snapshot=T3)
    heat = _port(got["features"]["hall"]["targets"][0], 2)["rate"]
    if expected is None:
        assert heat == gone("unit_unknown", None)
    else:
        _approx(heat, -1.0 if expected == "MW" else -1.0, expected)


def test_an_unbound_target_is_unavailable_on_its_own():
    n = _solved(oracle_network())
    scene = _scene(("hall", "Link", "electrolyser", None))
    got = R.project(scene, n, gate=GATE_OK, query=R.parse_query(n, snapshot=T3))     # uid never kept: gone
    target = got["features"]["hall"]["targets"][0]
    assert target["name"] is None and target["ports"] == [] and target["reason"] == "unbound_target"


def test_the_reason_codes_are_a_closed_list():
    assert R.REASONS == (
        "project_not_open", "run_not_solved", "run_failed", "run_cancelled", "run_running", "results_not_current",
        "run_changed", "snapshot_not_in_run", "window_out_of_range", "unbound_target", "series_missing",
        "non_finite_value", "unit_unknown", "conversion_factor_missing", "vintage_rows_unsupported", "mixed_units",
        "group_partial_allocation", "member_unavailable", "group_has_no_boundary")


def test_every_number_in_a_payload_is_finite():
    n = _solved(oracle_network())
    got = _project(n, _scene(("hall", "Link", "electrolyser", None), ("hall", "Link", "haber", None)),
                   snapshot=T3, window_from="2030-01-01T00:00:00", window_to=T3)

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, float):
            assert math.isfinite(x) and not (x == 0 and math.copysign(1, x) < 0)
    walk(got)


def test_a_missing_column_and_a_nan_are_named_not_zero():
    n = _solved(oracle_network())
    n.links_t.p3 = n.links_t.p3.drop(columns=["haber"])
    n.links_t.p2.loc[n.snapshots[3], "haber"] = float("nan")
    got = _project(n, _scene(("shed", "Link", "haber", None)), snapshot=T3)
    haber = got["features"]["shed"]["targets"][0]
    assert _port(haber, 3)["rate"] == gone("series_missing", "t/h")
    assert _port(haber, 2)["rate"] == gone("non_finite_value", "MW")


def test_vintage_rows_beside_a_bound_component_are_unavailable():
    """Owner choice B: `<name>@<year>` rows are not summed into a binding in SC-03."""
    n = oracle_network()
    n.add("Generator", "grid@2030", bus="elec", p_nom=0)
    _solved(n)
    got = _project(n, _scene(("hall", "Generator", "grid", None), groups=[{"id": "g", "label": "G",
                                                                           "members": ["hall"]}]), snapshot=T3)
    target = got["features"]["hall"]["targets"][0]
    assert target["ports"] == [] and target["reason"] == "vintage_rows_unsupported"
    assert got["groups"]["g"]["total"] == gone("member_unavailable", None)
    assert got["groups"]["g"]["unavailable_member"] == "Generator:grid"


def test_an_unbound_member_makes_the_group_unavailable():
    n = _solved(oracle_network())
    scene = _scene(("hall", "Link", "electrolyser", None), ("shed", "Link", "haber", None),
                   groups=[{"id": "plant", "label": "P", "members": ["hall", "shed"]}])
    from services import component_identity
    component_identity.keep(n, "Link", "electrolyser", "u-Link-electrolyser")     # haber's uid is never kept
    got = R.project(scene, n, gate=GATE_OK, query=R.parse_query(n, snapshot=T3))
    group = got["groups"]["plant"]
    assert group["boundary"] == [] and group["total"] == gone("member_unavailable", None)
    assert group["unavailable_member"] == "Link:haber"


def test_a_window_partly_outside_the_horizon_is_never_clipped(client, solved):
    got = _results(client, snapshot=T3, window_from="2029-12-31T23:00:00", window_to=T3)
    assert _port(_target(got, A, "electrolyser"), 0)["window_energy"] == gone("window_out_of_range", "MWh")


def test_a_closed_gate_leaves_the_window_count_null(client, project):
    got = _results(client, snapshot=T3, window_from="2030-01-01T00:00:00", window_to=T3)
    assert got["window"]["snapshots"] is None and got["snapshot"] is None


def test_a_group_with_only_internal_buses_names_that_not_mixed_units():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=1, freq="h"))
    n.add("Bus", "a", carrier="AC")
    n.add("Generator", "g", bus="a", p_nom=10, marginal_cost=1)
    n.add("Load", "l", bus="a", p_set=2)
    _solved(n)
    got = _project(n, _scene(("x", "Generator", "g", None), ("x", "Load", "l", None),
                             groups=[{"id": "g1", "label": "G", "members": ["x"]}]), snapshot="2030-01-01T00:00:00")
    group = got["groups"]["g1"]
    assert group["internal_buses"] == ["a"] and group["boundary"] == [] and group["totals"] == []
    assert group["total"] == gone("group_has_no_boundary", None)


def test_an_unreadable_efficiency_gives_an_unknown_role_and_an_explicit_mw_wins_over_the_catalogue():
    n = oracle_network()
    n.buses.loc["n2", "unit"] = "MWh"                    # declared: the N2 catalogue's t does not override it
    _solved(n)
    n.links.loc["haber", "efficiency2"] = float("nan")    # after the solve: the role reads unknown, not output
    got = _project(n, _scene(("shed", "Link", "haber", None)), snapshot=T3)
    haber = got["features"]["shed"]["targets"][0]
    assert _port(haber, 2)["role"] == "unknown" and _port(haber, 2)["reversed"] is False
    _approx(_port(haber, 3)["rate"], 1.0, "MW")

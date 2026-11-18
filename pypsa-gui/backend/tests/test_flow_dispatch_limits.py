"""Off-grid dispatch limits shown as assumed (control-strategy walkthrough 03,
asset-investment walkthrough improvement 2; owner RESERVE-SHOW-1).

An off-grid site's operating reserve and minimum renewable fraction are each an
assumed warning in section 8 while off in the solver config the solve reads;
never a blocker, gone once set, absent on a grid-connected site (GK B3 K5)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from services import flow_service as S
from services.pypsa_service import PyPSAService

CODES = {"assumed_operating_reserve", "assumed_min_renewable_fraction"}


def _codes(items):
    return {i["code"] for i in items}


# ── the rule ────────────────────────────────────────────────────────────────

def test_both_off_gives_two_assumed_warnings_in_section_8():
    items = S._dispatch_limit_items({})
    assert _codes(items) == CODES
    for i in items:
        assert i["severity"] == "warning" and i["blocking"] is False
        assert i["target"]["section"] == "scenarios" and i["target"]["panel"] == "simparams"
        assert S._is_assumed(i)
    msgs = {i["code"]: i["message"] for i in items}
    assert msgs["assumed_operating_reserve"].startswith("Operating reserve: off (assumed)")
    assert msgs["assumed_min_renewable_fraction"].startswith("Minimum renewable fraction: none (assumed)")
    # Plain words: no engine key in what the user reads.
    assert not any("reserve_share" in m or "min_renewable" in m for m in msgs.values())
    assert {i["target"]["field"] for i in items} == {"reserve_share_load", "min_renewable_fraction"}


@pytest.mark.parametrize("field", ["reserve_share_load", "reserve_share_pv", "reserve_share_wind"])
def test_any_reserve_share_set_drops_the_reserve_row_only(field):
    assert _codes(S._dispatch_limit_items({field: 0.1})) == {"assumed_min_renewable_fraction"}


def test_a_set_renewable_fraction_drops_its_row_only():
    assert _codes(S._dispatch_limit_items({"min_renewable_fraction": 0.6})) == {"assumed_operating_reserve"}


def test_zero_or_none_counts_as_off():
    cfg = {"reserve_share_load": 0.0, "reserve_share_pv": None, "min_renewable_fraction": 0}
    assert _codes(S._dispatch_limit_items(cfg)) == CODES


def test_both_set_gives_nothing():
    assert S._dispatch_limit_items({"reserve_share_wind": 0.2, "min_renewable_fraction": 0.5}) == []


# ── through GET /flow ───────────────────────────────────────────────────────

def _create(client, tid, name, **kw) -> str:
    r = client.post(f"/api/projects/from_template/{tid}", params={"name": name}, **kw)
    assert r.status_code == 200, r.text
    return r.json().get("name", name)


def _flow(client, name) -> dict:
    r = client.get(f"/api/projects/{name}/flow")
    assert r.status_code == 200, r.text
    return r.json()


def _items(body):
    return [i for s in body["sections"] for i in s["items"]]


def _set_cfg(client, bound_ctx, **kw):
    with bound_ctx(client):
        state = PyPSAService.get_solver_state()
        state["solver_config"] = replace(state["solver_config"], **kw)


def test_off_grid_site_shows_both_in_section_8_without_blocking_the_run(client, tmp_projects_dir, bound_ctx):
    name = _create(client, "eh_microgrid", "dl_island")
    body = _flow(client, name)
    scen = next(s for s in body["sections"] if s["section"] == "scenarios")
    assert CODES <= _codes(scen["items"])
    assert not any(i["blocking"] for i in scen["items"] if i["code"] in CODES)
    ready_before = (body["run_ready"], body["run_blocker"])

    _set_cfg(client, bound_ctx, reserve_share_load=0.1, min_renewable_fraction=0.6)
    after = _flow(client, name)
    assert not CODES & _codes(_items(after))
    # The rows never gated the Run: setting them leaves its verdict unchanged.
    assert (after["run_ready"], after["run_blocker"]) == ready_before


def test_grid_connected_site_has_neither(client, tmp_projects_dir):
    name = _create(client, "eh_h2_hub", "dl_grid")
    assert not CODES & _codes(_items(_flow(client, name)))


# ── K6: the card's save moves the solver-input fingerprint ──────────────────

def test_a_share_saved_through_the_solver_config_route_makes_results_stale(client, tmp_projects_dir, bound_ctx):
    from services.provenance_graph.runs import active_input_fingerprint

    _create(client, "eh_microgrid", "dl_fp")

    def fp():
        with bound_ctx(client):
            return active_input_fingerprint()

    before = fp()
    assert before is not None
    # Off sent as off (the drawer opened and saved nothing new): unchanged.
    r = client.put("/api/simulation/solver_config", json={"reserve_share_load": None})
    assert r.status_code == 200, r.text
    assert fp() == before
    r = client.put("/api/simulation/solver_config", json={"reserve_share_load": 0.1})
    assert r.status_code == 200, r.text
    after = fp()
    assert after != before
    r = client.put("/api/simulation/solver_config", json={"min_renewable_fraction": 0.6})
    assert r.status_code == 200, r.text
    assert fp() not in (before, after)

"""
The IEEE 39-bus end-to-end review's findings, closed.

`notes/2026-09-09-ieee39-e2e-review.md` §3 — what driving the whole
solution-FMEA journey on a real system (MATPOWER `case39`, 39 buses, 46
branches, a mixed fleet on hourly profiles) found that fixture-sized
networks had not:

* F2 — a full-row PUT of a component destroyed its time series, because
       `_update_component` updates by remove+add. Saving the wind farm's row
       from the Properties panel with NO field changed made it a firm
       must-take at `p_max_pu` 1.0 and moved the COPT LOLE 0.831 h → 0.319 h.
* F6 — M2 clamped the occurrence-bearing branch of the membership walk; a
       MUST-TAKE generator with a negative static still netted NEGATIVELY
       into the residual (residual 120 for a demand of 100).
* F7 — M2's other half: the preflight's `availability_may_include_outages`
       sentence still named negative statics and stated a formula no surface
       applies to them.
* F8 — M4 traded a wrong reason for no reason on one shape: a unit whose
       rate is TYPED 0 while carrying a profile was named on `/copt` (the
       rate-zero row note) but appeared in no `/mc` list at all.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.adequacy import copt as C


def _profiled(periods: int = 24) -> pypsa.Network:
    """A must-take farm on an hourly profile, a firm plant, a load on a
    profile and a store with an inflow series — one of every time-varying
    shape the editor can hand back."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=periods, freq="h"))
    n.add("Bus", "b", carrier="AC")
    for c in ("gas", "wind", "hydro"):
        n.add("Carrier", c)
    n.add("Generator", "firm", bus="b", carrier="gas", p_nom=300.0,
          marginal_cost=10.0, outage_rate_value=0.05,
          outage_rate_basis="EFORd", mttr_hours=24.0)
    n.add("Generator", "farm", bus="b", carrier="wind", p_nom=500.0,
          marginal_cost=0.0)
    n.generators_t.p_max_pu["farm"] = np.linspace(0.1, 0.9, periods)
    n.add("Load", "l", bus="b", p_set=100.0)
    n.loads_t.p_set["l"] = np.full(periods, 100.0)
    n.add("StorageUnit", "res", bus="b", carrier="hydro", p_nom=50.0,
          max_hours=6.0)
    n.storage_units_t.inflow["res"] = np.full(periods, 7.0)
    return n


# ── F2 ────────────────────────────────────────────────────────────────────

def _ts_column(client, component: str, attribute: str, col: str):
    """One column of a time-varying table, as the ROUTE serves it."""
    body = client.get(f"/api/network/timeseries/{component}/{attribute}").json()
    if col not in body["columns"]:
        return None
    i = body["columns"].index(col)
    return [row[i] for row in body["data"]]


def test_a_full_row_PUT_keeps_the_components_time_series(client, install_network):
    """★ F2 (SERIOUS). The Properties panel saves the whole row; the route
    updates by remove+add, and PyPSA drops the component's `_t` columns when
    it is removed. Nothing warned — the Time Series view prefers `_user_ts`
    and so kept showing a profile the engines no longer had.

    Driven through the SHIPPED routes, and the assertion is the one the
    review measured on the IEEE 39-bus network: the ENGINES read the same
    fleet after the edit as before it. Before the fix the 500 MW farm became
    a firm must-take at `p_max_pu` 1.0 and the COPT LOLE fell 62 %.

    Bite (verified): drop the `_detach_component_series` /
    `_reattach_component_series` pair.
    """
    install_network(_profiled())
    before = client.get("/api/results/copt").json()
    assert before["metrics"]["eue_mwh"] > 0.0, (
        "the fixture must shed something for the LOLE to be able to move")
    farm_before = _ts_column(client, "generators", "p_max_pu", "farm")
    load_before = _ts_column(client, "loads", "p_set", "l")
    inflow_before = _ts_column(client, "storage_units", "inflow", "res")
    assert farm_before and load_before and inflow_before

    for path, key in (("generators", "farm"), ("loads", "l"),
                      ("storage_units", "res")):
        rows = {r["name"]: r for r in client.get(f"/api/network/{path}").json()}
        r = client.put(f"/api/network/{path}/{key}", json=rows[key])
        assert r.status_code == 200, r.text

    assert _ts_column(client, "generators", "p_max_pu", "farm") == farm_before
    assert _ts_column(client, "loads", "p_set", "l") == load_before
    assert _ts_column(client, "storage_units", "inflow", "res") == inflow_before

    after = client.get("/api/results/copt").json()
    assert after["metrics"]["eue_mwh"] == pytest.approx(before["metrics"]["eue_mwh"])
    assert after["metrics"]["lole_hours"] == pytest.approx(
        before["metrics"]["lole_hours"])
    assert after["fleet"]["must_take"] == before["fleet"]["must_take"]


def test_a_one_field_edit_keeps_the_profile_too(client, install_network):
    """★ F2, the grid's inline edit — it sends the cached row with one field
    changed, so the same remove+add runs under it."""
    install_network(_profiled())
    before = _ts_column(client, "generators", "p_max_pu", "farm")
    rows = {r["name"]: r for r in client.get("/api/network/generators").json()}
    r = client.put("/api/network/generators/farm",
                   json=dict(rows["farm"], marginal_cost=0.5))
    assert r.status_code == 200, r.text
    rows2 = {r["name"]: r for r in client.get("/api/network/generators").json()}
    assert rows2["farm"]["marginal_cost"] == pytest.approx(0.5)
    assert _ts_column(client, "generators", "p_max_pu", "farm") == before


def test_renaming_a_non_bus_component_is_a_500_on_pypsa_1_3(client, install_network):
    """★ F9 (pre-existing on master, found while testing F2). The update
    route re-points a renamed component's dependents through PyPSA's
    `rename_component_names` — correct for a Bus, and a hard raise for
    everything else on pypsa 1.3.0: that function derives the cross-reference
    column from the RENAMED class (`generator`, `load`, `line`) and then
    indexes EVERY component's static frame with it, so the first frame
    without such a column raises `KeyError`. Renaming a generator from the
    Properties panel is therefore a 500 today, before any of this branch's
    code runs.

    Pinned as a KNOWN defect, not as desired behaviour: when it is fixed
    (in PyPSA, or by re-pointing dependents here) this test fails and says
    so, and the profile-preserving assertion below is what should replace
    it — the series are put back under the OLD name precisely so a working
    rename re-keys them.

    Verified on bare PyPSA 1.3.0: Generator, Load and Line renames all raise;
    a Bus rename succeeds (its column, `bus`, exists on the frames that carry
    it).
    """
    install_network(_profiled())
    rows = {r["name"]: r for r in client.get("/api/network/generators").json()}
    with pytest.raises(KeyError, match="generator"):
        client.put("/api/network/generators/farm",
                   json=dict(rows["farm"], name="farm_2"))


# ── F6 / F7 ───────────────────────────────────────────────────────────────

def test_a_negative_static_on_a_MUST_TAKE_farm_credits_zero(client, install_network):
    """★ F6. M2 clamped the fold, which is the occurrence-bearing branch of
    the membership walk. A must-take farm — no outage data, netted at its
    hourly availability — took the other branch, and a negative `p_max_pu`
    there SUBTRACTED from the residual: the farm appeared to consume.

    The three surfaces disagreed exactly as M2 said they must not: the
    engines netted −250 MW, the margin lists the unit unpriceable and credits
    nothing, and the LP can dispatch nothing from a negative bound.

    Bite (verified): drop the `max(static, 0.0)` — the residual reads 350 for
    a demand of 100.
    """
    n = _profiled(periods=4)
    n.generators.at["farm", "p_max_pu"] = -0.5
    n.generators_t.p_max_pu.drop(columns=["farm"], inplace=True)
    _units, residual = C.fleet_and_residual(n)[:2]
    assert np.allclose(residual.to_numpy(), 100.0), residual
    # …and the same clamp on the COLUMN branch, which is the shape an
    # imported profile takes.
    m = _profiled(periods=4)
    m.generators_t.p_max_pu["farm"] = np.full(4, -0.5)
    _u2, residual2 = C.fleet_and_residual(m)[:2]
    assert np.allclose(residual2.to_numpy(), 100.0), residual2


def test_the_may_include_outages_sentence_skips_a_negative_static():
    """★ F7. M2's expected fix had two halves — fold `max(cf, 0)` AND stop
    the preflight naming negative statics. Only the first shipped. The
    sentence tells the user the unit is "credited at nameplate x p_max_pu x
    (1 − q)", which is false of a value every surface now reads as 0 MW, and
    offers a remedy (set `p_max_pu_includes_outages`) that is not the fix.

    Bite (verified): drop the `0.0 <=` bound — `neg` is named again.
    """
    import services.validation_service as V

    n = _profiled(periods=4)
    n.generators_t.p_max_pu.drop(columns=["farm"], inplace=True)
    n.add("Generator", "neg", bus="b", carrier="gas", p_nom=100.0,
          marginal_cost=9.0, p_max_pu=-0.2, outage_rate_value=0.1,
          outage_rate_basis="EFORd", mttr_hours=24.0)
    n.add("Generator", "half", bus="b", carrier="gas", p_nom=100.0,
          marginal_cost=9.0, p_max_pu=0.5, outage_rate_value=0.1,
          outage_rate_basis="EFORd", mttr_hours=24.0)
    issues = V.__dict__["_check_profiled_occurrence_units"](n)
    msg = " ".join(i.message for i in issues
                   if i.code == "availability_may_include_outages")
    assert "half" in msg, msg
    assert "neg" not in msg, msg


# ── F8 ────────────────────────────────────────────────────────────────────

def test_a_typed_zero_unit_is_named_on_BOTH_payloads(client, install_network,
                                                     session_state):
    """★ F8. M4 stopped `deterministic_units` claiming the flag for a unit
    whose rate the user simply TYPED as 0 — the right call, and on `/copt`
    the rate-zero row note still names it. `/mc` has no rows, so there the
    same unit went from a wrong reason to no mention at all: a unit the
    sampler draws no outages for, in none of the payload's three lists.

    Both payloads carry the second list now, disjoint from the first and
    from `profile_units`.

    Bites (verified): drop `rate_zero_units` from either route.
    """
    import dataclasses
    import time

    from tests.test_review_minors import _flag_fixture

    install_network(_flag_fixture())
    fleet = client.get("/api/results/copt").json()["fleet"]
    assert sorted(fleet["deterministic_units"]) == ["flag_col", "flag_static"]
    assert fleet["rate_zero_units"] == ["typed0"]
    assert "typed0" not in fleet["profile_units"]
    assert not set(fleet["rate_zero_units"]) & set(fleet["deterministic_units"])

    st = session_state(client)
    st["solver_config"] = dataclasses.replace(st["solver_config"], voll=1000.0)
    r = client.post("/api/results/mc", json={"draws": 20, "seed": 1})
    assert r.status_code == 200, r.text
    body = None
    for _ in range(400):
        body = client.get("/api/results/mc").json()
        if body.get("status") in ("done", "failed", "aborted"):
            break
        time.sleep(0.05)
    assert body["status"] == "done", body
    res = body["result"]
    assert sorted(res["deterministic_units"]) == ["flag_col", "flag_static"]
    assert res["rate_zero_units"] == ["typed0"]
    assert "typed0" not in res["profile_units"]


# ── F3 ────────────────────────────────────────────────────────────────────

def test_the_frontier_record_states_the_margin_it_swept_under(
        client, install_network, session_state):
    """★ F3. The frontier does NOT strip the reserve margin — a margin is a
    standing standard, not a swept one, and the phase-8 plan calls that
    right "but must be stated on the panel, or the curve reads as cost-vs-ε
    when it is cost-vs-ε-at-margin-m". The record carried no margin field,
    so the panel could not state it: measured live on the IEEE 39-bus
    network, three swept targets came back with identical cost and zero ENS,
    `warning` null and `knee` null, and the panel's copy said "every step
    still buys more avoided-shed value than it costs" — of a curve where no
    step bought anything.

    This is the backend half: the number is on the wire, from the config the
    sweep actually ran under. The panel half (a flat-curve message that names
    the standard doing the work) is in `FrontierPanel.test.tsx`.

    Bite (verified): drop `reserve_margin` from the record.
    """
    import dataclasses
    import time

    n = _profiled(periods=4)
    n.generators_t.p_max_pu.drop(columns=["farm"], inplace=True)
    install_network(n)
    st = session_state(client)
    st["solver_config"] = dataclasses.replace(
        st["solver_config"], voll=1000.0, reserve_margin=0.15)

    r = client.post("/api/results/frontier", json={"targets_permyriad": [50.0]})
    assert r.status_code == 200, r.text
    body = None
    for _ in range(600):
        body = client.get("/api/results/frontier").json()
        if body.get("status") in ("done", "failed", "aborted"):
            break
        time.sleep(0.05)
    assert body is not None
    assert body["reserve_margin"] == pytest.approx(0.15), body

    # …and it is null, not absent and not 0, when no standard is in force.
    st["solver_config"] = dataclasses.replace(
        st["solver_config"], reserve_margin=None)
    r = client.post("/api/results/frontier", json={"targets_permyriad": [50.0]})
    assert r.status_code == 200, r.text
    for _ in range(600):
        body = client.get("/api/results/frontier").json()
        if body.get("status") in ("done", "failed", "aborted"):
            break
        time.sleep(0.05)
    assert "reserve_margin" in body and body["reserve_margin"] is None, body

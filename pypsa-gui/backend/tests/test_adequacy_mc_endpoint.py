"""
GET/POST /results/mc (Phase 6 Task 6): the sequential-MC study surface.

Driven over the REAL HTTP stack with an authenticated TestClient — never by
calling the handler functions directly. That distinction has already earned
its keep once on this router: a direct-call test cannot see a missing
``HTTPException`` import (the name only resolves when the raise executes
through the app's exception middleware), and it bypasses auth, CSRF and the
per-session project context that the worker thread's state writes have to
agree with.

The MC endpoint is asynchronous by construction — a ten-asset ELCC study is
minutes of arithmetic — so every test here drives POST → poll → payload, with
a tiny network (2 units, 24 h) and small draw counts at a FIXED seed so the
assertions are stable rather than merely usually true.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

import pandas as pd
import pypsa
import pytest

from services.adequacy.elcc import MAX_ELCC_ASSETS
from services.adequacy.mc import MAX_DRAWS, MC_WARNING_V1

# Small enough that a full ELCC bisection is a couple of seconds, large enough
# that the LOLE of this fixture is many multiples of the resolution floor.
DRAWS = 32
SEED = 7
# A CoV no 32-draw batch will miss, so the adaptive loop stops after one batch
# and `n_samples` is a pinned constant rather than a race with the sampler.
COV = 1.0

ELCC_ROW_KEYS = {
    "kind", "name", "nameplate_mw", "elcc_mw", "elcc_share",
    "status", "reason", "baseline_lole_h", "baseline_lole_ci",
}


def _network() -> pypsa.Network:
    """100 MW of flat load against two 60 MW units at q = 0.1.

    Either unit alone is 40 MW short, so P(shortfall) per hour is
    1 − 0.9² = 0.19 and the horizon LOLE (~4.6 h) sits two orders of magnitude
    above the 32-draw resolution floor (1/32 h) — i.e. ELCC is identifiable.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=24, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=100.0)
    for g in ("g1", "g2"):
        n.add("Generator", g, bus="b", carrier="gas", p_nom=60.0,
              outage_rate_value=0.1, outage_rate_basis="EFORd",
              mttr_hours=24.0)
    return n


def _barren_network() -> pypsa.Network:
    """The same shape with NO resolvable occurrence data — nothing to sample."""
    n = _network()
    for g in ("g1", "g2"):
        n.generators.at[g, "outage_rate_value"] = float("nan")
        n.generators.at[g, "carrier"] = "unobtainium"   # no library default
    return n


def _inconsistent_pair_network() -> pypsa.Network:
    """q = 0.95 with a 1 h MTTR — implied MTTF ≈ 0.05 h, under one timestep.

    ``transition_probs`` refuses the pair rather than clamping it (spec §2.2):
    a clamped chain simulates a DIFFERENT unavailability than the one entered,
    in the optimistic direction, with nothing said.
    """
    n = _network()
    n.generators.at["g1", "outage_rate_value"] = 0.95
    n.generators.at["g1", "mttr_hours"] = 1.0
    return n


@contextmanager
def _fake_running(state: dict, key: str):
    """Park a REAL live daemon thread under ``state[key]``.

    The 409 guards test ``thread.is_alive()``, not just the status string —
    a sentinel dict alone would prove nothing about the guard that ships.
    """
    release = threading.Event()
    t = threading.Thread(target=release.wait, daemon=True, name=f"fake-{key}")
    t.start()
    state[key] = {"status": "running", "result": None, "rows": [], "points": [],
                  "error": None, "started_at": time.time(), "thread": t}
    try:
        yield
    finally:
        release.set()
        t.join(timeout=5)
        state.pop(key, None)


def _poll(client, timeout: float = 300.0) -> dict:
    deadline = time.time() + timeout
    body: dict | None = None
    while time.time() < deadline:
        r = client.get("/api/results/mc")
        assert r.status_code == 200, r.text
        body = r.json()
        if body.get("status") in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"the MC run never finished: {body!r}")


# ── ★ 204 before any run ──────────────────────────────────────────────────

def test_get_mc_is_204_before_any_run(client, install_network):
    """★ Bite: serve 200 with an empty body instead of 204.

    204 is the "never run" signal the panel branches on; a 200 with a falsy
    body renders as a completed study with no numbers in it.
    """
    install_network(_network())
    r = client.get("/api/results/mc")
    assert r.status_code == 204, r.text
    assert r.content == b""


# ── ★ the run itself ──────────────────────────────────────────────────────

def test_post_runs_in_a_worker_and_serves_the_sibling_payload(
        client, install_network, session_state):
    """★ Bite: leak the worker-thread handle into the GET payload.

    ``_state["mc"]`` carries a Thread; serving the dict unfiltered either
    500s on serialization or ships a non-JSON object to the panel.

    Also pins "VoLL NOT required" (spec §4): the installed solver config
    carries voll = 0, on which the sweep and the frontier both 422 — the MC
    reports hours and MWh, not euros, so a missing VoLL cannot make it wrong.
    """
    install_network(_network())
    cfg = session_state(client).get("solver_config")
    assert float(getattr(cfg, "voll", 0.0) or 0.0) == 0.0

    r = client.post("/api/results/mc",
                    json={"draws": DRAWS, "seed": SEED, "cov_target": COV})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "running"

    body = _poll(client)
    assert body["status"] == "done", body
    assert "thread" not in body
    assert body["error"] is None
    assert isinstance(body.get("started_at"), float)

    res = body["result"]
    assert res["engine"] == "mc"
    assert res["fidelity"] == "sequential_mc"
    assert res["warning"] == MC_WARNING_V1
    assert res["elcc"] == []

    m = res["metrics"]
    assert set(m) >= {"lole_hours", "lole_ci", "eue_mwh", "eue_ci",
                      "by_period", "n_samples", "converged", "time_basis",
                      "horizon_years", "resolution_floor_h", "warning"}
    # CIs cross the wire as 2-element JSON arrays, not tuples.
    assert isinstance(m["lole_ci"], list) and len(m["lole_ci"]) == 2
    assert isinstance(m["eue_ci"], list) and len(m["eue_ci"]) == 2
    assert m["lole_ci"][0] <= m["lole_hours"] <= m["lole_ci"][1]
    assert m["eue_ci"][0] <= m["eue_mwh"] <= m["eue_ci"][1]
    assert m["n_samples"] == DRAWS
    assert m["converged"] is True
    assert m["time_basis"] == "hours_per_horizon"
    assert m["horizon_years"] == pytest.approx(24.0 / 8760.0)
    assert m["resolution_floor_h"] == pytest.approx(1.0 / DRAWS)
    assert set(m["by_period"]) == {"ALL"}
    # 2 × 60 MW at q=0.1 against 100 MW: P(short) = 1 − 0.81 per hour, so the
    # 24 h horizon LOLE is ~4.6 h. A wide band — the point is that it is
    # neither zero nor the whole horizon.
    assert 2.0 < m["lole_hours"] < 8.0
    assert m["eue_mwh"] > 0.0


# ── ★ the mutual-exclusion mesh ───────────────────────────────────────────

def test_409_mesh_between_mc_frontier_sweep_and_solve(
        client, install_network, session_state):
    """★ Bite: drop the mc check from ``post_frontier``.

    The mesh has to be registered in BOTH directions: an MC study and a
    frontier study each hold the same network for minutes, and the loser of an
    unguarded race silently reads a network the other one is mutating.
    """
    install_network(_network())
    st = session_state(client)

    with _fake_running(st, "mc"):
        r = client.post("/api/results/mc", json={})
        assert r.status_code == 409, r.text
        assert "MC" in r.json()["detail"] or "mc" in r.json()["detail"]
        # …and the OTHER two studies must now refuse while mc runs. Both would
        # otherwise fall through to their VoLL 422, which is why the assert
        # names 409 explicitly.
        rf = client.post("/api/results/frontier", json={})
        assert rf.status_code == 409, rf.text
        rs = client.post("/api/results/fmea_sweep", json={"scenarios": []})
        assert rs.status_code == 409, rs.text

    with _fake_running(st, "frontier"):
        r = client.post("/api/results/mc", json={})
        assert r.status_code == 409, r.text

    with _fake_running(st, "fmea_sweep"):
        r = client.post("/api/results/mc", json={})
        assert r.status_code == 409, r.text

    st["status"] = "running"
    try:
        r = client.post("/api/results/mc", json={})
        assert r.status_code == 409, r.text
    finally:
        st["status"] = "idle"


# ── ★ nothing to sample ───────────────────────────────────────────────────

def test_422_when_no_unit_carries_occurrence_data(client, install_network):
    """★ Bite: skip the emptiness check.

    An empty fleet samples a constant zero capacity and reports the whole
    horizon as loss-of-load — a catastrophic-looking number produced by
    missing input data, not by the system.
    """
    install_network(_barren_network())
    r = client.post("/api/results/mc", json={"draws": 8, "cov_target": COV})
    assert r.status_code == 422, r.text
    assert "nothing to sample" in r.json()["detail"]


# ── the §2.2 inconsistent pair, surfaced synchronously ────────────────────

def test_422_for_an_inconsistent_unavailability_mttr_pair(
        client, install_network):
    """Bite: drop the ``transition_probs`` pre-flight loop in ``post_mc``.

    The pair is a property of the SNAPSHOT alone, so there is no reason to
    discover it a batch into a background run and report it as a failed study
    (the bitten variant returns 200/"running" and the 422 becomes an error
    string minutes later). The message has to name the unit — "inconsistent"
    with no name is unactionable in a fleet of 32.
    """
    install_network(_inconsistent_pair_network())
    r = client.post("/api/results/mc", json={"draws": 8, "cov_target": COV})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "g1" in detail and "MTTF" in detail
    assert client.get("/api/results/mc").status_code == 204


# ── ★ request validation ──────────────────────────────────────────────────

def test_404_for_an_unknown_elcc_asset_at_post_time(client, install_network):
    """★ Bite: drop the synchronous ``_resolve`` pre-check in ``post_mc``.

    Unknown assets are caught SYNCHRONOUSLY: a typo must not cost the user
    seven minutes of spinner before failing. Without the pre-check the POST
    returns 200/"running" and the KeyError surfaces minutes later as a failed
    study, which is why this test asserts the 404 AND that no run was started.
    """
    install_network(_network())
    r = client.post("/api/results/mc", json={
        "draws": 8, "cov_target": COV,
        "elcc_assets": [{"kind": "generator", "name": "not_a_generator"}]})
    assert r.status_code == 404, r.text
    assert "not_a_generator" in r.json()["detail"]
    # …and nothing was started.
    assert client.get("/api/results/mc").status_code == 204


def test_422_when_more_than_the_cap_of_elcc_assets_is_asked_for(
        client, install_network):
    """★ Bite: drop the ELCC-asset cap enforcement."""
    install_network(_network())
    assets = [{"kind": "generator", "name": "g1"}] * (MAX_ELCC_ASSETS + 1)
    r = client.post("/api/results/mc",
                    json={"draws": 8, "cov_target": COV, "elcc_assets": assets})
    assert r.status_code == 422, r.text
    assert str(MAX_ELCC_ASSETS) in r.json()["detail"]
    assert client.get("/api/results/mc").status_code == 204


def test_422_when_draws_exceed_the_engine_cap(client, install_network):
    """★ Bite: drop the draws cap enforcement."""
    install_network(_network())
    r = client.post("/api/results/mc", json={"draws": MAX_DRAWS + 1})
    assert r.status_code == 422, r.text
    assert str(MAX_DRAWS) in r.json()["detail"]
    assert client.get("/api/results/mc").status_code == 204


def test_422_for_an_unknown_elcc_kind(client, install_network):
    install_network(_network())
    r = client.post("/api/results/mc", json={
        "draws": 8, "cov_target": COV,
        "elcc_assets": [{"kind": "transformer", "name": "g1"}]})
    assert r.status_code == 422, r.text


# ── the ELCC happy path ───────────────────────────────────────────────────

def test_elcc_row_carries_exactly_the_nine_contract_keys(
        client, install_network):
    install_network(_network())
    r = client.post("/api/results/mc", json={
        "draws": DRAWS, "seed": SEED, "cov_target": COV,
        "elcc_assets": [{"kind": "generator", "name": "g1"}]})
    assert r.status_code == 200, r.text

    body = _poll(client)
    assert body["status"] == "done", body
    rows = body["result"]["elcc"]
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == ELCC_ROW_KEYS
    assert row["kind"] == "generator"
    assert row["name"] == "g1"
    assert row["status"] == "ok"
    assert row["reason"] is None
    assert row["nameplate_mw"] == pytest.approx(60.0)
    # A last-in credit lives inside the bracket [0, nameplate] by construction.
    assert 0.0 <= row["elcc_mw"] <= 60.0
    assert row["elcc_share"] == pytest.approx(row["elcc_mw"] / 60.0)
    assert row["baseline_lole_h"] == pytest.approx(
        body["result"]["metrics"]["lole_hours"])
    assert len(row["baseline_lole_ci"]) == 2

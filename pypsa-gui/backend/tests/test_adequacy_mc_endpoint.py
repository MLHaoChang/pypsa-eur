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


def _mixed_network() -> pypsa.Network:
    """One asset of EACH ELCC kind, plus the three things that must not appear.

    * ``g1`` / ``g2`` — explicit occurrence data → occurrence-bearing units
      (kind "generator"), 60 MW each so the tie-break by name is exercised.
    * ``batt`` — a StorageUnit at the electrical bus → kind "storage_unit".
    * ``wind`` — a ``p_max_pu`` SERIES and no occurrence data (carrier "wind"
      is deliberately absent from ``occurrence.CARRIER_DEFAULTS``) → must-take,
      kind "vre" at its PEAK contribution 0.8 × 200 = 160 MW.
    * ``__voll_b`` — the LP's VoLL slack. A slack is not an asset, and
      ``_resolve`` would 404 on it: it is neither in the sampled fleet nor in
      ``vre_profiles``.
    * ``dead`` — must-take with an all-zero profile: peak contribution 0, so
      there is no credit to measure and the bracket [0, 0] is degenerate.
    * ``h2gen`` — a generator on a NON-electrical bus, out of scope entirely.
    """
    n = _network()
    n.add("Carrier", "wind")
    n.add("Carrier", "load_shedding")
    n.add("Carrier", "battery")
    n.add("Carrier", "H2")

    # Mostly becalmed, one windy hour: the residual stays deep enough for the
    # fleet to shed, so the baseline LOLE is identifiable rather than zero.
    profile = pd.Series(0.1, index=n.snapshots)
    profile.iloc[12] = 0.8
    n.add("Generator", "wind", bus="b", carrier="wind", p_nom=200.0,
          p_max_pu=profile)
    n.add("Generator", "dead", bus="b", carrier="wind", p_nom=90.0,
          p_max_pu=pd.Series(0.0, index=n.snapshots))
    n.add("Generator", "__voll_b", bus="b", carrier="load_shedding",
          p_nom=9999.0, marginal_cost=4000.0)

    n.add("StorageUnit", "batt", bus="b", carrier="battery", p_nom=50.0,
          max_hours=4.0, efficiency_store=0.95, efficiency_dispatch=0.95)

    n.add("Bus", "h2bus", carrier="H2")
    n.add("Generator", "h2gen", bus="h2bus", carrier="gas", p_nom=500.0,
          outage_rate_value=0.05, outage_rate_basis="EFORd", mttr_hours=12.0)
    return n


def _no_candidates_network() -> pypsa.Network:
    """Load and a slack only — nothing an ELCC study could ever price."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=6, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "load_shedding")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "__voll_b", bus="b", carrier="load_shedding",
          p_nom=9999.0, marginal_cost=4000.0)
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


# ── GET /results/mc/elcc_candidates — what the picker may ask for ──────────
#
# This endpoint exists for ONE reason: `elcc_assets` was API-only, so a user
# could not request an ELCC study from the UI without guessing asset names and
# kinds out of the network editor. Its whole contract is AGREEMENT — every
# candidate it lists must be one `POST /results/mc` accepts, because a picker
# that offers a name the run then 404s on is worse than no picker at all.

CANDIDATE_KEYS = {"kind", "name", "nameplate_mw"}


def _candidates(client) -> dict:
    r = client.get("/api/results/mc/elcc_candidates")
    assert r.status_code == 200, r.text
    return r.json()


def test_elcc_candidates_enumerate_the_three_kinds(client, install_network):
    """★ Bite: drop the "vre" branch from the enumeration.

    The must-take generators are the half of the fleet a user is MOST likely
    to want a credit for — a wind farm's ELCC is the number the whole study
    exists to produce — and they are also the half no other surface lists
    (they are, by construction, absent from the COPT unit table). With the
    branch dropped the picker silently offers thermal and storage only, and
    the omission is invisible: the response is still well-formed.

    Membership is pinned in BOTH directions here: the three assets that must
    appear, and the three that must not (a slack, a zero-profile must-take,
    and a generator at a non-electrical bus).
    """
    install_network(_mixed_network())
    body = _candidates(client)
    assets = body["assets"]
    for a in assets:
        assert set(a) == CANDIDATE_KEYS, a
    by_name = {a["name"]: a for a in assets}

    assert by_name["g1"]["kind"] == "generator"
    assert by_name["g1"]["nameplate_mw"] == pytest.approx(60.0)
    assert by_name["g2"]["kind"] == "generator"

    assert by_name["batt"]["kind"] == "storage_unit"
    assert by_name["batt"]["nameplate_mw"] == pytest.approx(50.0)

    # The vre nameplate is the PEAK must-take contribution over the horizon
    # (profile × capacity), exactly `elcc._resolve`'s bracket top — not the
    # installed 200 MW, which the bisection never prices.
    assert by_name["wind"]["kind"] == "vre"
    assert by_name["wind"]["nameplate_mw"] == pytest.approx(0.8 * 200.0)

    assert "__voll_b" not in by_name       # a slack is not an asset
    assert "dead" not in by_name           # zero peak → no credit to measure
    assert "h2gen" not in by_name          # non-electrical bus, out of scope
    assert set(by_name) == {"g1", "g2", "batt", "wind"}


def test_elcc_candidates_echo_the_cap_and_sort_by_nameplate_then_name(
        client, install_network):
    """The picker's cap comes FROM THE PAYLOAD, and the order is pinned.

    Descending nameplate puts the assets whose credit moves the answer at the
    top of a checkbox list the user may only tick ten of; ties break by name
    so the list is stable across requests rather than dependent on the
    component frames' insertion order.
    """
    install_network(_mixed_network())
    body = _candidates(client)
    assert body["max_assets"] == MAX_ELCC_ASSETS
    assert [(a["name"], a["nameplate_mw"]) for a in body["assets"]] == [
        ("wind", 160.0), ("g1", 60.0), ("g2", 60.0), ("batt", 50.0)]


def test_elcc_candidates_is_200_with_an_empty_list_when_nothing_qualifies(
        client, install_network):
    """200 + `[]`, never 204.

    An empty candidates list is an ANSWER — "this network has no asset whose
    capacity credit could be measured" — and the panel renders an explanatory
    line from it. A 204 collapses that into the same "no data" the client uses
    for "never fetched", and the picker would render a bare empty box.
    """
    install_network(_no_candidates_network())
    r = client.get("/api/results/mc/elcc_candidates")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assets"] == []
    assert body["max_assets"] == MAX_ELCC_ASSETS


def test_a_slack_generator_cannot_be_priced_as_vre(client, install_network):
    """kind="vre" must reject names that are not must-take generators.

    The hole this pins (found during the candidates task): ``snapshot_inputs``
    used to build a ``vre_profiles`` entry for WHATEVER names the request
    asked, with no slack/electrical/must-take test — so ``{"kind": "vre",
    "name": "__voll_b"}`` was accepted and a 9999 MW LP slack was priced as
    wind: its "profile" (static p_max_pu × p_nom) un-netted into the residual
    as if it were generation the system could count on. The candidates
    endpoint never OFFERS it, but the run must also refuse it when asked
    directly — agreement has to hold in both directions or the 404 is just a
    UI convention.

    Bite (verified): restore the unfiltered ``for name in vre_assets`` loop in
    ``snapshot_inputs`` — this POST then returns 200 and the test fails.
    """
    install_network(_mixed_network())
    r = client.post("/api/results/mc", json={
        "draws": 8,
        "elcc_assets": [{"kind": "vre", "name": "__voll_b"}]})
    assert r.status_code == 404, r.text
    assert "__voll_b" in r.json()["detail"]

    # The non-electrical generator is equally not must-take on the electrical
    # system, whatever its p_max_pu says.
    r2 = client.post("/api/results/mc", json={
        "draws": 8,
        "elcc_assets": [{"kind": "vre", "name": "h2gen"}]})
    assert r2.status_code == 404, r2.text


def test_every_candidate_is_accepted_by_the_run_that_prices_it(
        client, install_network):
    """★ Bite: let the enumeration also list a slack generator (any asset
    `_resolve` rejects — a slack is neither in the sampled fleet nor in
    `vre_profiles`, so it is a KeyError → 404).

    THE reason this endpoint exists. The candidates are posted back VERBATIM
    as `elcc_assets` and the study must accept every one of them and price it:
    a picker that offers an asset the run refuses turns a two-click study into
    a 404 the user cannot act on, and the failure is silent until they try.

    "Prices it" means the row RESOLVES — it comes back with the nine contract
    keys and a legal status. A refusal status ("unidentifiable") is a
    resolved row; a failed run, or a missing row, is not.
    """
    install_network(_mixed_network())
    body = _candidates(client)
    assets = [{"kind": a["kind"], "name": a["name"]} for a in body["assets"]]
    assert 0 < len(assets) <= body["max_assets"]

    r = client.post("/api/results/mc", json={
        "draws": DRAWS, "seed": SEED, "cov_target": COV, "elcc_assets": assets})
    assert r.status_code == 200, r.text

    done = _poll(client)
    assert done["status"] == "done", done
    assert done["error"] is None
    rows = done["result"]["elcc"]
    assert [(r_["kind"], r_["name"]) for r_ in rows] == [
        (a["kind"], a["name"]) for a in assets]
    for row in rows:
        assert set(row) == ELCC_ROW_KEYS, row
        # Phase 12e widened this union with `aborted` (a bisection stopped by
        # `/results/mc/abort`), and this set was not widened with it
        # (shipped-code review, finding 18). It cannot trip on this fixture —
        # nothing aborts here — but a set that silently lags the union is
        # exactly how the NEXT status ships unasserted.
        assert row["status"] in {"ok", "unidentifiable", "not_bracketed",
                                 "aborted"}
        assert row["nameplate_mw"] > 0.0

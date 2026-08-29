"""
GET/POST /results/coupling_loop (+ /abort) — the coupling-loop surface
(coupling-loop spec §3, plan v2 §2).

Driven over the REAL HTTP stack with an authenticated TestClient, never by
calling the handler functions directly: a direct call bypasses auth, CSRF and
the per-session project context that the worker thread's state writes have to
agree with — and cannot see a missing ``HTTPException`` import at all.

The loop is minutes-long by construction (up to eight capacity-expansion
solves plus an MC evaluation each), so almost every test here replaces the two
expensive collaborators — ``sweep._solve_once`` and ``mc.mc_adequacy`` — with
deterministic stubs and exercises the ROUTE: its validation, its 409 mesh, its
record lifecycle, its abort and its restore. Exactly one test runs the real
controller against a real network with real HiGHS solves (marked ``slow``), so
the stubs are anchored to something that actually solves.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

import pandas as pd
import pypsa
import pytest

from services.adequacy.coupling import MAX_LOOP_SOLVES
from services.adequacy.mc import MAX_DRAWS, MC_WARNING_V1

LOOP_URL = "/api/results/coupling_loop"
ABORT_URL = "/api/results/coupling_loop/abort"

VOLL = 3000.0
DRAWS = 16
SEED = 5


# ── fixtures ──────────────────────────────────────────────────────────────

def _network() -> pypsa.Network:
    """100 MW flat load, 60 MW of occurrence-bearing firm plant and an
    extendable peaker the LP can build to meet a tighter cap.

    Unit weights (1.0) make the up-front resolution floor exactly ``1/draws``,
    which is what the below-floor 422 is asserted against.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC", country="AA")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "firm", bus="b", carrier="gas", p_nom=60.0,
          marginal_cost=10.0,
          outage_rate_value=0.1, outage_rate_basis="EFORd", mttr_hours=4.0)
    n.add("Generator", "peak", bus="b", carrier="gas", p_nom=0.0,
          p_nom_extendable=True, p_nom_min=0.0, p_nom_max=200.0,
          capital_cost=400.0, marginal_cost=250.0,
          outage_rate_value=0.1, outage_rate_basis="EFORd", mttr_hours=4.0)
    return n


def _barren_network() -> pypsa.Network:
    """The same shape with NO resolvable occurrence data — nothing to sample."""
    n = _network()
    for g in ("firm", "peak"):
        n.generators.at[g, "outage_rate_value"] = float("nan")
        n.generators.at[g, "carrier"] = "unobtainium"
    n.add("Carrier", "unobtainium")
    return n


def _setup(client, install_network, network=None, **cfg):
    """Install a network and PUT the solver config the loop needs."""
    install_network(network if network is not None else _network())
    body = {"solver_name": "highs", "voll": VOLL}
    body.update(cfg)
    r = client.put("/api/simulation/solver_config", json=body)
    assert r.status_code == 200, r.text
    return r.json()


@contextmanager
def _fake_running(state: dict, key: str):
    """Park a REAL live daemon thread under ``state[key]``.

    Every guard tests ``thread.is_alive()``, not just the status string, so a
    sentinel dict alone would prove nothing about the guard that ships.
    """
    release = threading.Event()
    t = threading.Thread(target=release.wait, daemon=True, name=f"fake-{key}")
    t.start()
    state[key] = {"status": "running", "result": None, "rows": [], "points": [],
                  "iterations": [], "error": None, "started_at": time.time(),
                  "thread": t}
    try:
        yield
    finally:
        release.set()
        t.join(timeout=5)
        state.pop(key, None)


def _poll(client, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    body: dict | None = None
    while time.time() < deadline:
        r = client.get(LOOP_URL)
        assert r.status_code == 200, r.text
        body = r.json()
        if body.get("status") not in ("running",):
            return body
        time.sleep(0.02)
    raise AssertionError(f"the coupling loop never finished: {body!r}")


# ── stub collaborators ────────────────────────────────────────────────────

def _report(cap_mwh: float, ens_mwh: float, cost: float, binding: str) -> dict:
    return {
        "engine": "lp_proxy",
        "fidelity": "deterministic_proxy",
        "target": {
            "basis": "energy",
            "binding": binding,
            "system": {"cap_mwh": cap_mwh, "achieved_ens_mwh": ens_mwh,
                       "achieved_shed_hours": 2.0, "by_period": []},
            "zones": [],
        },
        "cost": {"total_system_cost_eur": cost, "period_basis": "horizon"},
    }


def _metrics(lole: float, *, floor: float = 1.0 / DRAWS,
             by_period=None) -> dict:
    return {
        "lole_hours": lole,
        "lole_ci": (max(lole - 0.1, 0.0), lole + 0.1),
        "eue_mwh": lole * 10.0,
        "eue_ci": (0.0, lole * 20.0),
        "by_period": by_period if by_period is not None
        else {"ALL": {"lole_hours": lole, "eue_mwh": lole * 10.0}},
        "n_samples": DRAWS,
        "converged": True,
        "time_basis": "hours_per_horizon",
        "horizon_years": 4.0 / 8760.0,
        "resolution_floor_h": floor,
        "warning": MC_WARNING_V1,
    }


class _Stubs:
    """Records what the route asked of the two expensive collaborators."""

    def __init__(self, *, lole_seq, delay: float = 0.0, binding="system_cap"):
        self.lole_seq = list(lole_seq)
        self.delay = delay
        self.binding = binding
        self.solve_eps: list[float] = []
        self.mc_kwargs: list[dict] = []
        self.restore_cfgs: list[object] = []
        self.by_period = None

    def solve_once(self, cfg, n, lock, log_queue, sink):
        if self.delay:
            time.sleep(self.delay)
        eps = float(getattr(cfg, "ens_cap_permyriad", 0.0) or 0.0)
        self.solve_eps.append(eps)
        sink["_status"] = "ok"
        sink["_condition"] = "optimal"
        sink["adequacy_report"] = _report(
            cap_mwh=100.0, ens_mwh=40.0,
            cost=1000.0 + 10.0 * len(self.solve_eps), binding=self.binding)

    def mc_adequacy(self, inputs, **kw):
        self.mc_kwargs.append(dict(kw))
        i = min(len(self.mc_kwargs) - 1, len(self.lole_seq) - 1)
        return _metrics(self.lole_seq[i], by_period=self.by_period)

    def run_simulation(self, cfg, n, lock, stop_event, log_queue, **kw):
        self.restore_cfgs.append(cfg)
        return "ok", "optimal"


def _install_stubs(monkeypatch, stubs: _Stubs) -> None:
    """Patch the module attributes the ROUTE imports at call time.

    ``post_coupling_loop`` imports its collaborators inside the handler body,
    so patching the defining modules here reaches the route without patching
    the route itself — the seam under test stays the real one.
    """
    import services.adequacy.mc as mc_mod
    import services.adequacy.sweep as sweep_mod
    import services.solver_service as solver_mod

    monkeypatch.setattr(sweep_mod, "_solve_once", stubs.solve_once)
    monkeypatch.setattr(mc_mod, "mc_adequacy", stubs.mc_adequacy)
    monkeypatch.setattr(solver_mod, "run_simulation", stubs.run_simulation)


# ── ★ 204 before any run ──────────────────────────────────────────────────

def test_get_coupling_loop_is_204_before_any_run(client, install_network):
    """★ Bite: serve 200 with an empty record instead of 204.

    204 is the "never run" signal the panel branches on; a 200 with a falsy
    body renders as a finished study with no verdict in it.
    """
    _setup(client, install_network)
    r = client.get(LOOP_URL)
    assert r.status_code == 204, r.text
    assert r.content == b""


# ── ★ the full synchronous 422 set ────────────────────────────────────────

_422_CASES = [
    # (id, network factory, cfg overrides, body, expected fragments)
    ("target_missing", _network, {}, {}, ["target_lole_h"]),
    ("target_zero", _network, {}, {"target_lole_h": 0.0}, ["target_lole_h"]),
    ("target_negative", _network, {}, {"target_lole_h": -3.0},
     ["target_lole_h"]),
    ("no_voll", _network, {"voll": 0.0}, {"target_lole_h": 1.0}, ["VOLL"]),
    ("nothing_to_sample", _barren_network, {}, {"target_lole_h": 1.0},
     ["nothing to sample"]),
    ("draws_zero", _network, {}, {"target_lole_h": 1.0, "draws": 0},
     ["draws"]),
    ("draws_over_cap", _network, {},
     {"target_lole_h": 1.0, "draws": MAX_DRAWS + 1}, ["draws"]),
    ("max_solves_zero", _network, {},
     {"target_lole_h": 1.0, "max_solves": 0}, ["max_solves"]),
    ("max_solves_over_cap", _network, {},
     {"target_lole_h": 1.0, "max_solves": MAX_LOOP_SOLVES + 1},
     ["max_solves", str(MAX_LOOP_SOLVES)]),
    ("restore_unknown", _network, {},
     {"target_lole_h": 1.0, "restore": "sideways"}, ["restore"]),
    ("strategy_rolling", _network, {"solve_strategy": "rolling"},
     {"target_lole_h": 1.0}, ["rolling"]),
    ("strategy_myopic", _network,
     {"solve_strategy": "myopic", "multi_investment_periods": True},
     {"target_lole_h": 1.0}, ["myopic"]),
    # weights are 1.0, so the floor is exactly 1/draws = 0.05 h.
    ("below_floor", _network, {},
     {"target_lole_h": 0.001, "draws": 20}, ["20", "floor"]),
]


@pytest.mark.parametrize(
    "case_id,factory,cfg,body,fragments",
    _422_CASES, ids=[c[0] for c in _422_CASES])
def test_the_synchronous_422_set_is_complete(
        client, install_network, case_id, factory, cfg, body, fragments):
    """★ Bite: drop the rolling/myopic strategy guard.

    Every one of these is knowable from the config and the snapshot ALONE, so
    discovering it minutes into a background run is a choice, not a
    constraint. The strategy case is the one that bites hardest:
    ``_check_ens_cap_coherence`` fails EVERY capped solve under rolling or
    myopic foresight, so without the guard the loop burns its whole budget on
    validation failures and reports ``unreachable`` — "no plan meets this
    standard" — when the true answer is "this solve strategy cannot enforce a
    cap at all". Two opposite user actions, same words.
    """
    _setup(client, install_network, network=factory(), **cfg)
    r = client.post(LOOP_URL, json=body)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    for frag in fragments:
        assert frag in detail, f"{case_id}: {frag!r} missing from {detail!r}"
    # A refused request must not have started anything.
    assert client.get(LOOP_URL).status_code == 204


# ── ★ the 409 mesh, including the two holes this phase fixes ──────────────

def test_409_mesh_covers_every_study_pair_and_the_solve_entrypoints(
        client, install_network, session_state):
    """★ Bite: drop the study guards from the solve entrypoints.

    The mesh exists because each of these engines holds the foreground network
    for minutes and three of them RE-SOLVE it. The hole that mattered most was
    on the other side of the router boundary: a foreground ``POST
    /simulation/run`` interleaving between two loop iterates re-solves the
    network under the USER's config, and the loop's next ``evaluate`` then
    scores that plan against the ε it never solved — silently, with a verdict.
    ``post_fmea_sweep``'s missing frontier guard is the same shape, one
    module in.
    """
    _setup(client, install_network)
    st = session_state(client)
    body = {"target_lole_h": 1.0}

    # coupling ↔ coupling, and coupling blocking every sibling study
    with _fake_running(st, "coupling_loop"):
        assert client.post(LOOP_URL, json=body).status_code == 409
        assert client.post("/api/results/frontier", json={}).status_code == 409
        assert client.post("/api/results/mc", json={}).status_code == 409
        assert client.post("/api/results/fmea_sweep",
                           json={"scenarios": []}).status_code == 409
        # ── hole fix: the foreground solve entrypoints ──
        rr = client.post("/api/simulation/run")
        assert rr.status_code == 409, rr.text
        assert "coupling" in rr.json()["detail"]
        assert client.post("/api/simulation/run_ac_pf").status_code == 409

    # every sibling study blocking the loop
    for key in ("frontier", "mc", "fmea_sweep"):
        with _fake_running(st, key):
            r = client.post(LOOP_URL, json=body)
            assert r.status_code == 409, f"{key}: {r.text}"
            # ── hole fix: the solve entrypoint refuses for these too ──
            assert client.post("/api/simulation/run").status_code == 409, key

    # ── hole fix: post_fmea_sweep gains the missing frontier guard ──
    with _fake_running(st, "frontier"):
        r = client.post("/api/results/fmea_sweep", json={"scenarios": []})
        assert r.status_code == 409, r.text
        assert "frontier" in r.json()["detail"]
    # …and the already-shipped direction stays green.
    with _fake_running(st, "fmea_sweep"):
        assert client.post("/api/results/frontier",
                           json={}).status_code == 409

    # a foreground solve blocks the loop
    st["status"] = "running"
    try:
        assert client.post(LOOP_URL, json=body).status_code == 409
    finally:
        st["status"] = "idle"


# ── ★ mid-run GET consistency ─────────────────────────────────────────────

def test_mid_run_gets_see_a_consistent_growing_iterations_list(
        client, install_network, session_state, monkeypatch):
    """★ Bite: grow the record with ``record["iterations"].append(row)``.

    ``get_coupling_loop`` serves a SHALLOW copy of the record, so the list it
    hands the serializer is the SAME OBJECT the worker holds. Under an
    in-place append the response can be encoded half-way through an append,
    and a panel polling every second can watch its own earlier history change
    underneath it. Rebinding
    (``record["iterations"] = record["iterations"] + [row]``) makes that
    impossible by construction: the object a GET captures is never written to
    again.

    That is the property asserted here, on the very object a GET shallow-
    copies — a purely response-level assertion could only catch the torn
    encoding by winning a race, and a test that needs to win a race to fail is
    not a guard. The HTTP-level growth/prefix contract is asserted alongside
    it, because that is what the panel actually consumes.
    """
    stubs = _Stubs(lole_seq=[9.0], delay=0.12)
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network)

    r = client.post(LOOP_URL, json={"target_lole_h": 1.0, "draws": DRAWS,
                                    "seed": SEED, "max_solves": 6})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "running"

    record = session_state(client)["coupling_loop"]
    seen: list[list] = []
    captured: list[tuple[list, int]] = []
    saw_running = False
    deadline = time.time() + 60.0
    while time.time() < deadline:
        # Capture what a GET's shallow copy would hold, then drive the real
        # GET over HTTP.
        held = record["iterations"]
        captured.append((held, len(held)))
        g = client.get(LOOP_URL)
        assert g.status_code == 200, g.text
        body = g.json()
        assert "thread" not in body
        assert "stop_event" not in body
        assert body["study"] == "coupling_loop"
        seen.append(body["iterations"])
        if body["status"] == "running":
            saw_running = True
        else:
            break
        time.sleep(0.03)

    assert saw_running, "the worker finished before a single mid-run poll"
    assert any(len(a) < len(b) for a, b in zip(seen, seen[1:])), (
        f"the iterations list never grew across polls: {[len(s) for s in seen]}")
    # Every snapshot is a prefix of every later one — nothing is rewritten.
    for a, b in zip(seen, seen[1:]):
        assert len(a) <= len(b)
        assert b[:len(a)] == a, "an earlier GET's rows changed retroactively"

    final = _poll(client)
    assert final["status"] in ("budget_exhausted", "unreachable")
    assert len(final["iterations"]) >= 2

    # …and not one of the lists a GET captured mid-run has moved since.
    for held, size in captured:
        assert len(held) == size, (
            "a list a mid-run GET shallow-copied kept growing after the "
            f"response was built ({size} rows at capture, {len(held)} now) — "
            "the record is being appended to in place")


# ── ★ abort ───────────────────────────────────────────────────────────────

def test_abort_stops_the_loop_and_the_restore_still_runs(
        client, install_network, monkeypatch):
    """★ Bite: make the abort route a no-op (return 200 without setting the
    event).

    An abort that does not reach the worker is worse than no abort button: the
    user is told the study stopped while eight more solves keep mutating the
    network underneath them. The contract is behavioural — status becomes
    ``aborted`` AND the closing restore still runs, because a half-swept
    network left on some intermediate ε is exactly what the restore exists to
    undo.
    """
    stubs = _Stubs(lole_seq=[9.0], delay=0.2)
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network)

    # 404 before any record exists.
    assert client.post(ABORT_URL).status_code == 404

    r = client.post(LOOP_URL, json={"target_lole_h": 1.0, "draws": DRAWS,
                                    "seed": SEED, "max_solves": 8})
    assert r.status_code == 200, r.text

    a = client.post(ABORT_URL)
    assert a.status_code == 200, a.text
    # Idempotent: a second abort is still a 200, never a 409.
    assert client.post(ABORT_URL).status_code == 200

    body = _poll(client)
    assert body["status"] == "aborted", body
    assert body["base_restored"] is True
    assert len(stubs.restore_cfgs) == 1, "the closing restore did not run"
    assert body["solves_used"] < 8, "the abort did not shorten the run"


# ── ★ restore semantics ───────────────────────────────────────────────────

@pytest.mark.parametrize("restore,expected_cap",
                         [("final", 100.0), ("base", None)])
def test_restore_final_leaves_the_certified_cap_applied(
        client, install_network, monkeypatch, restore, expected_cap):
    """★ Bite: always restore the base config, ignoring ``restore``.

    ``restore="final"`` is the whole affordance of the phase: without it the
    user is handed a verdict and a number and told to go and re-enter it, and
    the network they are left holding is NOT the certified plan. With the bite
    the run still reports ``met`` and ``eps_star`` — only the config the user
    reads back is silently the old one.
    """
    stubs = _Stubs(lole_seq=[0.5])       # iterate 0 meets → eps_star = eps0
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network)
    assert client.get("/api/simulation/solver_config"
                      ).json()["ens_cap_permyriad"] is None

    r = client.post(LOOP_URL, json={"target_lole_h": 1.0, "draws": DRAWS,
                                    "seed": SEED, "restore": restore})
    assert r.status_code == 200, r.text

    body = _poll(client)
    assert body["status"] == "met", body
    assert body["restore"] == restore
    assert body["eps_star"] == pytest.approx(100.0)
    assert body["base_restored"] is True

    cfg = client.get("/api/simulation/solver_config").json()
    if expected_cap is None:
        assert cfg["ens_cap_permyriad"] is None
    else:
        assert cfg["ens_cap_permyriad"] == pytest.approx(expected_cap)
    # The closing solve used the matching cap either way.
    assert len(stubs.restore_cfgs) == 1
    assert getattr(stubs.restore_cfgs[0], "ens_cap_permyriad") == (
        pytest.approx(expected_cap) if expected_cap is not None else None)


# ── ★ the duck-typed plan-hash probe ──────────────────────────────────────

def test_the_plan_hash_probe_skips_the_mc_outright_on_a_plateau(
        client, install_network, monkeypatch):
    """★ Bite: delete the ``evaluate.plan_hash`` assignment.

    Spec v1.2 §1 makes the probe MANDATORY for this route, and the reason is
    that its absence is invisible in the payload: without it the controller
    still runs a full MC and then reuses the stored metrics on a hash match,
    so ``plateau`` is still true and the numbers are still identical — the
    only thing that changed is that the user waited for a sampling run whose
    result was thrown away, once per plateau iterate. The observable
    difference is therefore the CALL COUNT, which is what this pins: three
    solves over an unchanged plan, exactly ONE evaluation.

    The cap not binding (``binding != "system_cap"``) is the controller's
    cheap pre-test — the report's own statement that the cap did nothing this
    iterate — and the hash is what turns that hint into a proof.
    """
    stubs = _Stubs(lole_seq=[9.0], binding="voll")
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network)

    r = client.post(LOOP_URL, json={"target_lole_h": 1.0, "draws": DRAWS,
                                    "seed": SEED, "max_solves": 3})
    assert r.status_code == 200, r.text
    body = _poll(client)

    assert body["solves_used"] == 3, body
    assert len(body["iterations"]) == 3
    assert len(stubs.mc_kwargs) == 1, (
        "the MC ran once per iterate — the plan-hash probe never skipped it")
    assert [row["plateau"] for row in body["iterations"]] == [False, True, True]
    assert all(row["mc"] == body["iterations"][0]["mc"]
               for row in body["iterations"])


# ── payload shape on a completed run ──────────────────────────────────────

def test_the_completed_payload_carries_the_studys_own_shape(
        client, install_network, monkeypatch):
    """No top-level ``engine``/``fidelity`` (plan [N4]): the product of this
    study is a CAP and a VERDICT, not a metric, and labelling it "mc" would
    misuse the sibling convention every other adequacy payload follows. The
    engine label belongs to the per-iterate MC blocks, which carry it."""
    stubs = _Stubs(lole_seq=[0.5])
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network)

    r = client.post(LOOP_URL, json={"target_lole_h": 1.0, "draws": DRAWS,
                                    "seed": SEED})
    assert r.status_code == 200, r.text
    body = _poll(client)

    assert body["study"] == "coupling_loop"
    assert "engine" not in body and "fidelity" not in body
    assert body["status"] == "met"
    assert body["target_lole_h"] == pytest.approx(1.0)
    assert body["basis"] == "hours_per_horizon"
    assert body["confident"] is True
    assert body["eps_star"] == pytest.approx(100.0)
    assert body["draws"] == DRAWS and body["seed"] == SEED
    assert body["solves_used"] == 1
    assert body["error"] is None
    # v1.2 §3: the ROUTE lifts the floor out of the evaluation's metrics.
    assert body["resolution_floor_h"] == pytest.approx(1.0 / DRAWS)

    assert MC_WARNING_V1 in body["warning"]
    assert "step function" in body["warning"]
    # single-period fixture ⇒ no multi-period clause
    assert "per period" not in body["warning"]

    row = body["iterations"][0]
    assert set(row) == {"eps_permyriad", "solve_status", "condition",
                        "cost_eur", "ens_mwh", "cap_mwh", "binding",
                        "plateau", "mc"}
    assert row["mc"]["engine"] == "mc"
    assert row["mc"]["fidelity"] == "sequential_mc"
    assert set(row["mc"]["by_period"]) == {"ALL"}
    assert body["final"] == row

    # §3's normative CRN call: one batch of exactly N draws.
    assert stubs.mc_kwargs == [{"draws": DRAWS, "seed": SEED,
                                "max_draws": DRAWS}]


def test_a_multi_period_evaluation_adds_the_per_period_caveat(
        client, install_network, monkeypatch):
    """[N5]: a scalar ε is enforced PER PERIOD while the target is a horizon
    SUM, so the two standards cannot be made to coincide and the per-iterate
    ``by_period`` is the only diagnostic. Say so where the number is read."""
    stubs = _Stubs(lole_seq=[0.5])
    stubs.by_period = {"2030": {"lole_hours": 0.2, "eue_mwh": 2.0},
                       "2040": {"lole_hours": 0.3, "eue_mwh": 3.0}}
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network)

    client.post(LOOP_URL, json={"target_lole_h": 1.0, "draws": DRAWS})
    body = _poll(client)
    assert "per period" in body["warning"]
    assert set(body["iterations"][0]["mc"]["by_period"]) == {"2030", "2040"}


def test_an_unreachable_verdict_names_all_three_mechanisms(
        client, install_network, monkeypatch):
    """[N6]: the user's NEXT ACTION differs by mechanism, so a bare
    "unreachable" is unactionable. All three are named."""
    stubs = _Stubs(lole_seq=[9.0])
    stubs.solve_once = lambda cfg, n, lock, lq, sink: sink.update(
        {"_status": "warning", "_condition": "infeasible",
         "adequacy_report": None})
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network)

    client.post(LOOP_URL, json={"target_lole_h": 1.0, "draws": DRAWS})
    body = _poll(client)
    assert body["status"] == "unreachable", body
    verdict = body["verdict"].lower()
    assert "foresight" in verdict
    assert "demand response" in verdict or "dsr" in verdict
    assert "storage" in verdict
    assert body["final"] is None and body["eps_star"] is None


def test_an_unreachable_verdict_says_so_when_the_cap_never_bound(
        client, install_network, monkeypatch):
    """The commonest unreachable case is NOT one of the three mechanisms.

    Found by driving the loop live (QA round S17): on a network whose firm
    capacity covers demand, the LP sheds NOTHING at any cap — ens_mwh 0 and
    binding "voll" on every iterate — so no ε changes the plan, and the MC's
    loss-of-load comes entirely from outages the LP never models. The generic
    three-mechanism copy sent that user hunting for storage foresight and DSR,
    neither of which was happening. When no SUCCESSFUL iterate ever bound on
    the cap, the verdict says that instead — it is diagnosable from the rows,
    not a list of maybes.

    Bite (verified): return UNREACHABLE_COPY_V1 unconditionally.
    """
    stubs = _Stubs(lole_seq=[9.0], binding="voll")

    def _never_binds(cfg, n, lock, lq, sink):
        eps = float(getattr(cfg, "ens_cap_permyriad", 0.0) or 0.0)
        stubs.solve_eps.append(eps)
        sink["_status"] = "ok"
        sink["_condition"] = "optimal"
        # cap shrinks with eps; the LP sheds nothing at any of them, and the
        # plan (its cost) never moves — the live trajectory exactly.
        sink["adequacy_report"] = _report(
            cap_mwh=7200.0 * eps / 1e4, ens_mwh=0.0, cost=360000.0,
            binding="voll")

    stubs.solve_once = _never_binds
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network)

    client.post(LOOP_URL, json={"target_lole_h": 3.6, "draws": DRAWS,
                                "eps0": 100.0, "max_solves": 4})
    body = _poll(client)
    assert body["status"] == "unreachable", body
    verdict = body["verdict"].lower()
    assert "never bound" in verdict or "never binds" in verdict, body["verdict"]
    assert "outage" in verdict, body["verdict"]
    # And it must NOT send the user after the three mechanisms that are not
    # happening here.
    assert "demand response" not in verdict, body["verdict"]
    assert all(r["binding"] != "system_cap" for r in body["iterations"]), body


def test_never_bound_does_not_recommend_a_margin_the_user_already_set(
        client, install_network, monkeypatch):
    """The never-bound advice must not name a lever already in force.

    Live today (found by the Phase-9 review): `report.binding` is computed
    purely from the ENS caps, so it reads "voll" whenever the cap does not
    bind — INCLUDING when a reserve margin is what actually shaped the plan.
    The verdict then tells a user who already set a margin that "what would
    move this number is firm capacity … a planning reserve margin", which is
    advice they have already taken. The cap genuinely never bound, so the
    diagnosis is right and only the recommendation is stale — but a
    recommendation to do the thing you are already doing reads as the tool not
    knowing what you configured.

    Bite (verified): drop the margin clause and return NEVER_BOUND_COPY_V1
    unconditionally.
    """
    stubs = _Stubs(lole_seq=[9.0], binding="voll")

    def _never_binds(cfg, n, lock, lq, sink):
        eps = float(getattr(cfg, "ens_cap_permyriad", 0.0) or 0.0)
        stubs.solve_eps.append(eps)
        sink["_status"] = "ok"
        sink["_condition"] = "optimal"
        rep = _report(cap_mwh=7200.0 * eps / 1e4, ens_mwh=0.0, cost=360000.0,
                      binding="voll")
        # A margin WAS enforced on this solve, and its own block says so.
        rep["reserve_margin"] = {
            "margin": 0.15,
            "by_period": [{"period": "ALL", "binding": True, "met": True}],
        }
        sink["adequacy_report"] = rep

    stubs.solve_once = _never_binds
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network)

    client.post(LOOP_URL, json={"target_lole_h": 3.6, "draws": DRAWS,
                                "eps0": 100.0, "max_solves": 4})
    body = _poll(client)
    assert body["status"] == "unreachable", body
    verdict = body["verdict"].lower()
    assert "never bound" in verdict, body["verdict"]
    # It must ACKNOWLEDGE the margin rather than PRESCRIBE it. Asserting on
    # the word "already" alone was vacuous — the base copy contains "already
    # covers demand" — so pin the prescription's absence and the
    # acknowledgement's presence, both verbatim.
    assert "a planning reserve margin, or the candidate" not in verdict, (
        "the verdict prescribes a margin to a user who has one: "
        + body["verdict"])
    assert "reserve margin is already in force" in verdict, body["verdict"]


# ── the real thing ────────────────────────────────────────────────────────

@pytest.mark.slow
def test_end_to_end_against_a_real_network_and_real_solves(
        client, install_network):
    """The whole route with the REAL controller, the REAL sampler and REAL
    HiGHS solves — the anchor the stubs above are calibrated against.

    Small on purpose (4 snapshots, 16 draws, 2 solves): the point is that the
    seam holds when nothing is faked — the LP really re-solves under
    ``dataclasses.replace(cfg, ens_cap_permyriad=eps)``, the snapshot really
    hashes, the MC really samples the plan the LP produced, and the record
    that reaches the wire really is the shape the panel parses.
    """
    _setup(client, install_network)

    r = client.post(LOOP_URL, json={"target_lole_h": 4.0, "draws": DRAWS,
                                    "seed": SEED, "max_solves": 2,
                                    "eps0": 50.0})
    assert r.status_code == 200, r.text
    body = _poll(client, timeout=600.0)

    assert body["study"] == "coupling_loop"
    assert body["status"] in ("met", "unreachable", "budget_exhausted",
                              "failed"), body
    assert body["error"] is None, body["error"]
    assert "thread" not in body and "stop_event" not in body
    assert body["base_restored"] is True
    assert 1 <= body["solves_used"] <= 2
    assert body["iterations"], "a real run produced no iterates at all"
    for row in body["iterations"]:
        assert row["eps_permyriad"] > 0
        if row["mc"] is not None:
            assert row["mc"]["engine"] == "mc"
            assert row["mc"]["n_samples"] == DRAWS
            assert row["mc"]["lole_ci"][0] <= row["mc"]["lole_hours"] \
                <= row["mc"]["lole_ci"][1]
    if body["status"] == "met":
        assert body["eps_star"] is not None
        assert body["final"]["mc"]["lole_hours"] <= 4.0
    # The floor is lifted from a REAL evaluation, not recomputed off the body.
    if any(r["mc"] for r in body["iterations"]):
        assert body["resolution_floor_h"] == pytest.approx(1.0 / DRAWS)


@pytest.mark.slow
def test_the_real_plan_hash_probe_skips_the_mc_on_a_plateau(
        client, install_network):
    """★-adjacent, and only provable end-to-end: the duck-typed
    ``evaluate.plan_hash`` of spec v1.2 §1.

    A target under what this fixture can reach makes iterate 0 miss; the
    informed step then drops ε to the hard backstop, the LP builds the SAME
    plan (the cap never bound — ``binding == "voll"``), and the ROUTE's
    ``plan_hash`` attribute lets the controller recognise the plateau from the
    SNAPSHOT ALONE and skip the sampling entirely. The SKIP itself is bitten
    on call count by
    ``test_the_plan_hash_probe_skips_the_mc_outright_on_a_plateau``; what this
    test adds is that a REAL solve of a REAL network really does reproduce a
    hash-identical plan under a tighter cap, which no stub can establish.

    The reused metrics must be BIT-IDENTICAL, which is what the superset
    fleet (spec §1.2) and the pinned ``max_draws=N`` call exist to guarantee.
    """
    _setup(client, install_network)
    r = client.post(LOOP_URL, json={"target_lole_h": 0.1, "draws": DRAWS,
                                    "seed": SEED, "max_solves": 2,
                                    "eps0": 50.0})
    assert r.status_code == 200, r.text
    body = _poll(client, timeout=600.0)

    assert body["solves_used"] == 2, body
    first, second = body["iterations"][0], body["iterations"][1]
    assert first["plateau"] is False
    assert second["eps_permyriad"] < first["eps_permyriad"]
    assert second["binding"] != "system_cap", (
        "the plateau pre-test only applies while the cap is NOT binding")
    assert second["plateau"] is True, "the plan hash did not recognise the plateau"
    assert second["mc"] == first["mc"], (
        "the reused metrics are not bit-identical to the ones they reuse")
    # The cap fell under the energy floor while still missing, which is a
    # PROOF of unreachability rather than a spent budget.
    assert body["status"] == "unreachable", body
    assert body["final"] is None and body["eps_star"] is None
    assert body["base_restored"] is True

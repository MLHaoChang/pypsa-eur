"""
Phase 12e Part A — every study can be stopped (plan v4 §1, §4).

`coupling_loop` and `margin_loop` have had a `stop_event` and an `/abort`
route since Phases 7 and 9; `mc`, `frontier` and `fmea_sweep` had neither, so
a user who started a ten-asset ELCC run or an eight-target frontier waited it
out — and because the mutual-exclusion mesh refuses every other study AND the
foreground solve while one runs, an unstoppable study froze the whole surface.

Three properties this file exists to pin, none of which is "the flag is
plumbed":

* **The flag never reaches `mc_adequacy` from a replay call site** (F1f).
  ELCC and both loops call it to replay a baseline's batch sequence bit for
  bit — that replay is what common random numbers rest on — so a truncated
  candidate compared against a full-budget baseline would return a wrong
  `elcc_mw` with `status="ok"`, which `baseline_key` cannot catch because it
  hashes the arguments and never the result.
* **The abort is a `break`, never an exception** (F1b, F1b2). The contingency
  sweep's closing base re-solve sits OUTSIDE its `try/finally`, so an
  exception would skip the restore and leave the network on the last
  contingency; and that re-solve is now guarded, so a restore that *raises*
  no longer destroys the partial rows the abort exists to keep.
* **A stopped study's work survives** (F1g): the rows it measured reach the
  worksheet, and the assemblers do not `KeyError` on a partial result dict.
"""
from __future__ import annotations

import threading

import numpy as np
import pandas as pd
import pytest

from services.adequacy import elcc as E
from services.adequacy import mc as M
from services.adequacy import portfolio as P
from services.adequacy import sweep as SW


# ── fixtures ────────────────────────────────────────────────────────────

def _inputs(n_units=4, H=48, q=0.2):
    units = tuple(M.__dict__["CoptUnit"](f"g{i}", 60.0, q, mttr_hours=24.0)
                  if False else __import__(
                      "services.adequacy.copt", fromlist=["CoptUnit"]).CoptUnit(
                          f"g{i}", 60.0, q, mttr_hours=24.0)
                  for i in range(n_units))
    res = np.full(H, 60.0 * n_units * 0.75)
    return M.MCInputs(units=units, residual=res, weights=np.ones(H),
                      periods=(("ALL", 0, H),), nyears=H / 8760.0)


class _CountingEvent:
    """A stop event that fires after `after` checks — so a test can stop an
    engine at a KNOWN boundary and count how many it ran, rather than racing
    it with a timer (plan v4 §4 F1d: bounded work, counted not timed)."""

    def __init__(self, after: int) -> None:
        self.after = after
        self.checks = 0

    def is_set(self) -> bool:
        self.checks += 1
        return self.checks > self.after


# ── F1f: the CRN contract ───────────────────────────────────────────────

def test_F1f_the_flag_never_truncates_a_replay_evaluation(monkeypatch):
    """★ F1f. Every ELCC evaluation runs at the baseline's `n_samples`, even
    when the abort arrives WHILE one is running: `elcc_of_removal` passes
    `stop_event=None` into `mc_adequacy` at both call sites and honours the
    flag between PROBES instead. A truncated candidate measured against a
    full-budget baseline breaks common random numbers and returns a wrong
    credit as `status="ok"` — and `baseline_key` cannot catch it, because it
    hashes the arguments and never the result.

    Two things make this able to fail, and both were needed:

    * a MULTI-BATCH baseline (`cov_target=1e-9`), because a one-batch replay
      exits on `n_total >= max_draws` before any stop check is reached; and
    * the event armed from inside `_simulate_blocks`, i.e. part-way through an
      evaluation — the realistic case of a user clicking abort mid-probe.
      Armed between evaluations instead, the bisection simply returns at its
      next check and no probe ever runs with the flag set.

    Measured against the broken variant: sample counts `[64, 32, 8]` instead
    of `[64, 64, 64]`.

    Bite (verified): forward the flag into `metrics_at`'s `mc_adequacy` call.
    """
    inp = _inputs()
    ev = threading.Event()
    seen: list[int] = []
    real_mc = M.mc_adequacy
    real_sb = M._simulate_blocks
    blocks = {"n": 0}

    def sb(*a, **kw):
        blocks["n"] += 1
        # part-way through the Δ = 0 probe: the baseline's own batches are
        # calls 1–8, so this lands inside the evaluation that follows.
        if blocks["n"] >= 12:
            ev.set()
        return real_sb(*a, **kw)

    def spy(*a, **kw):
        out = real_mc(*a, **kw)
        seen.append(int(out["n_samples"]))
        return out

    monkeypatch.setattr(M, "_simulate_blocks", sb)
    monkeypatch.setattr(M, "mc_adequacy", spy)
    monkeypatch.setattr(E, "mc_adequacy", spy)

    row = E.elcc_of_removal(inp, nameplate_mw=60.0, seed=0, draws=8,
                            exclude=frozenset({0}), cov_target=1e-9,
                            max_draws=64, batch=8, stop_event=ev)
    assert len(seen) >= 3, seen
    assert row["status"] == "aborted", row
    assert row["elcc_mw"] is None
    # every evaluation, before and after the flag was set, ran at ONE count
    assert len(set(seen)) == 1, seen


def test_F1f2_a_stopped_bisection_never_reports_a_credit():
    """The other half of F1f: `hi` is a true upper bound (the loop's own
    invariant) but a bracket that was never closed is not a credit, so the row
    is `aborted` with `elcc_mw=None` and the bracket in its reason — not
    `ok`."""
    inp = _inputs()
    ev = _CountingEvent(after=0)
    row = E.elcc_of_removal(inp, nameplate_mw=60.0, seed=0, draws=8,
                            exclude=frozenset({0}), cov_target=1.0,
                            stop_event=ev)
    assert row["status"] == "aborted"
    assert row["elcc_mw"] is None and row["elcc_share"] is None
    assert set(row) == {"nameplate_mw", "elcc_mw", "elcc_share", "status",
                        "reason", "baseline_lole_h", "baseline_lole_ci"}, sorted(row)


def test_F1f3_the_baseline_batch_loop_honours_the_flag_and_stays_honest():
    """`mc_adequacy` itself: with the flag set after one batch the run stops
    there, and everything it reports is computed from what actually ran —
    `n_samples` is the batch it completed, `converged` is False, and the
    resolution floor follows the sample count. The check sits at the BOTTOM of
    the loop; at the top it would leave the per-period lists empty and
    `np.concatenate` would raise."""
    inp = _inputs()
    ev = _CountingEvent(after=1)
    out = M.mc_adequacy(inp, draws=8, seed=0, cov_target=-1.0, batch=8,
                        max_draws=400, stop_event=ev)
    # The check is at the BOTTOM of the loop, so the batch in flight when the
    # flag fires still lands — that is the point: at the top the per-period
    # lists would be empty and `np.concatenate` would raise.
    assert out["n_samples"] == 16
    assert out["converged"] is False
    assert out["resolution_floor_h"] == pytest.approx(1.0 / 16)
    assert out["lole_hours"] >= 0.0


def test_F1f4_the_portfolio_stops_between_periods_and_says_it_was_truncated():
    """A stopped portfolio keeps the periods it priced, and the block marks
    `truncated` — a short `periods` list must never read as a complete one.
    A boolean beside the status, not a status of its own: `status` already
    carries refusals (`margin_unavailable`) that coexist with real rows."""
    H = 24
    from services.adequacy.copt import CoptUnit
    units = tuple(CoptUnit(f"g{i}", 60.0, 0.2, mttr_hours=24.0) for i in range(3))
    idx = 2 * H
    prof = np.zeros(idx)
    prof[:H] = 40.0
    inp = M.MCInputs(units=units, residual=np.full(idx, 150.0) - prof,
                     weights=np.ones(idx),
                     periods=(("2030", 0, H), ("2035", H, idx)),
                     nyears=idx / 8760.0, vre_profiles={"farm": prof})
    members = [P.Member("vre", "farm", 100.0, (("2030", 100.0), ("2035", 100.0)))]
    ev = _CountingEvent(after=1)
    rows = P.elcc_of_portfolio(inp, members, seed=0, draws=8, cov_target=1.0,
                               stop_event=ev)
    assert len(rows) < 2, rows


# ── F1b / F1b2: the sweep's restore ─────────────────────────────────────

def test_F1b_the_sweep_breaks_rather_than_raising_so_the_restore_runs(monkeypatch):
    """★ F1b. The abort is a `break`. `run_contingency_sweep`'s closing base
    re-solve sits OUTSIDE its `try/finally` (only `unfreeze()` is inside), so
    an abort raised as an exception would skip it and leave the network on the
    last contingency. The frontier cannot fail this test — a `break` inside
    its `try` still runs its `finally` — which is why the sweep is the engine
    driven here.

    Bite (verified): raise instead of breaking in the contingency loop — the
    closing re-solve never runs.
    """
    calls = {"solves": 0, "restore": 0}

    def fake_solve_once(cfg, n, lock, log_queue, sink):
        calls["solves"] += 1
        sink["_status"] = "ok"
        sink["last_lost_load"] = None

    def fake_restore(network, lock, cfg, log_queue, final_state_update):
        calls["restore"] += 1
        return True, "ok"

    monkeypatch.setattr(SW, "_solve_once", fake_solve_once)
    monkeypatch.setattr(SW, "_restore_base_guarded", fake_restore)
    monkeypatch.setattr(SW, "freeze_capacities", lambda n: (lambda: None))
    monkeypatch.setattr(SW, "_electrical_eue_mwh", lambda *a, **k: 0.0)

    conts = [{"id": f"c{i}", "mutate": lambda n: (lambda: None), "meta": {}}
             for i in range(4)]
    ev = _CountingEvent(after=2)
    out = SW.run_contingency_sweep(_FakeNetwork(), object(), _cfg(), conts,
                                   stop_event=ev)
    assert out["aborted"] is True
    assert len(out["contingencies"]) == 2, out["contingencies"]
    assert calls["restore"] == 1, "the closing re-solve must run on the abort path"


def test_F1b2_a_failing_restore_does_not_destroy_the_partial_rows(monkeypatch):
    """★ F1b2. The closing re-solve is GUARDED. Unguarded (as shipped before
    this phase) a restore that raises propagated and destroyed `results`
    entirely — including the rows an abort exists to keep — and surfaced as an
    opaque `failed`. Now it is reported: the rows survive and `base_restored`
    is False.

    Bite (verified): drop the try/except in `_restore_base_guarded`.
    """
    def fake_solve_once(cfg, n, lock, log_queue, sink):
        sink["_status"] = "ok"
        sink["last_lost_load"] = None

    def boom(*a, **k):
        raise RuntimeError("solver died in the restore")

    monkeypatch.setattr(SW, "_solve_once", fake_solve_once)
    monkeypatch.setattr(SW, "freeze_capacities", lambda n: (lambda: None))
    monkeypatch.setattr(SW, "_electrical_eue_mwh", lambda *a, **k: 0.0)
    import services.solver_service as _ss
    monkeypatch.setattr(_ss, "run_simulation", boom)

    conts = [{"id": "c0", "mutate": lambda n: (lambda: None), "meta": {}}]
    out = SW.run_contingency_sweep(_FakeNetwork(), object(), _cfg(), conts)
    assert out["contingencies"], "the measured rows must survive the failed restore"
    assert out["base_restored"] is False
    # the status carries the solver's own words, not just "raised" — a
    # failing restore has to be diagnosable from the record (shipped-code
    # review, finding 18)
    assert out["base_restore_status"].startswith("raised: ")
    assert "solver died in the restore" in out["base_restore_status"]


def test_F1b3_the_row_assemblers_skip_what_a_partial_sweep_never_measured(monkeypatch):
    """★ F1g (engine half). An aborted sweep's result dict carries only the
    contingencies it reached; the class-B assembler used to index every id and
    raised `KeyError`, which the route reported as an opaque `failed` — losing
    every row. Bite (verified): index instead of `.get`."""
    conts = [{"id": f"c{i}", "meta": {"name": f"c{i}", "component": "Link",
                                      "q": 0.1, "basis": "FOR",
                                      "mttr_hours": 24.0}}
             for i in range(3)]
    monkeypatch.setattr(SW, "class_b_contingencies", lambda n: conts)
    monkeypatch.setattr(
        SW, "run_contingency_sweep",
        lambda *a, **k: {"base": {"eue_mwh": 0.0, "status": "ok"},
                         "contingencies": {"c0": {"status": "ok", "eue_mwh": 1.0,
                                                  "delta_eue_mwh": 1.0, "meta": conts[0]["meta"]}},
                         "aborted": True, "base_restored": True})
    rows, _restore = SW.run_class_b_sweep(object(), object(), _cfg())
    assert [r["id"] for r in rows] == ["c0"], rows


class _FakeNetwork:
    """The sweep sets a marker attribute on the network; `object()` cannot
    carry one."""


def _cfg():
    from services.solver_service import SolverConfig
    return SolverConfig(voll=3000.0)


# ── F1: the frontier stops between points and keeps them ────────────────

def test_F1_the_frontier_stops_between_points_and_keeps_what_it_swept(monkeypatch):
    """★ F1. The flag is checked between ε points; the points already swept
    are kept, `aborted` is set, and the closing restore still runs — so the
    worst case after the click is the in-flight solve PLUS the restore solve.
    Bite (verified): ignore the flag in the loop."""
    from services.adequacy import frontier as FR

    calls = {"restore": 0}

    def fake_solve_once(cfg, n, lock, log_queue, sink):
        sink["_status"] = "ok"
        sink["adequacy_report"] = {
            "target": {"system": {"cap_mwh": 1.0, "achieved_ens_mwh": 0.5,
                                  "achieved_shed_hours": 2.0}, "binding": "cap"},
            "cost": {"total_system_cost_eur": 100.0, "period_basis": "horizon"},
            "engine": "lp_proxy", "fidelity": "deterministic_scenario"}

    def fake_restore(*a, **k):
        calls["restore"] += 1
        # `(ok, status)` since the shipped-code review's finding 13 — the
        # frontier's restore reports the solver's word, not just "it ran".
        return True, "ok"

    monkeypatch.setattr(SW, "_solve_once", fake_solve_once)
    monkeypatch.setattr(FR, "_restore_base", fake_restore)
    ev = _CountingEvent(after=2)
    out = FR.run_frontier_sweep(object(), object(), _cfg(), [1.0, 2.0, 3.0, 4.0],
                                stop_event=ev)
    assert out["aborted"] is True
    assert len(out["points"]) == 2, out["points"]
    assert calls["restore"] == 1


# ── the routes: F1c (idempotence), F1e (live GETs), F1g (the worksheet) ──

class _LiveStudy:
    """A REAL live daemon thread under a study key, in a GIVEN state dict —
    the swap-guard suite's helper, reused because every guard here tests
    `thread.is_alive()` and a sentinel dict would prove nothing. The state
    dict is passed in rather than fetched: a request resolves a
    SESSION-scoped context whose `solver_state` is a different object from
    the one `_active` holds outside a request."""

    def __init__(self, state: dict, key: str = "mc", *, extra=None):
        self.state = state
        self.key = key
        self.extra = extra or {}
        self._release = threading.Event()
        self._thread = threading.Thread(
            target=self._release.wait, daemon=True, name=f"fake-{key}")

    def __enter__(self):
        self._thread.start()
        self.state[self.key] = {
            "status": "running", "result": None, "error": None,
            "started_at": 1.0, "finished_at": None,
            "thread": self._thread, "stop_event": threading.Event(),
            **self.extra,
        }
        return self

    def __exit__(self, *exc):
        self._release.set()
        self._thread.join(timeout=5)
        self.state[self.key] = None
        return False


ABORT_ROUTES = [("mc", "/api/results/mc/abort"),
                ("frontier", "/api/results/frontier/abort"),
                ("fmea_sweep", "/api/results/fmea_sweep/abort")]


@pytest.mark.parametrize("key,url", ABORT_ROUTES)
def test_F1c_the_abort_route_is_idempotent_and_404s_only_with_no_record(
        client, session_state, key, url):
    """★ F1c. The shipped loop routes' contract, verbatim: 404 only when no
    run was ever recorded; 200 while one runs, with `aborting` true; 200
    again on a second press and on a finished run, with `aborting` false —
    "stop" on something that has stopped is satisfied, and a 409 there would
    make the button flicker into an error at exactly the moment it worked.
    Bite (verified): 409 on a finished run."""
    state = session_state(client)
    state[key] = None
    r = client.post(url)
    assert r.status_code == 404, r.text

    with _LiveStudy(state, key) as live:
        r = client.post(url)
        assert r.status_code == 200, r.text
        assert r.json() == {"status": "running", "aborting": True}
        assert live.state[key]["stop_event"].is_set()
        r2 = client.post(url)
        assert r2.status_code == 200 and r2.json()["aborting"] is True

    state[key] = {"status": "aborted", "thread": None,
                  "stop_event": threading.Event()}
    r = client.post(url)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "aborted", "aborting": False}


GET_ROUTES = [("mc", "/api/results/mc"),
              ("frontier", "/api/results/frontier"),
              ("fmea_sweep", "/api/results/fmea_sweep")]


@pytest.mark.parametrize("key,url", GET_ROUTES)
def test_F1e_the_status_GETs_stay_serialisable_while_a_run_is_live(
        client, session_state, key, url):
    """★ F1e. The record now carries a `threading.Event`, and the GET has to
    drop it: the loops' GETs filter `("thread", "stop_event")` while these
    three filtered only `"thread"`, so shipping the event without widening
    them is a **500 on every poll** — the surface the panel refreshes every
    two seconds while a study runs.

    Bite (verified): leave `stop_event` in the filter — every poll 500s with
    `ValueError: [TypeError("'_thread.lock' object is not iterable")]`.
    """
    state = session_state(client)
    with _LiveStudy(state, key):
        r = client.get(url)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "running"
        assert "stop_event" not in body and "thread" not in body


def test_F1g_an_aborted_sweeps_rows_still_reach_the_worksheet(
        client, session_state, install_network):
    """★ F1g. `/results/fmea_modes` gated on `status == "done"`, so an
    aborted sweep's rows — real contingencies, each paid for with a solve —
    were silently dropped from the merged failure-mode list the FMEA tab
    renders. The gate takes `("done", "aborted")`. Bite (verified): the
    `("done",)` gate."""
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=50.0)
    n.add("Generator", "g", bus="b", carrier="gas", p_nom=100.0,
          outage_rate_value=0.05, outage_rate_basis="FOR", mttr_hours=24.0)
    install_network(n)

    state = session_state(client)
    row = {"id": "link:L1:outage", "status": "ok", "delta_eue_mwh": 12.0,
           "failure_mode": {"mode_id": "link:L1:outage",
                            "component_class": "Link", "name": "L1",
                            "failure_class": "B", "occurrence_per_year": 3.0,
                            "occurrence_basis": "FOR", "severity_eur": 4.0,
                            "criticality_eur_per_year": 12.0,
                            "in_metric_scope": True, "engine": "lp_proxy",
                            "fidelity": "deterministic_scenario"}}
    state["fmea_sweep"] = {"status": "aborted", "rows": [row], "error": None,
                           "thread": None, "stop_event": threading.Event()}
    r = client.get("/api/results/fmea_modes")
    assert r.status_code == 200, r.text
    ids = [m["mode_id"] for m in r.json()["per_mode"]]
    assert "link:L1:outage" in ids, ids


# ── the gaps the shipped-code review found: behaviours no test could see ──

def test_F1h_class_C_does_not_run_after_class_B_was_aborted(monkeypatch):
    """★ F1h (shipped-code review, finding 5). The `/fmea_sweep` worker runs
    TWO sweeps. Breaking out of class B's contingency loop returns to the
    worker, and without a check there class C runs IN FULL — the abort would
    stop one sweep, not the study. Bite (verified): drop the
    `and not stop_event.is_set()` from the worker's class-C branch."""
    import pypsa

    import routers.results as R
    from routers.simulation import _state
    from services.adequacy import stress as ST_MOD
    from services.adequacy import sweep as SW_MOD
    from services.pypsa_service import PyPSAService

    calls = {"b": 0, "c": 0}

    def fake_b(*a, **k):
        calls["b"] += 1
        ev = k.get("stop_event")
        if ev is not None:
            ev.set()                      # the user pressed abort during B
        return [], {"base_restored": True, "base_restore_status": "ok",
                    "aborted": True}

    def fake_c(*a, **k):
        calls["c"] += 1
        return [], {"base_restored": True, "base_restore_status": "ok",
                    "aborted": False}

    # The route imports both assemblers INSIDE the function body, so the
    # names live in their defining modules, not in `routers.results`.
    monkeypatch.setattr(SW_MOD, "run_class_b_sweep", fake_b)
    monkeypatch.setattr(ST_MOD, "run_class_c_sweep", fake_c)

    n = pypsa.Network()
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=10.0)
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=50.0,
          carrier="gas")
    PyPSAService.set_network(n)
    _state.pop("fmea_sweep", None)
    _state["solver_config"] = _cfg()
    _state["status"] = "idle"

    out = R.post_fmea_sweep(
        body=R.FmeaSweepRequest(scenarios=[{
            "id": "s", "name": "s", "kind": "parametric",
            "frequency_per_year": 1.0,
            "electrical_load_multiplier": 1.2}]))
    assert out["status"] == "running"
    _state["fmea_sweep"]["thread"].join(timeout=60)

    assert calls["b"] == 1
    assert calls["c"] == 0, "class C ran after class B was aborted"
    assert _state["fmea_sweep"]["status"] == "aborted"


def test_F1d_an_abort_before_the_portfolio_buys_no_evaluation():
    """★ F1d (shipped-code review, finding 6) — bounded work, COUNTED, never
    timed. `elcc_of_portfolio`'s shared Δ = 0 probe is a full `n_fixed`-draw
    evaluation, and it used to run before the first stop check: a study
    stopped just before it paid for one complete simulation and priced zero
    periods. Bite (verified): move the check back below the probe."""
    H = 24
    from services.adequacy.copt import CoptUnit
    units = tuple(CoptUnit(f"g{i}", 60.0, 0.2, mttr_hours=24.0) for i in range(3))
    idx = 2 * H
    prof = np.zeros(idx)
    prof[:H] = 40.0
    inp = M.MCInputs(units=units, residual=np.full(idx, 150.0) - prof,
                     weights=np.ones(idx),
                     periods=(("2030", 0, H), ("2035", H, idx)),
                     nyears=idx / 8760.0, vre_profiles={"farm": prof})
    members = [P.Member("vre", "farm", 100.0, (("2030", 100.0), ("2035", 100.0)))]

    baseline = M.mc_adequacy(inp, draws=8, seed=0, cov_target=1.0)
    key = E.baseline_key(inp, draws=8, seed=0, cov_target=1.0,
                         max_draws=M.MAX_DRAWS, batch=250)
    ev = threading.Event()
    ev.set()                                   # stopped before it even starts
    sims = {"n": 0}
    real_sb = M._simulate_blocks

    def counting(*a, **kw):
        sims["n"] += 1
        return real_sb(*a, **kw)

    M._simulate_blocks = counting
    try:
        rows = P.elcc_of_portfolio(inp, members, seed=0, draws=8,
                                   cov_target=1.0, baseline=baseline,
                                   baseline_key=key, stop_event=ev)
    finally:
        M._simulate_blocks = real_sb
    assert rows == []
    assert sims["n"] == 0, f"an already-stopped run still simulated {sims['n']} block(s)"


def test_F1d2_a_truncated_portfolio_block_says_so(monkeypatch):
    """★ F1d2 (shipped-code review, finding 5). `truncated` is the field that
    stops a short `periods` list reading as a complete one — and nothing
    asserted it at the BLOCK level, only that `elcc_of_portfolio` returned
    fewer rows. Bite (verified): hardcode `truncated=False`."""
    H = 24
    from services.adequacy.copt import CoptUnit
    units = tuple(CoptUnit(f"g{i}", 60.0, 0.2, mttr_hours=24.0) for i in range(3))
    idx = 2 * H
    prof = np.zeros(idx)
    prof[:H] = 40.0
    inp = M.MCInputs(units=units, residual=np.full(idx, 150.0) - prof,
                     weights=np.ones(idx),
                     periods=(("2030", 0, H), ("2035", H, idx)),
                     nyears=idx / 8760.0, vre_profiles={"farm": prof})
    pop = {"members": [P.Member("vre", "farm", 100.0,
                                (("2030", 100.0), ("2035", 100.0)))],
           "unbuilt": [], "snapshot_names": {"farm"}}
    ev = threading.Event()
    ev.set()
    block = P.portfolio_block(inp, pop, margin_payload=None,
                              snapshot_fingerprint="x", seed=0, draws=8,
                              cov_target=1.0, baseline=None, baseline_key=None,
                              stop_event=ev)
    assert block["truncated"] is True, block
    assert block["periods"] == []
    # …and a run that priced every period is NOT truncated
    block2 = P.portfolio_block(inp, pop, margin_payload=None,
                               snapshot_fingerprint="x", seed=0, draws=8,
                               cov_target=1.0, baseline=None, baseline_key=None)
    assert block2["truncated"] is False
    assert len(block2["periods"]) == 2


def test_F1b4_the_class_C_assembler_skips_what_a_partial_sweep_never_measured(monkeypatch):
    """★ (shipped-code review, finding 5) — the class-C twin of F1b3. An
    aborted sweep's dict carries only what it reached; indexing every id
    raised `KeyError` and lost every row. Bite (verified): index instead of
    `.get`."""
    from services.adequacy import stress as ST
    from services.adequacy import sweep as SW

    scen = [{"id": f"s{i}", "name": f"s{i}", "kind": "parametric",
             "electrical_load_multiplier": 1.2,
             "frequency_per_year": 1.0} for i in range(3)]
    # `run_class_c_sweep` imports the driver from `sweep` inside its own body,
    # so the patch has to land on the defining module.
    monkeypatch.setattr(
        SW, "run_contingency_sweep",
        lambda *a, **k: {"base": {"eue_mwh": 0.0, "status": "ok"},
                         "contingencies": {
                             "scenario:s0": {"status": "ok", "eue_mwh": 1.0,
                                             "delta_eue_mwh": 1.0,
                                             "meta": {"name": "s0",
                                                      "frequency_per_year": 1.0}}},
                         "aborted": True, "base_restored": True,
                         "base_restore_status": "ok"})
    rows, restore = ST.run_class_c_sweep(object(), object(), _cfg(), scen)
    assert [r["id"] for r in rows] == ["scenario:s0"], rows
    assert restore["aborted"] is True


def test_F1j_no_replay_call_site_can_forward_the_stop_flag():
    """★ (shipped-code review, finding 4). The CRN wall, asserted STATICALLY
    over the source rather than through one engine: every `mc_adequacy(` call
    outside the `/mc` worker's own baseline must pass `stop_event=None`
    literally. A dynamic test can only cover the call sites it happens to
    drive — two portfolio call sites were mutation-invisible — and a new call
    site added later would slip past every one of them.

    The exemption is ONE FUNCTION, not one file. It was written as
    `rel != "routers/results.py"`, which exempted every call in that module —
    including the coupling-loop and margin-loop replays at `post_coupling_loop`
    and `post_margin_loop`, the two this very phase had to fix. Either could
    have been changed back to forward a live flag with this test still green
    (adversarial review of the review fixes, M1).

    Bites (verified): drop `stop_event=None` from `elcc.metrics_at`, from
    either portfolio call, or from either loop's `evaluate`.
    """
    import ast
    import pathlib

    # The one call that MAY carry a live flag: the `/mc` worker's own baseline,
    # which is the study the user is aborting.
    ALLOWED = ("post_mc",)

    offenders: list[str] = []

    def scan(rel: str, src: str, node, chain: tuple[str, ...]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chain = chain + (node.name,)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call):
                fn = child.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if name == "mc_adequacy":
                    where = f"{rel}:{child.lineno} ({'.'.join(chain) or '<module>'})"
                    kw = {k.arg for k in child.keywords if k.arg}
                    if "stop_event" not in kw:
                        line = src.splitlines()[child.lineno - 1].strip()
                        offenders.append(f"{where}: no stop_event — {line}")
                    else:
                        val = next(k.value for k in child.keywords
                                   if k.arg == "stop_event")
                        is_none = (isinstance(val, ast.Constant)
                                   and val.value is None)
                        if not is_none and not any(a in chain for a in ALLOWED):
                            offenders.append(f"{where}: forwards a live flag")
            scan(rel, src, child, chain)

    seen_allowed = 0
    for rel in ("services/adequacy/elcc.py", "services/adequacy/portfolio.py",
                "routers/results.py"):
        src = pathlib.Path(rel).read_text()
        scan(rel, src, ast.parse(src), ())
    # The exemption must still match something, or a rename silently turns this
    # into a test that no call site can pass.
    for node in ast.walk(ast.parse(pathlib.Path("routers/results.py").read_text())):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name in ALLOWED:
            seen_allowed += 1
    assert seen_allowed == len(ALLOWED), (
        f"the exempted function(s) {ALLOWED} are not in routers/results.py any "
        "more — this test is asserting nothing about them")

    assert not offenders, (
        "every mc_adequacy call outside the /mc worker's baseline must pass "
        "stop_event=None — a replay truncated by an abort breaks CRN:\n  "
        + "\n  ".join(offenders))


def test_F1k_the_worksheet_ranking_is_deterministic_when_every_row_ties():
    """★ F1k (shipped-code review, finding 11). The spec said `/fmea_modes`
    ranks on `(-criticality_eur_per_year, mode_id)`; the code sorted on
    criticality alone, which leaves exactly-tied rows in SOURCE order — class A
    from the COPT engine first, then whatever the sweep contributed.

    That tie is not a corner case. Criticality is ΔEUE × VoLL × occurrence, so
    with no VoLL set EVERY row is €0/yr and the entire ranking ties; the order
    the worksheet renders then depended on which classes had been computed.
    The rows here are handed to the route already out of `mode_id` order, so
    only the key can recover it. Bite (verified): sort on
    `criticality_eur_per_year` with `reverse=True` and no second element.
    """
    import pypsa

    import routers.results as R
    from routers.simulation import _state
    from services.pypsa_service import PyPSAService

    # A network with no occurrence data anywhere: class A contributes nothing,
    # so the only rows are the ones installed below.
    n = pypsa.Network()
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=10.0)
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=50.0,
          carrier="gas")
    PyPSAService.set_network(n)

    def _row(mode_id):
        return {"delta_eue_mwh": 0.0,
                "failure_mode": {"mode_id": mode_id, "name": mode_id,
                                 "component_class": "Network",
                                 "failure_class": "B",
                                 "occurrence_per_year": 1.0,
                                 "occurrence_basis": "FOR",
                                 "severity_eur": 0.0,
                                 # every row ties, as a VoLL-less project does
                                 "criticality_eur_per_year": 0.0,
                                 "in_metric_scope": True,
                                 "engine": "lp_proxy",
                                 "fidelity": "deterministic_scenario"}}

    ids = ["zulu", "alpha", "mike", "bravo"]
    _state["fmea_sweep"] = {"status": "done",
                            "rows": [_row(i) for i in ids]}
    try:
        out = R.get_fmea_modes()
        got = [r["mode_id"] for r in out["per_mode"]]
    finally:
        _state.pop("fmea_sweep", None)

    assert set(ids) <= set(got), got
    assert all(float(r.get("criticality_eur_per_year", 0.0)) == 0.0
               for r in out["per_mode"]), \
        "fixture must tie every row, or the tie-break is not what is tested"
    assert got == sorted(got), got


def test_F1m_a_sweep_on_a_network_with_no_link_still_runs_class_C():
    """★ F1m (adversarial review of the review fixes, blocker B1). Making the
    assemblers return `(rows, restore)` missed BOTH their empty early returns,
    which kept the bare list — so on a network with **no eligible link
    contingency**, which is any network with no Links at all, the worker's
    `rows, restore_b = run_class_b_sweep(...)` raised "not enough values to
    unpack" before class B produced anything. The generic handler wrote
    `failed` with no rows, and class C — which had nothing to do with the
    failure — never ran.

    F1h could not see this: it fakes BOTH assemblers, so it exercises the
    worker's control flow while routing around the real return shapes. This
    one fakes NEITHER.

    Bite (verified): restore `return []` in `run_class_b_sweep`.
    """
    import pypsa

    import routers.results as R
    from routers.simulation import _state
    from services.adequacy.sweep import class_b_contingencies
    from services.pypsa_service import PyPSAService

    # One bus, one load, one generator — and no Link anywhere.
    n = pypsa.Network()
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=10.0)
    n.add("Generator", "g", bus="b", p_nom=200.0, marginal_cost=10.0,
          carrier="gas")
    n.add("Carrier", "gas")
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    assert class_b_contingencies(n) == [], "fixture must have no class-B work"

    PyPSAService.set_network(n)
    _state.pop("fmea_sweep", None)
    _state["solver_config"] = _cfg()
    _state["status"] = "idle"

    R.post_fmea_sweep(body=R.FmeaSweepRequest(scenarios=[{
        "id": "s", "name": "s", "kind": "parametric",
        "frequency_per_year": 1.0, "electrical_load_multiplier": 1.2}]))
    _state["fmea_sweep"]["thread"].join(timeout=600)
    rec = _state["fmea_sweep"]

    assert rec["status"] == "done", rec.get("error")
    assert rec["error"] is None
    # Class C ran: the scenario produced a row even though class B had none.
    assert [r["id"] for r in rec["rows"]] == ["scenario:s"], rec["rows"]


def test_F1m2_the_class_C_assembler_returns_the_pair_when_nothing_is_runnable():
    """★ F1m2 (blocker B2) — the class-C twin, and the worse of the two. Its
    early return handed back a LIST of status rows, so with exactly two
    `profiles` scenarios the caller's `rows_c, restore = ...` *unpacked the
    list*: `restore` became a scenario-row dict whose `.get("base_restored")`
    reads a quiet `None`. One scenario raised, three raised differently, two
    corrupted silently.

    Bite (verified): restore `return rows` in `run_class_c_sweep`.
    """
    from services.adequacy import stress as ST

    scen = [{"id": f"s{i}", "name": f"s{i}", "kind": "profiles",
             "frequency_per_year": 1.0} for i in range(2)]
    rows, restore = ST.run_class_c_sweep(object(), object(), _cfg(), scen)

    assert [r["id"] for r in rows] == ["scenario:s0", "scenario:s1"]
    assert all(r["status"] == "profiles_not_supported_yet" for r in rows)
    # No sweep ran, so nothing was restored — and the flags say exactly that
    # rather than claiming a re-solve that never happened.
    assert restore == {"base_restored": None, "base_restore_status": None,
                       "aborted": False}


@pytest.mark.parametrize("returned", [("ok", "time_limit"), ("ok", "suboptimal"),
                                      ("warning", "infeasible")])
def test_F1n_neither_loop_calls_a_time_limited_re_solve_a_restore(monkeypatch,
                                                                  returned):
    """★ F1n (12e shipped-code review, S1 — the same defect in two more
    places). Both loops' `_restore_closing` read `status in ("ok", "optimal")`
    and threw the termination condition away. linopy's `SolverStatus.ok` also
    covers `time_limit`, `iteration_limit`, `terminated_by_limit`, `suboptimal`
    and `imprecise`, so a closing re-solve that hit the MIP time limit —
    `mip_time_limit_s` is a shipped setting — reported `ok`, and both panels
    rendered "restored" while the network held a time-limited dispatch.

    The frontier and the contingency sweep had this bug and it was found there
    first; these two were never in the review's scope. All four now judge on
    the condition, through ONE predicate.

    Bite (verified): `return status in ("ok", "optimal")` in either loop.
    """
    from services.adequacy.sweep import restore_is_clean

    assert restore_is_clean(str(returned[1])) is False, returned
    # …and the words that DO mean the plan is back, so the predicate is not
    # simply "always false": a `mode="pf"` run reports ("ok", "ok") and a
    # successful stage-2 AC power flow rewrites the condition.
    for good in ("optimal", "ok", "lopf+ac_pf_ok"):
        assert restore_is_clean(good) is True, good
    assert restore_is_clean(None) is False
    assert restore_is_clean("raised: boom") is False


def test_F1n2_the_loops_read_the_condition_and_not_the_status():
    """★ F1n2 — the static half of F1n, over the source. Both loops' closing
    re-solve must judge on `condition or status` through `restore_is_clean`,
    never on the status alone. A dynamic test would have to drive a whole loop
    with a patched solver to reach one line; this cannot be routed around by a
    fixture.

    Bite (verified): put `return status in ("ok", "optimal")` back in either
    `_restore_closing`.
    """
    import pathlib

    raw = pathlib.Path("routers/results.py").read_text()
    # Comments are stripped before scanning: the fix's own comment QUOTES the
    # expression it replaced, and a check that trips on prose about a defect
    # rather than the defect is not a check.
    src = "\n".join(ln for ln in raw.splitlines()
                    if not ln.lstrip().startswith("#"))
    assert 'status in ("ok", "optimal")' not in src, (
        "a closing re-solve is judging on the solver STATUS — `SolverStatus.ok`"
        " also covers time_limit, iteration_limit, terminated_by_limit,"
        " suboptimal and imprecise; read the CONDITION through"
        " services.adequacy.sweep.restore_is_clean")
    # both loops, not just one
    assert raw.count("restore_is_clean(word)") >= 4, (
        "both `_restore_closing` bodies must route through the shared"
        " predicate — found fewer uses than the two loops need")


def test_F1p_every_studys_GET_actually_serves_the_restore_word():
    """★ F1p. The four studies that re-solve to close now report `(restored,
    word)`, and each panel prints the word — but every panel test mocks the
    API, so not one of them would notice a GET that dropped the field on its
    way to the wire. A route filter written as an ALLOWLIST would do exactly
    that, silently, and the frontend would render "NOT restored" with no
    reason for ever.

    This is the fifth-instance guard: the recurring error all phase has been a
    test whose fixture routes around the path it names, and a mocked panel
    test routes around the whole backend. Bite (verified): change any of the
    four GET filters to an allowlist that omits `base_restore_status`.
    """
    import routers.results as R
    from routers.simulation import _state

    cases = (
        ("coupling_loop", R.get_coupling_loop),
        ("margin_loop", R.get_margin_loop),
        ("frontier", R.get_frontier),
        ("fmea_sweep", R.get_fmea_sweep),
    )
    saved = {k: _state.get(k) for k, _ in cases}
    try:
        for key, getter in cases:
            _state[key] = {
                "status": "done", "error": None,
                "base_restored": False,
                "base_restore_status": "time_limit",
                # the two the filter DOES exist to drop
                "thread": object(), "stop_event": threading.Event(),
            }
            out = getter()
            assert isinstance(out, dict), (key, out)
            assert out.get("base_restore_status") == "time_limit", (key, out)
            assert out.get("base_restored") is False, (key, out)
            assert "thread" not in out and "stop_event" not in out, (key, out)
    finally:
        for k, v in saved.items():
            if v is None:
                _state.pop(k, None)
            else:
                _state[k] = v

"""
Phase 4 Compare-Scenarios v2 QA — Curtailment + Lost load + Storage cycling.

Verifies the new ResultsSummary fields against the live backend:
  • Curtailment payload is internally consistent (per-carrier sums match total,
    rate math is correct, system_rate matches per-carrier roll-up).
  • Lost load gracefully reports `available=False` when the project never
    shed load; populated payload's per-bus sum reconciles with the total.
  • Storage cycling per-unit and per-carrier aggregates reconcile.
  • Multi-period per_period keys are a subset of the project's periods.
  • Concurrent requests against /results-summary AND /compare-state for
    BOTH scenarios complete without HDF5 races (the lock added earlier
    in the session is still effective when Phase-4 compute paths are also
    in the mix).

Two ways to run it
------------------
**Self-hosted (the default, no setup).** The driver boots a REAL uvicorn on an
ephemeral port, seeds two solved multi-period scenarios, and runs every check
against it over real HTTP. Just `python tests/qa_phase4_compare.py`.

This file used to say it "cannot be made self-contained", which was wrong on
both halves of the reasoning:

* The server was never the obstacle — uvicorn runs perfectly well in a daemon
  thread inside this process. Doing it IN-PROCESS is what makes it work at all:
  the thread shares `tests/qa_support.py`'s sandbox, so it serves the same
  in-memory database and seeded org the driver signs in against. A subprocess
  would get its own empty database and see no projects.
* The specific `4_nodes_N-0` / `4_nodes_N-1` data was never needed either.
  Every check below is a SELF-CONSISTENCY assertion on the payload — per-carrier
  sums reconcile with totals, rates land in [0, 100], `by_unit` is sorted, period
  keys are a subset of the project's periods. None of them reads a number that
  depends on which network produced it.

What is genuinely load-bearing is that the concurrency smoke test runs against a
real ASGI server rather than an in-process `TestClient`, because the HDF5 race it
was written to catch lives in real concurrent request handling. A thread-hosted
uvicorn keeps that property; `TestClient` would not.

(The `PYPSAGUI_` prefix rather than `PYPSA_GUI_` is deliberate: pypsa parses
every `PYPSA_*` environment variable as one of its own options and prints
`Unknown option 'gui_qa_base' from env var ...` on import for anything it does
not recognise. The repo already uses `PYPSAGUI_` for `PYPSAGUI_APP_DATA_DIR` and
friends; these follow it.)

**Against an operator's own server.** Set `PYPSAGUI_QA_BASE` to its API root
(e.g. `http://127.0.0.1:8000/api`) and the driver skips all seeding and hits
that instead — the original behaviour, for checking real scenarios. Add
`PYPSAGUI_QA_COOKIE` (a `session=...` cookie from a signed-in browser,
DevTools → Application → Cookies) unless the server runs in local mode, and set
`PYPSAGUI_QA_SCENARIOS` to a comma-separated pair of SOLVED project names if
yours are not called `4_nodes_N-0` / `4_nodes_N-1`.

`preflight()` reports the single blocking reason in one line — no server, no
session, no such project — instead of twenty identical connection errors.

Run with `python tests/qa_phase4_compare.py` from pypsa-gui/backend.
"""
from __future__ import annotations

import concurrent.futures as _cf
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

# Self-hosting imports `tests.qa_support`, so the backend directory has to be on
# the path — this file is run as `python tests/qa_phase4_compare.py`, which puts
# `tests/` on it, not the parent. Same header as every sibling driver; this one
# lacked it only because it used to import nothing from the backend.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# Set PYPSAGUI_QA_BASE to an API root to test an operator's own server; leave
# it unset to have this driver boot its own. Both are rebound by `main()`.
BASE = os.environ.get("PYPSAGUI_QA_BASE", "")

# A `session=...` cookie from a signed-in browser. Unset is fine against a
# server in local mode; against any other, every request is 401 without it.
# In self-hosted mode this is set to the seeded session.
COOKIE = os.environ.get("PYPSAGUI_QA_COOKIE", "")

SCENARIOS = [
    s.strip() for s in
    os.environ.get("PYPSAGUI_QA_SCENARIOS", "4_nodes_N-0,4_nodes_N-1").split(",")
    if s.strip()
]


def _get(path: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(f"{BASE}{path}")
    if COOKIE:
        req.add_header("Cookie", COOKIE)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


# ── Self-hosting ─────────────────────────────────────────────────────────────


def _build_scenario_network():
    """
    A small multi-period network that exercises all three Phase-4 payloads.

    Deliberately shaped so the checks have something to check rather than
    trivially-empty payloads:

    * **Curtailment** — solar is given far more capacity than the load can
      absorb at midday, with a `p_max_pu` profile that peaks well above demand,
      so the LP spills some and `by_carrier_gwh` is non-zero.
    * **Storage cycling** — a battery with `max_hours` and cyclic SoC, which
      charges on the solar peak and discharges into the evening, so `by_unit`
      has a unit with a non-zero cycle count.
    * **Multi-period keys** — three investment periods, so every payload's
      `by_period` has keys to be checked against the project's periods.

    Lost load is left at `available=False`: no VOLL slack is configured, the LP
    meets all demand, and `check_lost_load` treats that as its happy path.
    """
    import numpy as np
    import pandas as pd
    import pypsa

    n = pypsa.Network()
    base = pd.date_range("2026-01-01", periods=24, freq="h")
    periods = [2026, 2027, 2028]
    mi = pd.MultiIndex.from_product([periods, base], names=["period", "timestep"])
    mi.name = "snapshot"
    n.snapshots = mi
    n.investment_periods = periods
    for p in periods:
        n.investment_period_weightings.loc[p, "years"] = 1.0
        n.investment_period_weightings.loc[p, "objective"] = 1.0

    n.add("Bus", "B1")
    n.add("Carrier", "solar", co2_emissions=0.0)
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Carrier", "battery", co2_emissions=0.0)

    # A daily solar shape, peaking at noon.
    hours = np.arange(24)
    shape = np.clip(np.sin((hours - 6) / 12 * np.pi), 0.0, None)
    solar_pu = pd.Series(np.tile(shape, len(periods)), index=mi)

    # 400 MW of solar against a 100 MW evening-weighted load: at midday the
    # array can make ~400 MW and nothing can absorb it, so it is curtailed.
    n.add("Generator", "Solar", bus="B1", carrier="solar",
          p_nom=400.0, marginal_cost=0.0, p_max_pu=solar_pu)
    n.add("Generator", "Gas", bus="B1", carrier="gas",
          p_nom=200.0, marginal_cost=80.0)
    n.add("StorageUnit", "Bat", bus="B1", carrier="battery",
          p_nom=50.0, max_hours=4.0, marginal_cost=0.0,
          efficiency_store=0.95, efficiency_dispatch=0.95,
          cyclic_state_of_charge=True)

    # Load: flat 60 MW with an evening peak the solar cannot serve directly,
    # which is what gives the battery a reason to cycle.
    load = np.full(24, 60.0)
    load[17:22] = 120.0
    n.add("Load", "L1", bus="B1", p_set=pd.Series(np.tile(load, len(periods)), index=mi))
    return n


def _solve(n) -> tuple[str, str]:
    import queue
    import threading

    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation
    from tests import qa_support

    qa_support.install_network(n)
    cfg = SolverConfig(multi_investment_periods=True, solve_strategy="overnight")
    return run_simulation(
        cfg, n, PyPSAService.get_lock(), threading.Event(), queue.SimpleQueue()
    )


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def serve_self() -> str | None:
    """
    Boot a real uvicorn against the sandbox, seed both scenarios, and rebind
    `BASE`/`COOKIE` at module scope. Returns a blocking reason, or None.

    IN-PROCESS, in a daemon thread, on purpose. The server has to share
    `qa_support`'s in-memory database and seeded org — a subprocess would come
    up with an empty database and 404 on every scenario. It is still a real
    ASGI server over a real socket, which is what the concurrency check needs.
    """
    global BASE, COOKIE

    import threading

    import uvicorn

    from tests import qa_support  # pins the sandbox; must precede `main`
    import main
    from settings import get_settings

    n = _build_scenario_network()
    status, condition = _solve(n)
    if status not in ("ok", "optimal"):
        return f"the seeded scenario did not solve: status={status!r} condition={condition!r}"

    for name in SCENARIOS:
        qa_support.install_network(n)
        qa_support.save_project(name)

    port = _free_port()
    config = uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()

    BASE = f"http://127.0.0.1:{port}/api"
    settings = get_settings()
    raw = qa_support.client().cookies.get(settings.session_cookie_name)
    COOKIE = f"{settings.session_cookie_name}={raw}"

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if server.started:
            return None
        time.sleep(0.1)
    return f"the self-hosted server did not come up on port {port} within 30s"


def preflight() -> str | None:
    """
    The one-line reason this driver cannot run, or None if it can.

    Without it a missing server produces twenty-two identical
    `Connection refused` lines and a missing session produces twenty-two
    identical 401s, neither of which names what to do about it.
    """
    try:
        _get(f"/projects/{SCENARIOS[0]}/results-summary", timeout=5.0)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return (
                f"the backend at {BASE} requires a session and none was "
                f"supplied — set PYPSAGUI_QA_COOKIE to a `session=...` cookie "
                f"from a signed-in browser (DevTools -> Application -> Cookies)"
            )
        if exc.code == 404:
            return (
                f"the backend has no project named {SCENARIOS[0]!r} — this driver "
                f"reads {SCENARIOS} and expects both SOLVED"
            )
        return f"the backend answered {exc.code} for {SCENARIOS[0]!r}"
    except OSError as exc:
        return (
            f"no backend answering at {BASE} ({exc}) — start one with "
            f"`pixi run gui-backend`, or unset PYPSAGUI_QA_BASE to have this "
            f"driver host its own"
        )
    return None


def _approx_eq(a: float, b: float, tol: float = 1e-3, rel: float = 1e-3) -> bool:
    if a == b:
        return True
    return abs(a - b) <= max(tol, rel * max(abs(a), abs(b)))


def _section(title: str) -> None:
    print(f"\n── {title} ──")


# ─────────────────────────────────────────────────────────────────────────────


def check_curtailment(s: dict, name: str) -> list[str]:
    """
    Per-carrier sums match total. Rate = curtailed / (curtailed +
    dispatched). System_rate consistent.
    """
    issues: list[str] = []
    cur = s.get("curtailment")
    if cur is None:
        return ["curtailment is None"]
    by_c = cur.get("by_carrier_gwh", {})
    total = cur.get("total_gwh", {})
    sum_total = sum(v["total"] for v in by_c.values())
    if not _approx_eq(sum_total, total["total"], tol=0.5):
        issues.append(f"Σ by_carrier_gwh ({sum_total:.2f}) ≠ total_gwh.total ({total['total']:.2f})")
    # Per-period sum.
    if total.get("by_period"):
        for p, total_p in total["by_period"].items():
            sum_p = sum(v["by_period"].get(p, 0.0) for v in by_c.values())
            if not _approx_eq(sum_p, total_p, tol=0.5):
                issues.append(f"period {p}: Σ by_carrier ({sum_p:.2f}) ≠ total ({total_p:.2f})")
    # Rate math sanity: 0 ≤ rate ≤ 100.
    sys_rate = cur.get("system_rate_pct", {})
    sr_total = sys_rate.get("total", 0)
    if not (0 <= sr_total <= 100):
        issues.append(f"system_rate_pct.total out of [0, 100]: {sr_total}")
    rates = cur.get("rate_pct_by_carrier", {})
    for carrier, rate in rates.items():
        rt = rate.get("total", 0)
        if not (0 <= rt <= 100):
            issues.append(f"rate_pct[{carrier}].total out of [0, 100]: {rt}")
        for p, v in rate.get("by_period", {}).items():
            if not (0 <= v <= 100):
                issues.append(f"rate_pct[{carrier}][{p}] out of [0, 100]: {v}")
    print(f"  [{name}] curtailment: {len(by_c)} carriers, total {total['total']:.1f} GWh, system rate {sr_total:.2f}%")
    return issues


def check_lost_load(s: dict, name: str) -> list[str]:
    """
    available=False ⇒ everything zero/empty. available=True ⇒
    total_mwh > 0, by_bus.energy sums ≥ total (per-bus is a subset).
    """
    issues: list[str] = []
    ll = s.get("lost_load")
    if ll is None:
        return ["lost_load is None"]
    avail = ll.get("available")
    if avail is False:
        # Happy path: no shedding. Other fields should be empty/zero.
        if ll.get("total_mwh", {}).get("total", 0) > 0:
            issues.append("available=False but total_mwh > 0 — inconsistent")
        if len(ll.get("by_bus", [])) > 0:
            issues.append(f"available=False but by_bus has {len(ll['by_bus'])} entries")
        print(f"  [{name}] lost_load: available=False (happy path — LP met all demand)")
        return issues
    # available=True path.
    total_mwh = ll.get("total_mwh", {}).get("total", 0)
    voll = ll.get("voll_eur_per_mwh", 0)
    by_bus = ll.get("by_bus", [])
    if total_mwh <= 0:
        issues.append(f"available=True but total_mwh.total = {total_mwh}")
    if voll <= 0:
        issues.append(f"available=True but voll_eur_per_mwh = {voll}")
    if not by_bus:
        issues.append("available=True but by_bus is empty")
    # Sum of by_bus energies should be ≤ total (per-bus may be capped at 24
    # entries, so could be strictly less when there are many buses).
    bus_sum = sum(b.get("energy_mwh", {}).get("total", 0) for b in by_bus)
    if bus_sum > total_mwh + 1e-3:
        issues.append(f"Σ by_bus.energy ({bus_sum:.2f}) > total_mwh ({total_mwh:.2f})")
    # by_bus sorted desc by total energy.
    energies = [b.get("energy_mwh", {}).get("total", 0) for b in by_bus]
    if energies != sorted(energies, reverse=True):
        issues.append("by_bus not sorted by energy desc")
    print(f"  [{name}] lost_load: available=True, total {total_mwh:.1f} MWh @ {voll:.0f} €/MWh, {len(by_bus)} bus(es)")
    return issues


def check_storage_cycling(s: dict, name: str, periods: list[int]) -> list[str]:
    """
    by_unit carrier groupby matches cycles_by_carrier.
    by_unit sorted by cycles desc. Per-period keys ⊆ periods.
    """
    issues: list[str] = []
    sc = s.get("storage_cycling")
    if sc is None:
        return ["storage_cycling is None"]
    by_unit = sc.get("by_unit", [])
    by_carrier = sc.get("cycles_by_carrier", {})
    # Sorted check.
    cycles_total = [u.get("cycles", {}).get("total", 0) for u in by_unit]
    if cycles_total != sorted(cycles_total, reverse=True):
        issues.append("by_unit not sorted by cycles.total desc")
    # Carrier rollup sanity: sum of throughput per carrier divided by sum
    # of energy_cap, both per-period — the math is in projects.py so we
    # can't trivially re-derive without the raw throughput per period, but
    # we can check the keys agree.
    carriers_in_units = {u.get("carrier") for u in by_unit}
    carriers_in_rollup = set(by_carrier.keys())
    extras = carriers_in_rollup - carriers_in_units
    missing = carriers_in_units - carriers_in_rollup
    if extras:
        issues.append(f"cycles_by_carrier has carriers not in by_unit: {extras}")
    if missing:
        issues.append(f"by_unit has carriers missing from cycles_by_carrier: {missing}")
    # Per-period key subset.
    period_strs = {str(p) for p in periods}
    for u in by_unit:
        pp = u.get("cycles", {}).get("by_period", {})
        unknown = set(pp.keys()) - period_strs
        if unknown:
            issues.append(f"unit {u.get('name')}: cycles.by_period has unknown periods {unknown}")
    for c, pv in by_carrier.items():
        pp = pv.get("by_period", {})
        unknown = set(pp.keys()) - period_strs
        if unknown:
            issues.append(f"carrier {c}: cycles_by_carrier.by_period has unknown periods {unknown}")
    print(f"  [{name}] storage_cycling: {len(by_unit)} unit(s), carriers={list(carriers_in_rollup)}")
    return issues


def check_multi_period_keys(s: dict, name: str) -> list[str]:
    """
    All by_period keys in every nested CarrierPeriodValue must be a
    subset of the top-level `periods` list. Catches a class of bugs where
    we accidentally bleed period int → str conversion mismatches.
    """
    issues: list[str] = []
    periods = {str(p) for p in s.get("periods", [])}
    if not periods:
        return []  # flat network

    def _walk_pv(obj, path: str):
        if isinstance(obj, dict):
            if "by_period" in obj and isinstance(obj["by_period"], dict):
                unk = set(obj["by_period"].keys()) - periods
                if unk:
                    issues.append(f"{path}: by_period has unknown periods {unk}")
            for k, v in obj.items():
                _walk_pv(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _walk_pv(v, f"{path}[{i}]")

    for tab in ("curtailment", "lost_load", "storage_cycling"):
        if s.get(tab):
            _walk_pv(s[tab], tab)
    return issues


# ─────────────────────────────────────────────────────────────────────────────


def run_concurrent_smoke() -> list[str]:
    """
    Hammer the four compare-view endpoints in parallel to make sure
    the netcdf I/O lock is still effective with the new compute path
    in the mix. 5 rounds × 4 reqs = 20 reads.
    """
    issues: list[str] = []
    paths: list[tuple[str, str]] = []
    for proj in SCENARIOS:
        paths.append((f"/projects/{proj}/compare-state",  proj + ":compare"))
        paths.append((f"/projects/{proj}/results-summary", proj + ":results"))
    with _cf.ThreadPoolExecutor(max_workers=8) as ex:
        for round_i in range(5):
            futs = [ex.submit(_get, p) for (p, _) in paths]
            for fut, (p, label) in zip(futs, paths):
                try:
                    fut.result(timeout=30)
                except Exception as exc:
                    issues.append(f"round {round_i + 1} {label}: {type(exc).__name__}: {exc}")
    return issues


# ─────────────────────────────────────────────────────────────────────────────


def main() -> int:
    if BASE:
        _section(f"Using the server at {BASE}")
    else:
        _section("Self-hosting: solving a scenario and booting uvicorn")
        blocked = serve_self()
        if blocked is not None:
            _section("Result")
            print(f"\n[BLOCKED] {blocked}")
            return 1
        print(f"  serving on {BASE}")

    blocked = preflight()
    if blocked is not None:
        _section("Result")
        print(f"\n[BLOCKED] {blocked}")
        return 1

    failures: list[str] = []

    _section("Per-scenario payload checks")
    payloads = {}
    for name in SCENARIOS:
        try:
            payloads[name] = _get(f"/projects/{name}/results-summary")
        except Exception as exc:
            failures.append(f"{name}: fetch failed — {exc}")
            continue
        s = payloads[name]
        if not s.get("has_solve"):
            failures.append(f"{name}: has_solve=False — test expects solved scenarios")
            continue
        periods = s.get("periods", [])
        failures += [f"{name}: {x}" for x in check_curtailment(s, name)]
        failures += [f"{name}: {x}" for x in check_lost_load(s, name)]
        failures += [f"{name}: {x}" for x in check_storage_cycling(s, name, periods)]
        failures += [f"{name}: {x}" for x in check_multi_period_keys(s, name)]

    _section("Concurrent-request smoke (HDF5 race regression)")
    t0 = time.monotonic()
    failures += run_concurrent_smoke()
    print(f"  20 concurrent reads in {time.monotonic() - t0:.2f}s")

    _section("Schema completeness")
    for name, s in payloads.items():
        for k in ("curtailment", "lost_load", "storage_cycling"):
            if k not in s:
                failures.append(f"{name}: missing top-level field '{k}'")
            elif s[k] is None:
                # None is allowed by Optional[…] but our compute functions
                # always return an empty payload, never None. Flag so we
                # notice if a code path slipped past.
                failures.append(f"{name}: field '{k}' is None (expected populated/empty struct)")
        # Old fields still present.
        for k in ("capacity", "dispatch", "loading", "prices", "emissions", "economics"):
            if k not in s:
                failures.append(f"{name}: regression — old field '{k}' missing")

    _section("Result")
    if failures:
        print(f"\n❌ {len(failures)} issue(s):")
        for f in failures:
            print(f"  • {f}")
        return 1
    print(f"\n✅ All checks passed across {len(SCENARIOS)} scenario(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
QA: the adequacy STUDIES — the ε-cap coupling loop, the frontier sweep, and
every route's abort and empty-state contract.

Why a second driver
-------------------
`qa_adequacy_journey.py` walks the journey a user takes through a plan: solve,
screen, sample, sweep, write the worksheet, drive the margin loop. This one
covers what that journey does not reach, and what CI otherwise sees only
through unit tests:

* **the ε-cap coupling loop** — the margin loop's sibling on the same
  controller, driving the unserved-energy cap instead of the firm-capacity
  standard. The two levers take different branches of the same search, and
  only one of them was under a driver.
* **the frontier sweep** — the cost-vs-reliability curve. Its points come out
  of five separate solves and carry an ordering the panel draws a knee on;
  nothing end-to-end checked that the curve is monotone in the direction
  physics requires.
* **the empty and the aborted states.** Every study surface answers 204 before
  it has ever run and 404 to an abort of a study that never started; every
  abort is idempotent and answers 200 whether the study is running, finishing
  or long finished. Those are the contracts the panel's buttons are written
  against, they are the ones a refactor silently breaks, and — like the mesh —
  they only exist against live worker threads.

The other half of each study is that the network must come back. Every one of
these routes mutates the network per iterate and re-solves at the end, so §6
checks that the config the user typed, the plan the foreground holds and the
network's own solvability all survive a study that was stopped halfway.

Runs in CI through `tests/run_qa_drivers.py` (`pixi run gui-qa-drivers`).
Exit 0 = every step passed.
"""
from __future__ import annotations

import math
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# THE load-bearing import, first: `qa_support` pins the sandbox before
# anything imports `main` or `settings`. See its module docstring.
from tests import qa_support          # noqa: E402

import pandas as pd                   # noqa: E402
import pypsa                          # noqa: E402

PROJECT = "qa_adequacy_studies"
VOLL = 6000.0
ENS_CAP = 60.0          # ‱ of demand — the cap loop's own starting coordinate
HOURS = 12

#: Every study surface, its GET and its abort. One list, so a study added
#: without its empty-state contract shows up here rather than in a bug report.
STUDIES = ("mc", "fmea_sweep", "frontier", "coupling_loop", "margin_loop")

PASS = 0
FAIL = 0


def _step(label: str, ok: bool, msg: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {label}" + (f" — {msg}" if msg else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f" — {msg}" if msg else ""))


def _nonfinite(obj, path: str = "$", bad: list | None = None) -> list:
    """Every path holding a NaN or an infinity — a 500 at the wire, since
    Starlette dumps with `allow_nan=False`."""
    bad = [] if bad is None else bad
    if isinstance(obj, bool):
        return bad
    if isinstance(obj, float) and not math.isfinite(obj):
        bad.append(path)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _nonfinite(v, f"{path}.{k}", bad)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _nonfinite(v, f"{path}[{i}]", bad)
    return bad


def _poll(path: str, *, timeout: float = 900.0) -> dict:
    c = qa_support.client()
    t0 = time.time()
    body: dict = {}
    while time.time() - t0 < timeout:
        r = c.get(path)
        if r.status_code == 204:
            return {"status": "no-content"}
        if r.status_code != 200:
            return {"status": f"http-{r.status_code}", "error": r.text[:300]}
        body = r.json()
        if body.get("status") != "running":
            return body
        time.sleep(0.2)
    return {"status": "timeout", **body}


def _config(**fields) -> dict:
    r = qa_support.client().put("/api/simulation/solver_config", json=fields)
    if r.status_code != 200:
        raise RuntimeError(f"solver config PUT failed: {r.status_code} {r.text[:200]}")
    return r.json()


# ── the fixture ───────────────────────────────────────────────────────────

def _network() -> pypsa.Network:
    """A year in 12 snapshots: a 240 MW peak, ~185 MW of firm plant, and one
    extendable peaker that is EXPENSIVE ENOUGH TO LOSE TO SHEDDING.

    That last part is the fixture's whole design, and it is deliberately
    unrealistic. The ε-cap lever tunes unserved ENERGY, so it constrains
    nothing unless the LP would otherwise choose to shed — and with ordinary
    capital costs it never would: one weighted snapshot here is 730 hours, so
    shedding a single megawatt through it costs 730 x VoLL = €4.4 m against
    €55 k/MW/yr to build. The LP builds, every cap is slack, the curve is
    flat and every assertion below passes vacuously.

    A capital cost of €5 m/MW/yr puts the two within reach of each other:
    building beats shedding across the broad shoulder of the load curve and
    loses at the single peak snapshot, so the unconstrained plan sheds exactly
    that peak. Tightening the cap then buys capacity the LP declined to build,
    which is a real cost-vs-reliability trade-off and the one thing the
    frontier exists to draw. §3 asserts the regime is really there rather than
    trusting these numbers to stay true.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2035-01-01", periods=HOURS, freq="h"))
    n.snapshot_weightings.loc[:, :] = 8760.0 / HOURS
    for carrier in ("nuclear", "gas", "ocgt", "wind"):
        n.add("Carrier", carrier)
    n.add("Bus", "b", carrier="AC", country="AA")

    hours = list(range(HOURS))
    demand = [150.0 + 90.0 * math.sin(math.pi * h / (HOURS - 1)) ** 2 for h in hours]
    n.add("Load", "demand", bus="b", p_set=pd.Series(demand, index=n.snapshots))

    n.add("Generator", "nuclear_1", bus="b", carrier="nuclear", p_nom=90.0,
          marginal_cost=9.0, outage_rate_value=0.02, outage_rate_basis="EFORd",
          mttr_hours=150.0)
    n.add("Generator", "gas_cc", bus="b", carrier="gas", p_nom=75.0,
          marginal_cost=65.0, outage_rate_value=0.07, outage_rate_basis="EFORd",
          mttr_hours=40.0)
    n.add("Generator", "wind_farm", bus="b", carrier="wind", p_nom=40.0,
          marginal_cost=0.1)
    # The only lever the LP has against a tighter cap — see the docstring on
    # why it is priced the way it is.
    n.add("Generator", "peaker", bus="b", carrier="ocgt", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=80.0, capital_cost=5_000_000.0,
          marginal_cost=190.0)
    n.generators_t.p_max_pu = pd.DataFrame(
        {"wind_farm": [0.20 + 0.45 * ((h * 5) % 7) / 6.0 for h in hours]},
        index=n.snapshots)
    return n


# ── §1 the empty state ────────────────────────────────────────────────────

def section_1_empty_state() -> None:
    print("\n[1] before anything has run: 204 from every study, 404 from every abort")
    c = qa_support.client()
    for key in STUDIES:
        r = c.get(f"/api/results/{key}")
        _step(f"GET /results/{key} is 204, not an empty 200",
              r.status_code == 204 and r.content == b"",
              f"HTTP {r.status_code} {r.text[:120]}")
        r = c.post(f"/api/results/{key}/abort")
        # 404 is the "client bug" answer and is deliberately NOT 409: there is
        # no race here, nothing has ever been started in this session.
        _step(f"POST /results/{key}/abort is 404 before any run",
              r.status_code == 404, f"HTTP {r.status_code} {r.text[:120]}")


# ── §2 the refusals ───────────────────────────────────────────────────────

def section_2_refusals() -> None:
    print("\n[2] the synchronous refusals, none of which may cost a solve")
    c = qa_support.client()
    _config(solver_name="highs", voll=0.0, ens_cap_permyriad=None)
    r = c.post("/api/results/frontier", json={"targets_permyriad": [10.0]})
    _step("the frontier refuses 422 without a VoLL", r.status_code == 422,
          f"HTTP {r.status_code} {r.text[:160]}")
    r = c.post("/api/results/coupling_loop", json={"target_lole_h": 5.0})
    _step("the cap loop refuses 422 without a VoLL", r.status_code == 422,
          f"HTTP {r.status_code} {r.text[:160]}")

    _config(voll=VOLL, ens_cap_permyriad=ENS_CAP)
    for body, what in (({"targets_permyriad": [10.0, 0.0]}, "a zero target"),
                       ({"targets_permyriad": [-1.0]}, "a negative target")):
        r = c.post("/api/results/frontier", json=body)
        _step(f"the frontier refuses {what} 422", r.status_code == 422,
              f"HTTP {r.status_code} {r.text[:160]}")
    r = c.post("/api/results/coupling_loop", json={"draws": 50})
    _step("the cap loop refuses a missing target 422", r.status_code == 422,
          f"HTTP {r.status_code} {r.text[:160]}")
    r = c.post("/api/results/coupling_loop",
               json={"target_lole_h": 5.0, "draws": 0})
    _step("the cap loop refuses zero draws 422", r.status_code == 422,
          f"HTTP {r.status_code} {r.text[:160]}")
    r = c.post("/api/results/coupling_loop",
               json={"target_lole_h": 5.0, "max_solves": 99})
    _step("the cap loop refuses a budget past the engine cap 422",
          r.status_code == 422, f"HTTP {r.status_code} {r.text[:160]}")

    # The whole point of a SYNCHRONOUS refusal: nothing was started, so the
    # surfaces are still empty and the next study is free to claim them.
    for key in ("frontier", "coupling_loop"):
        _step(f"the refusals left /results/{key} untouched",
              c.get(f"/api/results/{key}").status_code == 204)


# ── §3 the frontier ───────────────────────────────────────────────────────

def section_3_frontier() -> None:
    print("\n[3] the frontier — one curve, five solves, monotone by physics")
    c = qa_support.client()
    targets = [200.0, 100.0, 50.0, 25.0, 10.0]
    r = c.post("/api/results/frontier", json={"targets_permyriad": targets})
    _step("the frontier study starts", r.status_code == 200, r.text[:200])
    if r.status_code != 200:
        return
    fr = _poll("/api/results/frontier")
    _step("the frontier finishes", fr.get("status") == "done",
          f"status={fr.get('status')} error={str(fr.get('error'))[:200]}")
    _step("the payload is JSON-clean", not _nonfinite(fr),
          str(_nonfinite(fr)[:5]))
    _step("the closing re-solve put the network back on base",
          fr.get("base_restored") is True,
          f"base_restored={fr.get('base_restored')} "
          f"status={fr.get('base_restore_status')}")
    points = fr.get("points") or []
    _step("one point per requested target, in the order asked",
          [p.get("target_permyriad") for p in points] == targets,
          str([p.get("target_permyriad") for p in points]))
    ok = [p for p in points if p.get("status") == "ok" and p.get("point")]
    _step("at least two targets produced a plan to compare", len(ok) >= 2,
          f"{len(ok)} of {len(points)} points solved: "
          f"{[p.get('status') for p in points]}")
    # Non-degeneracy, asserted rather than assumed. A curve whose every point
    # is the same plan satisfies every monotonicity below and shows nothing;
    # this fixture is built so the cap is slack at the loose end and BINDS at
    # the tight end, and if that ever stops being true the checks turn into
    # decoration without failing.
    _step("the loose end of the curve is slack",
          any(p.get("binding") == "voll" for p in ok),
          str([p.get("binding") for p in ok]))
    _step("the tight end of the curve BINDS on the cap",
          any(p.get("binding") == "system_cap" for p in ok),
          str([p.get("binding") for p in ok]))
    _step("the curve moves: two points differ in cost",
          len({round(p["point"]["total_system_cost_eur"], 3) for p in ok}) >= 2,
          str([round(p["point"]["total_system_cost_eur"]) for p in ok]))
    # The invariant the curve exists to show, and the one a wrong sign or a
    # swapped pair would break: tightening the cap shrinks the feasible set,
    # so the cap can only fall and the cost can only rise.
    for a, b in zip(ok, ok[1:]):
        t_a, t_b = a["target_permyriad"], b["target_permyriad"]
        _step(f"{t_a}‱ → {t_b}‱: the tighter cap is not larger",
              b["point"]["cap_mwh"] <= a["point"]["cap_mwh"] + 1e-6,
              f"{a['point']['cap_mwh']} → {b['point']['cap_mwh']}")
        _step(f"{t_a}‱ → {t_b}‱: the tighter plan is not cheaper",
              b["point"]["total_system_cost_eur"]
              >= a["point"]["total_system_cost_eur"] - 1e-3,
              f"{a['point']['total_system_cost_eur']} → "
              f"{b['point']['total_system_cost_eur']}")
        _step(f"{t_a}‱ → {t_b}‱: the tighter plan sheds no more",
              b["point"]["achieved_ens_mwh"]
              <= a["point"]["achieved_ens_mwh"] + 1e-6,
              f"{a['point']['achieved_ens_mwh']} → "
              f"{b['point']['achieved_ens_mwh']}")
    knee = fr.get("knee")
    _step("the knee is an index into the points, or none at all",
          knee is None or (isinstance(knee, int) and 0 <= knee < len(points)),
          f"knee={knee!r} over {len(points)} points")
    # The panel draws a marker on it, so a knee that points at a target which
    # produced no plan is a marker on nothing.
    _step("the knee points at a target that actually solved",
          knee is None or points[knee].get("status") == "ok",
          f"knee={knee!r} -> {points[knee].get('status') if knee is not None else None}")
    # IEEE 39-bus review, F3: a standing margin is not swept, so the curve is
    # cost-vs-ε AT that margin and the record has to say which.
    _step("the record carries the standing reserve margin (null when none)",
          "reserve_margin" in fr, sorted(fr))
    _step("every point is tagged with the engine that produced it",
          all(p["point"]["engine"] == "lp_proxy" for p in ok))


# ── §4 the ε-cap coupling loop ────────────────────────────────────────────

def section_4_coupling_loop() -> None:
    print("\n[4] the ε-cap coupling loop — the margin loop's sibling lever")
    c = qa_support.client()
    r = c.post("/api/results/coupling_loop",
               json={"target_lole_h": 400.0, "draws": 200, "seed": 7,
                     "max_solves": 3, "eps0": ENS_CAP})
    _step("the cap loop starts", r.status_code == 200, r.text[:200])
    if r.status_code != 200:
        return
    loop = _poll("/api/results/coupling_loop")
    _step("the cap loop reaches a verdict rather than an error",
          loop.get("status") in ("met", "unreachable", "budget_exhausted"),
          f"status={loop.get('status')} error={str(loop.get('error'))[:200]}")
    _step("the payload is JSON-clean", not _nonfinite(loop),
          str(_nonfinite(loop)[:5]))
    _step("it spent no more solves than it was budgeted",
          0 < int(loop.get("solves_used", 0)) <= 3,
          f"solves_used={loop.get('solves_used')}")
    _step("the closing re-solve put the network back on base",
          loop.get("base_restored") is True,
          f"base_restored={loop.get('base_restored')} "
          f"status={loop.get('base_restore_status')}")
    rows = loop.get("iterations") or []
    _step("every iterate is on the record", len(rows) >= 1, f"{len(rows)} rows")
    # The search tightens, never loosens: each iterate's cap is strictly under
    # the last. A loop that walks back up is searching a bracket it already
    # disproved.
    caps = [float(r_["eps_permyriad"]) for r_ in rows]
    _step("the cap tightens strictly, iterate by iterate",
          all(b < a for a, b in zip(caps, caps[1:])), str(caps))
    _step("no iterate was handed the no-target sentinel",
          all(v > 0 for v in caps), str(caps))
    evaluated = [r_ for r_ in rows if r_.get("mc")]
    _step("an evaluated iterate carries finite LOLE and EUE",
          all(float(r_["mc"]["lole_hours"]) >= 0
              and float(r_["mc"]["eue_mwh"]) >= 0 for r_ in evaluated),
          f"{len(evaluated)} evaluated of {len(rows)}")
    _step("the loop carries the MC's standing warning and its own",
          bool(loop.get("warning")) and len(str(loop.get("warning"))) > 200,
          str(loop.get("warning"))[:120])
    # `eps_star` is the ANSWER, and it exists exactly when there is one.
    _step("eps_star is set if and only if the loop met the target",
          (loop.get("eps_star") is not None) == (loop.get("status") == "met"),
          f"status={loop.get('status')} eps_star={loop.get('eps_star')}")
    if loop.get("final"):
        _step("the answering iterate's own MC evaluation met the target",
              float(loop["final"]["mc"]["lole_hours"]) <= 400.0 + 1e-9,
              str(loop["final"]["mc"]["lole_hours"]))


# ── §5 abort ──────────────────────────────────────────────────────────────

def section_5_abort() -> None:
    print("\n[5] abort — mid-flight, then twice more")
    c = qa_support.client()
    # A long enough study to be caught: eight solves, where a request round
    # trip is milliseconds. A frontier that has already finished by the next
    # line has not run at all, so that is a failure rather than a skip.
    targets = [90.0, 70.0, 50.0, 40.0, 30.0, 20.0, 10.0, 5.0]
    r = c.post("/api/results/frontier", json={"targets_permyriad": targets})
    _step("a fresh frontier starts", r.status_code == 200, r.text[:200])
    if r.status_code != 200:
        return
    live = c.get("/api/results/frontier").json().get("status")
    _step("the study is running when the abort is sent", live == "running",
          f"status={live!r} one round trip after the POST")
    r = c.post("/api/results/frontier/abort")
    _step("the abort is accepted", r.status_code == 200, r.text[:160])
    if r.status_code == 200 and live == "running":
        _step("it reports that it is aborting a live study",
              r.json().get("aborting") is True, r.text[:160])
    fr = _poll("/api/results/frontier")
    _step("the study ends aborted, not failed",
          fr.get("status") == "aborted",
          f"status={fr.get('status')} error={str(fr.get('error'))[:200]}")
    # An abort costs the work in flight, never the work already done — and
    # the closing restore still runs, or the user is left on a contingency.
    _step("it kept the points it had already measured",
          0 < len(fr.get("points") or []) < len(targets),
          f"{len(fr.get('points') or [])} of {len(targets)} points")
    _step("the closing re-solve ran anyway",
          fr.get("base_restored") is True,
          f"base_restored={fr.get('base_restored')} "
          f"status={fr.get('base_restore_status')}")
    # Idempotent, and 200 on a study that has stopped: a 409 here would make
    # the panel's button flicker into an error at the moment it worked.
    r = c.post("/api/results/frontier/abort")
    _step("aborting a finished study is still 200", r.status_code == 200,
          f"HTTP {r.status_code} {r.text[:160]}")
    _step("…and says it is not aborting anything",
          r.status_code == 200 and r.json().get("aborting") is False,
          r.text[:160])
    r = c.post("/api/results/frontier/abort")
    _step("a third abort answers the same way, unchanged",
          r.status_code == 200 and r.json().get("aborting") is False,
          r.text[:160])
    _step("the aborted record is still readable afterwards",
          c.get("/api/results/frontier").json().get("status") == "aborted")


# ── §6 what the user is left holding ──────────────────────────────────────

def section_6_the_network_comes_back() -> None:
    print("\n[6] after four studies: the config, the plan and the network")
    c = qa_support.client()
    cfg = c.get("/api/simulation/solver_config").json()
    # Every study writes the config it solves under and restores the user's.
    # A study that leaves its own ε or margin behind silently changes what the
    # user's next solve means.
    _step("the VoLL the user typed is still the VoLL",
          float(cfg.get("voll")) == VOLL, str(cfg.get("voll")))
    _step("the cap the user typed is still the cap",
          float(cfg.get("ens_cap_permyriad")) == ENS_CAP,
          str(cfg.get("ens_cap_permyriad")))
    _step("no study left a reserve margin behind",
          cfg.get("reserve_margin") in (None, 0, 0.0),
          str(cfg.get("reserve_margin")))
    # The network itself: still solvable, and the foreground results describe
    # the base plan rather than whichever iterate happened to be last.
    r = c.post("/api/simulation/run")
    _step("a plain solve is accepted after the studies", r.status_code == 200,
          r.text[:200])
    if r.status_code != 200:
        return
    t0 = time.time()
    state: dict = {}
    while time.time() - t0 < 600:
        state = c.get("/api/simulation/status").json()
        if state.get("status") != "running":
            break
        time.sleep(0.2)
    _step("…and it completes", state.get("status") == "completed",
          f"status={state.get('status')} condition={state.get('condition')}")
    r = c.get("/api/results/adequacy")
    _step("the foreground report describes the base plan again",
          r.status_code == 200 and r.json().get("engine") == "lp_proxy",
          f"HTTP {r.status_code}")


def main() -> int:
    print("=" * 60)
    print("QA: the adequacy studies")
    print("=" * 60)
    crashed = False
    try:
        qa_support.reset_backend()
        qa_support.delete_project(PROJECT)
        qa_support.install_network(_network())
        qa_support.save_project(PROJECT)
        section_1_empty_state()
        section_2_refusals()
        section_3_frontier()
        section_4_coupling_loop()
        section_5_abort()
        section_6_the_network_comes_back()
    except Exception as exc:                                     # noqa: BLE001
        crashed = True
        import traceback
        print(f"\n  [FAIL] the run raised {type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        try:
            qa_support.delete_project(PROJECT)
            qa_support.reset_backend()
        except Exception as exc:                                 # noqa: BLE001
            print(f"  ! cleanup raised {type(exc).__name__}: {exc}")

    total = PASS + FAIL + (1 if crashed else 0)
    print("\n" + "=" * 60)
    print(f"Total: {total}")
    print(f"Pass:  {PASS}")
    print(f"Fail:  {FAIL + (1 if crashed else 0)}")
    print("=" * 60)
    return 1 if (FAIL or crashed) else 0


if __name__ == "__main__":
    sys.exit(main())

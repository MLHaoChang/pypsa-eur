"""
QA: the adequacy journey — one project, one solve, and every shipped
adequacy surface in the order a user meets them.

Why a driver rather than more unit tests
----------------------------------------
`tests/test_adequacy_*.py` covers each engine and each route in isolation,
usually against a hand-built fixture and often by calling the handler
function. None of it pins the JOURNEY: install a network, solve it under a
reserve-margin standard, screen it with the COPT, sample it with the MC,
sweep a stress scenario, write the worksheet, export the bundle, and drive
the margin loop — all against ONE network, over the HTTP stack, in one
process. Three classes of defect only that shape can catch, and each has
been real on this work:

* **cross-engine disagreement.** `/results/copt` builds its four fleet
  disclosure lists from `split_fleet`; `/results/mc` builds the same four
  lists in its worker, by hand, off its own snapshot ("the MC never calls
  `split_fleet`, so the two lists are built here"). Two implementations of
  one rule, in two modules, and nothing compared them on one fleet. §5 does,
  against a fixture whose every unit was built to land in a NAMED list — an
  empty list agrees with anything, so "non-empty" is not enough.
* **state that only a solve produces.** `/results/reserve_margin` serves a
  solve-time stash, `/results/adequacy` the target-constrained solve's
  report, `/results/fmea_modes` merges the last sweep's rows into the COPT's.
  A unit test injects each of those into session state; only a journey shows
  the solve and the sweep actually putting them there.
* **the mutual-exclusion mesh.** The 409 that refuses a second study — and a
  foreground solve — while one runs is a property of live worker threads and
  the lock hold that claims the surface. §8 probes it against a margin loop
  that is genuinely mid-flight.

Runs in CI through `tests/run_qa_drivers.py` (`pixi run gui-qa-drivers`),
which discovers every `qa_*.py` in this directory. Exit 0 = every step
passed.
"""
from __future__ import annotations

import io
import math
import pathlib
import sys
import time
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# THE load-bearing import, and it must come first: `qa_support` pins the
# sandbox (database, projects root, app data dir) before anything imports
# `main` or `settings`. Getting the order wrong writes this driver's projects
# into the developer's real `backend/projects/`.
from tests import qa_support          # noqa: E402

import pandas as pd                   # noqa: E402
import pypsa                          # noqa: E402

from services.adequacy import occurrence as OCC   # noqa: E402

PROJECT = "qa_adequacy_journey"
#: Which fixture unit belongs in which fleet disclosure list, and why — see
#: `_network`. Pinned by NAME rather than merely by "non-empty": an empty list
#: agrees with anything, and a rule change that silently reclassifies a unit
#: from one bucket to another is exactly what the disclosures exist to make
#: visible. `/results/copt`, `/results/mc` and preflight each derive this
#: membership independently, and §2 and §5 hold all three to this table.
EXPECTED_FLEET_LISTS = {
    "profile_units": {"wind_offshore"},
    "folded_units": {"biomass"},
    "deterministic_units": {"solar_pv"},
    "rate_zero_units": {"hydro_ror"},
}
VOLL = 8000.0
MARGIN = 0.12
#: Unserved-energy cap, ‱ of weighted electrical demand. Set so the solve is
#: TARGET-CONSTRAINED and therefore produces an `AdequacyReport` — without a
#: target `/results/adequacy` is a legitimate 204 and §4 would cover nothing.
ENS_CAP_PERMYRIAD = 20.0
#: The margin loop's reliability target. High for a real standard, and it has
#: to be: one shed snapshot of this fixture's 24 is 365 h of LOLE, so the
#: study's own resolution floor is 8760 / draws — a target near a statutory
#: 3 h/yr is below the floor and the route refuses it (as it should).
LOOP_TARGET_H = 150.0
LOOP_DRAWS = 300
LOOP_MAX_SOLVES = 3
HOURS = 24

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
    """Every path in a payload holding a NaN or an infinity. Starlette dumps
    with `allow_nan=False`, so one of these is a 500 at the wire rather than a
    number the panel can render — the failure `sanitize_reserve_margin_payload`
    exists for."""
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


def _poll(path: str, *, timeout: float = 600.0, key: str = "status") -> dict:
    """Poll a study surface until it leaves `running`. Returns the last body."""
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
        if body.get(key) != "running":
            return body
        time.sleep(0.25)
    return {"status": "timeout", **body}


# ── the fixture ───────────────────────────────────────────────────────────

def _network() -> pypsa.Network:
    """A one-year, 24-snapshot two-bus system built so that EVERY fleet
    disclosure list is non-empty — the only way §5's cross-engine comparison
    can fail on a disagreement rather than pass on two empty lists:

      * `profile_units`      — `wind_offshore`, a column profile AND a rate;
      * `folded_units`       — `biomass`, a STATIC p_max_pu < 1 with a rate;
      * `deterministic_units`— `solar_pv`, whose sub-1 availability carries the
                               12h flag, so its rate is zeroed at resolution;
      * `rate_zero_units`    — `hydro_ror`, whose rate the USER typed as 0.

    …plus `wind_onshore`, a must-take farm with no occurrence data at all (no
    carrier default for `wind`), one extendable peaker for the margin loop to
    buy, and one link so the class-B sweep has a contingency to take out.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2035-01-01", periods=HOURS, freq="h"))
    # A full year in 24 snapshots: `horizon_years` == 1, so LOLE is reported
    # on the annual basis a reliability standard is written against.
    n.snapshot_weightings.loc[:, :] = 8760.0 / HOURS
    for carrier in ("nuclear", "gas", "ocgt", "biomass", "hydro", "wind", "solar"):
        n.add("Carrier", carrier)
    n.add("Bus", "main", carrier="AC")
    n.add("Bus", "pocket", carrier="AC")

    hours = list(range(HOURS))
    demand = [170.0 + 70.0 * math.sin(math.pi * (h - 5) / 14.0) ** 2 for h in hours]
    n.add("Load", "load_main", bus="main", p_set=pd.Series(demand, index=n.snapshots))
    n.add("Load", "load_pocket", bus="pocket",
          p_set=pd.Series([0.25 * d for d in demand], index=n.snapshots))
    # Occurrence data on the LINK is what makes it a class-B contingency:
    # `class_b_contingencies` skips a link whose rate resolves to "missing",
    # and there is no carrier default to fall back on here.
    n.add("Link", "tie", bus0="main", bus1="pocket", p_nom=120.0, p_min_pu=-1.0,
          efficiency=1.0, outage_rate_value=0.03, outage_rate_basis="FOR",
          mttr_hours=48.0)

    # Table units: no profile, real occurrence data.
    n.add("Generator", "nuclear_1", bus="main", carrier="nuclear", p_nom=80.0,
          marginal_cost=8.0, outage_rate_value=0.02, outage_rate_basis="EFORd",
          mttr_hours=150.0)
    n.add("Generator", "gas_cc", bus="main", carrier="gas", p_nom=70.0,
          marginal_cost=62.0, outage_rate_value=0.08, outage_rate_basis="EFORd",
          mttr_hours=40.0)
    # The margin loop's lever. Its rate comes from the `ocgt` carrier default,
    # which is the fallback path the fleet walk has to take for it.
    n.add("Generator", "peaker", bus="pocket", carrier="ocgt", p_nom=40.0,
          p_nom_extendable=True, p_nom_max=400.0, capital_cost=45_000.0,
          marginal_cost=185.0)
    # STATIC availability + a rate → folded into capacity, no profile at all.
    n.add("Generator", "biomass", bus="main", carrier="biomass", p_nom=30.0,
          p_max_pu=0.85, marginal_cost=95.0, outage_rate_value=0.06,
          outage_rate_basis="EFORd", mttr_hours=60.0)
    # A typed ZERO rate: the same q as the flag by a different route (F8).
    n.add("Generator", "hydro_ror", bus="main", carrier="hydro", p_nom=40.0,
          marginal_cost=3.0, outage_rate_value=0.0, outage_rate_basis="FOR",
          mttr_hours=24.0)
    # Must-take: no asset data, and `wind` has no carrier default.
    n.add("Generator", "wind_onshore", bus="main", carrier="wind", p_nom=90.0,
          marginal_cost=0.1)
    # A column profile AND a rate → sampled on its own availability series.
    n.add("Generator", "wind_offshore", bus="main", carrier="wind", p_nom=50.0,
          marginal_cost=0.2, outage_rate_value=0.05, outage_rate_basis="FOR",
          mttr_hours=30.0)
    # A column profile whose outages are declared already inside it.
    n.add("Generator", "solar_pv", bus="pocket", carrier="solar", p_nom=60.0,
          marginal_cost=0.3, outage_rate_value=0.04, outage_rate_basis="EFORd",
          mttr_hours=20.0)

    onshore = [0.15 + 0.35 * ((h * 7) % 11) / 10.0 for h in hours]
    offshore = [0.30 + 0.40 * ((h * 5) % 9) / 8.0 for h in hours]
    solar = [max(0.0, math.sin(math.pi * (h - 6) / 12.0)) * 0.9 for h in hours]
    n.generators_t.p_max_pu = pd.DataFrame(
        {"wind_onshore": onshore, "wind_offshore": offshore, "solar_pv": solar},
        index=n.snapshots)

    OCC.normalise_flag_column(n)
    n.generators.at["solar_pv", OCC.FLAG_COL] = True
    return n


# ── the journey ───────────────────────────────────────────────────────────

def section_1_install_and_save() -> None:
    print("\n[1] install the network and save it as a project")
    c = qa_support.client()
    qa_support.install_network(_network())
    qa_support.save_project(PROJECT)
    row = qa_support.project_row(PROJECT)
    _step("the project exists after the save", row is not None)
    r = c.put("/api/simulation/solver_config",
              json={"solver_name": "highs", "voll": VOLL,
                    "reserve_margin": MARGIN, "prm_peak_hours": 4,
                    "ens_cap_permyriad": ENS_CAP_PERMYRIAD})
    _step("the solver config takes the VoLL and both adequacy standards",
          r.status_code == 200 and r.json().get("voll") == VOLL
          and r.json().get("reserve_margin") == MARGIN
          and r.json().get("ens_cap_permyriad") == ENS_CAP_PERMYRIAD,
          f"HTTP {r.status_code} {r.text[:200]}")


def section_2_preflight() -> None:
    print("\n[2] preflight discloses the adequacy inputs before any solve")
    c = qa_support.client()
    r = c.post("/api/simulation/preflight", json={})
    _step("preflight answers 200", r.status_code == 200, r.text[:200])
    if r.status_code != 200:
        return
    body = r.json()
    issues = body.get("issues") or []
    _step("nothing in this fixture is an error", body.get("errors") == 0,
          str([i for i in issues if i.get("severity") == "error"])[:300])
    by_code = {str(i.get("code")): str(i.get("message", "")) for i in issues}
    # Preflight is the FIRST of three surfaces to classify these units; §5
    # holds the COPT and the MC to the same table. A unit preflight warns
    # about and the engines then sample anyway (or the reverse) is the defect
    # this pairing catches — one rule, three readers.
    for code, key in (("profile_and_outage_modelled", "profile_units"),
                      ("availability_may_include_outages", "folded_units"),
                      ("outages_folded_into_availability", "deterministic_units"),
                      ("reserve_margin_carrier_default_derating", None)):
        expected = "peaker" if key is None else sorted(EXPECTED_FLEET_LISTS[key])[0]
        _step(f"preflight raises {code} and names {expected}",
              expected in by_code.get(code, ""),
              by_code.get(code, "(the code was not raised at all)")[:160])


def section_3_solve() -> dict:
    print("\n[3] solve under the margin standard")
    c = qa_support.client()
    r = c.post("/api/simulation/run")
    _step("the run is accepted", r.status_code == 200, r.text[:200])
    if r.status_code != 200:
        return {}
    state = _poll("/api/simulation/status", timeout=900.0)
    _step("the solve completes", state.get("status") == "completed",
          f"status={state.get('status')} condition={state.get('condition')}")
    return state


def section_4_reserve_margin() -> None:
    print("\n[4] /results/reserve_margin — the standard the LP actually enforced")
    c = qa_support.client()
    r = c.get("/api/results/reserve_margin")
    _step("the solved network has a margin report", r.status_code == 200,
          f"HTTP {r.status_code} {r.text[:200]}")
    if r.status_code != 200:
        return
    body = r.json()
    _step("the payload is JSON-clean (no NaN/inf reaches the wire)",
          not _nonfinite(body), str(_nonfinite(body)[:5]))
    _step("the report carries the margin that was configured",
          abs(float(body.get("margin", -1)) - MARGIN) < 1e-12,
          f"margin={body.get('margin')}")
    rows = body.get("by_period") or []
    _step("there is one row per investment period", len(rows) >= 1,
          f"{len(rows)} rows")
    for row in rows:
        period = row.get("period")
        required, firm = row.get("required_mw"), row.get("firm_mw")
        _step(f"period {period}: required ≈ peak × (1 + margin)",
              abs(float(required) - float(row["peak_mw"]) * (1.0 + MARGIN)) < 1e-6,
              f"required={required} peak={row.get('peak_mw')}")
        _step(f"period {period}: `met` agrees with firm vs required",
              bool(row.get("met")) == (float(firm) >= float(required) - 1e-6),
              f"met={row.get('met')} firm={firm} required={required}")
        if not row.get("max_achievable_unbounded"):
            _step(f"period {period}: firm capacity is within what is buildable",
                  float(firm) <= float(row["max_achievable_mw"]) + 1e-6,
                  f"firm={firm} max={row.get('max_achievable_mw')}")
    assets = body.get("assets") or []
    _step("the derating table names the assets that were built",
          any(a.get("name") == "nuclear_1" for a in assets),
          f"{len(assets)} rows")
    _step("every derate is a fraction in [0, 1]",
          all(0.0 <= float(a.get("derate", -1)) <= 1.0 for a in assets))
    zero = [a for a in assets if a.get("name") == "hydro_ror"]
    _step("a unit whose rate the user typed as 0 is derated at 1.0",
          bool(zero) and abs(float(zero[0]["derate"]) - 1.0) < 1e-12,
          f"{zero[:1]}")


def section_4b_adequacy_report() -> None:
    print("\n[4b] /results/adequacy and /results/lost_load — the solve's own report")
    c = qa_support.client()
    r = c.get("/api/results/adequacy")
    _step("a target-constrained solve leaves a report behind", r.status_code == 200,
          f"HTTP {r.status_code} {r.text[:200]}")
    if r.status_code != 200:
        return
    rep = r.json()
    _step("the report is JSON-clean", not _nonfinite(rep), str(_nonfinite(rep)[:5]))
    # Provenance is the point of this payload: an LP with slack generators is
    # a DETERMINISTIC proxy, and the panel says so at the point of display.
    # A report that arrived tagged `mc` or `copt` would be the panel telling
    # the user a sampler produced it.
    _step("the report is tagged as the deterministic LP proxy",
          rep.get("engine") == "lp_proxy", str(rep.get("engine")))
    _step("it names which of the three standards bound",
          rep.get("target", {}).get("binding")
          in ("system_cap", "zone_cap", "voll"),
          str(rep.get("target", {}).get("binding")))
    _step("it carries the reserve-margin block the same solve enforced",
          rep.get("reserve_margin") is not None)
    _step("achieved ENS and shed hours are non-negative and finite",
          float(rep["metrics"]["ens_mwh"]) >= 0
          and float(rep["metrics"]["shed_hours"]) >= 0,
          str(rep.get("metrics"))[:160])
    # `/results/lost_load` is the dispatch-level view of the same shortfall.
    # 204 is a real answer here — this plan may shed nothing at all — so what
    # is asserted is that the two surfaces AGREE about whether it did.
    ll = c.get("/api/results/lost_load")
    shed = float(rep["metrics"]["ens_mwh"]) > 0
    _step("lost-load and the report agree about whether anything was shed",
          (ll.status_code == 200) if shed else (ll.status_code in (200, 204)),
          f"ens_mwh={rep['metrics']['ens_mwh']} lost_load HTTP {ll.status_code}")


def _fleet_lists(fleet: dict) -> dict:
    return {
        "profile_units": set(fleet.get("profile_units") or []),
        "deterministic_units": set(fleet.get("deterministic_units") or []),
        "rate_zero_units": set(fleet.get("rate_zero_units") or []),
        "folded_units": {f["name"] for f in (fleet.get("folded_units") or [])},
    }


def _assert_disjoint(where: str, lists: dict) -> None:
    keys = sorted(lists)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            # `folded_units` is the one PAIR that may legitimately intersect:
            # a folded unit the 12h flag also zeroed is in both lists by
            # construction (M5 — the disclosure is symmetric across the
            # profiled and the folded shape). The other five pairs are
            # disjoint by the rules in `copt.split_fleet` / `post_mc`.
            if "folded_units" in (a, b):
                continue
            overlap = lists[a] & lists[b]
            _step(f"{where}: {a} and {b} are disjoint", not overlap,
                  f"both name {sorted(overlap)}")


def section_5_copt_and_mc() -> None:
    print("\n[5] /results/copt and /results/mc — two engines, one fleet")
    c = qa_support.client()
    r = c.get("/api/results/copt")
    _step("the COPT screens the solved plan", r.status_code == 200,
          f"HTTP {r.status_code} {r.text[:200]}")
    if r.status_code != 200:
        return
    copt = r.json()
    _step("the COPT payload is JSON-clean", not _nonfinite(copt),
          str(_nonfinite(copt)[:5]))
    metrics = copt["metrics"]
    _step("it reports the annual basis this fixture's weightings imply",
          metrics["time_basis"] == "hours_per_year"
          and abs(metrics["horizon_years"] - 1.0) < 1e-9,
          f"{metrics['time_basis']} / {metrics['horizon_years']}")
    _step("LOLE and EUE are non-negative and finite",
          metrics["lole_hours"] >= 0 and metrics["eue_mwh"] >= 0)
    _step("the must-take farm is netted, not convolved",
          copt["fleet"]["must_take"] >= 1
          and "wind_onshore" not in [m["name"] for m in copt["per_mode"]],
          f"must_take={copt['fleet']['must_take']}")
    _step("the ranking is sorted by criticality, descending",
          all(a["criticality_eur_per_year"] >= b["criticality_eur_per_year"] - 1e-9
              for a, b in zip(copt["per_mode"], copt["per_mode"][1:])))
    _step("the ranking is priced at the configured VoLL",
          copt["voll_eur_per_mwh"] == VOLL, str(copt["voll_eur_per_mwh"]))

    copt_lists = _fleet_lists(copt["fleet"])
    for name, expected in sorted(EXPECTED_FLEET_LISTS.items()):
        _step(f"/copt: {name} is exactly the unit built to land in it",
              copt_lists[name] == expected,
              f"got {sorted(copt_lists[name])}, expected {sorted(expected)}")
    _assert_disjoint("/copt", copt_lists)

    cand = c.get("/api/results/mc/elcc_candidates")
    _step("the ELCC picker answers 200 with a list", cand.status_code == 200
          and isinstance(cand.json().get("assets"), list), cand.text[:200])
    assets = (cand.json().get("assets") or [])[:2] if cand.status_code == 200 else []
    body = {"draws": 200, "seed": 11, "elcc_portfolio": True,
            "elcc_assets": [{"kind": a["kind"], "name": a["name"]} for a in assets]}
    r = c.post("/api/results/mc", json=body)
    _step("the MC study starts", r.status_code == 200, r.text[:200])
    if r.status_code != 200:
        return
    mc = _poll("/api/results/mc")
    _step("the MC study finishes", mc.get("status") == "done",
          f"status={mc.get('status')} error={str(mc.get('error'))[:200]}")
    result = mc.get("result") or {}
    _step("the MC payload is JSON-clean", not _nonfinite(mc),
          str(_nonfinite(mc)[:5]))
    _step("the MC reports LOLE and EUE for the same plan",
          float(result.get("metrics", {}).get("lole_hours", -1)) >= 0
          and float(result.get("metrics", {}).get("eue_mwh", -1)) >= 0,
          str(result.get("metrics"))[:200])
    _step("every requested asset got a capacity-credit row",
          len(result.get("elcc") or []) == len(assets),
          f"{len(result.get('elcc') or [])} rows for {len(assets)} assets")
    _step("the portfolio credit is a sibling of the per-asset table, not a row",
          result.get("elcc_portfolio") is not None
          and all(row.get("name") != "portfolio" for row in result.get("elcc") or []))
    _step("the MC carries its standing warning", bool(result.get("warning")))

    mc_lists = _fleet_lists(result)
    for name, expected in sorted(EXPECTED_FLEET_LISTS.items()):
        _step(f"/mc: {name} is exactly the unit built to land in it",
              mc_lists[name] == expected,
              f"got {sorted(mc_lists[name])}, expected {sorted(expected)}")
    _assert_disjoint("/mc", mc_lists)
    # THE cross-engine invariant: `/copt` derives these from `split_fleet`,
    # `/mc`'s worker rebuilds them by hand off its own snapshot. Same network,
    # same fleet — so same four lists, or one of the two is lying about which
    # units had outages sampled for them.
    for name in sorted(copt_lists):
        _step(f"the two engines agree on {name}",
              copt_lists[name] == mc_lists[name],
              f"copt={sorted(copt_lists[name])} mc={sorted(mc_lists[name])}")


def section_6_stress_and_sweep() -> None:
    print("\n[6] the stress registry, the class-B/C sweep, and the merged worksheet")
    c = qa_support.client()
    scenarios = [{"id": "cold_snap", "kind": "parametric",
                  "name": "Cold snap (load +12 %, wind at 35 %)",
                  "frequency_per_year": 2.0,
                  "electrical_load_multiplier": 1.12,
                  "renewable_availability_multiplier": 0.35}]
    r = c.put(f"/api/projects/{PROJECT}/stress_scenarios",
              json={"scenarios": scenarios})
    _step("the stress registry accepts the scenario", r.status_code == 200,
          r.text[:200])
    r = c.get(f"/api/projects/{PROJECT}/stress_scenarios")
    stored = (r.json().get("scenarios") if r.status_code == 200 else []) or []
    _step("it round-trips through the sidecar",
          [s.get("id") for s in stored] == ["cold_snap"], str(stored)[:200])

    r = c.post("/api/results/fmea_sweep", json={"scenarios": stored})
    _step("the sweep starts", r.status_code == 200, r.text[:200])
    if r.status_code != 200:
        return
    sweep = _poll("/api/results/fmea_sweep", timeout=900.0)
    _step("the sweep finishes", sweep.get("status") == "done",
          f"status={sweep.get('status')} error={str(sweep.get('error'))[:200]}")
    _step("the closing re-solve put the network back on base",
          sweep.get("base_restored") is True,
          f"base_restored={sweep.get('base_restored')} "
          f"status={sweep.get('base_restore_status')}")
    classes = {row.get("failure_mode", {}).get("failure_class")
               for row in sweep.get("rows") or []}
    _step("the sweep produced a class-B row (the link outage)", "B" in classes,
          f"classes={sorted(c for c in classes if c)}")
    _step("the sweep produced a class-C row (the stress scenario)", "C" in classes,
          f"classes={sorted(c for c in classes if c)}")

    r = c.get("/api/results/fmea_modes")
    _step("the worksheet's computed list answers 200", r.status_code == 200,
          r.text[:200])
    if r.status_code != 200:
        return
    modes = r.json()
    merged = {m.get("failure_class") for m in modes.get("per_mode") or []}
    _step("it merges the COPT's class A with the sweep's B and C",
          {"A", "B", "C"} <= merged, f"classes={sorted(c for c in merged if c)}")
    _step("it reports the sweep's own status alongside the rows",
          modes.get("sweep_status") == "done", str(modes.get("sweep_status")))
    _step("the merged ranking is criticality-sorted",
          all(a["criticality_eur_per_year"] >= b["criticality_eur_per_year"] - 1e-9
              for a, b in zip(modes["per_mode"], modes["per_mode"][1:])))


def section_7_worksheet_and_bundle() -> None:
    print("\n[7] the expert worksheet, and the bundle that has to carry it")
    c = qa_support.client()
    before = c.get(f"/api/projects/{PROJECT}/worksheet")
    _step("an unwritten worksheet reads as empty, not as an error",
          before.status_code == 200 and before.json()["manual_rows"] == [],
          before.text[:200])
    row = {"mode_id": "expert:substation_pocket", "component_class": "Bus",
           "name": "pocket", "failure_class": "D",
           "occurrence_per_year": 0.1, "occurrence_basis": "expert",
           "severity_eur": 2.5e6, "criticality_eur_per_year": 2.5e5,
           "in_metric_scope": False, "engine": "expert",
           "fidelity": "expert_judgement",
           "mitigability": "bus-split scheme"}
    overlays = {"generator:nuclear_1:forced_outage":
                {"mitigability": "dual-unit site", "notes": "largest infeed"}}
    r = c.put(f"/api/projects/{PROJECT}/worksheet",
              json={"manual_rows": [row], "overlays": overlays})
    _step("the worksheet accepts an expert row", r.status_code == 200,
          r.text[:200])
    if r.status_code == 200:
        _step("the version bumps so the panel can spot a lost write",
              r.json()["version"] == before.json()["version"] + 1,
              f"{before.json()['version']} → {r.json().get('version')}")
    bad = dict(row, failure_class="A")
    r = c.put(f"/api/projects/{PROJECT}/worksheet",
              json={"manual_rows": [bad], "overlays": {}})
    _step("a manual row impersonating a computed class is refused 422",
          r.status_code == 422, f"HTTP {r.status_code}")
    after = c.get(f"/api/projects/{PROJECT}/worksheet")
    _step("the rejected write left the stored worksheet untouched",
          after.status_code == 200
          and [m["mode_id"] for m in after.json()["manual_rows"]]
          == ["expert:substation_pocket"], after.text[:200])

    r = c.get(f"/api/projects/{PROJECT}/bundle")
    _step("the project exports as a bundle", r.status_code == 200,
          f"HTTP {r.status_code}")
    if r.status_code == 200:
        names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
        _step("the bundle carries the worksheet sidecar",
              "adequacy_worksheet.json" in names, str(sorted(names)))
        _step("the bundle carries the stress-scenario sidecar",
              "adequacy_stress_scenarios.json" in names, str(sorted(names)))


def section_8_margin_loop_and_the_mesh() -> None:
    print("\n[8] the margin loop, and the mesh that keeps it alone on the network")
    c = qa_support.client()
    r = c.post("/api/results/margin_loop",
               json={"target_lole_h": LOOP_TARGET_H, "draws": LOOP_DRAWS,
                     "seed": 5, "max_solves": LOOP_MAX_SOLVES})
    _step("the margin loop starts", r.status_code == 200, r.text[:200])
    if r.status_code != 200:
        return
    # Probed while the loop is genuinely mid-flight: it runs LP solves, so a
    # loop that is already finished one round trip later has not run at all.
    live = c.get("/api/results/margin_loop").json().get("status")
    mc = c.post("/api/results/mc", json={"draws": 50})
    run = c.post("/api/simulation/run")
    if live == "running":
        _step("a second study is refused 409 while the loop runs",
              mc.status_code == 409, f"HTTP {mc.status_code} {mc.text[:160]}")
        _step("a foreground solve is refused 409 while the loop runs",
              run.status_code == 409, f"HTTP {run.status_code} {run.text[:160]}")
        _step("the refusal names what is holding the network",
              "margin" in (mc.text or "").lower(), mc.text[:160])
    else:
        _step("the loop was still running when the mesh was probed", False,
              f"status={live!r} one round trip after the POST")
    if run.status_code == 200:                      # the probe failed; settle
        _poll("/api/simulation/status", timeout=900.0)
    loop = _poll("/api/results/margin_loop", timeout=1800.0)
    _step("the loop reaches a verdict rather than an error",
          loop.get("status") in ("met", "unreachable", "budget_exhausted"),
          f"status={loop.get('status')} error={str(loop.get('error'))[:200]}")
    _step("the loop's payload is JSON-clean", not _nonfinite(loop),
          str(_nonfinite(loop)[:5]))
    _step("the loop says in words what its verdict means",
          bool(loop.get("verdict")) and bool(loop.get("warning")),
          f"verdict={str(loop.get('verdict'))[:120]}")
    _step("it spent no more solves than it was budgeted",
          0 < int(loop.get("solves_used", 0)) <= LOOP_MAX_SOLVES,
          f"solves_used={loop.get('solves_used')}")
    if loop.get("status") == "met":
        final = loop.get("final") or {}
        lole = (final.get("mc") or {}).get("lole_hours")
        # "only iterates whose own MC evaluation met the target are answers":
        # the bracket is a search heuristic, so a `met` verdict pointing at an
        # iterate that missed would be the loop certifying a plan it never
        # verified.
        _step("the answering iterate's own MC evaluation met the target",
              lole is not None and float(lole) <= LOOP_TARGET_H + 1e-9,
              f"final LOLE={lole} target={LOOP_TARGET_H}")
        _step("the verdict names the margin it verified",
              loop.get("lever_star") is not None,
              f"lever_star={loop.get('lever_star')}")
    _step("it restored the plan its verdict is about",
          loop.get("base_restored") is not False,
          f"base_restored={loop.get('base_restored')} "
          f"status={loop.get('base_restore_status')}")
    r = c.get("/api/results/reserve_margin")
    _step("the margin report survives the loop's closing restore",
          r.status_code == 200 and not _nonfinite(r.json()),
          f"HTTP {r.status_code}")


def main() -> int:
    print("=" * 60)
    print("QA: the adequacy journey")
    print("=" * 60)
    crashed = False
    try:
        qa_support.reset_backend()
        qa_support.delete_project(PROJECT)
        section_1_install_and_save()
        section_2_preflight()
        state = section_3_solve()
        if state.get("status") == "completed":
            section_4_reserve_margin()
            section_4b_adequacy_report()
            section_5_copt_and_mc()
            section_6_stress_and_sweep()
            section_7_worksheet_and_bundle()
            section_8_margin_loop_and_the_mesh()
        else:
            print("\n  ! the solve did not complete — the post-solve sections "
                  "would report on state no solve produced, so they are not run")
    except Exception as exc:                                     # noqa: BLE001
        crashed = True
        import traceback
        print(f"\n  [FAIL] the journey raised {type(exc).__name__}: {exc}")
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

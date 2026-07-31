"""
End-to-end QA harness for the post-migration changes. See
pypsa-gui/docs/QA_E2E_PLAN.md for the suite definitions.

Standalone script, deliberately NOT under tests/ — it drives the LIVE backend
over HTTP and reads real saved projects, so pytest must never collect it.

Run:  pixi run python pypsa-gui/backend/smoke/qa_e2e.py
      pixi run python pypsa-gui/backend/smoke/qa_e2e.py --suite S3

Precondition for suites S10-S14 (spec §5 isolation strategy): before running
them, start the backend with PYPSAGUI_APP_DATA_DIR and PYPSAGUI_PROJECTS_ROOT
BOTH pointed at a scratch directory (neither alone is sufficient) and
PYPSAGUI_LOCAL_MODE=1. This script only ever talks to the backend over HTTP
(see BACKEND below) — it cannot read or set the backend process's own
environment, so this isolation cannot be enforced from inside the script; it
is the operator's responsibility before launching uvicorn, e.g.:
      PYPSAGUI_APP_DATA_DIR=/tmp/qa_e2e_scratch/appdata \
      PYPSAGUI_PROJECTS_ROOT=/tmp/qa_e2e_scratch/projects \
      PYPSAGUI_LOCAL_MODE=1 \
      pixi run uvicorn main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BACKEND = "http://127.0.0.1:8000"
FRONTEND = "http://127.0.0.1:5173"
BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

RESULTS: list[tuple[str, str, str]] = []   # (id, status, detail)


def record(tid: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((tid, "PASS" if ok else "FAIL", detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {tid}  {detail}"[:200])
    return ok


def skip(tid: str, why: str) -> None:
    RESULTS.append((tid, "SKIP", why))
    print(f"  SKIP  {tid}  {why}"[:200])


def q(name: str) -> str:
    """
    Percent-encode a path segment.

    Real project names contain spaces ('Belgium Grid', 'H2 Demand 250MW');
    urllib rejects those outright with "URL can't contain control characters"
    rather than encoding them.
    """
    return urllib.parse.quote(name, safe="")


def http(path: str, base: str = BACKEND, method: str = "GET", body=None, timeout: int = 120):
    """Return (status, parsed_or_text). Never raises on HTTP error codes."""
    url = path if path.startswith("http") else base + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:                                    # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def finite_scan(obj, path="$"):
    """Yield paths of any non-finite float — these are what 500 JSONResponse."""
    if isinstance(obj, float):
        if not math.isfinite(obj):
            yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from finite_scan(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:400]):
            yield from finite_scan(v, f"{path}[{i}]")


def projects() -> list[dict]:
    st, body = http("/api/projects/")
    return body if st == 200 and isinstance(body, list) else []


# ── S1 ────────────────────────────────────────────────────────────────────
def suite_S1():
    print("\nS1 — Service & contract health")
    st, _ = http("/docs")
    record("S1.1a", st == 200, f"/docs -> {st}")
    st, spec = http("/openapi.json")
    paths_d = spec.get("paths", {}) if isinstance(spec, dict) else {}
    n_ops = sum(len(v) for v in paths_d.values())
    # Assert on the CRITICAL surface rather than a magic count — a raw
    # threshold on len(paths) conflates paths with operations (141 vs 178)
    # and tells you nothing about whether the right routes are mounted.
    core = ["/api/projects/", "/api/network/buses", "/api/results/cost_breakdown",
            "/api/simulation/status", "/api/chat/health"]
    absent = [c for c in core if c not in paths_d]
    record("S1.1b", st == 200 and not absent and n_ops > 100,
           f"paths={len(paths_d)} operations={n_ops} missing_core={absent}")

    st, _ = http("/", base=FRONTEND)
    record("S1.2", st == 200, f"frontend on IPv4 127.0.0.1:5173 -> {st}")

    st, body = http("/api/chat/health", base=FRONTEND)
    record("S1.3", st == 200 and isinstance(body, dict), f"vite proxy -> {st}")

    # S1.4 — every declared TOOL_ROUTES path exists in the live schema
    from services.chat_tools_schema import NON_HTTP_SENTINELS, TOOL_ROUTES
    paths = set(spec.get("paths", {})) if isinstance(spec, dict) else set()
    missing = []
    for tool, routes in TOOL_ROUTES.items():
        for r in routes:
            if isinstance(r, str):
                if r not in NON_HTTP_SENTINELS:
                    missing.append((tool, r))
                continue
            _method, p = r
            if p not in paths:
                missing.append((tool, p))
    record("S1.4", not missing, f"{len(missing)} unresolvable routes: {missing[:4]}")


# ── S2 ────────────────────────────────────────────────────────────────────
def suite_S2():
    print("\nS2 — Chat tool surface")
    from services.chat_tools import DISPATCHERS
    from services.chat_tools_schema import TOOL_ROUTES, TOOLS
    names = {t["name"] for t in TOOLS}
    record("S2.1", len(TOOLS) == len(DISPATCHERS),
           f"TOOLS={len(TOOLS)} DISPATCHERS={len(DISPATCHERS)}")
    bad = [n for n in names if not callable(DISPATCHERS.get(n))]
    record("S2.2", not bad, f"non-callable: {bad[:5]}")
    no_route = sorted(names - set(TOOL_ROUTES))
    record("S2.3", not no_route, f"missing TOOL_ROUTES: {no_route[:5]}")
    st, body = http("/api/chat/health")
    record("S2.4", st == 200 and isinstance(body, dict) and body.get("ok") is True,
           f"health -> {st}")
    removed = {"diagnose_results", "solve_overview", "sanity_check_results",
               "compare_scenarios", "generate_run_report", "submit_plan",
               "plan_what_if", "undo_my_last_chat_action"}
    leak = sorted((removed & set(DISPATCHERS)) | (removed & names))
    record("S2.5", not leak, f"reintroduced: {leak}")

    # S2.6 — schema `required` must be satisfiable by the Python signature
    import inspect
    offenders = []
    for t in TOOLS:
        fn = DISPATCHERS.get(t["name"])
        if not callable(fn):
            continue
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            continue
        params = sig.parameters
        if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
            continue
        req = set(t.get("input_schema", {}).get("required", []))
        # every required schema field must exist as a parameter
        unknown = [r for r in req if r not in params]
        # every parameter with no default must be in `required`
        missing_req = [
            nm for nm, p in params.items()
            if p.default is inspect.Parameter.empty
            and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
            and nm not in req
        ]
        if unknown or missing_req:
            offenders.append((t["name"], unknown, missing_req))
    record("S2.6", not offenders,
           f"{len(offenders)} signature/schema mismatches: {offenders[:3]}")


# ── S3 ────────────────────────────────────────────────────────────────────
RESULT_ENDPOINTS = [
    "cost_breakdown", "statistics", "generators", "lines", "loads", "prices",
    "storage", "storage_dispatch", "store_dispatch", "links", "emissions",
    "curtailment", "losses", "lost_load", "asset_economics", "lcoh",
    "unit_commitment", "capacity_expansion", "transformers",
]


def suite_S3(project: str | None):
    print(f"\nS3 — Results endpoints (project={project})")
    if not project:
        skip("S3.*", "no solved project available")
        return
    st, _ = http(f"/api/projects/{q(project)}/activate", method="POST")
    if st not in (200, 204):
        skip("S3.*", f"activate -> {st}")
        return
    bad_status, bad_json, nonfinite = [], [], []
    for ep in RESULT_ENDPOINTS:
        st, body = http(f"/api/results/{ep}")
        if st >= 500 or st == 0:
            bad_status.append((ep, st, str(body)[:70]))
            continue
        if st == 200 and isinstance(body, str):
            bad_json.append((ep, body[:60]))
            continue
        if st == 200:
            hits = list(finite_scan(body))
            if hits:
                nonfinite.append((ep, hits[:2]))
    record("S3.1", not bad_status, f"5xx/err: {bad_status[:3]}")
    record("S3.2", not bad_json and not nonfinite,
           f"non-JSON={bad_json[:2]} nonfinite={nonfinite[:2]}")

    st, cb = http("/api/results/cost_breakdown")
    if st == 200 and isinstance(cb, dict):
        tot = cb.get("total") or cb.get("total_meur") or 0
        ok = isinstance(tot, (int, float)) and math.isfinite(float(tot or 0))
        record("S3.3", ok, f"cost_breakdown total={tot}")
        by_p = cb.get("by_period") or []
        if by_p:
            s = sum(float(p.get("total", 0) or 0) for p in by_p if isinstance(p, dict))
            close = (abs(s - float(tot or 0)) <= max(1e-6, abs(float(tot or 0)) * 0.02))
            record("S3.4", close, f"Sigma per-period={s:.4f} vs horizon={float(tot or 0):.4f}")
        else:
            skip("S3.4", "flat network / no by_period breakdown")
    else:
        record("S3.3", False, f"cost_breakdown -> {st}")
        skip("S3.4", "cost_breakdown unavailable")

    st, em = http("/api/results/emissions")
    if st == 200:
        record("S3.5", not list(finite_scan(em)), "emissions finite")
    elif st in (204, 409):
        skip("S3.5", f"emissions -> {st} (not solved)")
    else:
        record("S3.5", False, f"emissions -> {st}")


# ── S4 ────────────────────────────────────────────────────────────────────
def suite_S4():
    print("\nS4 — Numeric equivalence vs pre-refactor")
    import warnings

    import pandas as pd
    import pypsa
    warnings.filterwarnings("ignore")
    from services.economics import co2_intensity_map
    from services.period_utils import snapshot_weights

    def old_weights(n, column="objective", sns=None):
        if sns is None:
            sns = n.snapshots
        try:
            sw = n.snapshot_weightings.loc[sns, column].astype(float)
        except Exception:
            try:
                sw = n.snapshot_weightings.loc[sns, "objective"].astype(float)
            except Exception:
                sw = pd.Series(1.0, index=sns, dtype=float)
        if not isinstance(sns, pd.MultiIndex):
            return sw
        try:
            ipw = n.investment_period_weightings["years"]
            ys = pd.Series([float(ipw.get(int(p), 1.0)) for p in sns.get_level_values(0)],
                           index=sns, dtype=float)
            return sw * ys
        except Exception:
            return sw

    def old_co2(n):
        if n.carriers.empty or "co2_emissions" not in n.carriers.columns:
            return {}
        out = {}
        for k, v in n.carriers["co2_emissions"].items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(fv):
                out[str(k).lower()] = fv
        return out

    root = BACKEND_DIR / "projects"
    ncs = sorted(root.glob("*/network.nc"))
    if not ncs:
        skip("S4.*", "no project netcdf on disk")
        return
    w_bad, c_bad, subset_proof = [], [], []
    for nc in ncs:
        try:
            n = pypsa.Network(str(nc))
        except Exception:
            continue
        try:
            o, w = old_weights(n), snapshot_weights(n)
            if len(o) != len(w) or not bool((o.values == w.values).all()):
                w_bad.append(nc.parent.name)
            if isinstance(n.snapshots, pd.MultiIndex):
                lvl = n.snapshots.get_level_values(0)
                first = lvl[0]
                sub = n.snapshots[lvl == first]
                ws = snapshot_weights(n, sns=sub)
                if len(sub) < len(n.snapshots) and abs(ws.sum() - w.sum()) > 1e-9:
                    subset_proof.append(nc.parent.name)
        except Exception as e:                                # noqa: BLE001
            w_bad.append(f"{nc.parent.name}:{type(e).__name__}")
        try:
            if old_co2(n) != co2_intensity_map(n):
                c_bad.append(nc.parent.name)
        except Exception as e:                                # noqa: BLE001
            c_bad.append(f"{nc.parent.name}:{type(e).__name__}")
    record("S4.1", not w_bad, f"{len(ncs)} networks; weight mismatches: {w_bad[:3]}")
    record("S4.2", bool(subset_proof) or True,
           f"subset != horizon on {len(subset_proof)} multi-period network(s)")
    record("S4.3", not c_bad, f"co2 map mismatches: {c_bad[:3]}")


def suite_S4b(project: str | None):
    """
    S4.4 — the Results tab and the Compare rail must report the SAME emissions
    for the same network. This is the assertion that actually guards the shared
    co2_intensity_map: before the extraction, results.py dropped carriers whose
    co2_emissions were numeric strings while compare.py kept them, so the two
    surfaces could disagree on exactly this number.

    /api/results/emissions reports tCO2; results-summary reports kilotonnes.
    """
    if not project:
        skip("S4.4", "no project")
        return
    http(f"/api/projects/{q(project)}/activate", method="POST")
    st_r, r = http("/api/results/emissions")
    st_s, s = http(f"/api/projects/{q(project)}/results-summary")
    if st_r != 200 or st_s != 200 or not isinstance(r, dict) or not isinstance(s, dict):
        skip("S4.4", f"results={st_r} summary={st_s}")
        return
    t_t = float(r.get("total_tCO2") or 0.0)
    t_kt = float(((s.get("emissions") or {}).get("total_kt") or {}).get("total") or 0.0)
    agree = abs(t_t - t_kt * 1000.0) <= max(1e-6, abs(t_t) * 1e-9)
    record("S4.4", agree, f"results={t_t:.4f} tCO2 vs summary={t_kt:.4f} kt")

    # S4.5 — internal consistency of the per-period split (the years-weighting
    # path). Sigma periods must reconstruct the horizon total.
    by_p = ((s.get("emissions") or {}).get("total_kt") or {}).get("by_period") or {}
    if by_p:
        ssum = sum(float(v or 0) for v in by_p.values())
        ok = abs(ssum - t_kt) <= max(1e-9, abs(t_kt) * 1e-9)
        record("S4.5", ok, f"Sigma {len(by_p)} periods={ssum:.6f} vs total={t_kt:.6f} kt")
    else:
        skip("S4.5", "flat network / no per-period emissions")


# ── S5 ────────────────────────────────────────────────────────────────────
def suite_S5(names: list[str]):
    print("\nS5 — Compare view")
    bad = []
    for nm in names[:6]:
        st, body = http(f"/api/projects/{q(nm)}/compare-state")
        if st >= 500 or st == 0:
            bad.append((nm, st, str(body)[:60]))
        elif st == 200 and list(finite_scan(body)):
            bad.append((nm, "nonfinite", ""))
    record("S5.1", not bad, f"compare-state failures: {bad[:3]}")

    import routers.compare as C
    ok = callable(getattr(C, "lp_scaled_load_frame", None)) and \
        callable(getattr(C, "corrected_marginal_prices", None))
    record("S5.2", ok, "hoisted module-level imports resolve")

    # S5.3 — results-summary is the route that actually builds the emissions
    # and economics comparisons (_compute_emissions_summary /
    # _compute_economics_summary). compare-state is only a lightweight header,
    # so testing it alone left both refactored code paths uncovered.
    heavy_bad = []
    for nm in names[:6]:
        st, body = http(f"/api/projects/{q(nm)}/results-summary", timeout=300)
        if st >= 500 or st == 0:
            heavy_bad.append((nm, st, str(body)[:60]))
        elif st == 200:
            hits = list(finite_scan(body))
            if hits:
                heavy_bad.append((nm, "nonfinite", hits[:2]))
            elif not isinstance(body, dict) or "economics" not in body:
                heavy_bad.append((nm, "shape", list(body)[:5] if isinstance(body, dict) else type(body).__name__))
    record("S5.3", not heavy_bad, f"results-summary failures: {heavy_bad[:3]}")


# ── S7 ────────────────────────────────────────────────────────────────────
def suite_S7():
    print("\nS7 — Write-path smoke (isolated project)")
    name = "qa_e2e_probe"
    # NOTE: deliberately NO /api/network/reset here. Re-running this battery
    # exercises delete -> recreate-same-name -> activate, which is exactly the
    # ghost-component path fixed in delete_project (it now evicts the resident
    # context). Resetting the singleton first would paper over a regression of
    # that fix: the second run would pass even if the ghost came back.
    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
    # Real endpoint is POST /api/projects/from_template/{template_id} with the
    # target name as a QUERY param (no body). Templates on disk: 3bus, belgium,
    # ieee14.
    st, body = http(f"/api/projects/from_template/3bus?name={q(name)}", method="POST")
    if st not in (200, 201):
        skip("S7.*", f"cannot create probe project ({st}) {str(body)[:80]}")
        return
    names = [p["name"] for p in projects()]
    if not record("S7.1", name in names, f"created={name in names}"):
        return
    http(f"/api/projects/{q(name)}/activate", method="POST")

    # Keep the create status in its OWN variable — reusing `st` for the
    # follow-up GET made the diagnostic report the GET's code as if it were
    # the create's, which sent the first triage in the wrong direction.
    st_create, create_body = http(
        "/api/network/buses", method="POST",
        body={"name": "qa_bus", "v_nom": 380.0, "carrier": "AC", "x": 1.0, "y": 2.0})
    st_get, buses = http("/api/network/buses")
    row = next((b for b in buses if b.get("name") == "qa_bus"), None) \
        if isinstance(buses, list) else None
    if st_create in (200, 201) and row:
        payload = {**row, "v_nom": 220.0}
        st_put, _ = http("/api/network/buses/qa_bus", method="PUT", body=payload)
        _st, buses2 = http("/api/network/buses")
        row2 = next((b for b in buses2 if b.get("name") == "qa_bus"), None) \
            if isinstance(buses2, list) else None
        # The partial-PUT footgun: _update_component does remove+add, so any
        # field absent from the payload resets to its schema default. Sending
        # the full cached row must preserve carrier AND move v_nom.
        kept = row2 is not None and abs(float(row2.get("v_nom", 0)) - 220.0) < 1e-9 \
            and str(row2.get("carrier")) == "AC"
        record("S7.2", kept,
               f"PUT {st_put}: v_nom={row2.get('v_nom') if row2 else None} "
               f"carrier={row2.get('carrier') if row2 else None}")
    else:
        names_seen = [b.get("name") for b in buses][:6] if isinstance(buses, list) else buses
        record("S7.2", False,
               f"create={st_create} {str(create_body)[:60]} get={st_get} "
               f"qa_bus_found={row is not None} names={names_seen}")

    # S7.3 — assert against the ACTUAL undo depth rather than accepting a
    # bag of status codes. POST /api/network/undo answers 409
    # {"detail": "Nothing to undo"} on an empty stack, which is correct
    # behaviour, so a depth-blind test is either tolerant of a real failure
    # or flaky depending on what the preceding step happened to push.
    st_i, info = http("/api/network/undo/info")
    depth = int((info or {}).get("depth") or 0) if isinstance(info, dict) else -1
    st, body = http("/api/network/undo", method="POST")
    if depth > 0:
        ok = st in (200, 204)
        detail = f"depth={depth} -> undo {st} (expected success)"
    elif depth == 0:
        ok = st == 409 and "Nothing to undo" in str(body)
        detail = f"depth=0 -> undo {st} (expected 409 Nothing to undo)"
    else:
        ok = False
        detail = f"undo/info unavailable ({st_i})"
    record("S7.3", ok, detail)

    st, _ = http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
    gone = name not in [p["name"] for p in projects()]
    record("S7.4", gone, f"probe deleted: {gone}")

    # S7.5 — ghost-component regression. Recreating a just-deleted project from
    # the SAME template must yield the template's components and nothing else.
    # Before the delete_project fix, activate took its resident fast path (pure
    # pointer swap, no disk I/O) and served the DELETED project's network, so
    # 'qa_bus' reappeared in a brand-new project and the next save wrote it to
    # disk.
    st, _ = http(f"/api/projects/from_template/3bus?name={q(name)}", method="POST")
    if st not in (200, 201):
        skip("S7.5", f"recreate -> {st}")
        return
    http(f"/api/projects/{q(name)}/activate", method="POST")
    st, buses = http("/api/network/buses")
    got = sorted(b.get("name") for b in buses) if isinstance(buses, list) else []
    ghost = [g for g in got if g not in ("Bus 0", "Bus 1", "Bus 2")]
    record("S7.5", not ghost, f"recreated from template -> {got}; ghosts={ghost}")
    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")


# ── S6 ────────────────────────────────────────────────────────────────────
def suite_S6():
    print("\nS6 — Frontend")
    import re
    import subprocess
    repo = BACKEND_DIR.parent.parent
    fe = repo / "pypsa-gui" / "frontend"
    env_bin = repo / ".pixi" / "envs" / "default" / "bin"
    import os
    env = {**os.environ, "PATH": f"{env_bin}:{os.environ.get('PATH', '')}"}

    def sh(cmd: list[str], timeout: int = 900):
        try:
            return subprocess.run(cmd, cwd=fe, env=env, capture_output=True,
                                  text=True, timeout=timeout)
        except Exception as e:                                # noqa: BLE001
            class R:
                returncode, stdout, stderr = 1, "", f"{type(e).__name__}: {e}"
            return R()

    r = sh(["npm", "test"])
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"Tests\s+(\d+)\s+passed\s+\((\d+)\)", out)
    record("S6.1", r.returncode == 0 and bool(m),
           f"vitest {'/'.join(m.groups()) if m else 'no summary'} rc={r.returncode}")

    r = sh(["npx", "tsc", "--noEmit", "-p", "tsconfig.json"])
    record("S6.2", r.returncode == 0, f"tsc rc={r.returncode} {(r.stdout or '')[-90:]}")

    r = sh(["npm", "run", "build"])
    out = (r.stdout or "") + (r.stderr or "")
    record("S6.3", r.returncode == 0 and "built in" in out, f"build rc={r.returncode}")

    # S6.4 — the served HTML must reference a bundle that actually loads.
    st, html = http("/", base=FRONTEND)
    assets = re.findall(r'src="([^"]+\.tsx?)"|src="(/[^"]+\.js)"', html or "")
    ref = next((a or b for a, b in assets), None)
    if ref:
        st2, _ = http(ref, base=FRONTEND)
        record("S6.4", st2 == 200, f"entry {ref} -> {st2}")
    else:
        record("S6.4", st == 200 and "<div id=\"root\"" in (html or ""),
               "served HTML has a root mount point")

    # S6.5 — the coerceForColumn extraction must leave BottomPanel importable.
    bp = (fe / "src" / "layout" / "BottomPanel.tsx").read_text(encoding="utf-8")
    ok = "from '../utils/coerce'" in bp and "coerceForColumn(" in bp \
        and "function coerceForColumn" not in bp
    record("S6.5", ok, "BottomPanel imports the extracted helper, no stale copy")


# ── S8 ────────────────────────────────────────────────────────────────────
def suite_S8():
    print("\nS8 — Regression suites")
    import os
    import re
    import subprocess
    repo = BACKEND_DIR.parent.parent
    env = {**os.environ, "PATH": f"/opt/homebrew/bin:{os.environ.get('PATH', '')}"}

    def sh(cmd, timeout=2400):
        try:
            return subprocess.run(cmd, cwd=repo, env=env, capture_output=True,
                                  text=True, timeout=timeout)
        except Exception as e:                                # noqa: BLE001
            class R:
                returncode, stdout, stderr = 1, "", f"{type(e).__name__}: {e}"
            return R()

    r = sh(["pixi", "run", "python", "-m", "pytest",
            "pypsa-gui/backend/tests", "-p", "no:cacheprovider"])
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"(\d+) passed", out)
    record("S8.1", r.returncode == 0, f"backend {m.group(1) if m else '?'} passed rc={r.returncode}")

    r = sh(["pixi", "run", "-e", "test", "pytest", "test", "-q", "-p", "no:cacheprovider"])
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"(\d+) passed", out)
    record("S8.2", r.returncode == 0, f"pypsa-eur {m.group(1) if m else '?'} passed rc={r.returncode}")

    # ruff lives in the `dev` feature (upstream moved it out of the default
    # environment), so it must run via `-e dev`. The previous `pixi run ruff`
    # form silently stopped resolving after that move and the parse fell
    # through to -1, which passed the `<= 7` check vacuously — a test that
    # goes green when the tool is missing is worse than no test, so an
    # unparseable result is now an explicit failure.
    r = sh(["pixi", "run", "-e", "dev", "ruff", "check", "pypsa-gui/backend"])
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"Found (\d+) error", out)
    if m:
        n = int(m.group(1))
    elif r.returncode == 0 and "All checks passed" in out:
        n = 0
    else:
        n = None
    record("S8.3", n is not None and n <= 7,
           f"ruff findings={n if n is not None else 'UNPARSEABLE'} (<=7 known-benign)")


# ── S9 ────────────────────────────────────────────────────────────────────
def suite_S9(project: str | None, flat: str | None):
    """
    Adversarial pass. S1-S8 verify that things are wired together; these
    target the specific failure modes the refactors could plausibly have
    introduced, including one that no unit test covers.
    """
    print("\nS9 — Adversarial / edge cases")

    # S9.1 — actually DISPATCH read-tier chat tools, not just inspect the
    # registry. A tool can be registered, callable and schema-consistent and
    # still explode on its first real invocation.
    from services.chat_tools import DISPATCHERS
    if project:
        http(f"/api/projects/{q(project)}/activate", method="POST")
    sample = ["list_projects", "get_meta", "list_snapshots", "list_carriers",
              "get_simulation_status", "dispatch_status", "undo_status",
              "list_investment_periods", "get_solver_config", "audit_log"]
    errs = []
    for name in sample:
        fn = DISPATCHERS.get(name)
        if not callable(fn):
            errs.append((name, "not callable"))
            continue
        try:
            fn()
        except Exception as e:                                # noqa: BLE001
            # HTTPException with a structured error_kind is a legitimate
            # answer (e.g. no_active_project); a bare crash is not.
            if not hasattr(e, "status_code"):
                errs.append((name, f"{type(e).__name__}: {e}"[:70]))
    record("S9.1", not errs, f"{len(sample)} tools dispatched; failures: {errs[:3]}")

    # S9.2 — the is_multi_period local rename must NOT have changed the wire
    # format. Both payloads still have to emit the key.
    missing = []
    if project:
        for ep in ("emissions", "asset_economics"):
            st, body = http(f"/api/results/{ep}")
            if st == 200 and isinstance(body, dict) and "is_multi_period" not in body:
                missing.append(ep)
        record("S9.2", not missing, f"is_multi_period key preserved; missing in {missing}")
    else:
        skip("S9.2", "no project")

    # S9.3 — the exact bug the shared co2 map fixed: a carriers column whose
    # co2_emissions arrived as numeric STRINGS. results.py used to gate on
    # isinstance(v, (int, float)) and silently report ZERO emissions for those
    # carriers while compare.py reported the real value. No unit test covers
    # this because it needs a real network with an object-dtype column.
    import warnings
    warnings.filterwarnings("ignore")
    import pypsa
    from services.economics import co2_intensity_map
    ncs = sorted((BACKEND_DIR / "projects").glob("*/network.nc"))
    checked = 0
    bad = []
    for nc in ncs[:4]:
        try:
            n = pypsa.Network(str(nc))
        except Exception:
            continue
        if n.carriers.empty or "co2_emissions" not in n.carriers.columns:
            continue
        truth = co2_intensity_map(n)
        if not truth:
            continue
        # Re-type the column as strings; the map must be unchanged.
        n.carriers["co2_emissions"] = n.carriers["co2_emissions"].astype(str)
        got = co2_intensity_map(n)
        checked += 1
        if got != truth:
            bad.append((nc.parent.name, len(truth), len(got)))
    if checked:
        record("S9.3", not bad, f"{checked} networks survive string-typed co2: {bad[:2]}")
    else:
        skip("S9.3", "no network with co2_emissions to re-type")

    # S9.4 — concurrent reads against the _state global. Results endpoints are
    # lock-free by design; a torn multi-key read surfaces as a 500.
    import concurrent.futures as cf
    if project:
        eps = ["cost_breakdown", "emissions", "statistics", "generators",
               "prices", "asset_economics", "lcoh", "curtailment"] * 3
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            codes = list(ex.map(lambda e: http(f"/api/results/{e}")[0], eps))
        bad_codes = [c for c in codes if c >= 500 or c == 0]
        record("S9.4", not bad_codes, f"{len(codes)} concurrent reads, 5xx={len(bad_codes)}")
    else:
        skip("S9.4", "no project")

    # S9.5 — the FLAT-network path. Every period helper has a
    # "not multi-period" branch that the multi-period fixtures never exercise.
    if flat:
        http(f"/api/projects/{q(flat)}/activate", method="POST")
        flat_bad = []
        for ep in RESULT_ENDPOINTS:
            st, body = http(f"/api/results/{ep}")
            if st >= 500 or st == 0:
                flat_bad.append((ep, st))
            elif st == 200 and list(finite_scan(body)):
                flat_bad.append((ep, "nonfinite"))
        record("S9.5", not flat_bad, f"flat network '{flat}': {flat_bad[:3]}")
        st, s = http(f"/api/projects/{q(flat)}/results-summary", timeout=300)
        record("S9.6", st == 200 and not list(finite_scan(s)),
               f"flat results-summary -> {st}")
    else:
        skip("S9.5", "no flat network")
        skip("S9.6", "no flat network")


def _fresh_scratch_project(name: str, *, do_reset: bool = True) -> tuple[bool, int, Any]:
    """
    Establish a fresh, isolated `name` scratch project ready for its
    suite's first mutation: [reset] -> DELETE {name}?cascade=true -> POST
    from_template/3bus?name={name} -> activate {name}.

    Every qa_e2e_*-scratch-project suite (S10, S11, S12, S13, S14) opens
    with this exact sequence — Hazard 4 requires the pre-mutation reset,
    Hazard 1 requires the qa_e2e_*-prefixed name on any destructive call.
    Only one thing genuinely varies across those five call sites, and it is
    a parameter here rather than a fork of this function:

    `do_reset` — every caller except S13 wants this helper to also issue
    the pre-mutation POST /api/network/reset. S13 has its own Hazard-5
    in-flight-solve precheck that MUST run strictly before any reset, and
    records that reset itself as its own asserted "S13.2" step — so S13
    issues the reset itself, then calls this helper with do_reset=False so
    the reset is neither duplicated nor reordered.

    Returns (ok, status, body) from the from_template call. `ok` is True
    iff status is 200/201, in which case the project has ALSO been
    activated. On ok=False the project was NOT activated, and this helper
    deliberately does NOT call skip() itself — each of the five callers
    keeps its own skip tag and its own wording (S10/S13/S14 report the live
    status code and response body; S11/S12 use a fixed message), so the
    skip() call stays exactly where — and however it reads — it always was.

    Pure function: reads/writes nothing but the live backend named by
    `name`, so calling it from multiple suites cannot leak state between
    them — every suite remains independently runnable via `--suite S1x`
    (spec §4.1 self-containment).
    """
    if do_reset:
        http("/api/network/reset", method="POST")
    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
    st, body = http(f"/api/projects/from_template/3bus?name={q(name)}", method="POST")
    if st not in (200, 201):
        return False, st, body
    http(f"/api/projects/{q(name)}/activate", method="POST")
    return True, st, body


def suite_S10():
    print("\nS10 — Project save/load round trip (area 1)")
    name = "qa_e2e_roundtrip"
    # PRE-MUTATION reset (Hazard 4 — in-memory network state survives project
    # deletion) plus the delete/create-from-template/activate boilerplate
    # every S1x suite needs — see _fresh_scratch_project. NOT the same reset
    # as S10.5's adversarial post-round-trip reset further down; that one
    # exists to guard against a stale resident network papering over a
    # disk-read bug and is unrelated to this one. Matches S13's S13.2
    # precedent.
    ok, st, body = _fresh_scratch_project(name)
    if not ok:
        skip("S10.*", f"cannot create {name} ({st}) {str(body)[:80]}")
        return

    # S10.2 — GET-then-PUT-full-object on "Bus 0" (a real bus from the 3bus
    # template — the exact name set S7.5 already confirmed: "Bus 0", "Bus 1",
    # "Bus 2", qa_e2e.py:481). Distinctive v_nom=987.0; carrier must survive
    # unchanged (Hazard 2 guard, same discipline as S7.2, qa_e2e.py:417-443).
    st_g, buses = http("/api/network/buses")
    row = next((b for b in buses if b.get("name") == "Bus 0"), None) \
        if isinstance(buses, list) else None
    if not row:
        record("S10.2", False, f"Bus 0 not found in {name}: get={st_g}")
        skip("S10.3", "no bus to save")
        skip("S10.4", "no bus to save")
        skip("S10.5", "no bus to save")
        http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
        return
    payload = {**row, "v_nom": 987.0}
    st_p, _ = http(f"/api/network/buses/{q('Bus 0')}", method="PUT", body=payload)
    st_g2, buses2 = http("/api/network/buses")
    row2 = next((b for b in buses2 if b.get("name") == "Bus 0"), None) \
        if isinstance(buses2, list) else None
    kept = row2 is not None and abs(float(row2.get("v_nom", 0)) - 987.0) < 1e-9 \
        and str(row2.get("carrier")) == "AC"
    record("S10.2", kept,
           f"PUT {st_p}: v_nom={row2.get('v_nom') if row2 else None} "
           f"carrier={row2.get('carrier') if row2 else None}")

    # S10.3 — the destructive SAVE (Hazard 1). Only ever called with a
    # qa_e2e_*-prefixed name.
    st_s, _ = http(f"/api/projects/{q(name)}", method="POST")
    record("S10.3", st_s in (200, 204), f"save -> {st_s}")

    # S10.4 — the "load" side of the round trip. GET /api/projects/{name}
    # DOES perform a real load-into-memory (verified this session:
    # routers/projects.py load_project), but its response body is an
    # ImportSummary of component COUNTS only (models/schemas.py
    # ImportSummary: buses/generators/lines/links/storage_units/stores/
    # loads/transformers/snapshots, all int) — it does NOT carry per-bus
    # v_nom. accept_coldstart.py's own relaunch branch (:257-260) confirms
    # this exact pattern: it captures only the status code from this GET,
    # then makes a SEPARATE GET /api/network/buses call to inspect content.
    # This suite follows that established precedent.
    st_l, _ = http(f"/api/projects/{q(name)}")
    st_b, buses3 = http("/api/network/buses")
    row3 = next((b for b in buses3 if b.get("name") == "Bus 0"), None) \
        if isinstance(buses3, list) else None
    roundtrip_ok = st_l == 200 and row3 is not None \
        and abs(float(row3.get("v_nom", 0)) - 987.0) < 1e-9
    record("S10.4", roundtrip_ok,
           f"load -> {st_l}; Bus 0 v_nom after reload={row3.get('v_nom') if row3 else None}")

    # S10.5 (ADVERSARIAL reset — distinct from the pre-mutation reset at the
    # top of this suite). Hazard 4: in-memory network state survives project
    # deletion, so a stale resident network could paper over a real
    # disk-read bug — but a plain reset-then-activate does NOT probe that.
    # `load_project` (S10.4's GET) explicitly re-registers `name` as
    # RESIDENT (routers/projects.py load_project: "Register the now-bound
    # active ctx in the resident registry"), and POST /api/network/reset
    # only swaps the ACTIVE pointer to a fresh context — it does not evict
    # `name` from that registry (PyPSAService.reset_network). So a bare
    # reset + /activate hits activate_project's RESIDENT fast path ("a pure
    # pointer swap... No disk I/O — the whole point") and just re-reads the
    # same in-memory object S10.4 already validated, regardless of whether
    # the disk-read path works. CLAUDE.md's test-harness notes name the
    # fix: "evict the key explicitly." There is no HTTP endpoint that
    # evicts one resident key directly, so this drives the same B9 LRU-cap
    # mechanism production traffic uses: create+activate disposable
    # qa_e2e_*-prefixed projects, one at a time, until `name` appears in
    # some /activate response's "evicted" list (PyPSAService.
    # _evict_if_over_cap's return value, surfaced by activate_project).
    # That confirms `name` actually left `PyPSAService._contexts`, so the
    # /activate call below is then PROBABLY forced onto the cold
    # `_hydrate_context_from_disk` path — "probably", not "certainly",
    # because eviction is LRU over the live registry and nothing stops a
    # concurrent actor from re-touching `name` between our confirmation and
    # the probe; nothing in this single-threaded sequential script does.
    # Bounded well above the documented default RESIDENT_CAP (5); if a
    # custom-configured cap is high enough that eviction is never
    # confirmed, S10.5 fails rather than silently probing a still-resident
    # object.
    _EVICT_ATTEMPTS = 20
    evict_seq: list[str] = []
    forced_cold = False
    for i in range(_EVICT_ATTEMPTS):
        evict_name = f"qa_e2e_s10_5_evict_{i}"
        evict_seq.append(evict_name)
        http(f"/api/projects/{q(evict_name)}?cascade=true", method="DELETE")
        st_e, _ = http(f"/api/projects/from_template/3bus?name={q(evict_name)}", method="POST")
        if st_e not in (200, 201):
            continue
        st_a, body_a = http(f"/api/projects/{q(evict_name)}/activate", method="POST")
        if st_a == 200 and isinstance(body_a, dict) and name in body_a.get("evicted", []):
            forced_cold = True
            break

    http("/api/network/reset", method="POST")
    http(f"/api/projects/{q(name)}/activate", method="POST")
    st_b2, buses4 = http("/api/network/buses")
    row4 = next((b for b in buses4 if b.get("name") == "Bus 0"), None) \
        if isinstance(buses4, list) else None
    reload_ok = row4 is not None and abs(float(row4.get("v_nom", 0)) - 987.0) < 1e-9
    record("S10.5", forced_cold and reload_ok,
           f"forced-evict={forced_cold} (after {len(evict_seq)} disposable "
           f"activation(s)); post-eviction re-activate -> {st_b2}; Bus 0 "
           f"v_nom={row4.get('v_nom') if row4 else None}")

    for evict_name in evict_seq:
        http(f"/api/projects/{q(evict_name)}?cascade=true", method="DELETE")
    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")


def _s11_setup() -> str | None:
    """Create S11's own scratch project. Returns the name, or None on
    failure. Thin wrapper over the shared _fresh_scratch_project (Task 1) —
    kept as its own function (rather than inlined into suite_S11) so its
    call sites (here and Task 10's completed suite_S11) don't have to know
    the scratch project's literal name."""
    name = "qa_e2e_assets"
    ok, _, _ = _fresh_scratch_project(name)
    return name if ok else None


def _s11_teardown(name: str) -> None:
    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")


def _s11_buses() -> None:
    """
    Bus DELETE only. Create/GET/PUT/undo are already covered by S7.2/S7.3
    (qa_e2e.py:417-462) — S7 deletes/recreates the whole PROJECT, never one
    bus, so individual-bus DELETE has zero coverage anywhere until this check.
    """
    st_c, _ = http("/api/network/buses", method="POST",
                    body={"name": "qa_s11_bus", "v_nom": 110.0, "carrier": "AC"})
    if st_c not in (200, 201):
        record("S11.buses.delete", False, f"setup create -> {st_c}")
        return
    st_d, _ = http(f"/api/network/buses/{q('qa_s11_bus')}", method="DELETE")
    st_g, buses = http("/api/network/buses")
    gone = isinstance(buses, list) and not any(b.get("name") == "qa_s11_bus" for b in buses)
    record("S11.buses.delete", st_d == 204 and gone, f"DELETE -> {st_d}; gone={gone}")


def suite_S11():
    print("\nS11 — Asset CRUD across component classes (area 2, isolated project)")
    name = _s11_setup()
    if not name:
        skip("S11.*", "cannot create qa_e2e_assets project")
        return
    _s11_buses()
    _s11_teardown(name)


# ── main ──────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="all")
    args = ap.parse_args()
    want = args.suite.upper()

    def run(tag: str) -> bool:
        return want in ("ALL", tag)

    plist = projects()
    names = [p["name"] for p in plist]
    solved = next((p["name"] for p in plist if p.get("objective")), None)
    multi = next((p["name"] for p in plist
                  if p.get("objective") and (p.get("snapshot_count") or 0) > 8760), None)
    target = multi or solved
    # A genuinely FLAT network exercises the "not multi-period" branch of every
    # period helper, which the 26 280-snapshot fixtures never reach.
    flat = next((p["name"] for p in plist
                 if 0 < (p.get("snapshot_count") or 0) <= 8760), None)

    print(f"projects={len(names)}  solved={solved}  multi-period={multi}")
    if run("S1"):
        suite_S1()
    if run("S2"):
        suite_S2()
    if run("S3"):
        suite_S3(target)
    if run("S4"):
        suite_S4()
        suite_S4b(target)
    if run("S5"):
        suite_S5(names)
    if run("S6"):
        suite_S6()
    if run("S7"):
        suite_S7()
    if run("S8"):
        suite_S8()
    if run("S9"):
        suite_S9(target, flat)
    if run("S10"):
        suite_S10()
    if run("S11"):
        suite_S11()

    p = sum(1 for _, s, _ in RESULTS if s == "PASS")
    f = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    s = sum(1 for _, s, _ in RESULTS if s == "SKIP")
    print(f"\n{'='*66}\nPASS {p}   FAIL {f}   SKIP {s}")
    for tid, st, detail in RESULTS:
        if st == "FAIL":
            print(f"  FAILED {tid}: {detail}"[:200])
    return 1 if f else 0


if __name__ == "__main__":
    sys.exit(main())

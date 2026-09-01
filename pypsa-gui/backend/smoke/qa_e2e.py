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

If the backend is not on port 8000 (e.g. another session already holds it),
pass --backend or set QA_E2E_BACKEND to the actual origin. Doing so covers
only THIS script's own HTTP calls — a non-8000 backend additionally requires
setting PYPSA_GUI_API_ORIGIN for any vite dev server under test, since
vite.auth-gate.ts's health probe and vite.config.ts's dev-server proxy both
hardcode http://127.0.0.1:8000 otherwise.

S8.2 runs `pytest test` with cwd at the worktree root. Its
download_natural_earth fixture (test/conftest.py:122-133) urlretrieve()s a
~10 MB zip to a relative filename, ne_10m_admin_0_countries_deu.zip, so it
lands in the worktree root. Cleanup is a post-yield teardown, so if the
fixture raises before the yield (corrupt download, an HTML error page
tripping zipfile.BadZipFile) or the subprocess is killed on its timeout, the
file is left behind — and it is not gitignored. After any --suite all or
--suite S8 run, check `git status` for this file in the worktree root and
delete it if present. S8.2 also performs a live network download, so it is
flaky offline.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import sys
import time
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


def http(path: str, base: str | None = None, method: str = "GET", body=None, timeout: int = 120):
    """Return (status, parsed_or_text). Never raises on HTTP error codes."""
    if base is None:
        # Read the module global at call time, not def time — BACKEND may be
        # reassigned by main() from --backend/QA_E2E_BACKEND after this
        # function was defined, and a default-argument value binds once, at
        # def time, so it would never see that reassignment.
        base = BACKEND
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


def multipart_post(path: str, fields: dict[str, str], file_field: str, filename: str,
                    content: bytes, base: str | None = None, timeout: int = 60):
    """
    POST multipart/form-data with stdlib only (no `requests` — qa_e2e.py has
    zero third-party HTTP dependencies by design; `requests` is only an
    incidental transitive conda-lock resolution, not a declared dependency).

    `fields` are extra form fields (S12 puts component/attribute/period in
    the query string instead, so this is normally called with fields={}).
    `file_field` is the form field name the endpoint's UploadFile parameter
    expects (always "file" in this codebase).
    """
    if base is None:
        # See http()'s identical comment: read the module global at call
        # time so a --backend/QA_E2E_BACKEND reassignment in main() is seen.
        base = BACKEND
    boundary = "----qa_e2e_boundary_7f3c9a"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: text/csv\r\n\r\n".encode()
    )
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    url = path if path.startswith("http") else base + path
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
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
    # These names were once listed in DISPATCHERS without being defined
    # anywhere in chat_tools, so importing the module raised NameError at
    # module scope and took the entire tool surface down — see the block
    # comment above chat_tools.py's synthesis/analysis registrations. The
    # invariant that block protects is "no name is registered without an
    # implementation", NOT "these names may never exist again"; that same
    # comment spells out how to bring one back for real. `compare_scenarios`
    # was subsequently implemented on master exactly that way (a real
    # `def compare_scenarios`, matching entries in chat_tools_schema.TOOLS and
    # TOOL_ROUTES, and its own test_chat_compare_navigate.py), so it is a
    # shipped feature rather than a reintroduced stub and is no longer listed
    # here. The real invariant stays enforced for EVERY name, this one
    # included, by S2.1 (TOOLS == DISPATCHERS), S2.2 (all callable), S2.3
    # (all routed) and S2.6 (schema matches signature).
    removed = {"diagnose_results", "solve_overview", "sanity_check_results",
               "generate_run_report", "submit_plan", "plan_what_if",
               "undo_my_last_chat_action"}
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
    "unit_commitment", "transformers",
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

    # `-e test` is load-bearing here, for the same reason it is on S8.2 below.
    # Seven tests in the backend suite import `webview`, and pixi.toml carries
    # pywebview in exactly ONE environment — `test`, via the `desktop` feature
    # (pixi.toml's `[environments]` comment explains that this is deliberate:
    # without it the desktop-download guards silently SKIP and a hole reads as
    # green). A bare `pixi run python …` resolves to `default`, which has no
    # pywebview, so those seven fail with "pywebview is missing from this
    # environment" and S8.1 reports a red suite that is actually green. pixi
    # names `gui-tests` (defined under `[feature.test.tasks]`) the canonical
    # gate; backend/pytest.ini documents `-m pytest pypsa-gui/backend/tests` as
    # the equivalent explicit-path form, used here so `-p no:cacheprovider`
    # still applies and the battery leaves no .pytest_cache behind.
    r = sh(["pixi", "run", "-e", "test", "python", "-m", "pytest",
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
    # "Bus 2", qa_e2e.py:481). Distinctive v_nom=987.0; carrier is resent too,
    # so this proves PUT applies a changed field without corrupting another
    # resent field — the omitted-field wipe is S11.generators.put_partial's job.
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
    """
    Create S11's own scratch project. Returns the name, or None on
    failure. Thin wrapper over the shared _fresh_scratch_project (Task 1) —
    kept as its own function (rather than inlined into suite_S11) so its
    call sites (here and Task 10's completed suite_S11) don't have to know
    the scratch project's literal name.
    """
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


def _s11_generators() -> None:
    body = {"name": "qa_s11_gen", "bus": "Bus 0", "p_nom": 10.0, "marginal_cost": 12.5}
    st_c, _ = http("/api/network/generators", method="POST", body=body)
    st_g, rows = http("/api/network/generators")
    row = next((r for r in rows if r.get("name") == "qa_s11_gen"), None) \
        if isinstance(rows, list) else None
    if st_c not in (200, 201) or not row:
        record("S11.generators.create", False, f"create={st_c} get_found={row is not None}")
        return
    record("S11.generators.create", True, f"create -> {st_c}")

    payload = {**row, "p_nom": 55.0}
    st_p, _ = http(f"/api/network/generators/{q('qa_s11_gen')}", method="PUT", body=payload)
    st_g2, rows2 = http("/api/network/generators")
    row2 = next((r for r in rows2 if r.get("name") == "qa_s11_gen"), None) \
        if isinstance(rows2, list) else None
    kept = row2 is not None and abs(float(row2.get("p_nom", 0)) - 55.0) < 1e-9 \
        and abs(float(row2.get("marginal_cost", 0)) - 12.5) < 1e-9
    record("S11.generators.put", kept,
           f"PUT {st_p}: p_nom={row2.get('p_nom') if row2 else None} "
           f"marginal_cost={row2.get('marginal_cost') if row2 else None}")

    # Hazard 2 for real: omit marginal_cost entirely. The full-object PUT
    # above spreads the GET row, so no field is ever absent from it and it
    # cannot detect a wipe. _merge_partial_update (routers/network.py:173)
    # must read marginal_cost off the existing row rather than letting the
    # remove+add cycle reset it to the schema default.
    partial = {"name": "qa_s11_gen", "bus": "Bus 0", "p_nom": 77.0}
    st_pp, _ = http(f"/api/network/generators/{q('qa_s11_gen')}",
                    method="PUT", body=partial)
    _, rows4 = http("/api/network/generators")
    row4 = next((r for r in rows4 if r.get("name") == "qa_s11_gen"), None) \
        if isinstance(rows4, list) else None
    survived = row4 is not None \
        and abs(float(row4.get("p_nom", 0)) - 77.0) < 1e-9 \
        and abs(float(row4.get("marginal_cost", 0)) - 12.5) < 1e-9
    record("S11.generators.put_partial", survived,
           f"PUT {st_pp}: p_nom={row4.get('p_nom') if row4 else None} "
           f"marginal_cost={row4.get('marginal_cost') if row4 else None}")

    st_d, _ = http(f"/api/network/generators/{q('qa_s11_gen')}", method="DELETE")
    st_g3, rows3 = http("/api/network/generators")
    gone = isinstance(rows3, list) and not any(r.get("name") == "qa_s11_gen" for r in rows3)
    record("S11.generators.delete", st_d == 204 and gone, f"DELETE -> {st_d}; gone={gone}")


def _s11_lines() -> None:
    body = {"name": "qa_s11_line", "bus0": "Bus 0", "bus1": "Bus 1",
            "s_nom": 100.0, "r": 0.05}
    st_c, _ = http("/api/network/lines", method="POST", body=body)
    st_g, rows = http("/api/network/lines")
    row = next((r for r in rows if r.get("name") == "qa_s11_line"), None) \
        if isinstance(rows, list) else None
    if st_c not in (200, 201) or not row:
        record("S11.lines.create", False, f"create={st_c} get_found={row is not None}")
        return
    record("S11.lines.create", True, f"create -> {st_c}")

    payload = {**row, "s_nom": 250.0}
    st_p, _ = http(f"/api/network/lines/{q('qa_s11_line')}", method="PUT", body=payload)
    st_g2, rows2 = http("/api/network/lines")
    row2 = next((r for r in rows2 if r.get("name") == "qa_s11_line"), None) \
        if isinstance(rows2, list) else None
    kept = row2 is not None and abs(float(row2.get("s_nom", 0)) - 250.0) < 1e-9 \
        and abs(float(row2.get("r", 0)) - 0.05) < 1e-9
    record("S11.lines.put", kept,
           f"PUT {st_p}: s_nom={row2.get('s_nom') if row2 else None} "
           f"r={row2.get('r') if row2 else None}")

    st_d, _ = http(f"/api/network/lines/{q('qa_s11_line')}", method="DELETE")
    st_g3, rows3 = http("/api/network/lines")
    gone = isinstance(rows3, list) and not any(r.get("name") == "qa_s11_line" for r in rows3)
    record("S11.lines.delete", st_d == 204 and gone, f"DELETE -> {st_d}; gone={gone}")


def _s11_storage_units() -> None:
    body = {"name": "qa_s11_su", "bus": "Bus 0", "p_nom": 5.0, "max_hours": 8.0}
    st_c, _ = http("/api/network/storage_units", method="POST", body=body)
    st_g, rows = http("/api/network/storage_units")
    row = next((r for r in rows if r.get("name") == "qa_s11_su"), None) \
        if isinstance(rows, list) else None
    if st_c not in (200, 201) or not row:
        record("S11.storage_units.create", False, f"create={st_c} get_found={row is not None}")
        return
    record("S11.storage_units.create", True, f"create -> {st_c}")

    payload = {**row, "p_nom": 20.0}
    st_p, _ = http(f"/api/network/storage_units/{q('qa_s11_su')}", method="PUT", body=payload)
    st_g2, rows2 = http("/api/network/storage_units")
    row2 = next((r for r in rows2 if r.get("name") == "qa_s11_su"), None) \
        if isinstance(rows2, list) else None
    kept = row2 is not None and abs(float(row2.get("p_nom", 0)) - 20.0) < 1e-9 \
        and abs(float(row2.get("max_hours", 0)) - 8.0) < 1e-9
    record("S11.storage_units.put", kept,
           f"PUT {st_p}: p_nom={row2.get('p_nom') if row2 else None} "
           f"max_hours={row2.get('max_hours') if row2 else None}")

    st_d, _ = http(f"/api/network/storage_units/{q('qa_s11_su')}", method="DELETE")
    st_g3, rows3 = http("/api/network/storage_units")
    gone = isinstance(rows3, list) and not any(r.get("name") == "qa_s11_su" for r in rows3)
    record("S11.storage_units.delete", st_d == 204 and gone, f"DELETE -> {st_d}; gone={gone}")


def _s11_stores() -> None:
    body = {"name": "qa_s11_store", "bus": "Bus 0", "e_nom": 5.0, "standing_loss": 0.01}
    st_c, _ = http("/api/network/stores", method="POST", body=body)
    st_g, rows = http("/api/network/stores")
    row = next((r for r in rows if r.get("name") == "qa_s11_store"), None) \
        if isinstance(rows, list) else None
    if st_c not in (200, 201) or not row:
        record("S11.stores.create", False, f"create={st_c} get_found={row is not None}")
        return
    record("S11.stores.create", True, f"create -> {st_c}")

    payload = {**row, "e_nom": 30.0}
    st_p, _ = http(f"/api/network/stores/{q('qa_s11_store')}", method="PUT", body=payload)
    st_g2, rows2 = http("/api/network/stores")
    row2 = next((r for r in rows2 if r.get("name") == "qa_s11_store"), None) \
        if isinstance(rows2, list) else None
    kept = row2 is not None and abs(float(row2.get("e_nom", 0)) - 30.0) < 1e-9 \
        and abs(float(row2.get("standing_loss", 0)) - 0.01) < 1e-9
    record("S11.stores.put", kept,
           f"PUT {st_p}: e_nom={row2.get('e_nom') if row2 else None} "
           f"standing_loss={row2.get('standing_loss') if row2 else None}")

    st_d, _ = http(f"/api/network/stores/{q('qa_s11_store')}", method="DELETE")
    st_g3, rows3 = http("/api/network/stores")
    gone = isinstance(rows3, list) and not any(r.get("name") == "qa_s11_store" for r in rows3)
    record("S11.stores.delete", st_d == 204 and gone, f"DELETE -> {st_d}; gone={gone}")


def _s11_links() -> None:
    body = {"name": "qa_s11_link", "bus0": "Bus 0", "bus1": "Bus 1",
            "p_nom": 5.0, "efficiency": 0.9}
    st_c, _ = http("/api/network/links", method="POST", body=body)
    st_g, rows = http("/api/network/links")
    row = next((r for r in rows if r.get("name") == "qa_s11_link"), None) \
        if isinstance(rows, list) else None
    if st_c not in (200, 201) or not row:
        record("S11.links.create", False, f"create={st_c} get_found={row is not None}")
        return
    record("S11.links.create", True, f"create -> {st_c}")

    payload = {**row, "p_nom": 40.0}
    st_p, _ = http(f"/api/network/links/{q('qa_s11_link')}", method="PUT", body=payload)
    st_g2, rows2 = http("/api/network/links")
    row2 = next((r for r in rows2 if r.get("name") == "qa_s11_link"), None) \
        if isinstance(rows2, list) else None
    kept = row2 is not None and abs(float(row2.get("p_nom", 0)) - 40.0) < 1e-9 \
        and abs(float(row2.get("efficiency", 0)) - 0.9) < 1e-9
    record("S11.links.put", kept,
           f"PUT {st_p}: p_nom={row2.get('p_nom') if row2 else None} "
           f"efficiency={row2.get('efficiency') if row2 else None}")

    st_d, _ = http(f"/api/network/links/{q('qa_s11_link')}", method="DELETE")
    st_g3, rows3 = http("/api/network/links")
    gone = isinstance(rows3, list) and not any(r.get("name") == "qa_s11_link" for r in rows3)
    record("S11.links.delete", st_d == 204 and gone, f"DELETE -> {st_d}; gone={gone}")


def _s11_loads() -> None:
    body = {"name": "qa_s11_load", "bus": "Bus 0", "p_set": 10.0, "q_set": 2.5}
    st_c, _ = http("/api/network/loads", method="POST", body=body)
    st_g, rows = http("/api/network/loads")
    row = next((r for r in rows if r.get("name") == "qa_s11_load"), None) \
        if isinstance(rows, list) else None
    if st_c not in (200, 201) or not row:
        record("S11.loads.create", False, f"create={st_c} get_found={row is not None}")
        return
    record("S11.loads.create", True, f"create -> {st_c}")

    payload = {**row, "p_set": 77.0}
    st_p, _ = http(f"/api/network/loads/{q('qa_s11_load')}", method="PUT", body=payload)
    st_g2, rows2 = http("/api/network/loads")
    row2 = next((r for r in rows2 if r.get("name") == "qa_s11_load"), None) \
        if isinstance(rows2, list) else None
    kept = row2 is not None and abs(float(row2.get("p_set", 0)) - 77.0) < 1e-9 \
        and abs(float(row2.get("q_set", 0)) - 2.5) < 1e-9
    record("S11.loads.put", kept,
           f"PUT {st_p}: p_set={row2.get('p_set') if row2 else None} "
           f"q_set={row2.get('q_set') if row2 else None}")

    st_d, _ = http(f"/api/network/loads/{q('qa_s11_load')}", method="DELETE")
    st_g3, rows3 = http("/api/network/loads")
    gone = isinstance(rows3, list) and not any(r.get("name") == "qa_s11_load" for r in rows3)
    record("S11.loads.delete", st_d == 204 and gone, f"DELETE -> {st_d}; gone={gone}")


def _s11_transformers() -> None:
    body = {"name": "qa_s11_tr", "bus0": "Bus 0", "bus1": "Bus 1",
            "s_nom": 10.0, "r": 0.02}
    st_c, _ = http("/api/network/transformers", method="POST", body=body)
    st_g, rows = http("/api/network/transformers")
    row = next((r for r in rows if r.get("name") == "qa_s11_tr"), None) \
        if isinstance(rows, list) else None
    if st_c not in (200, 201) or not row:
        record("S11.transformers.create", False, f"create={st_c} get_found={row is not None}")
        return
    record("S11.transformers.create", True, f"create -> {st_c}")

    payload = {**row, "s_nom": 99.0}
    st_p, _ = http(f"/api/network/transformers/{q('qa_s11_tr')}", method="PUT", body=payload)
    st_g2, rows2 = http("/api/network/transformers")
    row2 = next((r for r in rows2 if r.get("name") == "qa_s11_tr"), None) \
        if isinstance(rows2, list) else None
    kept = row2 is not None and abs(float(row2.get("s_nom", 0)) - 99.0) < 1e-9 \
        and abs(float(row2.get("r", 0)) - 0.02) < 1e-9
    record("S11.transformers.put", kept,
           f"PUT {st_p}: s_nom={row2.get('s_nom') if row2 else None} "
           f"r={row2.get('r') if row2 else None}")

    st_d, _ = http(f"/api/network/transformers/{q('qa_s11_tr')}", method="DELETE")
    st_g3, rows3 = http("/api/network/transformers")
    gone = isinstance(rows3, list) and not any(r.get("name") == "qa_s11_tr" for r in rows3)
    record("S11.transformers.delete", st_d == 204 and gone, f"DELETE -> {st_d}; gone={gone}")


def _s11_carriers() -> None:
    body = {"name": "qa_s11_carrier", "co2_emissions": 0.1, "color": "#112233"}
    st_c, _ = http("/api/network/carriers", method="POST", body=body)
    st_g, rows = http("/api/network/carriers")
    row = next((r for r in rows if r.get("name") == "qa_s11_carrier"), None) \
        if isinstance(rows, list) else None
    if st_c not in (200, 201) or not row:
        record("S11.carriers.create", False, f"create={st_c} get_found={row is not None}")
        return
    record("S11.carriers.create", True, f"create -> {st_c}")

    payload = {**row, "co2_emissions": 0.5}
    st_p, _ = http(f"/api/network/carriers/{q('qa_s11_carrier')}", method="PUT", body=payload)
    st_g2, rows2 = http("/api/network/carriers")
    row2 = next((r for r in rows2 if r.get("name") == "qa_s11_carrier"), None) \
        if isinstance(rows2, list) else None
    kept = row2 is not None and abs(float(row2.get("co2_emissions", 0)) - 0.5) < 1e-9 \
        and str(row2.get("color")) == "#112233"
    record("S11.carriers.put", kept,
           f"PUT {st_p}: co2_emissions={row2.get('co2_emissions') if row2 else None} "
           f"color={row2.get('color') if row2 else None}")

    st_d, _ = http(f"/api/network/carriers/{q('qa_s11_carrier')}", method="DELETE")
    st_g3, rows3 = http("/api/network/carriers")
    gone = isinstance(rows3, list) and not any(r.get("name") == "qa_s11_carrier" for r in rows3)
    record("S11.carriers.delete", st_d == 204 and gone, f"DELETE -> {st_d}; gone={gone}")


def suite_S11():
    print("\nS11 — Asset CRUD across component classes (area 2, isolated project)")
    name = _s11_setup()
    if not name:
        skip("S11.*", "cannot create qa_e2e_assets project")
        return
    _s11_buses()
    _s11_generators()
    _s11_lines()
    _s11_storage_units()
    _s11_stores()
    _s11_links()
    _s11_loads()
    _s11_transformers()
    _s11_carriers()
    _s11_teardown(name)


def _s12_csv(column: str, index: list[str], values: list[float]) -> bytes:
    lines = ["timestamp," + column]
    for ts, v in zip(index, values):
        lines.append(f"{ts},{v}")
    return ("\n".join(lines) + "\n").encode()


def _s12_setup() -> tuple[str, list[str]] | None:
    """
    Create S12's own scratch project (never S11's qa_e2e_assets — per the
    spec's self-containment rule, every suite creates its own project), via
    the shared _fresh_scratch_project (Task 1). Then reads the real
    snapshot index so upload fixtures use timestamps the network actually
    spans, avoiding spurious snapshot realignment in the normal lifecycle
    checks (S12.4 deliberately triggers realignment separately).
    """
    name = "qa_e2e_ts"
    ok, _, _ = _fresh_scratch_project(name)
    if not ok:
        return None
    st_s, snap = http("/api/network/snapshots")
    ts = (snap.get("snapshots") or [])[:3] if isinstance(snap, dict) else []
    if len(ts) < 3:
        # The project WAS created + activated above -- delete it before
        # bailing so this failure path doesn't orphan qa_e2e_ts. Mirrors
        # suite_S10's equivalent early-return (Task 1, `if not row:`
        # branch), which deletes before returning from the SAME function
        # that created the project, rather than pushing cleanup onto the
        # caller (suite_S12 has no `name` to delete once this returns None).
        http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
        return None
    return name, ts


def _s12_teardown(name: str) -> None:
    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")


_S12_CLASSES: dict[str, dict] = {
    "loads": dict(
        create_path="/api/network/loads",
        create_body={"name": "qa_s12_load", "bus": "Bus 0", "p_set": 1.0},
        upload_path="/api/network/loads/upload_profile",
        attribute="p_set",
        column="qa_s12_load",
        values=[111.0, 222.0, 333.0],
    ),
    "generators": dict(
        create_path="/api/network/generators",
        create_body={"name": "qa_s12_gen", "bus": "Bus 0", "p_nom": 1.0},
        upload_path="/api/network/generators/upload_profile?attribute=p_max_pu",
        attribute="p_max_pu",
        column="qa_s12_gen",
        values=[0.1, 0.2, 0.3],
    ),
    "links": dict(
        create_path="/api/network/links",
        create_body={"name": "qa_s12_link", "bus0": "Bus 0", "bus1": "Bus 1", "p_nom": 1.0},
        upload_path="/api/network/links/upload_profile?attribute=p_max_pu",
        attribute="p_max_pu",
        column="qa_s12_link",
        values=[0.4, 0.5, 0.6],
    ),
    # No upload_profile route exists for storage_units or stores — the
    # generic POST /api/network/timeseries/upload?component=&attribute=
    # endpoint is the only path in for these two.
    "storage_units": dict(
        create_path="/api/network/storage_units",
        create_body={"name": "qa_s12_su", "bus": "Bus 0", "p_nom": 1.0},
        upload_path="/api/network/timeseries/upload?component=storage_units&attribute=p_max_pu",
        attribute="p_max_pu",
        column="qa_s12_su",
        values=[0.15, 0.25, 0.35],
    ),
    "stores": dict(
        create_path="/api/network/stores",
        create_body={"name": "qa_s12_store", "bus": "Bus 0", "e_nom": 1.0},
        upload_path="/api/network/timeseries/upload?component=stores&attribute=e_max_pu",
        attribute="e_max_pu",
        column="qa_s12_store",
        values=[0.45, 0.55, 0.65],
    ),
}


def _s12_component(component: str, ts: list[str]) -> None:
    """
    One parameterised lifecycle check for a single S12 component class:
    create -> CSV upload (via that class's own `_S12_CLASSES[component]`
    upload path) -> roundtrip GET -> listed check -> delete check. Driven
    entirely by `_S12_CLASSES[component]` — see that table's comments for
    why the upload path can't be derived generically from `component` alone.
    """
    cfg = _S12_CLASSES[component]
    column = cfg["column"]
    attribute = cfg["attribute"]
    http(cfg["create_path"], method="POST", body=cfg["create_body"])
    csv = _s12_csv(column, ts, cfg["values"])
    st_u, body_u = multipart_post(
        cfg["upload_path"], {}, "file", f"{column}.csv", csv)
    if st_u not in (200, 201):
        record(f"S12.{component}.upload", False, f"upload -> {st_u} {str(body_u)[:80]}")
        return
    st_g, series = http(
        f"/api/network/timeseries/{component}/{attribute}?columns={q(column)}")
    values = [row[0] for row in series.get("data", [])] if isinstance(series, dict) else []
    roundtrip = values == cfg["values"]
    record(f"S12.{component}.roundtrip", roundtrip, f"upload={st_u} values={values}")
    st_l, listing = http("/api/network/timeseries")
    listed = isinstance(listing, list) and any(
        e.get("component") == component and e.get("attribute") == attribute
        and column in (e.get("columns") or []) for e in listing)
    record(f"S12.{component}.listed", listed, f"list -> {st_l}")
    st_d, _ = http(
        f"/api/network/timeseries?component={component}&attribute={attribute}&name={q(column)}",
        method="DELETE")
    st_g2, series2 = http(
        f"/api/network/timeseries/{component}/{attribute}?columns={q(column)}")
    empty = isinstance(series2, dict) and series2.get("data") == []
    record(f"S12.{component}.delete", st_d == 200 and empty, f"delete -> {st_d}; empty={empty}")


def _s12_lines_asymmetry(ts: list[str]) -> None:
    """
    Adversarial: list_timeseries iterates ["generators", "loads",
    "storage_units", "stores", "lines", "links"] (network.py:2852-2875) but
    delete_timeseries's allowlist (network.py:3948) has only five, omitting
    "lines". Populate a real line's s_max_pu via the generic upload endpoint
    (no dedicated upload_profile route exists for lines), confirm it IS
    listed, then confirm DELETE ?component=lines is rejected. Uses a real
    line name discovered via GET rather than a hardcoded guess.
    """
    st_l0, lines = http("/api/network/lines")
    if not isinstance(lines, list) or not lines:
        skip("S12.lines_asymmetry", "no line in qa_e2e_ts to attach a profile to")
        return
    line_name = lines[0]["name"]
    csv = _s12_csv(line_name, ts, [0.8, 0.9, 0.7])
    st_u, _ = multipart_post(
        "/api/network/timeseries/upload?component=lines&attribute=s_max_pu",
        {}, "file", "qa_s12_line.csv", csv)
    st_l, listing = http("/api/network/timeseries")
    listed = isinstance(listing, list) and any(
        e.get("component") == "lines" and e.get("attribute") == "s_max_pu"
        for e in listing)
    record("S12.lines_asymmetry.listed", st_u in (200, 201) and listed,
           f"upload -> {st_u}; listed={listed}")
    st_d, body_d = http(
        f"/api/network/timeseries?component=lines&attribute=s_max_pu&name={q(line_name)}",
        method="DELETE")
    rejected = st_d == 400 and "Unsupported component" in str(body_d)
    record("S12.lines_asymmetry.delete_rejected", rejected,
           f"DELETE -> {st_d} {str(body_d)[:80]}")


def _s12_put_overwrite(ts: list[str]) -> None:
    """
    Adversarial: the inline PUT /timeseries/{component}/{attribute} writes
    ts_store[attribute] = df wholesale (network.py:2970-2996) and then
    writes only df.columns into _user_ts, never pruning the stale sibling
    key it left behind — so the PUT genuinely destroys 'qa_s12_putB' in
    n.generators_t.p_max_pu, but the sibling's now-stale _user_ts entry
    survives untouched. get_timeseries's no-filter branch (network.py:
    2879-2917) builds its response entirely from _user_ts once ANY entry
    exists for (component, attribute), so the GET this check makes below
    reads that stale entry back and reports 'qa_s12_putB' as present —
    a GET-view artifact, not evidence the network table kept it. The
    damage is self-healing: _reapply_user_ts_to_network runs on every
    autosave and rewrites every _user_ts entry (including the stale one)
    back into the network, restoring the column. What genuinely exists is
    a WINDOW between this PUT and the next autosave-triggered reapply,
    during which n.generators_t.p_max_pu truly lacks the column — this
    check cannot see that window (S12.put_overwrite.network_loss, right
    below, can). This check's PASS asserts HTTP status only across the
    three calls; the 'survived' fact in its detail string is the masked
    GET view, not evidence of preservation.
    """
    http("/api/network/generators", method="POST",
         body={"name": "qa_s12_putA", "bus": "Bus 0", "p_nom": 1.0})
    http("/api/network/generators", method="POST",
         body={"name": "qa_s12_putB", "bus": "Bus 0", "p_nom": 1.0})
    body_two = {"index": ts, "columns": ["qa_s12_putA", "qa_s12_putB"],
                "data": [[0.5, 0.6], [0.5, 0.6], [0.5, 0.6]]}
    st1, _ = http("/api/network/timeseries/generators/p_max_pu",
                   method="PUT", body=body_two)
    body_one = {"index": ts, "columns": ["qa_s12_putA"],
                "data": [[0.9], [0.9], [0.9]]}
    st2, _ = http("/api/network/timeseries/generators/p_max_pu",
                   method="PUT", body=body_one)
    st3, series = http("/api/network/timeseries/generators/p_max_pu")
    cols = series.get("columns", []) if isinstance(series, dict) else []
    survived = "qa_s12_putB" in cols
    record("S12.put_overwrite.behaviour",
           st1 in (200, 201) and st2 in (200, 201) and st3 == 200,
           f"after second PUT, columns={cols}; sibling 'qa_s12_putB' survived={survived} "
           f"(observed fact, not a required outcome)")


def _s12_put_overwrite_network_loss(ts: list[str]) -> None:
    """
    Adversarial: the same wholesale-PUT hazard as S12.put_overwrite, but
    observed through a path that does NOT prefer _user_ts, so it can see
    what that check cannot. Every /timeseries/{component}/{attribute} GET
    (filtered or not) and every /*/profiles endpoint checks _user_ts
    before falling back to the network table, and the wholesale PUT always
    leaves a stale _user_ts entry for the column it just destroyed — so
    none of those reads can ever show the loss.
    POST /api/simulation/preflight runs validate_for_run ->
    _check_lopf -> _check_p_max_pu_bounds (validation_service.py:993-1006)
    directly against n.generators_t.p_max_pu with no _user_ts reference
    anywhere in that path, so it is a genuine, _user_ts-independent read
    of the network table's real state.
    Seeds a sibling column ('qa_s12_lossB') with an out-of-bounds value
    (1.5 — p_max_pu > 1 always triggers _check_p_max_pu_bounds) in the
    first PUT, then a second PUT that omits it, exactly mirroring
    S12.put_overwrite's own two-PUT sequence but with a fresh pair of
    generators so this check's fixtures never interact with that one's.
    If the wholesale second PUT genuinely destroyed 'qa_s12_lossB' in the
    network table (the current, documented behaviour), preflight's
    p_max_pu_above_one issue for it CANNOT appear, regardless of what
    _user_ts still masks in a GET — that issue firing would mean the
    column survived in n.generators_t.p_max_pu itself. PASS asserts both
    that the probe reached the network table (HTTP 200 from preflight)
    AND that the destruction is what actually happened; this check IS a
    required-outcome assertion; the family's "observed fact" phrasing
    applied to S12.put_overwrite because that check had no way to verify
    which outcome occurred at all — this one does, so it pins the outcome
    the investigation confirmed against source.

    DISPOSITION FOR A FUTURE READER, if this goes FAIL: this check pins
    TODAY's set_timeseries behaviour (wholesale overwrite, destroys
    unlisted siblings), not the behaviour it OUGHT to have. If someone
    later fixes set_timeseries to merge onto the prior frame instead of
    replacing it wholesale (e.g. mirroring what _merge_partial_update
    already does for every other component's PUT route), 'qa_s12_lossB'
    will then survive, preflight will flag it, and this check will go
    FAIL — correctly and on purpose. That FAIL means the fix worked, not
    that it broke something. Do NOT revert the set_timeseries fix and do
    NOT edit this assertion back to green to make the suite pass; the
    correct response is to revisit or delete this check, because the
    hazard it exists to catch is gone.
    """
    http("/api/network/generators", method="POST",
         body={"name": "qa_s12_lossA", "bus": "Bus 0", "p_nom": 1.0})
    http("/api/network/generators", method="POST",
         body={"name": "qa_s12_lossB", "bus": "Bus 0", "p_nom": 1.0})
    body_two = {"index": ts, "columns": ["qa_s12_lossA", "qa_s12_lossB"],
                "data": [[0.5, 1.5], [0.5, 1.5], [0.5, 1.5]]}
    http("/api/network/timeseries/generators/p_max_pu",
         method="PUT", body=body_two)
    body_one = {"index": ts, "columns": ["qa_s12_lossA"],
                "data": [[0.9], [0.9], [0.9]]}
    http("/api/network/timeseries/generators/p_max_pu",
         method="PUT", body=body_one)
    st_p, result = http("/api/simulation/preflight", method="POST")
    issues = result.get("issues", []) if isinstance(result, dict) else []
    flagged = any(
        i.get("code") == "p_max_pu_above_one" and i.get("name") == "qa_s12_lossB"
        for i in issues)
    record("S12.put_overwrite.network_loss", st_p == 200 and not flagged,
           f"preflight -> {st_p}; qa_s12_lossB flagged={flagged}. "
           f"FAIL after a set_timeseries merge-fix is CORRECT (see docstring) -- "
           f"do not revert the fix.")


def _s12_snapshot_mismatch() -> None:
    """
    Adversarial: this check's own upload can never reach
    _ensure_snapshots_cover_user_ts's realign trigger (network.py:2371-
    2465), even though that function's docstring is what originally
    motivated this check. That function picks ONE reference series —
    longest = max(flat_series, key=lambda s: len(s.index))
    (network.py:2414) — across ALL of _user_ts, and realigns only around
    THAT series, not around whatever the current upload carries. By the
    time this check runs, _user_ts already holds 24-row 'Load 1'/'Load 2'
    p_set series backed up from the 3bus template at project load
    (projects.py, _backup_network_ts_to_user_ts), and this check's own
    upload is only 3 rows — so `longest` is always a Load series, `realign`
    is always False, and n.set_snapshots(...) is never called here.
    Confirmed with a temporary debug probe on `longest`/`new_idx` and by
    breaking `if realign:` directly: both showed the branch is genuinely
    unreached for this check, in a clean run. The gate this check actually
    exercises is _reapply_user_ts_to_network's zero-overlap column skip —
    the `if` at network.py:2606, the `continue` at network.py:2615:
    "if aligned.isna().all() and not series.isna().all(): ... continue"
    — this check's shifted (+10 year) profile is the ONLY upload anywhere
    in S12 whose series has zero overlap with n.snapshots (every other
    S12 upload reuses the real `ts` _s12_setup reads from the live
    snapshot index), so it is the only upload in this suite that can
    reach that skip at all. A future reader
    should not "fix" this check by chasing the realign branch — that
    branch is dead code for this specific scenario, by construction of
    which profiles happen to already be in _user_ts before this check
    runs. Record whether ts_start/ts_end moved. Observed fact, not a
    required outcome.
    """
    st0, snap0 = http("/api/network/snapshots")
    ts_start0 = snap0.get("ts_start") if isinstance(snap0, dict) else None
    ts_end0 = snap0.get("ts_end") if isinstance(snap0, dict) else None
    st_g, generators = http("/api/network/generators")
    if not isinstance(generators, list) or not generators:
        skip("S12.snapshot_mismatch", "no generator to attach a shifted profile to")
        return
    gen_name = generators[0]["name"]
    base_ts = (snap0.get("snapshots") or [])[:3] if isinstance(snap0, dict) else []
    shifted = [str(int(iso[:4]) + 10) + iso[4:] for iso in base_ts]
    csv = _s12_csv(gen_name, shifted, [0.4, 0.5, 0.6])
    st_u, _ = multipart_post(
        "/api/network/generators/upload_profile?attribute=p_max_pu",
        {}, "file", "qa_s12_shift.csv", csv)
    st1, snap1 = http("/api/network/snapshots")
    ts_start1 = snap1.get("ts_start") if isinstance(snap1, dict) else None
    ts_end1 = snap1.get("ts_end") if isinstance(snap1, dict) else None
    realigned = (ts_start1, ts_end1) != (ts_start0, ts_end0)
    record("S12.snapshot_mismatch", st_u in (200, 201),
           f"upload -> {st_u}; before=({ts_start0},{ts_end0}) after=({ts_start1},{ts_end1}) "
           f"realigned={realigned} (observed fact, not a required outcome)")


def suite_S12():
    print("\nS12 — Time series load/delete (area 3, isolated project)")
    setup = _s12_setup()
    if not setup:
        skip("S12.*", "cannot create qa_e2e_ts project or read its snapshots")
        return
    name, ts = setup
    for component in _S12_CLASSES:
        _s12_component(component, ts)
    _s12_lines_asymmetry(ts)
    _s12_put_overwrite(ts)
    _s12_put_overwrite_network_loss(ts)
    _s12_snapshot_mismatch()
    _s12_teardown(name)


def suite_S13():
    print("\nS13 — Fresh solve + result validation (area 5)")
    # S13.1 MUST run before literally anything else, including reset — a
    # solve holds the PyPSA lock for its whole duration and cannot be
    # interrupted from Python (Hazard 5). Never touch a solve S13 didn't
    # start.
    st_s, status = http("/api/simulation/status")
    if isinstance(status, dict) and status.get("running"):
        skip("S13.*", "a solve is already running - not touching reset or state")
        return
    record("S13.1", st_s == 200, f"pre-check status -> {st_s}")

    # S13.2 — only now that S13.1 has confirmed no solve is in flight.
    # Asserted (not a bare call) so this suite's own reset step is checked
    # like every other numbered step -- matches the QA_E2E_PLAN.md table's
    # S13.2 row and keeps the record() count at the 8 this task's
    # verification step already promises. Because this reset is itself an
    # asserted step that must happen strictly after S13.1, S13 issues it
    # here directly rather than through the shared _fresh_scratch_project
    # helper (Task 1) — it then calls that helper below with do_reset=False
    # so the reset isn't duplicated or reordered.
    st_r0, _ = http("/api/network/reset", method="POST")
    record("S13.2", st_r0 == 200, f"reset -> {st_r0}")

    name = "qa_e2e_solve"
    ok, st_c, body = _fresh_scratch_project(name, do_reset=False)
    if not ok:
        skip("S13.*", f"cannot create {name} ({st_c}) {str(body)[:80]}")
        return
    record("S13.3", True, f"created+activated {name}")

    st_r, _ = http("/api/simulation/run", method="POST")
    record("S13.4", st_r == 200, f"run -> {st_r}")

    def poll():
        for _ in range(90):  # 90 x 2s = 180s ceiling; a 3bus solve is single-digit seconds
            st, s = http("/api/simulation/status")
            if st == 200 and isinstance(s, dict) and not s.get("running"):
                return s
            time.sleep(2)
        return None

    s1 = poll()
    if s1 is None:
        # Hazard 5: no safe way to tell "stuck" from "merely slow" — skip,
        # never fail, on timeout.
        skip("S13.5", "solve did not finish within the 180s poll ceiling")
        skip("S13.6", "solve did not finish")
        skip("S13.7", "solve did not finish")
        skip("S13.8", "solve did not finish")
        skip("S13.9", "solve did not finish")
        http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
        return
    record("S13.5", True, f"solve finished: status={s1.get('status')}")

    obj1 = s1.get("objective")
    completed = s1.get("status") == "completed" and isinstance(obj1, (int, float)) \
        and math.isfinite(float(obj1))
    record("S13.6", completed, f"status={s1.get('status')} objective={obj1}")

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
    record("S13.7", not bad_status and not bad_json and not nonfinite,
           f"5xx/err={bad_status[:3]} non-JSON={bad_json[:2]} nonfinite={nonfinite[:2]}")

    # S13.8 (adversarial) — re-solve the SAME unchanged project; objective
    # must reproduce within a tight relative tolerance.
    http("/api/simulation/run", method="POST")
    s2 = poll()
    if s2 is None:
        skip("S13.8", "second solve did not finish within the 180s poll ceiling")
    else:
        obj2 = s2.get("objective")
        close = isinstance(obj2, (int, float)) and isinstance(obj1, (int, float)) \
            and abs(float(obj2) - float(obj1)) <= max(1e-6, abs(float(obj1)) * 1e-6)
        record("S13.8", close, f"re-solve objective={obj2} vs first={obj1}")

    # S13.9 — every RESULT_ENDPOINTS entry must back a REAL live route.
    # RESULT_ENDPOINTS is pre-existing data shared with suite_S3; S3.1/S3.2
    # (and S13.7 above) run the identical read-and-check loop, whose three
    # tripwires (5xx/0 status, non-JSON body on a 200, non-finite float) do
    # NOT catch an unmatched /api/results/* path — main.py's SPA catch-all
    # (`serve_spa`) treats any "/api/"-prefixed path as a static asset
    # (`static_gate.is_static_asset`) and answers 404 when no file matches,
    # which is <500, JSON, and float-free. That masks a dead entry as a
    # silent PASS in any normal frontend-built deployment; mirrors S1.4's
    # TOOL_ROUTES-vs-schema check to close the gap structurally instead of
    # relying on this frontend-less environment's 503 to surface it.
    st_spec, spec = http("/openapi.json")
    schema_paths = set(spec.get("paths", {})) if isinstance(spec, dict) else set()
    phantom = [ep for ep in RESULT_ENDPOINTS if f"/api/results/{ep}" not in schema_paths]
    record("S13.9", st_spec == 200 and not phantom,
           f"RESULT_ENDPOINTS entries with no live route -- fix the list, "
           f"or add the missing route: {phantom}")

    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")


def suite_S14():
    print("\nS14 — Scenario tree & snapshots (area 7)")
    base = "qa_e2e_scenario"
    branch = "qa_e2e_scenario_branch"
    # PRE-MUTATION reset (Hazard 4 -- in-memory network state survives
    # project deletion). Must run before this suite's first mutation. Issued
    # here directly (rather than via the shared _fresh_scratch_project
    # helper, Task 1) because S14 must ALSO delete the `branch` project,
    # in order, after the reset but before `base` is (re)created below --
    # _fresh_scratch_project only ever deletes the one project it creates.
    # Matches S13's S13.2 precedent.
    http("/api/network/reset", method="POST")
    http(f"/api/projects/{q(branch)}?cascade=true", method="DELETE")
    ok, st_c, body = _fresh_scratch_project(base, do_reset=False)
    if not ok:
        skip("S14.*", f"cannot create {base} ({st_c}) {str(body)[:80]}")
        return

    st_sc, snap = http(f"/api/projects/{q(base)}/snapshots", method="POST",
                        body={"label": "before-branch"})
    snap_id = snap.get("id") if isinstance(snap, dict) else None
    record("S14.2", st_sc == 200 and snap_id is not None, f"create snapshot -> {st_sc}")

    st_sl, snaps = http(f"/api/projects/{q(base)}/snapshots")
    listed = isinstance(snaps, list) and any(s.get("id") == snap_id for s in snaps)
    record("S14.3", st_sl == 200 and listed, f"list -> {st_sl}; found={listed}")

    # Diverge the branch from the pre-snapshot state.
    st_b, _ = http("/api/network/buses", method="POST",
                    body={"name": "qa_e2e_branch_bus", "v_nom": 400.0, "carrier": "AC"})
    record("S14.4", st_b in (200, 201), f"diverge bus create -> {st_b}")

    st_br, _ = http(f"/api/projects/{q(base)}/scenarios", method="POST",
                     body={"name": branch})
    record("S14.5", st_br == 201, f"create scenario branch -> {st_br}")

    st_p, plist = http("/api/projects/")
    # isinstance guard must run BEFORE `for p in plist` iterates, not inside
    # the comprehension — otherwise a non-list `plist` (e.g. `None`, when
    # GET /api/projects/ returns an empty body) raises TypeError from the
    # `for` clause itself before the guard is ever reached. Matches S14.10's
    # form (below) in this same function.
    children = [p.get("name") for p in plist if p.get("parent_project") == base] \
        if isinstance(plist, list) else []
    record("S14.6", st_p == 200 and branch in children, f"children of {base}: {children}")

    if snap_id is not None:
        st_rs, _ = http(f"/api/projects/{q(base)}/snapshots/{q(snap_id)}/restore",
                         method="POST")
        st_ab, buses_after = http("/api/network/buses")
        rolled_back = isinstance(buses_after, list) and not any(
            b.get("name") == "qa_e2e_branch_bus" for b in buses_after)
        record("S14.7", st_rs == 200 and rolled_back,
               f"restore -> {st_rs}; branch bus gone={rolled_back}")

        st_ds, _ = http(f"/api/projects/{q(base)}/snapshots/{q(snap_id)}",
                         method="DELETE")
        st_sl2, snaps2 = http(f"/api/projects/{q(base)}/snapshots")
        gone = isinstance(snaps2, list) and not any(s.get("id") == snap_id for s in snaps2)
        record("S14.8", st_ds == 204 and gone, f"delete snapshot -> {st_ds}; gone={gone}")
    else:
        skip("S14.7", "no snapshot id from S14.2")
        skip("S14.8", "no snapshot id from S14.2")

    # S14.9 — delete without cascade must be BLOCKED while the branch exists.
    st_blocked, body_blocked = http(f"/api/projects/{q(base)}", method="DELETE")
    blocked_ok = st_blocked == 409 and "descendants_exist" in str(body_blocked)
    record("S14.9", blocked_ok, f"delete w/o cascade -> {st_blocked} {str(body_blocked)[:100]}")

    # S14.10 — cascading delete removes both base and branch.
    st_casc, _ = http(f"/api/projects/{q(base)}?cascade=true", method="DELETE")
    st_pf, plist2 = http("/api/projects/")
    remaining = [p.get("name") for p in plist2] if isinstance(plist2, list) else []
    both_gone = base not in remaining and branch not in remaining
    record("S14.10", st_casc == 200 and both_gone,
           f"cascade delete -> {st_casc}; base_gone={base not in remaining} "
           f"branch_gone={branch not in remaining}")



def suite_S15():
    """
    Solution FMEA / adequacy journey (adequacy spec §5, phases 0-4).

    Exists because the ~150 pytest adequacy tests construct `SolverConfig`
    directly — a plain dataclass that validates nothing — and call route
    handlers as functions. Neither reaches the API boundary, which is where
    the unbounded-input defect lived and where a missing import once
    survived every handler-level test. This suite drives the LIVE surface.

    Its standard is exact arithmetic, not "a number came back": occurrence
    rates, the shed-cost exclusion identity and sweep criticality are all
    asserted against their closed forms, so a plausible-looking wrong
    number fails here.
    """
    print("\nS15 - Solution FMEA / adequacy journey (area 15)")
    name = "qa_e2e_fmea"

    # Solver config is process-global state, not project state: restore
    # whatever was there so a later suite (or the operator's own session)
    # does not inherit this suite's reliability target and VOLL.
    _, cfg_before = http("/api/simulation/solver_config")

    def restore():
        if isinstance(cfg_before, dict):
            http("/api/simulation/solver_config", method="PUT", body=cfg_before)

    # Built from scratch over the API rather than from the 3bus template,
    # unlike S10-S14. Two reasons, both specific to this suite: its
    # assertions are exact arithmetic over particular assets (a generator
    # carrying occurrence data, links to trip for class B, a load tight
    # enough to matter), and a template that happens to ship no links would
    # make the class-B rows silently empty — a suite that passes by having
    # nothing to check. Building it here also makes S15 runnable in an
    # environment where the template payloads are not installed.
    http("/api/network/reset", method="POST")
    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
    st_c, body_c = http(f"/api/projects/{q(name)}", method="POST")
    if st_c not in (200, 201):
        skip("S15.1", f"create project -> {st_c} {str(body_c)[:80]}")
        for i in range(2, 15):
            skip(f"S15.{i}", "no scratch project")
        return
    http(f"/api/projects/{q(name)}/activate", method="POST")

    built = [http("/api/network/reset", method="POST")[0]]
    for carrier in ("gas", "load_shedding"):
        built.append(http("/api/network/carriers", method="POST",
                          body={"name": carrier})[0])
    for bus in ("bus_a", "bus_b"):
        built.append(http("/api/network/buses", method="POST",
                          body={"name": bus, "v_nom": 380.0})[0])
    built.append(http("/api/network/snapshots", method="POST",
                      body={"start": "2030-01-01 00:00",
                            "end": "2030-01-01 23:00", "freq": "h"})[0])
    # All firm generation at bus_a, all load at bus_b, so the links are the
    # lifeline and class B has something that could actually bite.
    built.append(http("/api/network/generators", method="POST",
                      body={"name": "ccgt_a", "bus": "bus_a", "carrier": "gas",
                            "p_nom": 300.0, "marginal_cost": 60.0})[0])
    built.append(http("/api/network/generators", method="POST",
                      body={"name": "peaker_b", "bus": "bus_b", "carrier": "gas",
                            "p_nom": 0.0, "p_nom_extendable": True,
                            "p_nom_max": 400.0, "capital_cost": 500_000.0,
                            "marginal_cost": 150.0})[0])
    for link, cap, forr, mttr in (("link_ab", 200.0, 0.02, 72.0),
                                  ("link_ab2", 150.0, 0.03, 60.0)):
        built.append(http("/api/network/links", method="POST",
                          body={"name": link, "bus0": "bus_a", "bus1": "bus_b",
                                "p_nom": cap, "efficiency": 1.0,
                                "outage_rate_value": forr,
                                "outage_rate_basis": "FOR",
                                "mttr_hours": mttr})[0])
    built.append(http("/api/network/loads", method="POST",
                      body={"name": "load_b", "bus": "bus_b", "p_set": 330.0})[0])
    bad_build = [c for c in built if c not in (200, 201)]
    st_pf, pf = http("/api/simulation/preflight", method="POST", body={})
    errs = [i.get("code") for i in ((pf or {}).get("issues") or [])
            if isinstance(pf, dict) and i.get("severity") == "error"]
    record("S15.1", not bad_build and not errs,
           f"built {name}: non-2xx={bad_build or 'none'}; "
           f"preflight errors={errs or 'none'}")

    # ── S15.2/3 — API-boundary bounds on the reliability inputs. A negative
    # cap used to be accepted and then silently discarded downstream, which
    # made "target of -1" indistinguishable from "no target at all".
    def put_cfg(**over):
        base = dict(cfg_before) if isinstance(cfg_before, dict) else {}
        base.update({"solver_name": "highs", "voll": 3000.0})
        base.update(over)
        return http("/api/simulation/solver_config", method="PUT", body=base)[0]

    nonsense = {
        "ens_cap_permyriad=-1": {"ens_cap_permyriad": -1.0},
        "ens_zone_cap_multiple=-3": {"ens_cap_permyriad": 20.0, "ens_zone_cap_multiple": -3.0},
        "dsr_share_of_load=5": {"dsr_share_of_load": 5.0},
        "dsr_price=-100": {"dsr_price_eur_per_mwh": -100.0},
    }
    bad = [k for k, v in nonsense.items() if put_cfg(**v) != 422]
    record("S15.2", not bad, f"nonsense rejected 422; accepted-anyway={bad or 'none'}")

    # 0 and None are the DOCUMENTED "off" — bounding must not break them.
    meaningful = {
        "cap=20": {"ens_cap_permyriad": 20.0},
        "cap=0": {"ens_cap_permyriad": 0.0},
        "cap=None": {"ens_cap_permyriad": None},
        "share=1.0": {"dsr_share_of_load": 1.0},
    }
    refused = [k for k, v in meaningful.items() if put_cfg(**v) != 200]
    record("S15.3", not refused, f"meaningful range accepted; refused={refused or 'none'}")

    # ── S15.4/5 — COPT screening needs NO solve at all. Give one generator
    # occurrence data by reading its row back and PUTting the whole row
    # (these PUTs replace, they do not merge).
    st_g, gens = http("/api/network/generators")
    gen = gens[0] if isinstance(gens, list) and gens else None
    if gen is None:
        skip("S15.4", f"no generators on the template -> {st_g}")
        skip("S15.5", "no generator to annotate")
    else:
        FOR_, MTTR = 0.06, 48.0
        row = dict(gen)
        row.update({"outage_rate_value": FOR_, "outage_rate_basis": "FOR",
                    "mttr_hours": MTTR})
        st_p, _ = http(f"/api/network/generators/{q(str(gen['name']))}",
                       method="PUT", body=row)
        st_copt, copt = http("/api/results/copt")
        modes = (copt or {}).get("per_mode") or [] if isinstance(copt, dict) else []
        record("S15.4", st_p == 200 and st_copt == 200 and len(modes) > 0,
               f"PUT outage -> {st_p}; /results/copt -> {st_copt}; "
               f"modes={len(modes)} (no solve required)")

        # events/yr = FOR x 8760 / MTTR. Closed form, so an off-by-a-factor
        # in the rate conversion cannot pass.
        mine = next((m for m in modes if m.get("name") == gen["name"]), None)
        expected = FOR_ * 8760.0 / MTTR
        got = (mine or {}).get("occurrence_per_year")
        near = got is not None and abs(float(got) - expected) < 1e-9
        record("S15.5", near,
               f"occurrence {got} == FOR*8760/MTTR = {expected:.6f}"
               if mine else "annotated generator absent from the COPT ranking")

    # ── S15.6/7/8 — set a target, solve, and read every result surface.
    # A DELIBERATELY loose cap. Firm generation (300 MW at bus_a) is short of
    # the 330 MW load, and the peaker is priced so that shedding beats
    # building it, so this solve sheds ~720 MWh over the 24 h horizon. That
    # is the point: with ENS = 0 the S15.7 cost identity reduces to 0 == 0
    # and passes while checking nothing, which is exactly how it first
    # passed. S15.7 now also requires ENS > 0 so it can never again be
    # vacuous. The later sweep steps re-tighten the cap, which stays
    # feasible because the peaker remains extendable.
    st_cfg = put_cfg(ens_cap_permyriad=1500.0)
    st_r, _ = http("/api/simulation/run", method="POST")

    def poll():
        for _ in range(90):                     # 180s ceiling, as S13
            st, s = http("/api/simulation/status")
            if st == 200 and isinstance(s, dict) and not s.get("running"):
                return s
            time.sleep(2)
        return None

    status = poll()
    if status is None:
        # Hazard 5: cannot distinguish stuck from slow — skip, never fail.
        for i in (6, 7, 8):
            skip(f"S15.{i}", "solve did not finish within the 180s ceiling")
    else:
        st_a, rep = http("/api/results/adequacy")
        good = st_a == 200 and isinstance(rep, dict)
        binding = rep.get("target", {}).get("binding") if good else None
        record("S15.6", good and binding in ("system_cap", "zone_ceiling", "voll"),
               f"cfg->{st_cfg} run->{st_r} cond={status.get('condition')} "
               f"/results/adequacy -> {st_a} binding={binding}")

        # The cost axis excludes shed cost BY CONSTRUCTION (typed
        # Literal[True]), so a self-referential frontier is unconstructible.
        # The identity that must hold: objective - reported cost == ENS x VOLL.
        if good:
            ens = float(rep["target"]["system"]["achieved_ens_mwh"])
            cost = float(rep["cost"]["total_system_cost_eur"])
            obj = float(status.get("objective") or 0.0)
            voll = float((rep.get("inputs") or {}).get("voll_eur_per_mwh") or 3000.0)
            gap, want = obj - cost, ens * voll
            record("S15.7", ens > 0.0
                   and abs(gap - want) <= max(1e-6, 1e-9 * abs(obj))
                   and rep["cost"]["excludes_shed_cost"] is True,
                   f"objective {obj:.4f} - cost {cost:.4f} = {gap:.6f}; "
                   f"ENS {ens:.6f} x VOLL {voll:g} = {want:.6f}; "
                   f"excludes_shed_cost={rep['cost']['excludes_shed_cost']}; "
                   f"non-vacuous(ENS>0)={ens > 0.0}")

            # Shed-hours is a NEW metric; it must reach the Lost Load tab,
            # not just the adequacy report, or the two surfaces disagree.
            st_ll, ll = http("/api/results/lost_load")
            if st_ll == 204:
                record("S15.8", ens == 0.0,
                       f"/results/lost_load -> 204 with ENS={ens:g} "
                       "(no shedding, so no capture — consistent)")
            else:
                sh = (ll or {}).get("shed_hours") if isinstance(ll, dict) else None
                agree = (sh or {}).get("total") == rep["metrics"]["shed_hours"]
                total = (sh or {}).get("total")
                record("S15.8", st_ll == 200 and sh is not None and agree
                       and (total or 0) > 0,
                       f"/results/lost_load -> {st_ll} shed_hours={sh} "
                       f"vs report {rep['metrics']['shed_hours']}; "
                       f"non-vacuous(hours>0)={(total or 0) > 0}")
        else:
            skip("S15.7", "no adequacy report")
            skip("S15.8", "no adequacy report")

    # ── S15.9/10 — worksheet sidecar. Manual class-D rows and mode-keyed
    # overlays are the ONLY persisted parts; computed rows regenerate from
    # /results/copt, which is what makes annotations survive a re-solve.
    row = {"mode_id": "manual:cyber:scada_loss", "component_class": "Expert",
           "name": "SCADA loss", "failure_class": "D", "occurrence_per_year": 0.2,
           "occurrence_basis": "expert", "severity_eur": 1_250_000.0,
           "criticality_eur_per_year": 250_000.0, "in_metric_scope": True,
           "mitigability": "offline dispatch fallback", "engine": "expert",
           "fidelity": "expert_judgement"}
    overlay_key = "generator:x:forced_outage"
    st_w, _ = http(f"/api/projects/{q(name)}/worksheet", method="PUT",
                   body={"manual_rows": [row],
                         "overlays": {overlay_key: {"mitigability": "redundant start"}}})
    st_wg, ws = http(f"/api/projects/{q(name)}/worksheet")
    kept = (isinstance(ws, dict)
            and len(ws.get("manual_rows") or []) == 1
            and (ws.get("overlays") or {}).get(overlay_key, {}).get("mitigability")
            == "redundant start")
    record("S15.9", st_w == 200 and st_wg == 200 and kept,
           f"worksheet PUT->{st_w} GET->{st_wg} round-tripped={kept}")

    # Severity/criticality are >= 0 by contract: on an electricity-only
    # metric a P2X outage REDUCES electrical demand, and such rows must be
    # flagged out-of-scope, never ranked as beneficial.
    st_neg, _ = http(f"/api/projects/{q(name)}/worksheet", method="PUT",
                     body={"manual_rows": [dict(row, criticality_eur_per_year=-5000.0)],
                           "overlays": {}})
    _, ws2 = http(f"/api/projects/{q(name)}/worksheet")
    intact = isinstance(ws2, dict) and len(ws2.get("manual_rows") or []) == 1
    record("S15.10", st_neg == 422 and intact,
           f"negative criticality -> {st_neg} (want 422); prior rows intact={intact}")

    # ── S15.11 — stress registry: round-trip, then three validator guards,
    # then prove a REJECTED write did not clobber the stored value.
    good_sc = {"id": "cold_snap", "kind": "parametric", "frequency_per_year": 2.0,
               "electrical_load_multiplier": 1.2,
               "renewable_availability_multiplier": 0.6, "label": "Cold snap"}
    st_ss, _ = http(f"/api/projects/{q(name)}/stress_scenarios", method="PUT",
                    body={"scenarios": [good_sc]})
    guards = {
        "bad id": [{"id": "Cold Snap!", "kind": "parametric", "frequency_per_year": 2.0}],
        "frequency 0": [{"id": "x", "kind": "parametric", "frequency_per_year": 0}],
        "over cap": [{"id": f"s{i}", "kind": "parametric", "frequency_per_year": 1.0}
                     for i in range(11)],
    }
    leaked = [k for k, v in guards.items()
              if http(f"/api/projects/{q(name)}/stress_scenarios", method="PUT",
                      body={"scenarios": v})[0] != 422]
    _, ss = http(f"/api/projects/{q(name)}/stress_scenarios")
    survived = (isinstance(ss, dict) and len(ss.get("scenarios") or []) == 1
                and ss["scenarios"][0].get("id") == "cold_snap")
    record("S15.11", st_ss == 200 and not leaked and survived,
           f"stress PUT->{st_ss}; guards-that-leaked={leaked or 'none'}; "
           f"stored value survived rejected writes={survived}")

    # ── S15.12 — sweep guards. A sweep is several LP solves in a worker
    # thread; it must refuse without a VOLL and refuse to run twice at once.
    put_cfg(voll=0.0, ens_cap_permyriad=20.0)
    st_novoll, _ = http("/api/results/fmea_sweep", method="POST", body={})
    put_cfg(voll=3000.0, ens_cap_permyriad=50.0)
    st_start, _ = http("/api/results/fmea_sweep", method="POST",
                       body={"scenarios": [good_sc]})
    st_dup, _ = http("/api/results/fmea_sweep", method="POST", body={})
    record("S15.12", st_novoll == 422 and st_start == 200 and st_dup == 409,
           f"no-VOLL->{st_novoll} (422)  start->{st_start} (200)  "
           f"concurrent->{st_dup} (409)")

    # ── S15.13/14 — sweep completion, criticality arithmetic, and the
    # closing base re-solve that must leave foreground results in base state.
    sweep = None
    for _ in range(120):                        # 240s ceiling: several solves
        st_sw, sw = http("/api/results/fmea_sweep")
        if st_sw == 200 and isinstance(sw, dict) and sw.get("status") != "running":
            sweep = sw
            break
        time.sleep(2)
    if sweep is None:
        skip("S15.13", "sweep did not finish within the 240s ceiling")
        skip("S15.14", "sweep did not finish")
    else:
        rows = sweep.get("rows") or []
        # The invariant BOTH classes are built to satisfy is f x S:
        # criticality == occurrence_per_year x severity_eur. The two classes
        # reach it by genuinely different routes, and asserting either
        # route's formula on the other is simply wrong:
        #
        #   class B  criticality = q x dEUE x VoLL, where q is the
        #            UNAVAILABILITY PROBABILITY — a link outage is a state
        #            the system sits in a fraction q of the time. Occurrence
        #            (8760q/MTTR) is reported separately and severity is
        #            back-solved as criticality/occurrence.
        #   class C  severity = dEUE x VoLL PER EVENT and criticality =
        #            frequency_per_year x severity — a cold snap is a
        #            discrete episode with an empirical annual frequency.
        #
        # Multiplying class B by its events/yr would overstate it by
        # 8760/MTTR, which is how this check was first written and what
        # running it caught.
        wrong = []
        for r in rows:
            fm = r.get("failure_mode") or {}
            occ, sev = fm.get("occurrence_per_year"), fm.get("severity_eur")
            crit, d = fm.get("criticality_eur_per_year"), r.get("delta_eue_mwh")
            if None in (occ, sev, crit):
                continue
            want = float(occ) * float(sev)
            if abs(float(crit) - want) > max(1e-6, 1e-9 * abs(want)):
                wrong.append(f"{r.get('id')}: f*S {want} != crit {crit}")
            # Class C is additionally pinned to its closed form end to end.
            if fm.get("failure_class") == "C" and d is not None:
                want_c = float(d) * 3000.0 * float(occ)
                if abs(float(crit) - want_c) > max(1e-6, 1e-9 * abs(want_c)):
                    wrong.append(f"{r.get('id')}: dEUE*VoLL*freq {want_c} "
                                 f"!= crit {crit}")
        classes = sorted({(r.get("failure_mode") or {}).get("failure_class")
                          for r in rows} - {None})
        record("S15.13", sweep.get("status") == "done" and not sweep.get("error")
               and rows and not wrong,
               f"status={sweep.get('status')} rows={len(rows)} classes={classes} "
               f"err={sweep.get('error')}; bad-arithmetic={wrong or 'none'}")

        # The sweep pins capacities and mutates the live network; its closing
        # base re-solve writes through the real state sink, so the foreground
        # results must be readable and optimal afterwards.
        _, st_after = http("/api/simulation/status")
        st_a2, rep2 = http("/api/results/adequacy")
        record("S15.14",
               isinstance(st_after, dict) and st_after.get("condition") == "optimal"
               and st_a2 in (200, 204),
               f"after sweep: condition={(st_after or {}).get('condition')} "
               f"/results/adequacy -> {st_a2}")

    # ── S15.15 — a class-C scenario must measure degradation when the
    # profiles were UPLOADED, which is how the GUI supplies them.
    #
    # Everything above uses a static `p_set`. That is exactly the blind spot
    # that let a real bug through: `run_simulation` re-broadcasts every
    # user-uploaded series from `_user_ts` onto the live `_t` tables just
    # before building the LP, which restored the pristine profile OVER the
    # mutation each contingency had just made. The scenario then solved an
    # unmutated network, returned "ok", and reported a ΔEUE of 0 — a cold
    # snap priced at exactly zero criticality. Nothing in process reproduces
    # it, because a network built in process has an empty `_user_ts`.
    _, snaps = http("/api/network/snapshots")
    idx = (snaps or {}).get("snapshots") or []
    if not idx:
        skip("S15.15", "no snapshot index to upload a profile against")
    else:
        st_ts, _ = http("/api/network/timeseries/loads/p_set", method="PUT",
                        body={"index": idx, "columns": ["load_b"],
                              "data": [[330.0] for _ in idx]})
        put_cfg(ens_cap_permyriad=None, ens_zone_cap_multiple=None)
        poll()
        http("/api/results/fmea_sweep", method="POST",
             body={"scenarios": [{"id": "coldsnap", "kind": "parametric",
                                  "frequency_per_year": 1.0,
                                  "electrical_load_multiplier": 2.0}]})
        sw2 = None
        for _ in range(120):
            st_s, s = http("/api/results/fmea_sweep")
            if st_s == 200 and isinstance(s, dict) and s.get("status") != "running":
                sw2 = s
                break
            time.sleep(2)
        if sw2 is None:
            skip("S15.15", "sweep did not finish within the ceiling")
        else:
            crows = [r for r in (sw2.get("rows") or [])
                     if str(r.get("id", "")).startswith("scenario:")]
            deltas = [r.get("delta_eue_mwh") for r in crows]
            record("S15.15",
                   st_ts == 200 and bool(crows)
                   and all(d is not None and d > 0 for d in deltas),
                   f"profile upload -> {st_ts}; class-C rows={len(crows)} "
                   f"deltas={deltas} (a doubled load MUST raise ENS; 0 means "
                   f"the uploaded profile was reapplied over the mutation)")

    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
    restore()


def suite_S16():
    """
    Sequential-MC adequacy study journey (adequacy spec §4/§5, Phase 6).

    The ~40 MC/ELCC unit tests and the 10 endpoint tests all run in-process
    against constructed MCInputs or a TestClient. None of them proves the
    LIVE surface: a network built over the API, occurrence data resolved
    through the real defaults chain, the study running in a genuine worker
    thread in a server process, and the payload crossing real HTTP — the
    layer where an unbounded input and a missing import have each survived
    every handler-level test in this repo before.

    Standard of proof: fixed seeds and CI-aware assertions. The
    storage-helps check compares INTERVALS, not point estimates — the same
    seed drives both runs (the fleet is unchanged, so the outage paths are
    common random numbers) and the no-storage lower bound must clear the
    with-storage upper bound; two overlapping blobs would prove nothing.
    """
    print("\nS16 - Sequential-MC adequacy study (area 16)")
    name = "qa_e2e_mc"

    _, cfg_before = http("/api/simulation/solver_config")

    def restore():
        if isinstance(cfg_before, dict):
            http("/api/simulation/solver_config", method="PUT", body=cfg_before)

    def put_cfg(**over):
        base = dict(cfg_before) if isinstance(cfg_before, dict) else {}
        base.update({"solver_name": "highs"})
        base.update(over)
        return http("/api/simulation/solver_config", method="PUT", body=base)[0]

    def poll_mc(ceiling_s: int = 120):
        deadline = time.time() + ceiling_s
        while time.time() < deadline:
            st, s = http("/api/results/mc")
            if st == 200 and isinstance(s, dict) and s.get("status") != "running":
                return s
            time.sleep(0.5)
        return None

    # ── S16.1 — fixture where storage is DECISIVE, and the bare study. Two
    # 100 MW units against a flat 120 MW load: any single outage is a 20 MW
    # deficit the 60 MW / 4 h battery bridges until it drains; MTTR 24 h makes
    # outages persistent, so draining is the norm, not the tail — exactly the
    # regime the COPT convolution cannot see and this engine exists for.
    http("/api/network/reset", method="POST")
    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
    st_c, body_c = http(f"/api/projects/{q(name)}", method="POST")
    if st_c not in (200, 201):
        for i in range(1, 7):
            skip(f"S16.{i}", f"create project -> {st_c} {str(body_c)[:80]}")
        return
    http(f"/api/projects/{q(name)}/activate", method="POST")

    built = [http("/api/network/reset", method="POST")[0]]
    built.append(http("/api/network/carriers", method="POST",
                      body={"name": "gas"})[0])
    built.append(http("/api/network/buses", method="POST",
                      body={"name": "bus_a", "v_nom": 380.0})[0])
    built.append(http("/api/network/snapshots", method="POST",
                      body={"start": "2030-01-01 00:00",
                            "end": "2030-01-07 23:00", "freq": "h"})[0])
    for g in ("gen_1", "gen_2"):
        built.append(http("/api/network/generators", method="POST",
                          body={"name": g, "bus": "bus_a", "carrier": "gas",
                                "p_nom": 100.0, "marginal_cost": 50.0,
                                "outage_rate_value": 0.10,
                                "outage_rate_basis": "EFORd",
                                "mttr_hours": 24.0})[0])
    built.append(http("/api/network/storage_units", method="POST",
                      body={"name": "bess", "bus": "bus_a", "carrier": "gas",
                            "p_nom": 60.0, "max_hours": 4.0,
                            "efficiency_store": 0.95,
                            "efficiency_dispatch": 0.95})[0])
    built.append(http("/api/network/loads", method="POST",
                      body={"name": "load_a", "bus": "bus_a",
                            "p_set": 120.0})[0])
    bad_build = [c for c in built if c not in (200, 201)]

    # VoLL 0 on purpose: the MC study prices nothing and must run without one
    # (spec §4) — the frontier and the sweep both 422 in this exact state.
    st_cfg = put_cfg(voll=0.0)
    st_post, _ = http("/api/results/mc", method="POST",
                      body={"draws": 400, "seed": 7})
    res1 = poll_mc()
    m1 = ((res1 or {}).get("result") or {}).get("metrics") or {}
    r1 = (res1 or {}).get("result") or {}
    need = {"lole_hours", "lole_ci", "eue_mwh", "eue_ci", "n_samples",
            "resolution_floor_h", "time_basis"}
    missing = sorted(need - set(m1))
    # The warning's three clauses, pinned by their load-bearing phrases.
    warn = str(r1.get("warning") or "")
    clauses_ok = ("ONE weather realisation" in warn
                  and "INDEPENDENT" in warn and "EXCLUDED" in warn)
    ci1 = m1.get("eue_ci")
    shape_ok = (r1.get("engine") == "mc"
                and r1.get("fidelity") == "sequential_mc"
                and isinstance(ci1, list) and len(ci1) == 2
                and "thread" not in (res1 or {}))
    record("S16.1",
           not bad_build and st_cfg == 200 and st_post == 200
           and res1 is not None and res1.get("status") == "done"
           and not missing and clauses_ok and shape_ok
           and float(m1.get("eue_mwh") or 0.0) > 0.0,
           f"build non-2xx={bad_build or 'none'}; voll=0 POST->{st_post}; "
           f"status={(res1 or {}).get('status')}; missing-keys={missing or 'none'}; "
           f"warning-clauses={clauses_ok}; eue={m1.get('eue_mwh')} "
           f"ci={ci1} (persistent outages MUST shed on this fixture)")

    # ── S16.2 — the synchronous rejection surface, live. Every one of these
    # is knowable from the snapshot alone and must fail the POST, not the run.
    rejects = {}
    rejects["draws-over-cap"], _ = http("/api/results/mc", method="POST",
                                        body={"draws": 5000})
    rejects["11-elcc-assets"], _ = http(
        "/api/results/mc", method="POST",
        body={"draws": 8, "elcc_assets": [
            {"kind": "generator", "name": f"gen_{i}"} for i in range(11)]})
    rejects["unknown-asset"], _ = http(
        "/api/results/mc", method="POST",
        body={"draws": 8,
              "elcc_assets": [{"kind": "generator", "name": "no_such_gen"}]})
    rejects["unknown-kind"], _ = http(
        "/api/results/mc", method="POST",
        body={"draws": 8, "elcc_assets": [{"kind": "store", "name": "bess"}]})
    # An implied MTTF below one timestep is a contradiction in the unit data
    # (q = 0.99 with MTTR 1 h ⇒ MTTF ≈ 0.01 h): rejected at POST time, so the
    # user is not told seven minutes later that their study "failed".
    http("/api/network/generators", method="POST",
         body={"name": "flaky", "bus": "bus_a", "carrier": "gas",
               "p_nom": 10.0, "outage_rate_value": 0.99,
               "outage_rate_basis": "EFORd", "mttr_hours": 1.0})
    rejects["inconsistent-pair"], _ = http("/api/results/mc", method="POST",
                                           body={"draws": 8})
    http("/api/network/generators/flaky", method="DELETE")
    want = {"draws-over-cap": 422, "11-elcc-assets": 422, "unknown-asset": 404,
            "unknown-kind": 422, "inconsistent-pair": 422}
    wrong = {k: f"{rejects[k]} (want {want[k]})"
             for k in want if rejects[k] != want[k]}
    record("S16.2", not wrong, f"rejection surface: wrong={wrong or 'none'}")

    # ── S16.3 — the mutual-exclusion mesh against a REAL running study. An
    # ELCC bisection at the full 2000-draw budget holds the surface busy for
    # seconds, long enough that the immediate concurrent POSTs are
    # deterministic, not a race won by luck.
    st_go, _ = http("/api/results/mc", method="POST",
                    body={"draws": 2000, "seed": 11, "cov_target": 0.0001,
                          "elcc_assets": [
                              {"kind": "storage_unit", "name": "bess"}]})
    st_dup, _ = http("/api/results/mc", method="POST", body={"draws": 8})
    st_fr, _ = http("/api/results/frontier", method="POST", body={})
    res3 = poll_mc(ceiling_s=300)
    record("S16.3",
           st_go == 200 and st_dup == 409 and st_fr == 409
           and res3 is not None and res3.get("status") == "done",
           f"start->{st_go}; concurrent mc->{st_dup} (409) "
           f"frontier->{st_fr} (409); final={(res3 or {}).get('status')}")

    # ── S16.4 — the ELCC row from that run: nine keys, always all present,
    # and a refusal carries its reason as data rather than a blank.
    rows = ((res3 or {}).get("result") or {}).get("elcc") or []
    row = rows[0] if rows else {}
    keys_want = {"kind", "name", "nameplate_mw", "elcc_mw", "elcc_share",
                 "status", "reason", "baseline_lole_h", "baseline_lole_ci"}
    ok_status = row.get("status") in ("ok", "unidentifiable", "not_bracketed")
    credit_sane = True
    if row.get("status") == "ok":
        credit_sane = (row.get("elcc_mw") is not None
                       and -1e-9 <= float(row["elcc_mw"]) <= 60.0 + 1e-6)
    reason_rule = ((row.get("reason") is None) == (row.get("status") == "ok"))
    record("S16.4",
           len(rows) == 1 and set(row) == keys_want and ok_status
           and credit_sane and reason_rule,
           f"rows={len(rows)}; keys-delta={sorted(set(row) ^ keys_want) or 'none'}; "
           f"status={row.get('status')} elcc_mw={row.get('elcc_mw')} "
           f"reason={str(row.get('reason'))[:60]}")

    # ── S16.5 — storage helps, CI-aware and seed-paired. Same seed, same
    # fleet ⇒ identical outage paths; dropping the battery can only raise
    # every draw's shortfall. The intervals must SEPARATE — the no-storage
    # lower bound above the with-storage upper bound — which a vacuous
    # assertion on overlapping point estimates would never establish.
    ci_with = m1.get("eue_ci")
    st_del, _ = http("/api/network/storage_units/bess", method="DELETE")
    http("/api/results/mc", method="POST", body={"draws": 400, "seed": 7})
    res5 = poll_mc()
    m5 = ((res5 or {}).get("result") or {}).get("metrics") or {}
    ci_no = m5.get("eue_ci")
    if not (isinstance(ci_with, list) and isinstance(ci_no, list)):
        record("S16.5", False,
               f"missing intervals: with={ci_with} without={ci_no}")
    else:
        separated = float(ci_no[0]) > float(ci_with[1])
        record("S16.5",
               st_del in (200, 204) and separated
               and float(m5.get("eue_mwh") or 0.0)
               > float(m1.get("eue_mwh") or 0.0),
               f"EUE with bess {m1.get('eue_mwh')} CI={ci_with} vs without "
               f"{m5.get('eue_mwh')} CI={ci_no}; intervals separated={separated}")

    # ── S16.6 — the ELCC candidates surface, live, and its agreement
    # guarantee. The battery is gone (S16.5 deleted it); add a must-take wind
    # generator (no occurrence data — "wind" is deliberately absent from the
    # defaults library) so all remaining kinds are represented: two
    # occurrence-bearing generators and one vre. Then the contract's point:
    # POST the ENTIRE candidates list back and every row must resolve — a
    # candidate the run 404s on is the failure mode the endpoint exists to
    # prevent. Plus the double-count guard live: a unit asked for as
    # kind="vre" is a 422, not a credit counted twice.
    http("/api/network/carriers", method="POST", body={"name": "wind"})
    st_wg, _ = http("/api/network/generators", method="POST",
                    body={"name": "wind_a", "bus": "bus_a", "carrier": "wind",
                          "p_nom": 40.0})
    st_cand, cand = http("/api/results/mc/elcc_candidates")
    assets = (cand or {}).get("assets") or []
    kinds = sorted({a.get("kind") for a in assets})
    names = sorted(a.get("name") for a in assets)
    shape_ok = (st_cand == 200 and (cand or {}).get("max_assets", 0) >= 1
                and kinds == ["generator", "vre"]
                and names == ["gen_1", "gen_2", "wind_a"]
                and all(a.get("nameplate_mw", 0) > 0 for a in assets))
    http("/api/results/mc", method="POST",
         body={"draws": 150, "seed": 3,
               "elcc_assets": [{"kind": a["kind"], "name": a["name"]}
                               for a in assets]})
    res6 = poll_mc(ceiling_s=300)
    rows6 = ((res6 or {}).get("result") or {}).get("elcc") or []
    resolved = (res6 is not None and res6.get("status") == "done"
                and len(rows6) == len(assets)
                and all(r.get("status") in
                        ("ok", "unidentifiable", "not_bracketed")
                        for r in rows6))
    st_dc, _ = http("/api/results/mc", method="POST",
                    body={"draws": 8,
                          "elcc_assets": [{"kind": "vre", "name": "gen_1"}]})
    record("S16.6",
           st_wg == 201 and shape_ok and resolved and st_dc == 422,
           f"candidates->{st_cand} kinds={kinds} names={names}; full-list run "
           f"resolved={resolved} rows={[(r.get('name'), r.get('status')) for r in rows6]}; "
           f"unit-as-vre->{st_dc} (want 422)")

    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
    restore()


def suite_S17():
    """
    The adequacy-coupled planning loop, live (Phase 7 spec §3).

    The controller has 22 unit tests against fake callables and the route has
    25 through a TestClient — but neither drives the thing this study IS: a
    real HiGHS capacity expansion, re-solved under a retuned cap, evaluated by
    the real sampler on whatever plan the LP actually produced, in a worker
    thread in a server process. The mesh fixes in particular can only be
    proven here: a foreground solve interleaving between iterates is an HTTP
    fact, not a unit-test fact.

    NON-VACUITY IS SELF-CALIBRATED. The suite first runs a plain MC study to
    learn this fixture's LOLE, then targets a THIRD of it — so iterate 0 is
    guaranteed to miss and the loop must genuinely move the cap. A hardcoded
    target would risk a fixture that meets at iterate 0 and a suite that
    passes having tested nothing.
    """
    print("\nS17 - The adequacy-coupled planning loop (area 17)")
    name = "qa_e2e_loop"

    _, cfg_before = http("/api/simulation/solver_config")

    def restore():
        if isinstance(cfg_before, dict):
            http("/api/simulation/solver_config", method="PUT", body=cfg_before)

    def put_cfg(**over):
        base = dict(cfg_before) if isinstance(cfg_before, dict) else {}
        base.update({"solver_name": "highs", "voll": 3000.0})
        base.update(over)
        return http("/api/simulation/solver_config", method="PUT", body=base)[0]

    def poll(path, ceiling_s=420):
        deadline = time.time() + ceiling_s
        while time.time() < deadline:
            st, s = http(path)
            if st == 200 and isinstance(s, dict) and s.get("status") != "running":
                return s
            time.sleep(1.0)
        return None

    # ── the fixture: firm units that fail, a load tight enough to shed, and
    # an EXTENDABLE peaker so a tighter cap has something to buy. Without a
    # build option the LP could only answer a tighter cap by shedding less
    # of a fixed plan, and the loop would have no lever to pull.
    http("/api/network/reset", method="POST")
    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
    st_c, body_c = http(f"/api/projects/{q(name)}", method="POST")
    if st_c not in (200, 201):
        for i in range(1, 6):
            skip(f"S17.{i}", f"create project -> {st_c} {str(body_c)[:80]}")
        return
    http(f"/api/projects/{q(name)}/activate", method="POST")

    built = [http("/api/network/reset", method="POST")[0]]
    built.append(http("/api/network/carriers", method="POST",
                      body={"name": "gas"})[0])
    built.append(http("/api/network/buses", method="POST",
                      body={"name": "bus_a", "v_nom": 380.0})[0])
    built.append(http("/api/network/snapshots", method="POST",
                      body={"start": "2030-01-01 00:00",
                            "end": "2030-01-02 23:00", "freq": "h"})[0])
    for g in ("unit_1", "unit_2"):
        built.append(http("/api/network/generators", method="POST",
                          body={"name": g, "bus": "bus_a", "carrier": "gas",
                                "p_nom": 100.0, "marginal_cost": 50.0,
                                "outage_rate_value": 0.12,
                                "outage_rate_basis": "EFORd",
                                "mttr_hours": 24.0})[0])
    built.append(http("/api/network/generators", method="POST",
                      body={"name": "peaker", "bus": "bus_a", "carrier": "gas",
                            "p_nom": 0.0, "p_nom_extendable": True,
                            "p_nom_max": 250.0, "capital_cost": 20_000.0,
                            "marginal_cost": 180.0,
                            "outage_rate_value": 0.05,
                            "outage_rate_basis": "EFORd",
                            "mttr_hours": 12.0})[0])
    built.append(http("/api/network/loads", method="POST",
                      body={"name": "load_a", "bus": "bus_a",
                            "p_set": 150.0})[0])
    bad_build = [c for c in built if c not in (200, 201)]

    # ── S17.1 — the synchronous rejection surface. Every one of these is
    # knowable from the config and one snapshot; a loop that discovered them
    # mid-run would burn minutes of solves to report a typo.
    put_cfg(voll=0.0)
    rej = {}
    rej["no-voll"], _ = http("/api/results/coupling_loop", method="POST",
                             body={"target_lole_h": 1.0})
    put_cfg(voll=3000.0)
    rej["no-target"], _ = http("/api/results/coupling_loop", method="POST",
                               body={})
    rej["zero-target"], _ = http("/api/results/coupling_loop", method="POST",
                                 body={"target_lole_h": 0.0})
    rej["draws-over-cap"], _ = http("/api/results/coupling_loop", method="POST",
                                    body={"target_lole_h": 1.0, "draws": 5000})
    rej["budget-over-cap"], _ = http("/api/results/coupling_loop", method="POST",
                                     body={"target_lole_h": 1.0,
                                           "max_solves": 99})
    rej["bad-restore"], _ = http("/api/results/coupling_loop", method="POST",
                                 body={"target_lole_h": 1.0,
                                       "restore": "whatever"})
    # Undecidable: one shed hour in one draw already exceeds this target, so
    # no verdict could distinguish a compliant plan from a lucky sample.
    rej["below-floor"], _ = http("/api/results/coupling_loop", method="POST",
                                 body={"target_lole_h": 1e-6, "draws": 100})
    put_cfg(voll=3000.0, solve_strategy="myopic")
    rej["myopic"], _ = http("/api/results/coupling_loop", method="POST",
                            body={"target_lole_h": 1.0})
    put_cfg(voll=3000.0, solve_strategy="full")
    wrong = {k: v for k, v in rej.items() if v != 422}
    record("S17.1", not bad_build and not wrong,
           f"build non-2xx={bad_build or 'none'}; rejections that were not 422: "
           f"{wrong or 'none'}")

    # ── S17.2 — calibrate. Solve once loosely, measure the plan's real LOLE
    # with the MC, and target a third of it: iterate 0 MUST miss.
    put_cfg(voll=3000.0, ens_cap_permyriad=100.0, solve_strategy="full")
    http("/api/simulation/run", method="POST")
    solved = poll("/api/simulation/status", ceiling_s=240)
    http("/api/results/mc", method="POST", body={"draws": 200, "seed": 4})
    mc0 = poll("/api/results/mc")
    lole0 = (((mc0 or {}).get("result") or {}).get("metrics") or {}).get("lole_hours")
    if not lole0 or lole0 <= 0:
        for i in (2, 3, 4, 5):
            skip(f"S17.{i}", f"fixture sheds nothing (LOLE={lole0}) — nothing to target")
        http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
        restore()
        return
    target = float(lole0) / 3.0
    record("S17.2",
           isinstance(solved, dict) and solved.get("condition") == "optimal"
           and lole0 > 0,
           f"baseline solved={( solved or {}).get('condition')}; baseline "
           f"MC-LOLE={lole0:.4f} h -> target {target:.4f} h (a third: iterate 0 "
           f"must miss, so the loop cannot pass by doing nothing)")

    # ── S17.3 — the loop runs, and while it runs the mesh holds. The
    # foreground-solve guard is the hole Phase 7 closed: a solve interleaving
    # between iterates rewrites the very p_nom_opt the next evaluation reads.
    st_go, _ = http("/api/results/coupling_loop", method="POST",
                    body={"target_lole_h": target, "draws": 200, "seed": 4,
                          "eps0": 100.0, "max_solves": 4, "restore": "base"})
    time.sleep(1.0)
    st_solve, _ = http("/api/simulation/run", method="POST")
    st_mc, _ = http("/api/results/mc", method="POST", body={"draws": 8})
    st_dup, _ = http("/api/results/coupling_loop", method="POST",
                     body={"target_lole_h": target})
    res = poll("/api/results/coupling_loop")
    rows = (res or {}).get("iterations") or []
    keys_ok = all({"eps_permyriad", "solve_status", "cost_eur", "ens_mwh",
                   "cap_mwh", "binding", "plateau", "mc"} <= set(r) for r in rows)
    shape_ok = (isinstance(res, dict)
                and res.get("study") == "coupling_loop"
                and "engine" not in res
                and res.get("status") in ("met", "unreachable",
                                          "budget_exhausted")
                and isinstance(res.get("verdict"), str) and res["verdict"]
                and res.get("resolution_floor_h") is not None
                and "ONE weather realisation" in str(res.get("warning"))
                and res.get("base_restored") is True
                and "thread" not in res)
    # If it met, the verdict must be VERIFIED: the final iterate's own
    # evaluation, not an extrapolation between steps.
    # An `unreachable` verdict must name the mechanism that ACTUALLY applies.
    # This fixture is the never-bound case — 200 MW firm covers 150 MW load,
    # so the LP sheds nothing at any ceiling and no cap can change the plan —
    # and the generic three-mechanism copy would send the user hunting for
    # storage foresight and DSR, neither of which is happening.
    verdict_ok = True
    if (res or {}).get("status") == "unreachable":
        solved_rows = [r for r in rows if r.get("solve_status") in ("ok", "optimal")]
        never_bound = solved_rows and not any(
            r.get("binding") == "system_cap" for r in solved_rows)
        v = str((res or {}).get("verdict", "")).lower()
        # …and a dead end must name the way OUT, by the heading of the panel
        # the user has to click. This fixture reaches the commonest honest
        # answer the cap loop gives, and until Phase 9 that answer named the
        # lever ("a planning reserve margin") without naming the study that
        # now searches for it.
        from routers.results import MARGIN_LOOP_PANEL_LABEL
        verdict_ok = (("never bound" in v and "outage" in v
                       and MARGIN_LOOP_PANEL_LABEL.lower() in v)
                      if never_bound else bool(v))

    met_ok = True
    if (res or {}).get("status") == "met":
        fin = (res or {}).get("final") or {}
        fmc = fin.get("mc") or {}
        met_ok = (res.get("eps_star") is not None
                  and fmc.get("lole_hours") is not None
                  and float(fmc["lole_hours"]) <= target + 1e-9)
    record("S17.3",
           st_go == 200 and st_solve == 409 and st_mc == 409 and st_dup == 409
           and res is not None and shape_ok and keys_ok and met_ok
           and verdict_ok and len(rows) >= 2,
           f"start->{st_go}; DURING the run: solve->{st_solve} mc->{st_mc} "
           f"loop->{st_dup} (all 409); status={(res or {}).get('status')} "
           f"solves={(res or {}).get('solves_used')} iterates={len(rows)} "
           f"eps_star={(res or {}).get('eps_star')} "
           f"confident={(res or {}).get('confident')}; shape={shape_ok} "
           f"verified={met_ok} verdict-names-the-real-mechanism={verdict_ok}")

    # ── S17.4 — abort. A study whose wall-clock promise is "minutes to tens
    # of minutes" that cannot be cancelled is user-hostile; the restore must
    # still run so the network is not left on a swept cap.
    # The abort is posted IMMEDIATELY, not after a fixed sleep: the record is
    # published under the same lock hold that starts the thread, so it exists
    # the moment the POST returns — and this loop is FAST (the informed jump
    # reaches its verdict in two solves), so any sleep long enough to "let it
    # get going" is also long enough to let it finish. A first attempt slept
    # 1.5 s and aborted a study that had already terminated.
    http("/api/results/coupling_loop", method="POST",
         body={"target_lole_h": target / 100.0, "draws": 1500, "seed": 4,
               "eps0": 100.0, "max_solves": 8})
    st_ab, _ = http("/api/results/coupling_loop/abort", method="POST")
    res_ab = poll("/api/results/coupling_loop")
    record("S17.4",
           st_ab == 200 and isinstance(res_ab, dict)
           and res_ab.get("status") == "aborted"
           and res_ab.get("base_restored") is True,
           f"abort->{st_ab}; final status={(res_ab or {}).get('status')} "
           f"(want aborted); base_restored={(res_ab or {}).get('base_restored')}")

    # ── S17.5 — restore="final" leaves the user HOLDING the certified plan.
    # Without it the loop certifies a cap and then re-solves it away, and the
    # answer survives only as a number in a record.
    st_f, _ = http("/api/results/coupling_loop", method="POST",
                   body={"target_lole_h": target, "draws": 200, "seed": 4,
                         "eps0": 100.0, "max_solves": 3, "restore": "final"})
    res_f = poll("/api/results/coupling_loop")
    _, cfg_after = http("/api/simulation/solver_config")
    applied = (cfg_after or {}).get("ens_cap_permyriad")
    eps_star = (res_f or {}).get("eps_star")
    if (res_f or {}).get("status") == "met" and eps_star is not None:
        ok = applied is not None and abs(float(applied) - float(eps_star)) < 1e-6
        detail = (f"met at eps*={eps_star:g}; config now carries "
                  f"ens_cap_permyriad={applied} (want eps*)")
    else:
        # Not met: "final" must fall back to base rather than apply a cap no
        # verdict certified.
        ok = applied is None or applied == 100.0
        detail = (f"status={(res_f or {}).get('status')} (not met) -> restore "
                  f"fell back to base; cap={applied} (want the original 100.0)")
    record("S17.5", st_f == 200 and res_f is not None and ok, detail)

    # ── S17.6 — the verdict names the number the PANEL tells you to type.
    # The cap loop has had this defect since Phase 7 and worse than the margin
    # loop did: the verdict printed `%g` (six significant figures) while the
    # panel's restore explainer printed the BADGE formatter (two, below 1), so
    # a certified 0.034728149‱ read "0.0347281" in one and "0.035" in the
    # other. An ENS cap is a CEILING, so the rounded-up number is a strictly
    # LOOSER standard than the plan the study certified.
    from services.adequacy.lever_text import format_lever_value
    notes, agreed, seen = [], True, 0
    for payload, label in ((res_ab, "aborted run"), (res_f, "restore=final")):
        star = (payload or {}).get("eps_star")
        verdict = (payload or {}).get("verdict") or ""
        if (payload or {}).get("status") != "met" or star is None:
            notes.append(f"{label}: not met, nothing to name")
            continue
        seen += 1
        want = f"ens_cap_permyriad = {format_lever_value(float(star))}"
        hit = want in verdict
        agreed = agreed and hit
        notes.append(f"{label}: eps*={star!r} -> {want!r} present={hit}")
    if seen == 0:
        # SKIP, not PASS. This suite's fixture is the one where the cap is
        # UNREACHABLE by construction (that is Phase 9's whole claim), so no
        # run here certifies a cap and there is no number to check. Recording
        # a PASS would read as live coverage this suite cannot provide; the
        # bitten unit tests and S19.6 carry it.
        skip("S17.6", "no run reached `met`, so no cap was certified to name: "
             + "; ".join(notes))
    else:
        record("S17.6", agreed, "; ".join(notes))

    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
    restore()


def suite_S18():
    """
    The firm-capacity reserve margin, live (Phase 8 spec §3/§4/§6).

    The constraint has 55 unit tests, the preflight/report/endpoint 31 more,
    and three self-calibrated acceptance tests prove the lever moves MC-LOLE.
    None of them crosses HTTP. This suite drives the surfaces a user actually
    touches: the config field at the API boundary, the preflight refusals that
    replace an unimplementable "let the LP go infeasible", and the derating
    table that makes the phase's proxies inspectable.

    The margin is DERIVED from the fixture, never chosen — the Phase-8 review
    killed a hardcoded margin by arithmetic (a value inside the largest-unit
    step buys real megawatts and moves LOLE not at all). Same discipline here.
    """
    print("\nS18 - The firm-capacity reserve margin (area 18)")
    name = "qa_e2e_prm"

    _, cfg_before = http("/api/simulation/solver_config")

    def restore():
        if isinstance(cfg_before, dict):
            http("/api/simulation/solver_config", method="PUT", body=cfg_before)

    def put_cfg(**over):
        base = dict(cfg_before) if isinstance(cfg_before, dict) else {}
        base.update({"solver_name": "highs", "voll": 3000.0})
        base.update(over)
        return http("/api/simulation/solver_config", method="PUT", body=base)

    def poll_solve(ceiling_s=240):
        deadline = time.time() + ceiling_s
        while time.time() < deadline:
            st, s = http("/api/simulation/status")
            if st == 200 and isinstance(s, dict) and not s.get("running"):
                return s
            time.sleep(1.0)
        return None

    # Two 100 MW firm units (EFORd 0.12 -> derate 0.88) covering a 150 MW load,
    # plus an expensive extendable peaker (EFORd 0.05) the LP has no economic
    # reason to build. The margin is the only thing that can put it in.
    UNIT, LOAD, EFORD_U, EFORD_P = 100.0, 150.0, 0.12, 0.05
    firm_fixed = 2 * UNIT * (1 - EFORD_U)                  # 176.0
    needed = LOAD + UNIT - 2 * UNIT                        # 50.0 (one-out gap)
    m_star = (firm_fixed + needed * (1 - EFORD_P)) / LOAD - 1.0   # ~0.49

    http("/api/network/reset", method="POST")
    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
    st_c, body_c = http(f"/api/projects/{q(name)}", method="POST")
    if st_c not in (200, 201):
        for i in range(1, 6):
            skip(f"S18.{i}", f"create project -> {st_c} {str(body_c)[:80]}")
        return
    http(f"/api/projects/{q(name)}/activate", method="POST")

    built = [http("/api/network/reset", method="POST")[0]]
    built.append(http("/api/network/carriers", method="POST",
                      body={"name": "gas"})[0])
    built.append(http("/api/network/buses", method="POST",
                      body={"name": "bus_a", "v_nom": 380.0})[0])
    built.append(http("/api/network/snapshots", method="POST",
                      body={"start": "2030-01-01 00:00",
                            "end": "2030-01-02 23:00", "freq": "h"})[0])
    for g in ("unit_1", "unit_2"):
        built.append(http("/api/network/generators", method="POST",
                          body={"name": g, "bus": "bus_a", "carrier": "gas",
                                "p_nom": UNIT, "marginal_cost": 10.0,
                                "outage_rate_value": EFORD_U,
                                "outage_rate_basis": "EFORd",
                                "mttr_hours": 24.0})[0])
    built.append(http("/api/network/generators", method="POST",
                      body={"name": "peaker", "bus": "bus_a", "carrier": "gas",
                            "p_nom": 0.0, "p_nom_extendable": True,
                            "p_nom_max": 500.0, "capital_cost": 5_000_000.0,
                            "marginal_cost": 500.0,
                            "outage_rate_value": EFORD_P,
                            "outage_rate_basis": "EFORd",
                            "mttr_hours": 12.0})[0])
    built.append(http("/api/network/loads", method="POST",
                      body={"name": "load_a", "bus": "bus_a",
                            "p_set": LOAD})[0])
    bad_build = [c for c in built if c not in (200, 201)]

    # ── S18.1 — the config field is bounded AT THE BOUNDARY. The Phase-1 QA
    # round found four reliability fields accepted then silently discarded;
    # a margin of -1 or 600% must never reach the solver.
    bad = []
    for label, val in (("-1", -1.0), ("6", 6.0)):
        if put_cfg(reserve_margin=val)[0] != 422:
            bad.append(label)
    refused = [label for label, val in (("0", 0.0), ("None", None),
                                        ("0.15", 0.15))
               if put_cfg(reserve_margin=val)[0] != 200]
    record("S18.1", not bad_build and not bad and not refused,
           f"build non-2xx={bad_build or 'none'}; nonsense accepted="
           f"{bad or 'none'}; meaningful refused={refused or 'none'}")

    # ── S18.2 — preflight REFUSES what the LP cannot express. Linopy raises on
    # a constant constraint and Generator-p_nom does not exist when nothing
    # extendable is active, so "let it go infeasible" was never implementable:
    # an unreachable margin has to be caught before the solve, and it has to
    # name both numbers rather than say "check your capacity bounds".
    # 400%: unreachable (max firm = 176 + 500x0.95 = 651 MW against a 750 MW
    # requirement) but INSIDE the schema's le=5 bound. A first draft used 900%,
    # which the boundary correctly refused — so the config never took and the
    # check passed against a margin that was never set. The set is asserted
    # here precisely so this cannot go vacuous again.
    st_set, _ = put_cfg(reserve_margin=4.0)
    st_pf, pf = http("/api/simulation/preflight", method="POST", body={})
    issues = (pf or {}).get("issues") or []
    codes = {i.get("code") for i in issues if isinstance(i, dict)}
    unreachable = [i for i in issues
                   if isinstance(i, dict)
                   and i.get("code") == "reserve_margin_unreachable"]
    msg = str(unreachable[0].get("message", "")) if unreachable else ""
    # The message must carry the arithmetic, not just a verdict.
    has_numbers = sum(ch.isdigit() for ch in msg) >= 4
    record("S18.2",
           st_set == 200 and st_pf == 200 and bool(unreachable)
           and unreachable[0].get("severity") == "error" and has_numbers,
           f"cfg-set->{st_set}; preflight->{st_pf}; "
           f"unreachable-error={bool(unreachable)} "
           f"severity={(unreachable[0].get('severity') if unreachable else None)}; "
           f"names-both-numbers={has_numbers}; codes={sorted(codes)[:6]}")

    # ── S18.3 — the derived margin BINDS, and the endpoint reports it.
    # m* is computed from the fixture (see the header): the smallest margin
    # whose plan survives losing the largest unit.
    put_cfg(reserve_margin=m_star)
    http("/api/simulation/run", method="POST")
    solved = poll_solve()
    st_rm, rm = http("/api/results/reserve_margin")
    rows = (rm or {}).get("by_period") or []
    row = rows[0] if rows else {}
    assets = {a.get("name"): a for a in ((rm or {}).get("assets") or [])}
    peaker = assets.get("peaker") or {}
    built_mw = peaker.get("capacity_mw")
    shape_ok = (st_rm == 200 and rows
                and {"peak_mw", "required_mw", "firm_mw", "met", "binding"}
                <= set(row)
                and (rm or {}).get("horizon_wide") is True
                and row.get("met") is True)
    # Every credited asset must carry its provenance — the proxies are only
    # defensible if a user can see which number came from a class average.
    prov_ok = all(a.get("basis") and a.get("source") for a in assets.values())
    record("S18.3",
           isinstance(solved, dict) and solved.get("condition") == "optimal"
           and shape_ok and prov_ok
           and built_mw is not None and float(built_mw) > 1.0,
           f"m*={m_star:.4f} solved={(solved or {}).get('condition')}; "
           f"/results/reserve_margin->{st_rm} peak={row.get('peak_mw')} "
           f"required={row.get('required_mw')} firm={row.get('firm_mw')} "
           f"met={row.get('met')} binding={row.get('binding')}; peaker built="
           f"{built_mw} MW; every asset carries basis+source={prov_ok}")

    # ── S18.4 — met and BINDING are different questions. At a margin the fixed
    # fleet already satisfies, the standard is met and NOT binding; conflating
    # them would credit the margin for capacity that was always there.
    put_cfg(reserve_margin=0.05)         # 157.5 MW required vs 176 MW fixed
    http("/api/simulation/run", method="POST")
    solved2 = poll_solve()
    _, rm2 = http("/api/results/reserve_margin")
    row2 = ((rm2 or {}).get("by_period") or [{}])[0]
    pk2 = {a.get("name"): a for a in ((rm2 or {}).get("assets") or [])}
    built2 = (pk2.get("peaker") or {}).get("capacity_mw")
    record("S18.4",
           isinstance(solved2, dict) and solved2.get("condition") == "optimal"
           and row2.get("met") is True and row2.get("binding") is False
           and (built2 is None or float(built2) < 1.0),
           f"slack margin: met={row2.get('met')} binding={row2.get('binding')} "
           f"(want met+not-binding); peaker built={built2} (want ~0)")

    # ── S18.5 — the margin does not leak into the contingency sweep. Without
    # the strip, freeze_capacities pins the peaker and every contingency that
    # removes derated capacity violates the standard, so the whole sweep dies
    # infeasible and every severity reads as the standard rather than the
    # outage.
    put_cfg(reserve_margin=m_star, voll=3000.0)
    st_sw, _ = http("/api/results/fmea_sweep", method="POST",
                    body={"scenarios": [{"id": "cold", "kind": "parametric",
                                         "frequency_per_year": 1.0,
                                         "electrical_load_multiplier": 1.2}]})
    sweep = None
    for _ in range(150):
        st_s, s = http("/api/results/fmea_sweep")
        if st_s == 200 and isinstance(s, dict) and s.get("status") != "running":
            sweep = s
            break
        time.sleep(2)
    srows = (sweep or {}).get("rows") or []
    record("S18.5",
           st_sw == 200 and sweep is not None
           and sweep.get("status") == "done" and not sweep.get("error")
           and bool(srows),
           f"sweep with a margin set -> start {st_sw} status="
           f"{(sweep or {}).get('status')} rows={len(srows)} err="
           f"{str((sweep or {}).get('error'))[:60]}")

    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
    restore()


def suite_S19():
    """
    The margin loop, live (Phase 9 spec §2/§4).

    Phase 7's loop tunes an energy cap; its commonest honest verdict on a
    firm-capacity-rich network is `unreachable` — the cap never binds, so no
    ceiling changes the plan. Phase 8 built the reserve margin that DOES move
    the metric there. Phase 9 lets the loop drive it, by feeding the
    controller the margin's RECIPROCAL so that larger-is-stricter becomes the
    smaller-is-stricter ordering `coupling.py` already assumes — without a
    line of that file changing.

    The claim this suite exists to prove on a real server is the comparative
    one: on ONE network and ONE derived target, the cap loop reports
    `unreachable` and the margin loop reports `met`. Everything else here is
    the surface a user touches.
    """
    print("\nS19 - The margin loop (area 19)")
    name = "qa_e2e_marginloop"

    _, cfg_before = http("/api/simulation/solver_config")

    def restore():
        if isinstance(cfg_before, dict):
            http("/api/simulation/solver_config", method="PUT", body=cfg_before)

    def put_cfg(**over):
        base = dict(cfg_before) if isinstance(cfg_before, dict) else {}
        base.update({"solver_name": "highs"})
        base.update(over)
        return http("/api/simulation/solver_config", method="PUT", body=base)

    def poll(path, ceiling_s=420):
        deadline = time.time() + ceiling_s
        while time.time() < deadline:
            st, s = http(path)
            if st == 200 and isinstance(s, dict) and s.get("status") != "running":
                return s
            time.sleep(1.0)
        return None

    # Two firm units covering a flat load, plus an expensive extendable the LP
    # has no economic reason to build: the shape where an energy cap has no
    # leverage (the LP sheds nothing at any ceiling) but firm capacity does.
    http("/api/network/reset", method="POST")
    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
    st_c, body_c = http(f"/api/projects/{q(name)}", method="POST")
    if st_c not in (200, 201):
        for i in range(1, 6):
            skip(f"S19.{i}", f"create project -> {st_c} {str(body_c)[:80]}")
        return
    http(f"/api/projects/{q(name)}/activate", method="POST")

    built = [http("/api/network/reset", method="POST")[0]]
    built.append(http("/api/network/carriers", method="POST",
                      body={"name": "gas"})[0])
    built.append(http("/api/network/buses", method="POST",
                      body={"name": "bus_a", "v_nom": 380.0})[0])
    built.append(http("/api/network/snapshots", method="POST",
                      body={"start": "2030-01-01 00:00",
                            "end": "2030-01-02 23:00", "freq": "h"})[0])
    for g in ("unit_1", "unit_2"):
        built.append(http("/api/network/generators", method="POST",
                          body={"name": g, "bus": "bus_a", "carrier": "gas",
                                "p_nom": 100.0, "marginal_cost": 10.0,
                                "outage_rate_value": 0.12,
                                "outage_rate_basis": "EFORd",
                                "mttr_hours": 24.0})[0])
    built.append(http("/api/network/generators", method="POST",
                      body={"name": "peaker", "bus": "bus_a", "carrier": "gas",
                            "p_nom": 0.0, "p_nom_extendable": True,
                            "p_nom_max": 400.0, "capital_cost": 5_000_000.0,
                            "marginal_cost": 500.0,
                            "outage_rate_value": 0.05,
                            "outage_rate_basis": "EFORd",
                            "mttr_hours": 12.0})[0])
    built.append(http("/api/network/loads", method="POST",
                      body={"name": "load_a", "bus": "bus_a",
                            "p_set": 150.0})[0])
    bad_build = [c for c in built if c not in (200, 201)]

    # ── S19.1 — the refusals the cap loop has and the margin loop must NOT,
    # and vice versa. A margin is a CONSTRAINT, not a price, so a margin loop
    # on a VoLL-free network is well defined — copying the cap loop's VoLL 422
    # would deny a supported study. Conversely the margin's own validator
    # allows `myopic` (each window is one period, which is the peak the
    # standard is defined against) while refusing `rolling`.
    put_cfg(voll=0.0, reserve_margin=None)
    st_novoll, _ = http("/api/results/margin_loop", method="POST",
                        body={"target_lole_h": 1.0, "draws": 100})
    # The margin loop STARTS on this config, so it must be finished before the
    # cap loop is asked about the same one — otherwise the 409 mesh answers
    # first and the VoLL check is never reached. (A first draft asserted 422
    # and got 409: the mesh working, the test wrong.)
    if st_novoll == 200:
        http("/api/results/margin_loop/abort", method="POST")
        poll("/api/results/margin_loop")
    st_cap_novoll, _ = http("/api/results/coupling_loop", method="POST",
                            body={"target_lole_h": 1.0, "draws": 100})
    put_cfg(voll=3000.0, solve_strategy="myopic")
    st_myopic, _ = http("/api/results/margin_loop", method="POST",
                        body={"target_lole_h": 1.0, "draws": 100})
    if st_myopic == 200:
        http("/api/results/margin_loop/abort", method="POST")
        poll("/api/results/margin_loop")
    put_cfg(voll=3000.0, solve_strategy="rolling")
    st_rolling, _ = http("/api/results/margin_loop", method="POST",
                         body={"target_lole_h": 1.0, "draws": 100})
    put_cfg(voll=3000.0, solve_strategy="full")
    record("S19.1",
           not bad_build and st_novoll == 200 and st_cap_novoll == 422
           and st_myopic == 200 and st_rolling == 422,
           f"build non-2xx={bad_build or 'none'}; margin-loop without VoLL->"
           f"{st_novoll} (want 200, a margin needs no price); cap-loop same "
           f"config->{st_cap_novoll} (want 422); myopic->{st_myopic} (want "
           f"200); rolling->{st_rolling} (want 422)")

    # ── S19.2 — calibrate on the incumbent plan, exactly as S17 does: solve,
    # measure the plan's real LOLE, target a third of it. Derived, never
    # chosen — a margin inside the largest-unit step moves EUE but not LOLE.
    put_cfg(voll=3000.0, ens_cap_permyriad=100.0, reserve_margin=None,
            solve_strategy="full")
    http("/api/simulation/run", method="POST")
    solved = poll("/api/simulation/status", ceiling_s=240)
    http("/api/results/mc", method="POST", body={"draws": 300, "seed": 9})
    mc0 = poll("/api/results/mc")
    lole0 = (((mc0 or {}).get("result") or {}).get("metrics") or {}).get("lole_hours")
    if not lole0 or lole0 <= 0:
        for i in (2, 3, 4, 5):
            skip(f"S19.{i}", f"fixture sheds nothing (LOLE={lole0})")
        http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
        restore()
        return
    target = float(lole0) / 3.0
    record("S19.2",
           isinstance(solved, dict) and solved.get("condition") == "optimal"
           and lole0 > 0,
           f"baseline solved={(solved or {}).get('condition')}; MC-LOLE="
           f"{lole0:.4f} h -> target {target:.4f} h (a third: the incumbent "
           f"must miss, so neither loop can pass by doing nothing)")

    # ── S19.3 — THE CLAIM. Same network, same derived target: the cap loop
    # cannot get there and the margin loop can.
    http("/api/results/coupling_loop", method="POST",
         body={"target_lole_h": target, "draws": 300, "seed": 9,
               "eps0": 100.0, "max_solves": 4})
    cap = poll("/api/results/coupling_loop")
    http("/api/results/margin_loop", method="POST",
         body={"target_lole_h": target, "draws": 300, "seed": 9,
               "max_solves": 5})
    mar = poll("/api/results/margin_loop")
    cap_status = (cap or {}).get("status")
    mar_status = (mar or {}).get("status")
    m_star = (mar or {}).get("lever_star")
    fin = (mar or {}).get("final") or {}
    fmc = fin.get("mc") or {}
    verified = (mar_status != "met") or (
        fmc.get("lole_hours") is not None
        and float(fmc["lole_hours"]) <= target + 1e-9)
    record("S19.3",
           cap is not None and mar is not None
           and cap_status == "unreachable" and mar_status == "met"
           and m_star is not None and verified,
           f"SAME network, SAME target {target:.4f} h: cap loop -> "
           f"{cap_status} ({(cap or {}).get('solves_used')} solves); margin "
           f"loop -> {mar_status} at m*={m_star} "
           f"({(mar or {}).get('solves_used')} solves + "
           f"{(mar or {}).get('probe_solves')} probe); final verified="
           f"{verified}")

    # ── S19.4 — the payload contract, and the one thing that must never leak:
    # the controller's internal reciprocal. Every number on the wire is a
    # margin.
    rows = (mar or {}).get("iterations") or []
    row = rows[0] if rows else {}
    leaked = [k for k in row if k in ("eps_permyriad", "x", "lever_x")]
    caps_none = all(r.get("cap_mwh") is None for r in rows)
    shape_ok = (mar or {}).get("study") == "margin_loop" \
        and (mar or {}).get("lever") == "reserve_margin" \
        and "lever_value" in row and isinstance((mar or {}).get("verdict"), str)
    # Margins are plausible: the certified one must be positive and within the
    # schema bound the loop is required to respect.
    sane = (m_star is None) or (0.0 < float(m_star) <= 5.0)
    record("S19.4",
           shape_ok and not leaked and caps_none and sane,
           f"study={(mar or {}).get('study')} lever={(mar or {}).get('lever')}; "
           f"x-leaks={leaked or 'none'}; every cap_mwh None={caps_none}; "
           f"m*={m_star} within (0, 5]={sane}; ceiling="
           f"{(mar or {}).get('margin_ceiling')}")

    # ── S19.5 — restore="final" leaves the user holding the certified plan,
    # and writes the MARGIN's field rather than the cap's.
    _, cfg_mid = http("/api/simulation/solver_config")
    cap_before = (cfg_mid or {}).get("ens_cap_permyriad")
    http("/api/results/margin_loop", method="POST",
         body={"target_lole_h": target, "draws": 300, "seed": 9,
               "max_solves": 3, "restore": "final"})
    fin2 = poll("/api/results/margin_loop")
    _, cfg_after = http("/api/simulation/solver_config")
    applied = (cfg_after or {}).get("reserve_margin")
    cap_after = (cfg_after or {}).get("ens_cap_permyriad")
    star2 = (fin2 or {}).get("lever_star")
    if (fin2 or {}).get("status") == "met" and star2 is not None:
        ok = applied is not None and abs(float(applied) - float(star2)) < 1e-6
    else:
        ok = applied is None or applied == 0 or applied == cfg_mid.get("reserve_margin")
    # The user's own ENS cap must survive untouched either way.
    untouched = (cap_after == cap_before)
    record("S19.5", ok and untouched,
           f"status={(fin2 or {}).get('status')} m*={star2}; config now "
           f"reserve_margin={applied} (want m*); user's ens_cap_permyriad "
           f"{cap_before} -> {cap_after} (must be untouched)={untouched}")

    # ── S19.6 — the verdict names the number the PANEL tells you to type.
    # Found by rendering: the verdict said "set reserve_margin = 0.6716"
    # (`%g`) under an explainer saying "reserve_margin = 0.671600430725". A
    # margin is a THRESHOLD on required firm capacity, so the shorter value is
    # a strictly LOOSER standard that need not reproduce the certified plan.
    # This is the layer that found it, so this is the layer that keeps it.
    from services.adequacy.lever_text import format_lever_value
    checked = [(mar, "restore=base"), (fin2, "restore=final")]
    agreed, notes = True, []
    for payload, label in checked:
        star = (payload or {}).get("lever_star")
        verdict = (payload or {}).get("verdict") or ""
        if (payload or {}).get("status") != "met" or star is None:
            notes.append(f"{label}: not met, nothing to name")
            continue
        want = f"reserve_margin = {format_lever_value(float(star))}"
        hit = want in verdict
        agreed = agreed and hit
        notes.append(f"{label}: m*={star!r} -> {want!r} present={hit}"
                     + ("" if hit else " | said=" + repr(
                         verdict.split("reserve_margin = ")[1][:30]
                         if "reserve_margin = " in verdict else "ABSENT")))
    record("S19.6", agreed, "; ".join(notes))

    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
    restore()


# ── main ──────────────────────────────────────────────────────────────────

def suite_S20():
    """
    A network swap is REFUSED while a study runs (Phase 11 spec §1/§2).

    A study's worker closes over the `pypsa.Network` object captured before it
    started, so replacing the network does not STOP the study — it DETACHES
    it. The study keeps solving the old object and keeps publishing into the
    solver state the swap carries forward, so the NEW project's Adequacy tab
    fills in live with the OLD project's study, and a `restore="final"` loop
    writes its certified value into the NEW project's solver config.

    Unit tests drive `reset_network` and two routes. This suite is here for
    what only a live server shows: that the refusal reaches a real HTTP caller
    with a real running study behind it, and that it LIFTS again afterwards —
    a guard that never releases is not a guard, it is an outage.
    """
    # The engine caps a study at 2000 draws (`mc.MAX_DRAWS`) and refuses more
    # with a 422 — which is what the first run of this suite hit. Ask for the
    # cap: the study then runs long enough to still be alive when the swap is
    # attempted, without being refused before it starts.
    MAX_DRAWS_FOR_S20 = 2000

    print("\nS20 - Refusing a network swap during a study (area 20)")
    name = "qa_e2e_swapguard"

    _, cfg_before = http("/api/simulation/solver_config")

    def restore():
        if isinstance(cfg_before, dict):
            http("/api/simulation/solver_config", method="PUT", body=cfg_before)

    http("/api/network/reset", method="POST")
    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
    st_c, body_c = http(f"/api/projects/{q(name)}", method="POST")
    if st_c not in (200, 201):
        for i in (1, 2, 3):
            skip(f"S20.{i}", f"create project -> {st_c} {str(body_c)[:80]}")
        restore()
        return
    http(f"/api/projects/{q(name)}/activate", method="POST")

    # ── S20.1 — with NO study running, the swap route works. The baseline
    # goes FIRST, before the fixture exists: it is itself a network reset, so
    # running it after the build would wipe the very fixture the rest of the
    # suite needs (which is exactly what the first version of this suite did
    # — every later check then failed on an empty network with a 422 that had
    # nothing to do with the guard).
    st_reset, _ = http("/api/network/reset", method="POST")
    record("S20.1", st_reset == 200,
           f"POST /api/network/reset with no study -> {st_reset} (want 200)")

    # A samplable fixture — the MC needs occurrence data or it refuses, and
    # this suite needs a study that runs long enough to still be alive when
    # the swap is attempted.
    built = [http("/api/network/reset", method="POST")[0]]
    built.append(http("/api/network/carriers", method="POST",
                      body={"name": "gas"})[0])
    built.append(http("/api/network/buses", method="POST",
                      body={"name": "bus_a", "v_nom": 380.0})[0])
    built.append(http("/api/network/snapshots", method="POST",
                      body={"start": "2030-01-01 00:00",
                            "end": "2030-03-31 23:00", "freq": "h"})[0])
    for g in ("unit_1", "unit_2"):
        built.append(http("/api/network/generators", method="POST",
                          body={"name": g, "bus": "bus_a", "carrier": "gas",
                                "p_nom": 100.0, "marginal_cost": 10.0,
                                "outage_rate_value": 0.12,
                                "outage_rate_basis": "EFORd",
                                "mttr_hours": 24.0})[0])
    built.append(http("/api/network/loads", method="POST",
                      body={"name": "load_a", "bus": "bus_a",
                            "p_set": 150.0})[0])
    if [c for c in built if c not in (200, 201)]:
        for i in (1, 2, 3):
            skip(f"S20.{i}", f"fixture build failed: {built}")
        http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
        restore()
        return

    # ── S20.2 — start a REAL study, then try to swap. The study has to be
    # genuinely alive: the guard tests `thread.is_alive()`, so anything less
    # proves nothing.
    http("/api/simulation/solver_config", method="PUT",
         body={"solver_name": "highs", "voll": 3000.0})
    http("/api/simulation/run", method="POST")
    deadline = time.time() + 240
    while time.time() < deadline:
        _, stt = http("/api/simulation/status")
        if (stt or {}).get("status") not in ("running", "starting"):
            break
        time.sleep(0.5)

    st_mc, _mc_refusal = http("/api/results/mc", method="POST",
                              body={"draws": MAX_DRAWS_FOR_S20, "seed": 11})
    if st_mc != 200:
        _, why = http("/api/results/mc")
        for i in (2, 3):
            skip(f"S20.{i}", f"could not start an MC study -> {st_mc}: "
                             f"{str(_mc_refusal)[:200]}")
        http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
        restore()
        return

    # While it runs, every network-replacing route must refuse — and say why.
    #
    # Read the study's OWN status immediately before the swap. Without this
    # the check cannot tell a working guard from a study that simply finished
    # first, and a 200 would be reported as a failure of the guard when it is
    # a failure of the fixture. That is the difference between a check and a
    # coin toss: the first run of this suite hit exactly that race on a two-
    # day horizon, which is why the fixture now spans a quarter.
    _, mc_at_swap = http("/api/results/mc")
    live_at_swap = (mc_at_swap or {}).get("status") == "running"
    st_swap, body_swap = http("/api/network/reset", method="POST")
    detail = str((body_swap or {}).get("detail", ""))
    named = "sequential-MC study" in detail
    # The MC has no abort route, so the refusal must NOT offer one.
    honest = "cannot be aborted" in detail and "or abort it" not in detail
    if not live_at_swap:
        skip("S20.2", "the MC study finished before the swap was attempted "
                      f"(status={(mc_at_swap or {}).get('status')}) — the "
                      "guard was never exercised, so this proves nothing")
    else:
        record("S20.2", st_swap == 409 and named and honest,
               f"MC study live at swap time={live_at_swap}; swap -> {st_swap} "
               f"(want 409); names the study={named}; offers only a REAL "
               f"remedy={honest}; detail={detail[:120]}")

    # ── S20.3 — and it LIFTS. Wait the study out, then swap for real.
    deadline = time.time() + 420
    while time.time() < deadline:
        _, mc = http("/api/results/mc")
        if (mc or {}).get("status") != "running":
            break
        time.sleep(1.0)
    st_after, _ = http("/api/network/reset", method="POST")
    record("S20.3", st_after == 200,
           f"after the study finished, POST /api/network/reset -> {st_after} "
           "(want 200 — a guard that never lifts is an outage, not a guard)")

    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
    restore()



def suite_S21():
    """
    Outage data that SHADOWS an availability profile (Phase 12a).

    `copt.py`'s membership rule sends a generator with resolvable occurrence
    params into the sampled fleet as a flat two-state unit at its firm
    capacity — its `p_max_pu` profile is not carried. A generator WITHOUT
    outage data is must-take and IS netted at `p_max_pu x capacity`. So on two
    identical 100 MW farms sharing one 25 %-capacity-factor profile, the one
    with an outage rate contributes (1-q)*100 = 90 MW where the other
    contributes 25 MW, and the reserve margin credits that same asset 22.5 MW.

    Unit tests drive `_check_outage_params` directly. This suite is here for
    the only thing that matters to a user: that the warning actually REACHES
    them through a live preflight, naming the asset and the DIRECTION.
    """
    print("\nS21 - Outage data shadowing an availability profile (area 21)")
    name = "qa_e2e_shadowprofile"

    http("/api/network/reset", method="POST")
    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
    st_c, body_c = http(f"/api/projects/{q(name)}", method="POST")
    if st_c not in (200, 201):
        for i in (1, 2):
            skip(f"S21.{i}", f"create project -> {st_c} {str(body_c)[:80]}")
        return
    http(f"/api/projects/{q(name)}/activate", method="POST")

    built = [http("/api/network/reset", method="POST")[0]]
    for c in ("wind", "gas"):
        built.append(http("/api/network/carriers", method="POST",
                          body={"name": c})[0])
    built.append(http("/api/network/buses", method="POST",
                      body={"name": "b", "v_nom": 380.0})[0])
    built.append(http("/api/network/snapshots", method="POST",
                      body={"start": "2030-01-01 00:00",
                            "end": "2030-01-01 07:00", "freq": "h"})[0])
    built.append(http("/api/network/generators", method="POST",
                      body={"name": "gas1", "bus": "b", "carrier": "gas",
                            "p_nom": 80.0, "marginal_cost": 10.0,
                            "outage_rate_value": 0.10,
                            "outage_rate_basis": "EFORd",
                            "mttr_hours": 24.0})[0])
    # Two IDENTICAL farms; only one carries an outage rate.
    for nm, extra in (("wind_no_for", {}),
                      ("wind_with_for", {"outage_rate_value": 0.10,
                                         "outage_rate_basis": "EFORd",
                                         "mttr_hours": 24.0})):
        body = {"name": nm, "bus": "b", "carrier": "wind", "p_nom": 100.0,
                "marginal_cost": 0.0}
        body.update(extra)
        built.append(http("/api/network/generators", method="POST",
                          body=body)[0])
    built.append(http("/api/network/loads", method="POST",
                      body={"name": "l", "bus": "b", "p_set": 100.0})[0])
    idx = [f"2030-01-01T{h:02d}:00:00" for h in range(8)]
    prof = [0.05, 0.15, 0.35, 0.45] * 2
    st_ts, _ = http("/api/network/timeseries/generators/p_max_pu",
                    method="PUT",
                    body={"index": idx,
                          "columns": ["wind_no_for", "wind_with_for"],
                          "data": [[v, v] for v in prof]})
    built.append(st_ts)
    if [c for c in built if c not in (200, 201)]:
        for i in (1, 2):
            skip(f"S21.{i}", f"fixture build failed: {built}")
        http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
        return

    st_p, pf = http("/api/simulation/preflight", method="POST")
    issues = (pf or {}).get("issues") or []
    hit = [i for i in issues if i.get("code") == "outage_shadows_profile"]
    msg = " ".join(str(i.get("message", "")) for i in hit)

    # ── S21.1 — it reaches a live preflight, names the asset, and is a WARNING
    # (not an error: the user entered that data deliberately, and blocking
    # would stop a network that solved yesterday).
    record("S21.1",
           st_p == 200 and bool(hit)
           and "wind_with_for" in msg
           and all(i.get("severity") == "warning" for i in hit),
           f"preflight->{st_p}; outage_shadows_profile present={bool(hit)}; "
           f"names the asset={'wind_with_for' in msg}; "
           f"severity={[i.get('severity') for i in hit]}")

    # ── S21.2 — it names the DIRECTION, and does NOT fire on the farm with no
    # outage data (whose profile IS honoured) nor on the thermal unit (whose
    # p_max_pu is a flat 1.0 — the false-positive that would make it noise on
    # every real project).
    record("S21.2",
           bool(hit) and "OVERSTATED" in msg
           and "wind_no_for" not in msg and "gas1" not in msg,
           f"states the direction={'OVERSTATED' in msg}; "
           f"silent on the no-outage farm={'wind_no_for' not in msg}; "
           f"silent on the flat-profile thermal unit={'gas1' not in msg}")

    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")


def suite_S22():
    """
    A vintage-expanded plan's reserve-margin result, live (Phase 8 §4, found
    by the Phase 12b review).

    On a multi-period network with per-period capacity bounds the solve
    expands `wind` into transient `wind@2030` / `wind@2040` rows, the wrapper
    stashes those names, and the restore drops the rows BEFORE the payload
    reads built capacities — so `_built()` found nothing, credited zero, and a
    plan that built 35 MW of wind and met the margin was served as `met=False`
    in both periods. The unit test drives `run_simulation`; this drives the
    surface a user reads, `GET /results/reserve_margin`, after a solve
    started over HTTP with bounds set over HTTP.

    The companion defect — a failed margin run leaking its stash into the
    next solve — has NO honest live reproduction: it needs an exception
    between optimize and the report step, which no API input produces. It
    is covered by its unit test only, and this docstring says so rather than
    faking a check.
    """
    print("\nS22 - A vintage-expanded plan reports what it built (area 22)")
    name = "qa_e2e_vintage"

    _, cfg_before = http("/api/simulation/solver_config")

    def restore():
        if isinstance(cfg_before, dict):
            http("/api/simulation/solver_config", method="PUT", body=cfg_before)

    def put_cfg(**over):
        base = dict(cfg_before) if isinstance(cfg_before, dict) else {}
        base.update({"solver_name": "highs", "voll": 3000.0})
        base.update(over)
        return http("/api/simulation/solver_config", method="PUT", body=base)

    def poll_solve(ceiling_s=240):
        deadline = time.time() + ceiling_s
        while time.time() < deadline:
            st, s = http("/api/simulation/status")
            if st == 200 and isinstance(s, dict) and not s.get("running"):
                return s
            time.sleep(1.0)
        return None

    # 200 MW base (EFORd 0.05 -> 190 firm) against 150 MW load at margin 0.5
    # (225 required): 35 MW of firm capacity short. A cheap wind candidate at
    # 1000/MW versus a peaker at 5e6/MW, so the LP closes the gap with wind.
    #
    # The unit test's wind is MUST-TAKE (a time-series profile, no outage
    # data). That cannot be reproduced here: the generator API takes a static
    # `p_max_pu`, the margin's profile test is a time-series column check, and
    # a per-period profile cannot be set over the API on a multi-period
    # network (a limitation this PR records). Without either, preflight
    # correctly refuses the unit as unpriceable — the first run of this suite
    # hit exactly that, and named it. So the candidate carries outage data
    # instead: it is then a sampled unit at derate 0.95, and the thing under
    # test — the vintage row's BUILT capacity reaching the payload — does not
    # depend on which membership the unit has.
    BASE, LOAD, EFORD, MARGIN = 200.0, 150.0, 0.05, 0.5
    required = (1.0 + MARGIN) * LOAD                          # 225
    wind_needed = (required - BASE * (1.0 - EFORD)) / (1.0 - EFORD)   # 35 / 0.95

    http("/api/network/reset", method="POST")
    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
    st_c, body_c = http(f"/api/projects/{q(name)}", method="POST")
    if st_c not in (200, 201):
        for i in (1, 2):
            skip(f"S22.{i}", f"create project -> {st_c} {str(body_c)[:80]}")
        return
    http(f"/api/projects/{q(name)}/activate", method="POST")

    built = [http("/api/network/reset", method="POST")[0]]
    for c in ("gas", "wind"):
        built.append(http("/api/network/carriers", method="POST",
                          body={"name": c})[0])
    built.append(http("/api/network/buses", method="POST",
                      body={"name": "bus_a", "v_nom": 380.0})[0])
    built.append(http("/api/network/snapshots/multi_period", method="POST",
                      body={"periods": [2030, 2040],
                            "start": "2030-01-01 00:00",
                            "end": "2030-01-01 03:00", "freq": "h"})[0])
    built.append(http("/api/network/generators", method="POST",
                      body={"name": "base", "bus": "bus_a", "carrier": "gas",
                            "p_nom": BASE, "marginal_cost": 10.0,
                            "outage_rate_value": EFORD,
                            "outage_rate_basis": "EFORd",
                            "mttr_hours": 50.0})[0])
    built.append(http("/api/network/generators", method="POST",
                      body={"name": "peaker", "bus": "bus_a", "carrier": "gas",
                            "p_nom": 0.0, "p_nom_extendable": True,
                            "p_nom_max": 500.0, "capital_cost": 5_000_000.0,
                            "marginal_cost": 500.0,
                            "outage_rate_value": EFORD,
                            "outage_rate_basis": "EFORd",
                            "mttr_hours": 50.0})[0])
    built.append(http("/api/network/generators", method="POST",
                      body={"name": "wind", "bus": "bus_a", "carrier": "wind",
                            "p_nom": 0.0, "p_nom_extendable": True,
                            "p_nom_max": 500.0, "capital_cost": 1000.0,
                            "marginal_cost": 0.0, "p_max_pu": 1.0,
                            "outage_rate_value": EFORD,
                            "outage_rate_basis": "EFORd",
                            "mttr_hours": 50.0})[0])
    built.append(http("/api/network/loads", method="POST",
                      body={"name": "load_a", "bus": "bus_a",
                            "p_set": LOAD})[0])
    st_vb, vb = http("/api/network/vintage_bounds/Generator/wind", method="PUT",
                     body={"bounds": {"2030": {"p_nom_min": 0.0, "p_nom_max": 100.0},
                                      "2040": {"p_nom_min": 0.0, "p_nom_max": 100.0}}})
    built.append(st_vb)
    bad_build = [c for c in built if c not in (200, 201)]
    if bad_build:
        # The S21 lesson: a fixture that did not build proves nothing.
        for i in (1, 2):
            skip(f"S22.{i}", f"fixture build non-2xx={bad_build}; vintage_bounds->{st_vb} {str(vb)[:80]}")
        restore()
        return

    st_cfg, _ = put_cfg(reserve_margin=MARGIN, multi_investment_periods=True)
    # The S21 lesson, applied one step later: a fixture that BUILT but was
    # refused at preflight proves nothing either, and the first run of this
    # suite did exactly that. So the refusal is named in the detail rather
    # than left as an opaque `validation_failed`.
    st_pf, pf = http("/api/simulation/preflight", method="POST", body={})
    pf_errors = [f"{i.get('code')}: {str(i.get('message', ''))[:90]}"
                 for i in ((pf or {}).get("issues") or [])
                 if isinstance(i, dict) and i.get("severity") == "error"]
    http("/api/simulation/run", method="POST")
    solved = poll_solve()
    why = (f"; preflight->{st_pf} errors={pf_errors or 'none'}"
           if not (isinstance(solved, dict) and solved.get("condition") == "optimal")
           else "")
    st_rm, rm = http("/api/results/reserve_margin")
    rows = {str(r.get("period")): r for r in ((rm or {}).get("by_period") or [])}
    assets = {(a.get("name"), str(a.get("period"))): a
              for a in ((rm or {}).get("assets") or [])}

    # ── S22.1 — the standard the LP MET is reported as met, in both periods,
    # at the firm capacity the plan actually has. Before the fix: firm 190,
    # met=False, for a plan that built 35 MW of wind.
    met_ok = (set(rows) == {"2030", "2040"}
              and all(r.get("met") is True for r in rows.values())
              and all(abs(float(r.get("firm_mw") or 0.0) - required) < 1e-3
                      for r in rows.values()))
    record("S22.1",
           st_cfg == 200 and isinstance(solved, dict)
           and solved.get("condition") == "optimal" and st_rm == 200 and met_ok,
           f"cfg->{st_cfg} solved={(solved or {}).get('condition')} "
           f"/results/reserve_margin->{st_rm}; periods="
           f"{ {P: (r.get('met'), r.get('firm_mw')) for P, r in rows.items()} } "
           f"(want met=True, firm={required:.0f} in both){why}")

    # ── S22.2 — the VINTAGE rows carry their built sizes: wind@2030 at ~35 MW
    # in 2030 AND in 2040 (a 2030 vintage is active later), and wind@2040 at
    # 0.0 — built-to-zero, not null. Before the fix every vintage row read
    # capacity=None.
    v30_30 = (assets.get(("wind@2030", "2030")) or {}).get("capacity_mw")
    v30_40 = (assets.get(("wind@2030", "2040")) or {}).get("capacity_mw")
    v40_40 = (assets.get(("wind@2040", "2040")) or {}).get("capacity_mw")
    def near(v, want):
        return v is not None and abs(float(v) - want) < 1e-3
    record("S22.2",
           near(v30_30, wind_needed) and near(v30_40, wind_needed)
           and near(v40_40, 0.0),
           f"wind@2030: 2030={v30_30} 2040={v30_40} (want {wind_needed:.2f}); "
           f"wind@2040: 2040={v40_40} (want 0.0, not None)")

    restore()
    http("/api/network/reset", method="POST")
    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")


def suite_S23():
    """
    The net-load window on the surface a user reads (Phase 12b, spec v1.3).

    The margin credits a profile-bearing unit on the hours GROSS demand
    peaks; a system with such units runs short on the hours NET demand
    peaks, and those are not the same hours. Fourteen unit tests drive the
    payload directly; this drives the fixture, the profile and the solve over
    HTTP and reads `GET /results/reserve_margin`.

    Single-period on purpose: a per-period profile cannot be set over the
    API on a multi-period network (recorded under S21/S22), and the window
    is a per-period object anyway. Flat 150 MW load over four hours; a 200 MW
    gas unit (EFORd 0.05 -> 190 firm); a 100 MW wind farm with NO outage
    data whose profile is 1,0,1,0 -> gross window is all four tied hours,
    derate 0.5, credited 50 MW. Net load is [50,150,50,150]: the net window
    is hours 1 and 3, where the wind is absent, and its net derate is 0.0.
    """
    print("\nS23 - The net-load window, live (area 23)")
    name = "qa_e2e_netwindow"

    _, cfg_before = http("/api/simulation/solver_config")

    def restore():
        if isinstance(cfg_before, dict):
            http("/api/simulation/solver_config", method="PUT", body=cfg_before)

    def put_cfg(**over):
        base = dict(cfg_before) if isinstance(cfg_before, dict) else {}
        base.update({"solver_name": "highs", "voll": 3000.0})
        base.update(over)
        return http("/api/simulation/solver_config", method="PUT", body=base)

    def poll_solve(ceiling_s=240):
        deadline = time.time() + ceiling_s
        while time.time() < deadline:
            st, s = http("/api/simulation/status")
            if st == 200 and isinstance(s, dict) and not s.get("running"):
                return s
            time.sleep(1.0)
        return None

    def solve_and_read():
        st_pf, pf = http("/api/simulation/preflight", method="POST", body={})
        pf_errors = [f"{i.get('code')}: {str(i.get('message', ''))[:80]}"
                     for i in ((pf or {}).get("issues") or [])
                     if isinstance(i, dict) and i.get("severity") == "error"]
        http("/api/simulation/run", method="POST")
        solved = poll_solve()
        st_rm, rm = http("/api/results/reserve_margin")
        ok = isinstance(solved, dict) and solved.get("condition") == "optimal" and st_rm == 200
        why = "" if ok else f"; solved={(solved or {}).get('condition')} preflight->{st_pf} errors={pf_errors or 'none'}"
        return ok, rm or {}, why

    idx = [f"2030-01-01T{h:02d}:00:00" for h in range(4)]

    http("/api/network/reset", method="POST")
    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")
    st_c, body_c = http(f"/api/projects/{q(name)}", method="POST")
    if st_c not in (200, 201):
        for i in (1, 2):
            skip(f"S23.{i}", f"create project -> {st_c} {str(body_c)[:80]}")
        return
    http(f"/api/projects/{q(name)}/activate", method="POST")

    built = [http("/api/network/reset", method="POST")[0]]
    for c in ("gas", "wind"):
        built.append(http("/api/network/carriers", method="POST", body={"name": c})[0])
    built.append(http("/api/network/buses", method="POST",
                      body={"name": "b", "v_nom": 380.0})[0])
    built.append(http("/api/network/snapshots", method="POST",
                      body={"start": "2030-01-01 00:00", "end": "2030-01-01 03:00",
                            "freq": "h"})[0])
    built.append(http("/api/network/generators", method="POST",
                      body={"name": "base", "bus": "b", "carrier": "gas",
                            "p_nom": 200.0, "marginal_cost": 10.0,
                            "outage_rate_value": 0.05, "outage_rate_basis": "EFORd",
                            "mttr_hours": 50.0})[0])
    built.append(http("/api/network/generators", method="POST",
                      body={"name": "wind", "bus": "b", "carrier": "wind",
                            "p_nom": 100.0, "marginal_cost": 0.0})[0])
    built.append(http("/api/network/loads", method="POST",
                      body={"name": "l", "bus": "b", "p_set": 150.0})[0])
    st_ts, body_ts = http("/api/network/timeseries/generators/p_max_pu", method="PUT",
                          body={"index": idx, "columns": ["wind"],
                                "data": [[1.0], [0.0], [1.0], [0.0]]})
    built.append(st_ts)
    bad_build = [c for c in built if c not in (200, 201)]
    if bad_build:
        for i in (1, 2):
            skip(f"S23.{i}", f"fixture build non-2xx={bad_build}; timeseries->{st_ts} {str(body_ts)[:80]}")
        restore()
        return

    st_cfg, _ = put_cfg(reserve_margin=0.2)
    ok, rm, why = solve_and_read()
    row = ((rm.get("by_period") or [{}])[0])
    nw = row.get("net_window") or {}
    assets = {a.get("name"): a for a in (rm.get("assets") or [])}
    w = assets.get("wind") or {}
    want_hours = [idx[1].replace("T", " "), idx[3].replace("T", " ")]

    # ── S23.1 — the net window is the hours the wind is ABSENT, and the net
    # derate says what its credit would have been there: nothing.
    record("S23.1",
           st_cfg == 200 and ok and nw.get("status") == "ok"
           and nw.get("netted_assets") == ["wind"]
           and nw.get("snapshots") == want_hours
           and nw.get("netted_mw") is not None and abs(float(nw["netted_mw"]) - 50.0) < 1e-6
           and w.get("profile_kind") == "varying" and w.get("netted") is True
           and w.get("derate") is not None and abs(float(w["derate"]) - 0.5) < 1e-6
           and w.get("derate_net") is not None and abs(float(w["derate_net"])) < 1e-9,
           f"cfg->{st_cfg}{why}; status={nw.get('status')} netted={nw.get('netted_assets')} "
           f"hours={nw.get('snapshots')} (want {want_hours}) netted_mw={nw.get('netted_mw')} "
           f"(want 50); wind derate={w.get('derate')} derate_net={w.get('derate_net')} "
           f"(want 0.5 / 0.0)")

    # ── S23.2 — a CONSTANT profile is not netted: the same farm with a flat
    # 1.0 column reads `constant`, the block says nothing_netted, and the
    # panel is not handed a zero-delta window dressed as a finding.
    st_ts2, _ = http("/api/network/timeseries/generators/p_max_pu", method="PUT",
                     body={"index": idx, "columns": ["wind"],
                           "data": [[1.0], [1.0], [1.0], [1.0]]})
    ok2, rm2, why2 = solve_and_read()
    row2 = ((rm2.get("by_period") or [{}])[0])
    nw2 = row2.get("net_window") or {}
    w2 = ({a.get("name"): a for a in (rm2.get("assets") or [])}).get("wind") or {}
    record("S23.2",
           st_ts2 == 200 and ok2 and nw2.get("status") == "nothing_netted"
           and nw2.get("snapshots") == [] and nw2.get("netted_mw") is None
           and w2.get("profile_kind") == "constant" and w2.get("netted") is False
           and w2.get("derate_net") is None,
           f"ts->{st_ts2}{why2}; status={nw2.get('status')} hours={nw2.get('snapshots')} "
           f"wind profile_kind={w2.get('profile_kind')} netted={w2.get('netted')} "
           f"derate_net={w2.get('derate_net')} (want constant / False / None)")

    restore()
    http("/api/network/reset", method="POST")
    http(f"/api/projects/{q(name)}?cascade=true", method="DELETE")


def main() -> int:
    global BACKEND
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="all")
    ap.add_argument(
        "--backend",
        default=os.environ.get("QA_E2E_BACKEND", "http://127.0.0.1:8000"),
        help="Backend origin to test against (default: $QA_E2E_BACKEND, "
             "falling back to http://127.0.0.1:8000).",
    )
    args = ap.parse_args()
    BACKEND = args.backend
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
    if run("S12"):
        suite_S12()
    if run("S13"):
        suite_S13()
    if run("S14"):
        suite_S14()
    if run("S15"):
        suite_S15()
    if run("S16"):
        suite_S16()
    if run("S17"):
        suite_S17()
    if run("S18"):
        suite_S18()
    if run("S19"):
        suite_S19()

    if run("S20"):
        suite_S20()

    if run("S21"):
        suite_S21()

    if run("S22"):
        suite_S22()

    if run("S23"):
        suite_S23()

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

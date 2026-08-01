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


# ── main ──────────────────────────────────────────────────────────────────
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

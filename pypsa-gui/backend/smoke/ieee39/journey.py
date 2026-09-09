"""IEEE 39-bus FMEA journey against a live backend. Every response is written
to <outdir>/NN_step.json as it lands (a rate limit or crash loses nothing),
and a findings list is printed at the end. Stdlib only."""
from __future__ import annotations
import json, math, sys, time, urllib.request, urllib.error, pathlib, urllib.parse

BASE = "http://127.0.0.1:8000"
OUT = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "journey_out")
OUT.mkdir(parents=True, exist_ok=True)
NC = pathlib.Path(sys.argv[1])
PROJ = sys.argv[3] if len(sys.argv) > 3 else "ieee39_fmea"
LOOP_TARGET = float(sys.argv[4]) if len(sys.argv) > 4 else 2.4
STEP = [0]
FINDINGS: list[str] = []


def http(path, method="GET", body=None, timeout=600, raw=None, ctype=None):
    url = BASE + path
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", ctype or "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode("utf-8", "replace")
            try: return r.status, json.loads(txt) if txt else None
            except json.JSONDecodeError: return r.status, txt
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        try: return e.code, json.loads(txt) if txt else None
        except json.JSONDecodeError: return e.code, txt


def save(name, st, body):
    STEP[0] += 1
    p = OUT / f"{STEP[0]:02d}_{name}.json"
    p.write_text(json.dumps({"status": st, "body": body}, indent=1, default=str))
    print(f"[{STEP[0]:02d}] {name}: HTTP {st}", flush=True)
    return body


def finding(msg):
    FINDINGS.append(msg); print("  !! " + msg, flush=True)


def finite_scan(obj, path="$", bad=None):
    bad = [] if bad is None else bad
    if isinstance(obj, float) and not math.isfinite(obj): bad.append(path)
    elif isinstance(obj, dict):
        for k, v in obj.items(): finite_scan(v, f"{path}.{k}", bad)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5000]): finite_scan(v, f"{path}[{i}]", bad)
    return bad


def poll(path, done=("done", "failed", "aborted", "completed", "error"), timeout=3600, key="status"):
    t0 = time.time(); last = None
    while time.time() - t0 < timeout:
        st, b = http(path)
        if st != 200:
            return st, b
        s = b.get(key) if isinstance(b, dict) else None
        if s != last:
            print(f"    {path} -> {s} ({time.time()-t0:.0f}s)", flush=True); last = s
        if s in done or (s is not None and s not in ("running", "pending", "queued", "starting")):
            return st, b
        time.sleep(1.0)
    return 0, {"timeout": path}


def multipart(path, filename, content, field="file"):
    boundary = "----ieee39boundary"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return http(path, "POST", raw=body, ctype=f"multipart/form-data; boundary={boundary}")


q = urllib.parse.quote

# ── 0. fresh project, import the network, save ─────────────────────────
save("cap", *http("/api/simulation/capabilities"))
http(f"/api/projects/{PROJ}?cascade=true", "DELETE")
save("create_project", *http(f"/api/projects/{PROJ}", "POST"))
save("activate", *http(f"/api/projects/{PROJ}/activate", "POST"))
st, imp = multipart("/api/io/import/netcdf", "ieee39.nc", NC.read_bytes())
save("import_netcdf", st, imp)
if st != 200: sys.exit("import failed")
save("save", *http(f"/api/projects/{PROJ}", "POST"))
st, gens = http("/api/network/generators"); save("generators", st, gens)
st, sus = http("/api/network/storage_units"); save("storage_units", st, sus)
# outage data as the editor sees it
for g in gens if isinstance(gens, list) else []:
    if g.get("name") in ("G30", "G31") and g.get("outage_rate_basis") not in ("EFORd",):
        finding(f"generator {g['name']} basis round-trip: {g.get('outage_rate_basis')!r} (typed EFORd)")

# ── 1. preflight + solve with margin, VOLL ─────────────────────────────
st, cfg0 = http("/api/simulation/solver_config"); save("config_before", st, cfg0)
cfg = dict(cfg0 or {})
cfg.update({"solver_name": "highs", "voll": 10000.0, "reserve_margin": 0.15, "prm_peak_hours": 10})
save("config_put", *http("/api/simulation/solver_config", "PUT", cfg))
st, pf = http("/api/simulation/preflight", "POST", {}); save("preflight", st, pf)
if isinstance(pf, dict):
    for it in pf.get("issues", pf.get("items", [])):
        print("    preflight:", it.get("severity", it.get("level")), it.get("code"), (it.get("message") or "")[:140])
st, run = http("/api/simulation/run", "POST", {}); save("run", st, run)
st, stt = poll("/api/simulation/status", done=("completed", "failed", "aborted", "error", "idle"))
save("run_status", st, stt)
if stt.get("status") != "completed":
    finding(f"solve did not complete: {stt}")
save("reserve_margin", *http("/api/results/reserve_margin"))
save("adequacy", *http("/api/results/adequacy"))
save("lost_load", *http("/api/results/lost_load"))
save("cost_breakdown", *http("/api/results/cost_breakdown"))
save("statistics", *http("/api/results/statistics"))
st, fm = http("/api/results/fmea_modes"); save("fmea_modes", st, fm)

# ── 2. COPT ────────────────────────────────────────────────────────────
st, copt = http("/api/results/copt"); save("copt", st, copt)
if st == 200 and isinstance(copt, dict):
    bad = finite_scan(copt)
    if bad: finding(f"copt non-finite at {bad[:5]}")
    fl = copt.get("fleet", {})
    print("    copt fleet keys:", sorted(fl.keys()) if isinstance(fl, dict) else fl)
    print("    copt metrics:", copt.get("metrics"))
else:
    finding(f"copt {st}: {str(copt)[:300]}")

# ── 3. MC + ELCC ───────────────────────────────────────────────────────
st, cands = http("/api/results/mc/elcc_candidates"); save("elcc_candidates", st, cands)
assets = (cands or {}).get("assets", [])[:3] if isinstance(cands, dict) else []
body = {"draws": 200, "seed": 39, "elcc_assets": [{"kind": a["kind"], "name": a["name"]} for a in assets],
        "elcc_portfolio": True}
save("mc_post", *http("/api/results/mc", "POST", body))
st, mc = poll("/api/results/mc"); save("mc", st, mc)
if isinstance(mc, dict):
    if mc.get("status") != "done": finding(f"mc status {mc.get('status')}: {str(mc.get('error') or mc.get('message'))[:300]}")
    bad = finite_scan(mc)
    if bad: finding(f"mc non-finite at {bad[:5]}")
    r = mc.get("result") or mc
    print("    mc metrics:", {k: r.get(k) for k in ("lole_h", "eue_mwh", "lolp", "draws", "converged") if k in r})

# ── 4. stress scenarios + sweep ────────────────────────────────────────
SCEN = [{"id": "cold_snap", "kind": "parametric", "name": "Cold snap (load +15 %, wind 30 %)",
         "frequency_per_year": 2.0, "electrical_load_multiplier": 1.15,
         "renewable_availability_multiplier": 0.3},
        {"id": "heat_wave", "kind": "parametric", "name": "Heat wave (load +10 %)",
         "frequency_per_year": 3.0, "electrical_load_multiplier": 1.10},
        {"id": "climate_1987", "kind": "profiles", "name": "1987 climate year",
         "frequency_per_year": 1.0}]
save("stress_put", *http(f"/api/projects/{PROJ}/stress_scenarios", "PUT", {"scenarios": SCEN}))
st, ss = http(f"/api/projects/{PROJ}/stress_scenarios"); save("stress_scenarios", st, ss)
scen = ss.get("scenarios", []) if isinstance(ss, dict) else []
save("sweep_post", *http("/api/results/fmea_sweep", "POST", {"scenarios": scen}))
st, sw = poll("/api/results/fmea_sweep"); save("fmea_sweep", st, sw)
if isinstance(sw, dict) and sw.get("status") != "done":
    finding(f"sweep status {sw.get('status')}: {str(sw.get('error'))[:300]}")
save("fmea_modes_after_sweep", *http("/api/results/fmea_modes"))
save("status_after_sweep", *http("/api/simulation/status"))

# ── 5. frontier ────────────────────────────────────────────────────────
save("frontier_post", *http("/api/results/frontier", "POST", {"targets_permyriad": [10.0, 3.0, 1.0]}))
st, fr = poll("/api/results/frontier"); save("frontier", st, fr)
if isinstance(fr, dict) and fr.get("status") != "done":
    finding(f"frontier status {fr.get('status')}: {str(fr.get('error'))[:300]}")

# ── 6. coupling loop ───────────────────────────────────────────────────
save("coupling_post", *http("/api/results/coupling_loop", "POST",
                            {"target_lole_h": LOOP_TARGET, "draws": 100, "seed": 3, "max_solves": 6}))
st, cl = poll("/api/results/coupling_loop"); save("coupling_loop", st, cl)
save("status_after_coupling", *http("/api/simulation/status"))
save("reserve_margin_after_coupling", *http("/api/results/reserve_margin"))
if isinstance(cl, dict) and cl.get("status") not in ("met", "done"):
    finding(f"coupling status {cl.get('status')}: {str(cl.get('error'))[:300]}")

# ── 7. margin loop ─────────────────────────────────────────────────────
save("margin_post", *http("/api/results/margin_loop", "POST",
                          {"target_lole_h": LOOP_TARGET, "draws": 100, "seed": 3, "max_solves": 6}))
st, ml = poll("/api/results/margin_loop"); save("margin_loop", st, ml)
save("status_after_margin", *http("/api/simulation/status"))
save("reserve_margin_after_margin", *http("/api/results/reserve_margin"))
save("adequacy_after_margin", *http("/api/results/adequacy"))
if isinstance(ml, dict) and ml.get("status") not in ("met", "done"):
    finding(f"margin loop status {ml.get('status')}: {str(ml.get('error'))[:300]}")

# ── 8. worksheet, save, snapshot, bundle ───────────────────────────────
save("worksheet_get", *http(f"/api/projects/{PROJ}/worksheet"))
save("worksheet_put", *http(f"/api/projects/{PROJ}/worksheet", "PUT",
                            {"manual_rows": [{"mode_id": "expert:substation_B16", "component_class": "Bus", "name": "B16",
                                              "failure_class": "D", "occurrence_per_year": 0.1,
                                              "occurrence_basis": "expert", "severity_eur": 2.5e6,
                                              "criticality_eur_per_year": 2.5e5, "in_metric_scope": False,
                                              "engine": "expert", "fidelity": "expert_judgement",
                                              "mitigability": "bus-split scheme"}],
                             "overlays": {"generator:G39:forced_outage": {"mitigability": "black-start contract",
                                                                         "notes": "largest single infeed"}}}))
save("save2", *http(f"/api/projects/{PROJ}", "POST"))
save("snapshot", *http(f"/api/projects/{PROJ}/snapshots", "POST", {"label": "after journey", "message": "ieee39"}))
st, bundle = http(f"/api/projects/{PROJ}/bundle")
save("bundle", st, {"bytes": len(bundle) if isinstance(bundle, (bytes, str)) else None, "type": type(bundle).__name__})
save("worksheet_after", *http(f"/api/projects/{PROJ}/worksheet"))
save("config_restore", *http("/api/simulation/solver_config", "PUT", cfg0))

print("\nFINDINGS:" if FINDINGS else "\nno automated findings")
for f in FINDINGS: print(" -", f)
(OUT / "findings.json").write_text(json.dumps(FINDINGS, indent=1))

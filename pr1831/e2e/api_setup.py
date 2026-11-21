import httpx, json, time, pathlib, sys
S = pathlib.Path(sys.argv[1]); P = "acpf_e2e"
pw = (pathlib.Path.home()/".local/share/PyPSA Studio/dev-admin-password").read_text().strip()
c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=300)
r = c.post("/api/auth/login", json={"email": "admin@example.com", "password": pw}); print("login", r.status_code)
c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
def show(lbl, r): print(lbl, r.status_code, r.text[:300])
pass
show("activate", c.post(f"/api/projects/{P}/activate"))
with open(S/"acpf_partial.nc", "rb") as f:
    show("import", c.post("/api/io/import/netcdf", files={"file": ("acpf_partial.nc", f, "application/octet-stream")}))
show("save", c.post(f"/api/projects/{P}", params={"force": "true"}))
cfg = c.get("/api/simulation/solver_config").json()
print("cfg keys sample", {k: cfg.get(k) for k in ("mode","solver_name","run_ac_pf_after_lopf")})
show("cfg", c.put("/api/simulation/solver_config", json={"run_ac_pf_after_lopf": True}))
show("run", c.post("/api/simulation/run"))
for i in range(120):
    st = c.get("/api/simulation/status").json()
    if st.get("status") not in ("running", "queued", None): break
    time.sleep(1)
print("status", {k: st.get(k) for k in ("status","condition","objective")})
r = c.get("/api/results/ac_pf/status"); show("acpf", r)
(S/"acpf_status.json").write_text(r.text)

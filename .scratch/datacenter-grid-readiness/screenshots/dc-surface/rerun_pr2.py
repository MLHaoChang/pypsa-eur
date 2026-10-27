# Publish pq_demo and run the campus study with it (DC app surface PR 2: harmonic scan stage).
import os, time, httpx
B = "http://127.0.0.1:8000/api"
s = httpx.Client(timeout=600)
pw = open(os.path.expanduser("~/.local/share/PyPSA Studio/dev-admin-password")).read().strip()
print("login", s.post(f"{B}/auth/login", json={"email": "admin@example.com", "password": pw}).status_code)
s.headers["X-CSRF-Token"] = s.cookies.get("pypsa_gui_csrf")
C = f"{B}/campus-electrical/dcsurf-demo"
r = s.post(f"{C}/grid-codes/drafts/pq_demo/publish", json={"allow_unconfirmed": True}); print("publish", r.status_code, r.text[:200])
r = s.post(f"{C}/run", json={"profile": "pq_demo", "invest": False}); print("run", r.status_code, r.text[:200])
for _ in range(100):
    time.sleep(5)
    st = s.get(C).json()
    job = st.get("run_job")
    if not job or job.get("status") not in ("queued", "running"):
        print("done", (job or {}).get("status"), str((job or {}).get("error"))[:300]); break
st = s.get(C).json(); hs = (st.get("results") or {}).get("harmonic_scan") or {}; print("resonance", hs.get("resonance"), "notes", hs.get("notes"), "err", (st.get("results") or {}).get("harmonic_scan_error"))

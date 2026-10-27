# Publish extracted_demo (both DC 15 limits confirmed) and run the campus study with it,
# so Results shows the deadband in the "Needs an RMS study" line. Run after setup_demo.py.
import os, time, requests
B = "http://127.0.0.1:8000/api"
s = requests.Session()
pw = open(os.path.expanduser("~/.local/share/PyPSA Studio/dev-admin-password")).read().strip()
print("login", s.post(f"{B}/auth/login", json={"email": "admin@example.com", "password": pw}).status_code)
s.headers["X-CSRF-Token"] = s.cookies.get("pypsa_gui_csrf")
C = f"{B}/campus-electrical/dc15-demo"
for lim in ("q_capability_unit", "voltage_deadband_pct"):
    print("confirm", lim, s.post(f"{C}/grid-codes/drafts/extracted_demo/confirm", json={"limit": lim}).status_code)
r = s.post(f"{C}/grid-codes/drafts/extracted_demo/publish", json={"allow_unconfirmed": True}); print("publish", r.status_code, r.text[:200])
print("activate", s.post(f"{B}/projects/dc15-demo/activate").status_code)
if not os.environ.get("SKIP_SOLVE"):
  print("solve", s.post(f"{B}/simulation/run").status_code)
  for _ in range(100):
    time.sleep(5)
    st = s.get(f"{B}/simulation/status").json()
    if st.get("status") not in ("running", "queued", "starting"):
        print("solved", st.get("status"), st.get("objective")); break
print("save", s.post(f"{B}/projects/dc15-demo").status_code)
r = s.post(f"{C}/draft", json={"overwrite": True}); print("campus draft", r.status_code, r.text[:200])
r = s.post(f"{C}/run", json={"profile": "extracted_demo", "invest": False}); print("run", r.status_code, r.text[:200])
for _ in range(100):
    time.sleep(5)
    st = s.get(C).json()
    job = st.get("run_job")
    if not job or job.get("status") not in ("queued", "running"):
        print("done", (job or {}).get("status"), str((job or {}).get("error"))[:300]); break

# DC follow-ups item 5: a curve that a PV fails at half load and that stops at 0.9 pu (rated P not checked), then a
# campus run (no solve) so the Unit reactive capability table has pass, fail and not-checked rows.
# Generic values only (SAGC-1). Needs dc-surface/setup_pr1.py and run_pr2.py first.
import copy, os, time, httpx, yaml
B = "http://127.0.0.1:8000/api"; s = httpx.Client(timeout=600)
pw = open(os.path.expanduser("~/.local/share/PyPSA Studio/dev-admin-password")).read().strip()
s.post(f"{B}/auth/login", json={"email": "admin@example.com", "password": pw})
s.headers["X-CSRF-Token"] = s.cookies.get("pypsa_gui_csrf")
C = f"{B}/campus-electrical/dcsurf-demo"; P = f"{C}/grid-codes"
prof = yaml.safe_load(s.get(f"{P}/drafts/pq_demo").json()["yaml"])
prof["q_capability_unit"]["points"] = [[0.0, -0.4, 0.4], [0.5, -0.95, 0.95], [0.9, -0.3, 0.3]]
print("save draft", s.put(f"{P}/drafts/pq_demo", json={"yaml": yaml.safe_dump(prof, sort_keys=False)}).status_code)
print("publish", s.post(f"{P}/drafts/pq_demo/publish", json={"allow_unconfirmed": True}).status_code)
r = s.post(f"{C}/run", json={"profile": "pq_demo", "invest": False}); print("run", r.status_code)
for _ in range(100):
    time.sleep(5)
    job = s.get(C).json().get("run_job")
    if not job or job.get("status") not in ("queued", "running"):
        print("done", (job or {}).get("status"), str((job or {}).get("error"))[:300]); break
for row in s.get(C).json()["results"].get("ibr_q_curve") or []:
    print(row["unit"], row["p_pu"], row["status"], row["detail"][:90])

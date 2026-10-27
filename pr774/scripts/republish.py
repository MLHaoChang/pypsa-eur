# Republish pq_demo with another unbalance limit, so the last campus run's grid code reads as changed.
import os, httpx, yaml
B = "http://127.0.0.1:8000/api"; s = httpx.Client(timeout=300)
pw = open(os.path.expanduser("~/.local/share/PyPSA Studio/dev-admin-password")).read().strip()
s.post(f"{B}/auth/login", json={"email": "admin@example.com", "password": pw})
if s.cookies.get("pypsa_gui_csrf"): s.headers["X-CSRF-Token"] = s.cookies.get("pypsa_gui_csrf")
P = f"{B}/campus-electrical/dcsurf-demo/grid-codes"
r = s.get(f"{P}/drafts/pq_demo"); print("get draft", r.status_code)
prof = yaml.safe_load(r.json()["yaml"])
prof["unbalance_limit_pct"]["value"] = float(os.environ.get("V", "2.0"))
print("save", s.put(f"{P}/drafts/pq_demo", json={"yaml": yaml.safe_dump(prof, sort_keys=False)}).status_code)
print("publish", s.post(f"{P}/drafts/pq_demo/publish", json={"allow_unconfirmed": True}).status_code)
st = s.get(f"{B}/campus-electrical/dcsurf-demo").json(); print("grid_code_changed", st.get("grid_code_changed"), "stale", st.get("stale"))

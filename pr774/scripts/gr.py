import os, httpx, json
B="http://127.0.0.1:8000/api"; s=httpx.Client(timeout=120)
pw=open(os.path.expanduser("~/.local/share/PyPSA Studio/dev-admin-password")).read().strip()
s.post(f"{B}/auth/login", json={"email":"admin@example.com","password":pw})
rows={x["id"]:x for x in s.get(f"{B}/campus-electrical/dcsurf-demo/grid-readiness").json()["rows"]}
r=rows["current_distortion"]; print(r["verdict"], r["project_value"], json.dumps(r.get("tdd"))[:300])

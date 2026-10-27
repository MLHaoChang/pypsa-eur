import os, httpx as requests, yaml
B = "http://127.0.0.1:8000/api"
s = requests.Client(timeout=300)
pw = open(os.path.expanduser("~/.local/share/PyPSA Studio/dev-admin-password")).read().strip()
r = s.post(f"{B}/auth/login", json={"email": "admin@example.com", "password": pw}); print("login", r.status_code)
csrf = s.cookies.get("pypsa_gui_csrf")
if csrf: s.headers["X-CSRF-Token"] = csrf
r = s.post(f"{B}/projects/from_template/eh_datacenter", params={"name": "dcsurf-demo"}); print("create", r.status_code, r.text[:120])
P = f"{B}/campus-electrical/dcsurf-demo/grid-codes"
doc = {"sha256": "a" * 64, "filename": "grid-code.pdf", "title": "A grid code"}
r = s.post(f"{P}/drafts", json={"profile_id": "pq_demo", "overwrite": True}); print("draft", r.status_code)
prof = r.json()["profile"]; prof.pop("name", None); prof["document"] = doc
ext = lambda **kw: {**kw, "source": "extracted"}
prof["harmonic_resonance_clearance_h"] = ext(value=0.5, clause="generic test clause (harmonic resonance)", page=40,
                                             quote="no parallel resonance within 0.5 of a characteristic harmonic order")
prof["unbalance_limit_pct"] = ext(value=2.0, clause="generic test clause (voltage unbalance)", page=41,
                                  quote="the negative-sequence voltage shall not exceed 2 % of the positive-sequence voltage")
prof["current_tdd_standard"] = ext(value="ieee519-2014", clause="generic test clause (current distortion)", page=42,
                                   quote="current distortion limits as given in IEEE 519")
prof["q_capability_unit"] = ext(points=[[0.0, -0.4, 0.4], [0.5, -0.5, 0.5], [1.0, -0.3, 0.3]],
                               clause="generic test clause (reactive capability chart)", page=31,
                               quote="each generating unit shall provide reactive power within the capability chart")
prof["voltage_deadband_pct"] = ext(value=0.5, clause="generic test clause (voltage control)", page=32,
                                   quote="a deadband of 0.5 % of nominal voltage")
r = s.put(f"{P}/drafts/pq_demo", json={"yaml": yaml.safe_dump(prof, sort_keys=False)}); print("save", r.status_code, r.text[:300])

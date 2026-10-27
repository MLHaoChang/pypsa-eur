import json, os, sys, requests, yaml
B = "http://127.0.0.1:8000/api"
s = requests.Session()
pw = open(os.path.expanduser("~/.local/share/PyPSA Studio/dev-admin-password")).read().strip()
r = s.post(f"{B}/auth/login", json={"email": "admin@example.com", "password": pw}); print("login", r.status_code)
csrf = s.cookies.get("pypsa_gui_csrf")
if csrf: s.headers["X-CSRF-Token"] = csrf
print("cookies", list(s.cookies.keys()))
r = s.post(f"{B}/projects/from_template/eh_datacenter", params={"name": "dc15-demo"}); print("create", r.status_code, r.text[:200])
P = f"{B}/campus-electrical/dc15-demo/grid-codes"
doc = {"sha256": "a" * 64, "filename": "grid-code.pdf", "title": "A grid code"}
long_q = ("Each generating unit shall be capable of supplying and absorbing reactive power within the capability "
          "chart at every active power output from zero to rated, measured at the unit terminals, with the "
          "voltage within its continuous operating range.")
for pid, extra in [("blank_demo", None), ("extracted_demo", "extracted")]:
    r = s.post(f"{P}/drafts", json={"profile_id": pid, "overwrite": True}); print("draft", pid, r.status_code)
    if not extra:
        continue
    prof = r.json()["profile"]
    prof.pop("name", None)
    prof["document"] = doc
    prof["q_capability_unit"] = {"points": [[0.0, -0.4, 0.4], [0.5, -0.5, 0.5], [1.0, -0.3, 0.3]],
                                 "clause": "generic test clause (reactive capability chart)", "source": "extracted",
                                 "page": 31, "quote": long_q}
    prof["voltage_deadband_pct"] = {"value": 0.5, "clause": "generic test clause (voltage control)",
                                    "source": "extracted", "page": 32, "quote": "a deadband of 0.5 % of nominal voltage"}
    r = s.put(f"{P}/drafts/{pid}", json={"yaml": yaml.safe_dump(prof, sort_keys=False)}); print("save", r.status_code, r.text[:300])

# Creates the demo project the slice (a) shots open. Run with the backend venv.
import os

import httpx

B = "http://127.0.0.1:8000/api"
s = httpx.Client(timeout=120)
pw = (
    open(os.path.expanduser("~/.local/share/PyPSA Studio/dev-admin-password"))
    .read()
    .strip()
)
print(
    "login",
    s.post(
        f"{B}/auth/login", json={"email": "admin@example.com", "password": pw}
    ).status_code,
)
csrf = s.cookies.get("pypsa_gui_csrf")
if csrf:
    s.headers["X-CSRF-Token"] = csrf
r = s.post(f"{B}/projects/from_template/eh_h2_hub", params={"name": "theme-demo"})
print("create", r.status_code, r.text[:150])

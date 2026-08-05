"""
U-1 in LOCAL MODE — the deployment the feature exists for.

Every route test in `tests/` runs against the multi-tenant harness. Local mode
is a different code path: `main.py` mounts the admin router behind
`reject_in_local_mode`, `optional_user` resolves an injected seeded user rather
than a cookie session, and there is no login. A gate that works under pytest
could still be unreachable — or wide open — in the packaged app.

Standalone, like `smoke/regress_chat_acting_identity.py`. Not collected by
pytest (`testpaths = tests`).
"""
import os
import pathlib
import shutil
import sys
import tempfile

_SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="u1-local-mode-"))
os.environ["PYPSAGUI_LOCAL_MODE"] = "1"
os.environ["PYPSAGUI_APP_DATA_DIR"] = str(_SCRATCH / "appdata")
os.environ["PYPSAGUI_PROJECTS_ROOT"] = str(_SCRATCH / "projects")
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{(_SCRATCH / 'local.db').as_posix()}"
os.environ["SECRET_KEY"] = "local-mode-smoke-not-a-real-key"
os.environ.pop("ANTHROPIC_API_KEY", None)

# Same header as the sibling smoke scripts: make `main`, `routers`, `services`
# importable from anywhere.
_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

import app_paths  # noqa: E402
import main  # noqa: E402

ENDPOINT = "/api/chat/settings/api-key"
SAMPLE = "sk-ant-api03-LOCAL-MODE-SMOKE-abcd"

passed = failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}  {detail}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


with TestClient(main.app) as c:
    # The premise: this is genuinely local mode, so the admin router is gone.
    # If this ever starts returning 200, the routes could have lived there and
    # this whole placement decision needs revisiting.
    admin = c.get("/api/admin/organizations")
    check(
        "L1 admin router is unreachable in local mode",
        admin.status_code == 404,
        f"GET /api/admin/organizations -> {admin.status_code}",
    )

    before = c.get("/api/chat/health").json()
    check(
        "L2 no key configured at start",
        before["anthropic_api_key_present"] is False,
        f"anthropic_api_key_present={before['anthropic_api_key_present']}",
    )

    status = c.get(ENDPOINT)
    check(
        "L3 the seeded local user passes the super-admin gate",
        status.status_code == 200,
        f"GET {ENDPOINT} -> {status.status_code} {status.text[:80]}",
    )

    saved = c.put(ENDPOINT, json={"value": SAMPLE})
    check(
        "L4 a key can be saved from inside the app",
        saved.status_code == 200 and saved.json()["configured"] is True,
        f"PUT -> {saved.status_code} {saved.text[:80]}",
    )

    after = c.get("/api/chat/health").json()
    check(
        "L5 chat reports the key present with no restart",
        after["anthropic_api_key_present"] is True,
        f"anthropic_api_key_present={after['anthropic_api_key_present']}",
    )

    env_file = app_paths.user_env_file()
    check(
        "L6 it landed in app-data, mode 0600",
        env_file.exists() and oct(env_file.stat().st_mode)[-3:] == "600",
        f"{env_file} mode={oct(env_file.stat().st_mode)[-3:] if env_file.exists() else 'MISSING'}",
    )

    check(
        "L7 the value is never echoed back",
        SAMPLE not in c.get(ENDPOINT).text and SAMPLE not in c.get("/api/chat/health").text,
        f"hint={c.get(ENDPOINT).json()['hint']}",
    )

    removed = c.delete(ENDPOINT)
    final = c.get("/api/chat/health").json()
    check(
        "L8 it can be removed again",
        removed.status_code == 200 and final["anthropic_api_key_present"] is False,
        f"DELETE -> {removed.status_code}",
    )

os.environ.pop("ANTHROPIC_API_KEY", None)
shutil.rmtree(_SCRATCH, ignore_errors=True)
print(f"\nSUMMARY  PASS {passed}  FAIL {failed}")
sys.exit(1 if failed else 0)

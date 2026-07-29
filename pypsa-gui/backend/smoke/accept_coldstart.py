"""
Cold start: the condition a PACKAGED app runs under, which no other acceptance
step exercises — **cwd `/`**.

A macOS `.app` launched from Finder has cwd `/`; a Windows shortcut can leave
cwd at `C:\\Windows\\system32`. Every other harness here is launched from a
shell sitting in the repo, so every relative path in the configuration resolves
to something that happens to exist. That is the difference between "the app
runs" and "the app can be installed", and it is the gap this file closes.

It found one on its first run, before any packaging existed:

    sqlalchemy.exc.OperationalError: (sqlite3.OperationalError)
    unable to open database file
    uvicorn.error: Application startup failed. Exiting.

`Settings.model_config` binds `env_file=<backend>/.env`, pydantic ranks an
env-file entry above a field default, and `.env` carries a cwd-relative
`sqlite+pysqlite:///./auth_dev.db`. So `default_database_url()` — the absolute
app-data path — never applied, and the app died on the splash. Fixed by
`build_environment` pinning `DATABASE_URL`; this harness is what proves it, so
it reports the database file the engine actually opened rather than trusting the
URL.

What it asserts, in one run per launch:

  * the window and server come up at all, from cwd `/`
  * every resolved path is absolute and inside the two throwaway roots
  * `/` serves the real SPA, not the 503 fallback
  * `auth_enabled: false` — no login wall in a local app
  * first launch with ZERO projects lists none (Task 7 step 2)
  * create -> save -> edit-after-save -> clean quit
  * relaunch loads the project and **the post-save edit survived the flush**

usage:
    cd / && PYPSAGUI_APP_DATA_DIR=<throwaway> PYPSAGUI_PROJECTS_ROOT=<throwaway> \\
      python <repo>/pypsa-gui/backend/smoke/accept_coldstart.py {first|relaunch}

Run `first`, then `relaunch` against the SAME two directories. Launch it through
`pixi run -e desktop bash -c 'cd / && python …'` — `pixi run` alone resolves cwd
to the manifest root, which is exactly the condition this harness exists to
avoid.
"""
import http.client
import json
import os
import sys
import threading
import time
from pathlib import Path

# Resolved from this file, not from cwd — cwd is the variable under test.
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

ACTION = sys.argv[1] if len(sys.argv) > 1 else "first"
PROJECT = "ColdStart"
out = {"action": ACTION, "cwd": os.getcwd()}

# BOTH are required. `PYPSAGUI_APP_DATA_DIR` moves the database, the log, the
# lock and the FLAT store; project storage is governed by the separate
# `PYPSAGUI_PROJECTS_ROOT`, default ~/Documents/PyPSA GUI/Projects. Setting only
# the first wrote an earlier harness's throwaway projects into the developer's
# real Documents folder while reporting that app-data had been redirected.
_appdata = os.environ.get("PYPSAGUI_APP_DATA_DIR", "")
_projects = os.environ.get("PYPSAGUI_PROJECTS_ROOT", "")
assert _appdata, "refusing to run: set PYPSAGUI_APP_DATA_DIR to a throwaway directory"
assert _projects, "refusing to run: set PYPSAGUI_PROJECTS_ROOT to a throwaway directory"
for _label, _v in (("PYPSAGUI_APP_DATA_DIR", _appdata), ("PYPSAGUI_PROJECTS_ROOT", _projects)):
    _p = Path(_v).expanduser().resolve()
    assert _p != (BACKEND / "projects").resolve(), f"refusing to run: {_label} IS the real projects tree"
    assert Path.home() / "Documents" not in _p.parents, f"refusing to run: {_label} is inside Documents"

# Do NOT import pypsa (or anything that pulls it) at module scope:
# `launcher.apply_environment` refuses to run once the backend is imported and
# the whole launch then dies on the splash.
from desktop import gui, launcher  # noqa: E402

# The real legacy tree is 113 MB of irreplaceable projects. Never inventory it.
launcher.resolve_legacy_root = lambda backend_dir=None: None


def inventory(root):
    root = Path(root)
    if not root.exists():
        return "<absent>"
    return sorted(
        f"{p.relative_to(root)}{'/' if p.is_dir() else ''}"
        for p in root.rglob("*")
        # The webview profile is a Safari data store with dozens of files that
        # say nothing about this app. Its presence is the signal; its contents
        # are noise.
        if "webview" not in p.parts or p.parent == root
    )[:40]


out["appdata_before"] = inventory(_appdata)
out["projects_before"] = inventory(_projects)


def api(method, path, body=None, port=None, timeout=120):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    hdrs = {"Content-Type": "application/json"} if body is not None else {}
    c.request(method, path, json.dumps(body) if body is not None else None, hdrs)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw or b"null")
    except Exception:
        return r.status, raw[:300]


def work():
    for _ in range(600):
        time.sleep(0.25)
        st = getattr(gui, "_S", None)
        if st and st.get("main_window") is not None and st.get("server"):
            break
    else:
        # The launch failing IS a result, not a harness error. The reason is in
        # `pypsa-gui.log` inside the app-data directory.
        out["never_up"] = True
        print("RESULT " + json.dumps(out), flush=True)
        os._exit(1)

    port = st["port"]
    try:
        # Which database did it ACTUALLY open? The URL is only half the answer
        # when it is relative — `sqlite+pysqlite:///./auth_dev.db` reads as
        # configured and resolves somewhere the user cannot write.
        from db.session import get_engine
        from settings import get_settings

        s = get_settings()
        dbfile = get_engine().url.database
        out["config"] = {
            "database_url": s.database_url,
            "db_file_resolved": str(Path(dbfile).resolve()) if dbfile else None,
            "db_file_exists": bool(dbfile) and Path(dbfile).exists(),
            "projects_root": str(s.projects_root),
            "flat_projects_root": str(s.flat_projects_root),
            "frontend_dist": str(s.frontend_dist),
            "frontend_dist_exists": s.frontend_dist.exists(),
        }

        out["health"] = api("GET", "/api/health", port=port)[1]

        # The SPA, not the 503 page. A packaged app serving the fallback is
        # indistinguishable from a working one until a human looks at it.
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        c.request("GET", "/")
        r = c.getresponse()
        body = r.read()
        c.close()
        out["root_page"] = {
            "status": r.status,
            "bytes": len(body),
            "is_spa": b'<div id="root"' in body or b"<div id=root" in body,
        }

        out["projects_seen"] = [
            p.get("name") for p in (api("GET", "/api/projects/", port=port)[1] or [])
        ]

        if ACTION == "first":
            out["reset"] = api("POST", "/api/network/reset", port=port)[0]
            out["bus"] = api("POST", "/api/network/buses",
                             {"name": "ColdBus", "v_nom": 380.0}, port=port)[0]
            out["save"] = api("POST", f"/api/projects/{PROJECT}", port=port)[0]
            # AFTER the save, so disk and memory differ and only the quit-flush
            # can reconcile them. This is the edit `relaunch` looks for.
            out["edit_after_save"] = api("POST", "/api/network/buses",
                                         {"name": "SurvivedColdStart", "v_nom": 220.0},
                                         port=port)[0]
        else:
            out["load"] = api("GET", f"/api/projects/{PROJECT}", port=port)[0]
            out["buses"] = sorted(
                b.get("name") for b in (api("GET", "/api/network/buses", port=port)[1] or [])
            )

        handler = st.get("close_handler")
        handler.on_closing()
        handler.wait_for_completion(180)
        rep = handler.report
        out["quit"] = {
            "quit": rep.quit,
            "unflushed": rep.unflushed,
            "errors": rep.errors,
            "server_stage": rep.server_stage,
        }
    except Exception as exc:
        import traceback
        out["error"] = repr(exc)
        out["traceback"] = traceback.format_exc()[-800:]

    out["appdata_after"] = inventory(_appdata)
    out["projects_after"] = inventory(_projects)
    print("RESULT " + json.dumps(out), flush=True)


def watchdog():
    time.sleep(420)
    out["timeout"] = True
    print("RESULT " + json.dumps(out), flush=True)
    os._exit(1)


_orig = gui._bootstrap


def spy(state):
    gui._S = state
    return _orig(state)


gui._bootstrap = spy
threading.Thread(target=watchdog, daemon=True).start()
threading.Thread(target=work, daemon=True).start()
code = gui.main()
print("EXITED " + json.dumps({"status": code}), flush=True)

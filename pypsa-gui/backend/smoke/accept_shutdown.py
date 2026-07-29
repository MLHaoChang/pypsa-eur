"""
Acceptance step 5, in the REAL app: solve -> close -> Cancel -> still usable
-> close -> Quit -> the edit survived.

This is the first end-to-end exercise of the shutdown workstream. Everything in
`services/shutdown.py` has until now been tested against injected fakes on a
headless box — the confirm modal, the bounded abort, the flush ordering and the
mutation gate have never run against a real solve, a real window and a real
uvicorn.

Only two things are substituted, both of which need a human:
  * `create_confirmation_dialog` answers from a script (Cancel, then Quit).
  * `hide` is recorded rather than performed, so the test can assert the
    window was NEVER hidden on the cancel path. A hidden window that comes
    back is the failure this ordering exists to prevent.

usage:
    PYPSAGUI_APP_DATA_DIR=<throwaway> PYPSAGUI_PROJECTS_ROOT=<throwaway> \\
      pixi run -e desktop python pypsa-gui/backend/smoke/accept_shutdown.py \\
      {solve-cancel-quit|relaunch|load-only}

Run `solve-cancel-quit` first, then `relaunch` against the SAME two directories:
the second asserts the edit the first made after its save survived the quit.
"""
import http.client
import json
import os
import sys
import threading
import time
from pathlib import Path

# Relative: this repo is developed on Windows and macOS both.
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

ACTION = sys.argv[1] if len(sys.argv) > 1 else "solve-cancel-quit"
PROJECT = "SolveCancelProject"
out = {"action": ACTION}

# BOTH are required. `PYPSAGUI_APP_DATA_DIR` moves the database, the log, the
# lock and the FLAT store; project storage is governed by the separate
# `PYPSAGUI_PROJECTS_ROOT`, default ~/Documents/PyPSA GUI/Projects. Setting
# only the first wrote this harness's throwaway projects into the developer's
# real Documents folder while reporting that app-data had been redirected.
_appdata = os.environ.get("PYPSAGUI_APP_DATA_DIR", "")
_projects = os.environ.get("PYPSAGUI_PROJECTS_ROOT", "")
assert _appdata, "refusing to run: set PYPSAGUI_APP_DATA_DIR to a throwaway directory"
assert _projects, "refusing to run: set PYPSAGUI_PROJECTS_ROOT to a throwaway directory"
for _label, _v in (("PYPSAGUI_APP_DATA_DIR", _appdata), ("PYPSAGUI_PROJECTS_ROOT", _projects)):
    _p = Path(_v).expanduser().resolve()
    assert _p != (BACKEND / "projects").resolve(), f"refusing to run: {_label} IS the real projects tree"
    assert Path.home() / "Documents" not in _p.parents, f"refusing to run: {_label} is inside Documents"

# NOTE: do NOT import pypsa (or anything that pulls it) at module scope here.
# `launcher.apply_environment` refuses to run once the backend is imported --
# `BackendAlreadyImported: already imported: pypsa, matplotlib` -- and the
# whole launch then fails on the splash. An earlier version of this file
# installed netcdf I/O spies up here and killed every run that way.

from desktop import gui, launcher  # noqa: E402

launcher.resolve_legacy_root = lambda backend_dir=None: None


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


def build_a_slow_network(port):
    """
    Big enough that the LP is still running when the close arrives.

    A storage unit is the point: cyclic state-of-charge couples every snapshot
    to the next, so the LP cannot be decomposed per-hour and 8760 snapshots
    stay genuinely slow. Without it HiGHS finishes a few thousand independent
    dispatch decisions faster than the close can be issued, `solves_in_flight()`
    returns empty, the confirm is never reached, and the step passes while
    testing nothing.
    """
    steps = {}
    steps["reset"] = api("POST", "/api/network/reset", port=port)[0]
    for i in range(3):
        steps[f"bus{i}"] = api("POST", "/api/network/buses",
                               {"name": f"Bus{i}", "v_nom": 380.0}, port=port)[0]
    for i in range(12):
        steps[f"gen{i}"] = api("POST", "/api/network/generators", {
            "name": f"Gen{i}", "bus": f"Bus{i % 3}", "carrier": "gas",
            # A finite p_nom_max is REQUIRED for an extendable component —
            # validation rejects `0 / +inf` outright, and the run then fails
            # with `validation_failed` before any LP is built.
            "p_nom": 0.0, "p_nom_extendable": True,
            "p_nom_min": 0.0, "p_nom_max": 5000.0,
            "marginal_cost": 10.0 + i, "capital_cost": 1000.0 + 10 * i,
        }, port=port)[0]
    steps["storage"] = api("POST", "/api/network/storage_units", {
        "name": "Battery", "bus": "Bus0", "carrier": "battery",
        "p_nom": 0.0, "p_nom_extendable": True,
        "p_nom_min": 0.0, "p_nom_max": 5000.0, "max_hours": 6.0,
        "capital_cost": 500.0, "cyclic_state_of_charge": True,
    }, port=port)[0]
    steps["load"] = api("POST", "/api/network/loads",
                        {"name": "Demand", "bus": "Bus0", "p_set": 500.0},
                        port=port)[0]
    steps["snapshots"] = api("POST", "/api/network/snapshots",
                             {"start": "2030-01-01", "end": "2030-12-31 23:00",
                              "freq": "h"}, port=port)[0]
    s, meta = api("GET", "/api/network/meta", port=port)
    steps["snapshot_count"] = (meta or {}).get("snapshot_count")
    return steps


def work():
    for _ in range(1200):
        time.sleep(0.25)
        st = getattr(gui, "_S", None)
        if st and st.get("main_window") is not None and st.get("server"):
            break
    else:
        out["never_up"] = True
        print("RESULT " + json.dumps(out), flush=True)
        os._exit(1)

    port = st["port"]
    window = st["main_window"]
    handler = st["close_handler"]
    from services import shutdown as shutdown_service

    try:
        if ACTION == "load-only":
            # Catch the removal itself rather than bisecting the load by
            # reading it. Wrapping `remove` records who dropped what, with the
            # frames that did it.
            import traceback as _tb
            import pypsa as _pypsa

            _orig_remove = _pypsa.Network.remove

            def spy_remove(self, class_name, name, *a, **k):
                out.setdefault("removals", []).append({
                    "class": str(class_name),
                    "name": str(name)[:100],
                    "stack": [f.strip().replace("\n", " | ")[:160]
                              for f in _tb.format_stack()[-5:-1]],
                })
                return _orig_remove(self, class_name, name, *a, **k)

            _pypsa.Network.remove = spy_remove

            _orig_import = _pypsa.Network.import_from_netcdf

            def spy_import(self, path, *a, **k):
                r = _orig_import(self, path, *a, **k)
                # Read the SAME path independently, so "the app imported 3"
                # and "the file the app opened has 3" are distinguishable.
                try:
                    independent = sorted(_pypsa.Network(str(path)).buses.index.tolist())
                except Exception as exc:
                    independent = f"<{exc}>"
                out.setdefault("imports", []).append({
                    "path": str(path),
                    "after_import": sorted(self.buses.index.tolist()),
                    "same_file_read_independently": independent,
                })
                return r

            _pypsa.Network.import_from_netcdf = spy_import

        if ACTION in ("relaunch", "load-only"):
            s, projects = api("GET", "/api/projects/", port=port)
            out["projects_seen"] = [p.get("name") for p in (projects or [])]
            out["buses_before_load"] = sorted(
                b.get("name") for b in (api("GET", "/api/network/buses", port=port)[1] or [])
            )
            s, summary = api("GET", f"/api/projects/{PROJECT}", port=port)
            out["load"] = s
            out["load_summary"] = ({k: v for k, v in (summary or {}).items() if k != "lock"}
                                   if isinstance(summary, dict) else summary)
            s, buses = api("GET", "/api/network/buses", port=port)
            out["buses"] = sorted(b.get("name") for b in (buses or []))
            out["meta"] = api("GET", "/api/network/meta", port=port)[1]
            if ACTION == "load-only":
                # Exit WITHOUT the shutdown sequence. The graceful quit flushes
                # the in-memory network back to disk, so a relaunch that closes
                # cleanly overwrites the very file it was meant to inspect —
                # which is how a 4-bus file silently became a 3-bus one between
                # two reads and made the load look innocent.
                print("RESULT " + json.dumps(out), flush=True)
                os._exit(0)
            h = st.get("close_handler")
            if h:
                h.on_closing()
                h.wait_for_completion(120)
            print("RESULT " + json.dumps(out), flush=True)
            return

        out["build"] = build_a_slow_network(port)
        out["save"] = api("POST", f"/api/projects/{PROJECT}", port=port)[0]

        # The edit whose survival step 5 is about: made AFTER the save, so disk
        # and memory differ and only the shutdown flush can reconcile them.
        out["edit"] = api("POST", "/api/network/buses",
                          {"name": "SurvivedQuit", "v_nom": 220.0}, port=port)[0]
        s, buses = api("GET", "/api/network/buses", port=port)
        out.setdefault("trace", {})["after_edit"] = {
            "buses": sorted(b.get("name") for b in (buses or [])),
            "loaded_project": [
                getattr(c, "loaded_project", None)
                for c in shutdown_service.resident_contexts()
            ],
        }

        confirmed: list = []
        hidden: list = []
        answers = [False, True]          # Cancel, then Quit

        def fake_confirm(title, message):
            confirmed.append(message)
            return answers.pop(0) if answers else True

        window.create_confirmation_dialog = fake_confirm
        window.hide = lambda: hidden.append(True)

        s, pre = api("POST", "/api/simulation/preflight", port=port)
        out["preflight"] = {"status": s, "issues": pre}
        out["run"] = api("POST", "/api/simulation/run", port=port)[0]

        probe = []
        for i in range(240):
            time.sleep(0.25)
            if shutdown_service.solves_in_flight():
                break
            if i % 8 == 0:
                s, status = api("GET", "/api/simulation/status", port=port)
                ctxs = shutdown_service.resident_contexts()
                probe.append({
                    "t": round(i * 0.25, 1),
                    "status": (status or {}).get("status"),
                    "contexts": len(ctxs),
                    "solver_states": [
                        {k: str(v)[:40] for k, v in (getattr(c, "solver_state", {}) or {}).items()}
                        for c in ctxs
                    ],
                })
                if (status or {}).get("status") in ("completed", "failed", "aborted"):
                    break
        out["probe"] = probe[:8]
        in_flight = shutdown_service.solves_in_flight()
        out["in_flight_at_close"] = [s.label for s in in_flight]
        if not in_flight:
            # Say so rather than proceeding: without a solve the confirm is
            # never reached and every assertion below would pass vacuously.
            out["error"] = "no solve was in flight — step 5 would prove nothing"
            print("RESULT " + json.dumps(out), flush=True)
            os._exit(1)

        # ── close #1: Cancel ────────────────────────────────────────────────
        out["close1_vetoed"] = handler.on_closing() is False
        handler.wait_for_completion(180)
        r1 = handler.report
        out["cancel"] = {
            "confirm_shown": len(confirmed),
            "message": confirmed[0][:120] if confirmed else None,
            "quit": r1.quit,
            "window_hidden": len(hidden),
            "still_gated": shutdown_service.mutations_gated(),
            "errors": r1.errors,
        }

        # Usable again? A write must NOT be refused by the SHUTDOWN gate. It is
        # still legitimately refused by the SOLVER gate (409 solver_in_flight)
        # because the solve was not aborted — cancelling the quit does not
        # cancel the solve. 503 shutting_down here would mean the gate leaked.
        s, body = api("POST", "/api/network/buses",
                      {"name": "AfterCancel", "v_nom": 110.0}, port=port)
        out["mutation_after_cancel"] = {
            "status": s,
            "code": (body or {}).get("code") if isinstance(body, dict) else None,
        }
        out["solve_still_running"] = bool(shutdown_service.solves_in_flight())

        # Where did the edit go? Sampled at each stage so a missing bus after
        # the relaunch can be attributed, rather than blamed on the flush by
        # default. `loaded_project` matters because `gui.py`'s flush SKIPS a
        # context that has none — silently, and without counting it unflushed.
        def snapshot_state(tag):
            s, buses = api("GET", "/api/network/buses", port=port)
            ctxs = shutdown_service.resident_contexts()
            out.setdefault("trace", {})[tag] = {
                "buses": sorted(b.get("name") for b in (buses or [])),
                "contexts": len(ctxs),
                "loaded_project": [getattr(c, "loaded_project", None) for c in ctxs],
            }

        snapshot_state("after_cancel")

        # ── close #2: Quit ──────────────────────────────────────────────────
        out["close2_vetoed"] = handler.on_closing() is False
        handler.wait_for_completion(240)
        r2 = handler.report
        out["quit"] = {
            "confirm_shown": len(confirmed),
            "quit": r2.quit,
            "window_hidden": len(hidden),
            "abort_timed_out": r2.abort_timed_out,
            "unflushed": r2.unflushed,
            "errors": r2.errors,
            "server_stage": r2.server_stage,
        }
    except Exception as exc:
        import traceback
        out["error"] = repr(exc)
        out["traceback"] = traceback.format_exc()[-600:]

    print("RESULT " + json.dumps(out), flush=True)


def watchdog():
    time.sleep(900)
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

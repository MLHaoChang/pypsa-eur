"""
Run one planning → dynamics study through a RUNNING app, over HTTP only.

Written for the frozen macOS `.app`, which nothing else exercises: the build
script's gate (`pixi run gui-tests`) runs against SOURCE, and `check_bundle.py`
only lists files. Whether the frozen bundle can actually solve — PyPSA's unit
commitment through HiGHS, pandapower's load flow, lightsim2grid's native N-1
screen, the IEC 60909 fault levels, the PSS/E writers — is the question a
packaged app exists to answer, and the one macOS risk the gridspine plans name
(lightsim2grid's binary under the hardened runtime) only shows up here.

Stdlib only, on purpose: it must run from any interpreter against any build, so
it proves nothing about its own environment.

A CLIENT, not a harness: it launches nothing, so `smoke.isolation`'s guard (which
exists because a backend launched from SOURCE reads `backend/.env`'s cwd-relative
DATABASE_URL) does not apply. Isolating the app it talks to is the launcher's
job — the CI workflow sets PYPSAGUI_APP_DATA_DIR and PYPSAGUI_PROJECTS_ROOT, and
the frozen app pins its own DATABASE_URL (`launcher.build_environment`).

    python frozen_study.py --port 51234 [--out bundle.zip]

Exit 0 only when a study reached `completed` AND a handoff bundle for a
selected hour downloaded with a `.raw` and a `.dyr` inside it.
"""
from __future__ import annotations

import argparse
import http.client
import io
import json
import sys
import time
import urllib.parse
import zipfile

NAME = "Frozen Smoke"
# Small enough to finish in minutes on a CI runner, large enough to exercise
# every stage: 24 generated hours, one hour per criterion, screening ON.
CONFIG = {"hours": 24, "k": 1, "window": 24, "overlap": 0, "screen": True}


def call(port, method, path, body=None, timeout=120):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    headers = {}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


def wait_for_health(port, deadline_s):
    t0 = time.monotonic()
    while time.monotonic() - t0 < deadline_s:
        try:
            status, _ = call(port, "GET", "/api/health", timeout=5)
            if status == 200:
                return time.monotonic() - t0
        except OSError:
            pass
        time.sleep(2)
    raise SystemExit(f"FAIL: /api/health never answered 200 within {deadline_s}s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--out", default="frozen_bundle.zip")
    ap.add_argument("--health-timeout", type=int, default=300)
    ap.add_argument("--run-timeout", type=int, default=2400)
    args = ap.parse_args()
    port = args.port
    q = urllib.parse.quote(NAME)

    up = wait_for_health(port, args.health_timeout)
    print(f"health: 200 after {up:.0f}s")

    status, data = call(port, "POST", "/api/gridspine/projects", {"name": NAME, "config": CONFIG})
    if status not in (200, 201):
        raise SystemExit(f"FAIL: create_study {status}: {data[:500]!r}")
    print("created study:", json.loads(data)["name"])

    status, data = call(port, "POST", f"/api/gridspine/{q}/run", {})
    if status not in (200, 201, 202):
        raise SystemExit(f"FAIL: run {status}: {data[:500]!r}")
    print("queued:", json.loads(data).get("status", "?"))

    t0, last = time.monotonic(), None
    while True:
        if time.monotonic() - t0 > args.run_timeout:
            raise SystemExit(f"FAIL: study did not finish within {args.run_timeout}s (last: {last})")
        time.sleep(5)
        status, data = call(port, "GET", f"/api/gridspine/{q}/status")
        if status != 200:
            raise SystemExit(f"FAIL: status {status}: {data[:500]!r}")
        st = json.loads(data)
        line = st["status"] + " | " + " ".join(f"{k}:{v['state']}" for k, v in st["stages"].items())
        if line != last:
            print(f"{time.monotonic() - t0:6.0f}s  {line}")
            last = line
        if st["status"] in ("failed", "aborted"):
            raise SystemExit(f"FAIL: study {st['status']}: {json.dumps(st.get('error'))[:800]}")
        if st["status"] == "completed":
            break

    hours = st.get("selected_hours") or []
    if not hours:
        raise SystemExit("FAIL: completed with no selected hours")
    hour = int(hours[0])
    status, data = call(port, "GET", f"/api/gridspine/{q}/bundles/{hour}", timeout=300)
    if status != 200:
        raise SystemExit(f"FAIL: bundle h{hour} {status}: {data[:300]!r}")
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    with open(args.out, "wb") as fh:
        fh.write(data)
    raw = [n for n in names if n.endswith(".raw")]
    dyr = [n for n in names if n.endswith(".dyr")]
    print(f"bundle h{hour}: {len(data)} bytes, {len(names)} files; raw={raw} dyr={dyr}")
    if not raw or not dyr:
        raise SystemExit("FAIL: bundle is missing the .raw or the .dyr")
    print("PASS: the running app completed a planning → dynamics study and produced a PSS/E bundle")
    return 0


if __name__ == "__main__":
    sys.exit(main())

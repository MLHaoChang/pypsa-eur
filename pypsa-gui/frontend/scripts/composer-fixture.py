# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Private loopback backend for smoke-chat-composer.mjs; never calls a model."""
import argparse
import http.client
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage", type=Path, required=True)
    args = parser.parse_args()
    storage = args.storage.resolve()
    storage.mkdir(parents=True, exist_ok=True)
    storage.chmod(0o700)
    repository = Path(__file__).resolve().parents[3]
    backend_dir = repository / "pypsa-gui/backend"
    dist = repository / "pypsa-gui/frontend/dist"
    if not (dist / "spa.html").is_file():
        raise RuntimeError("Build the current frontend with npm run build first")

    class NoModel(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            # A layout action must not send inference or start media.
            (storage / "unexpected-provider-call").touch()
            self.send_error(503, "No inference is allowed in this layout fixture")

    model = ThreadingHTTPServer(("127.0.0.1", 0), NoModel)
    threading.Thread(target=model.serve_forever, daemon=True).start()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("PYPSA_GUI_LLM_KEY__", "ANTHROPIC_", "OPENAI_", "MOONSHOT_",
                           "KIMI_", "DEEPGRAM_", "LIVEKIT_", "PYPSA_GUI_OPENAI_")):
            env.pop(key)
    app_data = storage / "app-data"
    app_data.mkdir()
    env.update(
        PYPSAGUI_APP_DATA_DIR=str(app_data), PYPSAGUI_LOCAL_MODE="0",
        DATABASE_URL="sqlite:///" + str(app_data / "fixture.sqlite"),
        PROJECTS_ROOT=str(storage / "projects"), LEGACY_ROOT=str(storage / "legacy"),
        FLAT_PROJECTS_ROOT=str(storage / "flat-projects"),
        SECRET_KEY=secrets.token_urlsafe(32), PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=os.pathsep.join((str(backend_dir), str(repository))),
        FRONTEND_DIST=str(dist), CORS_ALLOWED_ORIGINS=f"http://127.0.0.1:{port}",
        OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1",
        MPLCONFIGDIR=str(storage / "mpl"), XDG_CACHE_HOME=str(storage / "cache"),
        PYPSA_GUI_ANSWER_ASSURANCE_MODE="pilot",
        PYPSA_GUI_ANSWER_ASSURANCE_PROFILES="generator-dispatch/1,generator-cost/1",
    )
    env["NO_PROXY"] = env.get("NO_PROXY", "") + ",127.0.0.1,localhost"
    credentials = storage / "credentials.json"
    credentials.write_text(json.dumps({"email": "composer@example.test", "password": secrets.token_urlsafe(24)}))
    credentials.chmod(0o600)
    (app_data / "llm-profiles.json").write_text(json.dumps({
        "active_profile_id": "composer-local", "profiles": [{
            "id": "composer-local", "label": "Offline layout fixture", "preset": "custom",
            "wire": "openai", "base_url": f"http://127.0.0.1:{model.server_port}/v1",
            "model": "no-inference", "tools": True, "vision": False, "auth": "none",
        }],
    }))
    seed = """
import json, sys
from pathlib import Path
from local_bootstrap import ensure_app_dirs, ensure_schema
from settings import get_settings
ensure_app_dirs(); ensure_schema(get_settings().database_url)
from tools.bootstrap_super_admin import bootstrap_super_admin
from db.session import SessionLocal
from db.models import User
from services.tenancy_service import ensure_personal_org
user = bootstrap_super_admin(**json.loads(Path(sys.argv[1]).read_text()))
with SessionLocal() as db:
    ensure_personal_org(db, db.get(User, user.id))
"""
    backend = None
    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        with (storage / "seed.log").open("w") as log:
            subprocess.run([sys.executable, "-c", seed, str(credentials)], cwd=backend_dir,
                           env=env, stdout=log, stderr=log, check=True)
        with (storage / "backend.log").open("w") as log:
            backend = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--host",
                                        "127.0.0.1", "--port", str(port)], cwd=backend_dir,
                                       env=env, stdout=log, stderr=log)
            for _ in range(120):
                if backend.poll() is not None:
                    raise RuntimeError("Backend exited; inspect private backend.log")
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                try:
                    connection.request("GET", "/api/health")
                    if connection.getresponse().status == 200:
                        break
                except OSError:
                    pass
                finally:
                    connection.close()
                time.sleep(0.5)
            else:
                raise RuntimeError("Backend readiness timeout")
            print(json.dumps({"base": f"http://127.0.0.1:{port}",
                              "credentials_file": str(credentials)}), flush=True)
            backend.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if backend is not None and backend.poll() is None:
            backend.terminate()
            try:
                backend.wait(timeout=8)
            except subprocess.TimeoutExpired:
                backend.kill()
                backend.wait()
        model.shutdown()
        model.server_close()


if __name__ == "__main__":
    main()

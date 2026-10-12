"""Run the mandatory finite suite; optional live calls require --live."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from harness.pilot_evaluation import report  # noqa: E402


def source_identity():
    # Include uncommitted evaluation additions as well as tracked backend code.
    digest = hashlib.sha256()
    for path in sorted(BACKEND.rglob("*.py")):
        if "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        digest.update(path.relative_to(BACKEND).as_posix().encode() + b"\0")
        digest.update(path.read_bytes())
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=BACKEND,
        capture_output=True, text=True, check=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=BACKEND,
        capture_output=True, text=True, check=True).stdout.strip())
    return {"commit": commit, "dirty": dirty, "backend_python_sha256": digest.hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="Also run five bounded paid model cases")
    parser.add_argument("--live-model", default="gpt-6.1-sol")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}", args.live_model):
        parser.error("invalid model identifier")
    destination = args.report.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    initial = report([], live=args.live, execution_failed=True)
    initial.update(source=source_identity(), policy_version="1", model=args.live_model if args.live else None, live_budget=None)
    # Overwrite a previous pass before starting. Interrupted runs cannot leave it green.
    destination.write_text(json.dumps(initial, indent=2) + "\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop("PYPSAGUI_LOCAL_MODE", None)
    # Keep this one metered serial process; inherited pytest selection/worker
    # flags must not multiply live budgets or silently deselect required cases.
    env.pop("PYTEST_ADDOPTS", None)
    env["PYPSA_GUI_PILOT_EVALUATION_LIVE"] = "1" if args.live else "0"
    env["PYPSA_GUI_PILOT_EVALUATION_MODEL"] = args.live_model
    env["PYPSA_GUI_PILOT_EVALUATION_REPORT"] = str(destination)
    env["PYPSA_GUI_PILOT_EVALUATION_SOURCE"] = json.dumps(initial["source"])
    selections = ["tests/test_pilot_evaluation.py::test_pilot_case"]
    if args.live:
        selections.append("tests/test_pilot_evaluation.py::test_live_pilot_case")
    command = [sys.executable, "-m", "pytest", "-p", "tools.pilot_evaluation_plugin",
               "-p", "no:xdist", "-o", "addopts=", "-q", "--tb=no", "--show-capture=no", *selections]
    timed_out = False
    with destination.with_suffix(destination.suffix + ".log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=BACKEND, env=env, stdout=log, stderr=log, start_new_session=True)
        def interrupted(signum, frame):
            raise KeyboardInterrupt
        previous_term = signal.signal(signal.SIGTERM, interrupted)
        try:
            code = process.wait(timeout=300 if args.live else 600)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            timed_out = True
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
            code = 1
        finally:
            signal.signal(signal.SIGTERM, previous_term)
    value = json.loads(destination.read_text(encoding="utf-8"))
    if code or timed_out:
        value["passed"] = False
        value["execution_failed"] = True
    value["wall_timeout_or_interruption"] = timed_out
    value["process_exit_code"] = code
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(destination)
    print(json.dumps({"passed": value["passed"], "required_cases": value["required_cases"],
                      "live_status": value["live_status"], "report": str(destination)}))
    return 0 if value["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

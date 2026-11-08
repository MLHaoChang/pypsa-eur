"""Run the trajectory suite (study harness issue 07b, HH-05B) offline.

Every case of `harness.trajectory_evaluation.CASES` runs in one child pytest
process with the fake provider and the pilot smoke's socket guard (any
non-loopback connect or name lookup raises), and the suite report is written
to --report. The report is written failed before the run starts, so an
interrupted run never leaves it green. No timing is scored; `--timeout`
(off by default) only kills a hung run, which then fails.

    pixi run gui-trajectory-evaluation
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from harness.trajectory_evaluation import report  # noqa: E402
from tools.run_pilot_evaluation import source_identity  # noqa: E402

TARGET = "tests/test_trajectory_evaluation.py::test_trajectory_case"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="kill a hung run after this many seconds (off by default; never a pass threshold)",
    )
    args = parser.parse_args(argv)
    destination = args.report.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    initial = report([])
    initial.update(source=source_identity(), passed=False, execution_failed=True)
    destination.write_text(json.dumps(initial, indent=2) + "\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop(
        "PYTEST_ADDOPTS", None
    )  # an inherited -k or -x must not deselect required cases
    for name in [k for k in env if k.endswith("_API_KEY")]:
        env.pop(name)  # offline: no provider credential reaches the child
    env["PYPSA_GUI_TRAJECTORY_REPORT"] = str(destination)
    env["PYPSA_GUI_TRAJECTORY_SOURCE"] = json.dumps(initial["source"])
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "smoke.accept_harness_pilot",
        "-p",
        "tools.trajectory_evaluation_plugin",
        "-p",
        "no:xdist",
        "-p",
        "no:cacheprovider",
        "-o",
        "addopts=",
        "-q",
        "--tb=short",
        TARGET,
    ]
    timed_out = False
    with destination.with_suffix(destination.suffix + ".log").open(
        "w", encoding="utf-8"
    ) as log:
        process = subprocess.Popen(
            command,
            cwd=BACKEND,
            env=env,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=args.timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            timed_out = True
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()
            code = 1
    value = json.loads(destination.read_text(encoding="utf-8"))
    if code or timed_out:
        value["passed"] = False
    value.update(process_exit_code=code, hung_and_killed=timed_out)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    temporary.replace(destination)
    print(
        json.dumps(
            {
                "passed": value["passed"],
                "required_cases": value["required_cases"],
                "release_blockers": value.get("release_blockers"),
                "report": str(destination),
            }
        )
    )
    return 0 if value["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Tier B owner-journey run (LC-13): a real model plays each journey.

Manual only (.github/workflows/owner-journeys-live.yaml, workflow_dispatch).
Never a merge gate. A hard dollar cap applies across all trials; the report
lists the pass count and denominator per journey and the upper-bound spend.
Without --live it only checks that the live module is collected and skipped.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

BACKEND = Path(__file__).resolve().parents[1]
MODULE = "tests/test_owner_journeys_live.py"


def journey_of(testcase) -> str:
    match = re.match(r"test_oj(\d+)_live", testcase.get("name", ""))
    return f"OJ-{match.group(1)}" if match else testcase.get("name", "?")


def tally(junit: Path) -> dict:
    journeys: dict[str, dict] = {}
    for case in ET.parse(junit).getroot().iter("testcase"):
        row = journeys.setdefault(journey_of(case), {"passed": 0, "failed": 0, "skipped": 0})
        if case.find("skipped") is not None:
            row["skipped"] += 1
        elif case.find("failure") is not None or case.find("error") is not None:
            row["failed"] += 1
        else:
            row["passed"] += 1
    for row in journeys.values():
        row["denominator"] = row["passed"] + row["failed"]
    return journeys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Call the paid model (needs a key and --cap-usd)")
    parser.add_argument("--cap-usd", type=float, default=0.0, help="Hard spend cap across all trials")
    parser.add_argument("--trials", type=int, default=3, help="Runs per journey")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--report", type=Path, default=Path("owner-journeys-live.json"))
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}", args.model):
        parser.error("invalid model identifier")
    if args.live and args.cap_usd <= 0:
        parser.error("--live requires a positive --cap-usd")
    report = args.report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    junit, spend = report.with_suffix(".xml"), report.with_suffix(".spend.json")
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
    env.update(PYPSA_GUI_OJ_LIVE="1" if args.live else "0", PYPSA_GUI_OJ_LIVE_MODEL=args.model,
               PYPSA_GUI_OJ_LIVE_CAP_USD=str(args.cap_usd), PYPSA_GUI_OJ_LIVE_TRIALS=str(args.trials),
               PYPSA_GUI_OJ_LIVE_SPEND=str(spend))
    junit.unlink(missing_ok=True)
    spend.unlink(missing_ok=True)
    command = [sys.executable, "-m", "pytest", "-p", "no:xdist", "-o", "addopts=", "-q", "--tb=short",
               f"--junitxml={junit}", MODULE]
    code = subprocess.run(command, cwd=BACKEND, env=env, timeout=1800).returncode
    journeys = tally(junit) if junit.is_file() else {}
    spent = json.loads(spend.read_text()) if spend.is_file() else {"cap_usd": args.cap_usd, "upper_cost_usd": 0.0}
    report.write_text(json.dumps({"live": args.live, "model": args.model if args.live else None,
                                  "trials": args.trials, "journeys": journeys, "spend": spent,
                                  "pytest_exit": code}, indent=2) + "\n")
    for name, row in sorted(journeys.items()):
        print(f"{name}: {row['passed']}/{row['denominator']} passed ({row['skipped']} skipped)")
    print(f"upper-bound spend ${spent['upper_cost_usd']:.4f} of ${spent['cap_usd']:.2f} cap")
    # Tier B reports; a failing journey is a result, not a gate. Only a run that
    # could not execute at all (pytest exit >1) is an error.
    return 1 if code > 1 else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Export coverage levels without copying credentials or conversation history.

Run from backend after the paid tests and offline handler capture:
    python smoke/report_openai_coverage.py --output /tmp/openai-coverage.json
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.catalogue import TOOLS


def coverage(live, offline):
    observed = offline.get("tools", {})
    cases = live.get("cases", {})
    actual = set()
    tested_errors = set()
    for key, case in cases.items():
        if not key.startswith("conversation:"):
            continue
        for turn in case.get("turns", []):
            errors = {e["tool_name"] for e in turn.get("tool_errors", [])}
            for call in turn.get("tools", []):
                (tested_errors if call["tool_name"] in errors else actual).add(call["tool_name"])
    result = {}
    for tool in TOOLS:
        name = tool["name"]
        samples = observed.get(name, {})
        row = {
            "api_contract": cases.get("contract:" + name, {}).get("status", "not_run"),
            "offline_handler_success_tests": len(samples.get("success_tests", [])),
            "real_handler_live_success": name in actual,
            "real_handler_live_expected_error": name in tested_errors,
        }
        if name not in actual:
            row["remaining_check"] = "Real-handler live success needs a domain fixture and its prerequisites."
        result[name] = row
    return {"tools": result, "summary": {
        "registered": len(result),
        "api_contract_passed": sum(r["api_contract"] == "passed" for r in result.values()),
        "offline_handler_success_observed": sum(r["offline_handler_success_tests"] > 0 for r in result.values()),
        "real_handler_live_success": len(actual),
        "real_handler_live_expected_error": len(tested_errors),
        "charged_tokens": live.get("charged_tokens"),
        "cached_input_tokens": live.get("cached_input_tokens"),
        "upper_cost_dollars": live.get("upper_cost_dollars"),
    }, "limits": "Contract cases use dispatch doubles. Offline tests may mock services. Only real-handler live success verifies a live execution."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", default="/tmp/openai-comprehensive.json")
    parser.add_argument("--offline", default="/tmp/tool-handler-coverage.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = coverage(json.loads(Path(args.live).read_text()), json.loads(Path(args.offline).read_text()))
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report["summary"], sort_keys=True))


if __name__ == "__main__":
    main()

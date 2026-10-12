"""Prepare blind review files or score supplied independent judgments offline."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.review_calibration import calibrate, judgment_template, synthetic_packet  # noqa: E402
from harness.reliability_cases import MANIFEST_SHA256, ORACLE_VERSION, SCENARIOS, expected_values  # noqa: E402
from harness.pilot_evaluation import PROFILE_METRICS  # noqa: E402


def read(path):
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("file_limit")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_field")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json")))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--directory", type=Path, required=True)
    score = commands.add_parser("score")
    score.add_argument("--packet", type=Path, required=True)
    score.add_argument("--judgments", type=Path, action="append", default=[])
    score.add_argument("--observations", type=Path)
    score.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        packet = synthetic_packet()
        targets = [args.directory / name for name in ("packet.json", "reviewer-a.json", "reviewer-b.json", "calibration.json", "numerical-oracles.json")]
        if any(path.exists() for path in targets):
            parser.error("preparation refuses to overwrite review evidence")
        write(targets[0], packet)
        for path, reviewer in zip(targets[1:3], ("replace-with-reviewer-a", "replace-with-reviewer-b")):
            write(path, judgment_template(packet, reviewer))
        write(targets[3], calibrate(packet, []))
        write(targets[4], {"oracle_version": ORACLE_VERSION, "manifest_sha256": MANIFEST_SHA256,
                          "sampling": "synthetic", "independent_domain_approval": False,
                          "scenarios": [{"inputs": vars(s), "expected": {profile: expected_values(s, profile)
                                          for profile in PROFILE_METRICS}} for s in SCENARIOS]})
        print(json.dumps({"prepared_cases": len(packet["cases"]), "status": "pending_independent_judgments"}))
        return 0
    # Never leave a previous measurement green if validation or parsing fails.
    inputs = [args.packet, *args.judgments, *([args.observations] if args.observations else [])]
    outputs = [args.report, args.report.with_suffix(args.report.suffix + ".tmp")]
    if any(output.resolve() == path.resolve() for output in outputs for path in inputs):
        parser.error("report must not overwrite an input")
    write(args.report, {"measurement_complete": False, "status": "invalid_or_interrupted", "independent_domain_approval": False})
    try:
        if len(args.judgments) > 10:
            raise ValueError("reviewer_limit")
        value = calibrate(read(args.packet), [read(path) for path in args.judgments],
                          read(args.observations) if args.observations else None)
        write(args.report, value)
    except (ValueError, OSError, TypeError, KeyError, RecursionError):
        print(json.dumps({"measurement_complete": False, "status": "invalid_input"}))
        return 1
    print(json.dumps({"measurement_complete": value["measurement_complete"], "status": value["status"]}))
    return 0 if value["measurement_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

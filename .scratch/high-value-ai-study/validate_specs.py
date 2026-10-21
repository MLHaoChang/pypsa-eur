# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: CC0-1.0

"""
Offline consistency checks for a research spec, not feature certification.

Run from any directory. Standard library only; no model, service or network use.
Source drift is intentional grounds for reassessment, not automatic repinning.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(name: str) -> dict:
    return json.loads((HERE / name).read_text(), object_pairs_hook=unique_pairs)


def main() -> None:
    plan = read_json("implementation-plan.json")
    evidence = read_json("native-evidence.json")
    validation = read_json("VALIDATION.json")
    require(
        plan["schema_version"]
        == evidence["schema_version"]
        == validation["schema_version"]
        == 1,
        "Unknown schema version",
    )
    require(
        plan["status"] == "research_only_not_implementation_authorization",
        "Research scope changed",
    )
    require(plan["runtime_changes"] is False, "Runtime status changed")
    base = plan["assessed_master"]
    require(re.fullmatch(r"[0-9a-f]{40}", base) is not None, "Invalid master pin")
    require(
        base == evidence["assessed_master"] == validation["assessed_master"],
        "Baseline mismatch",
    )

    slices = {item["id"]: item for item in plan["slices"]}
    require(len(slices) == len(plan["slices"]) == 9, "Slice IDs/count mismatch")
    capabilities = plan["capabilities"]
    require(
        {item["id"] for item in capabilities} == {"HV-S", "HV-D", "HV-I"},
        "Capability set changed",
    )
    require(
        sorted(item["priority"] for item in capabilities) == [1, 2, 3],
        "Priority mismatch",
    )
    visiting, visited = set(), set()

    def visit(key: str) -> None:
        require(key in slices, f"Unknown slice dependency {key}")
        require(key not in visiting, f"Cycle at {key}")
        if key in visited:
            return
        visiting.add(key)
        item = slices[key]
        require(
            item["status"] == "proposed_not_started",
            f"Implementation status changed: {key}",
        )
        require(
            bool(item["existing_owners"]) and bool(item["external_gates"]),
            f"Missing owner/gates: {key}",
        )
        for dependency in item["depends_on"]:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for key in slices:
        visit(key)
    cases = plan["acceptance_cases"]
    ids = [case["id"] for case in cases]
    require(len(ids) == len(set(ids)) == 48, "Acceptance IDs/count mismatch")
    require(
        set(ids)
        == {f"{prefix}{number:02}" for prefix in "SDI" for number in range(1, 17)},
        "Missing capability acceptance case",
    )
    declared = [key for item in slices.values() for key in item["tests"]]
    require(
        len(declared) == len(set(declared)) and set(declared) == set(ids),
        "Missing or repeated test mapping",
    )
    table = (HERE / "05-IMPLEMENTATION-AND-TESTS.md").read_text()
    for case in cases:
        require(case["slice"] in slices, f"Unknown test owner: {case['id']}")
        require(
            case["id"] in slices[case["slice"]]["tests"],
            f"Wrong test mapping: {case['id']}",
        )
        require(
            case["status"] == "planned_not_run",
            f"Future test misrepresented: {case['id']}",
        )
        require(
            bool(case["stimulus"]) and bool(case["expected"]),
            f"Incomplete case: {case['id']}",
        )
        row = f"| {case['id']} / {case['slice']} | {case['level']} | {case['stimulus']} | {case['expected']} |"
        require(table.count(row) == 1, f"Rendered matrix drift: {case['id']}")

    paths = [item["path"] for item in evidence["files"]]
    require(len(paths) == len(set(paths)), "Repeated source pin")
    for item in evidence["files"]:
        path = (ROOT / item["path"]).resolve()
        require(
            path.is_relative_to(ROOT) and path.is_file(),
            f"Missing/unsafe source path: {path}",
        )
        require(
            hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"],
            f"Native source drift; reassess before changing pin: {item['path']}",
        )
        require(
            item["url"]
            == f"https://github.com/MLHaoChang/pypsa-eur/blob/{base}/{item['path']}",
            "Source URL is not pinned",
        )
    for context in evidence["external_context"]:
        require(
            re.fullmatch(r"[0-9a-f]{40}", context["head"]) is not None,
            "Invalid external context pin",
        )
        require(
            "OPEN" in context["state_at_inspection"],
            "External status needs explicit reassessment",
        )

    baseline = validation["baseline_tests"]
    require(
        baseline["result"] == "passed" and baseline["tests"] == 161,
        "Baseline result changed",
    )
    require(
        baseline["failures"] == baseline["errors"] == baseline["skipped"] == 0,
        "Baseline has nonpasses",
    )
    require(
        len(baseline["files"]) == 6
        and all((ROOT / x).is_file() for x in baseline["files"]),
        "Missing baseline suites",
    )
    require(
        validation["planned_acceptance"]
        == {"cases": 48, "result": "not_run_features_not_implemented"},
        "Planned tests represented as executed",
    )
    require(
        validation["paid_calls"] == 0
        and validation["runtime_modified"] is False
        and validation["reviewers_contacted"] is False,
        "Research boundaries changed",
    )

    markdown = [
        *HERE.glob("*.md"),
        ROOT / "docs/research/gridspine-high-value-ai-overview.md",
    ]
    for path in markdown:
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text()):
            if target.startswith(("https://", "http://", "#")):
                continue
            local = (path.parent / target.split("#", 1)[0]).resolve()
            require(
                local.is_relative_to(ROOT) and local.exists(),
                f"Broken local link: {path.name}: {target}",
            )
    ast.parse(Path(__file__).read_text())
    print(
        f"Specification integrity passed: 3 capabilities, {len(slices)} acceptance slices, {len(cases)} planned tests."
    )
    print(
        f"Native pins: {len(paths)}. Existing baseline: 161 passed, 0 skipped. Future feature tests: NOT RUN."
    )


if __name__ == "__main__":
    main()

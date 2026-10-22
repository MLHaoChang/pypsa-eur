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
import subprocess
import xml.etree.ElementTree as ET
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


def check_implementation_spec() -> None:
    spec = read_json("harness-implementation-plan.json")
    require(
        spec["status"] == "implementation_spec_for_review",
        "Implementation spec status changed",
    )
    require(
        spec["runtime_implemented"] is False, "Runtime implementation misrepresented"
    )
    sha = spec["assessed_master"]
    require(
        re.fullmatch(r"[0-9a-f]{40}", sha) is not None,
        "Invalid implementation source pin",
    )
    choices = spec["closed_design_choices"]
    expected = {
        "plain_steps_max": 12,
        "brief_steps_max": 24,
        "plain_steps_chars_max": 16000,
        "brief_steps_chars_max": 32000,
        "step_args_chars_max": 1800,
        "task_view_chars_max": 3800,
        "network_cases_executable_max": 2,
        "working_context_bytes_max": 6144,
        "mutable_history_bytes_max": 65536,
    }
    require(
        all(
            type(choices.get(k)) is int and choices[k] == v for k, v in expected.items()
        ),
        "Closed implementation limits drifted",
    )
    require(
        choices["every_brief_write_run_requires_click"] is True
        and choices["card_boundary_requires_continue"] is True
        and choices["new_top_level_modules"] is False,
        "Owner gate drift",
    )
    require(
        choices["diagnostic_loop"]
        == {
            "validation_passes_per_step_max": 3,
            "diagnostic_reads_per_step_max": 2,
            "correction_drafts_per_step_max": 1,
            "tool_calls_per_step_max": 6,
            "provider_requests_per_step_max": 4,
            "unchanged_equivalent_failures_max": 2,
            "persistent_task_wide_allowance_across_attempts": True,
            "automatic_effect_retry": False,
        },
        "Diagnostic-loop contract drift",
    )
    units = {item["id"]: item for item in spec["units"]}
    require(len(units) == len(spec["units"]) == 7, "Implementation unit IDs/count")
    require(
        set(units) == {f"HS-{i:02}" for i in range(1, 8)}, "Implementation unit set"
    )
    active, done = set(), set()

    def visit(key: str) -> None:
        require(
            key in units and key not in active, f"Invalid implementation DAG at {key}"
        )
        if key in done:
            return
        active.add(key)
        item = units[key]
        require(
            item["status"] == "residual_contracts_planned",
            "Unit falsely claimed implemented",
        )
        require(
            bool(item["existing_owners"])
            and bool(item["build_gates"])
            and bool(item["code_targets"]),
            f"Incomplete delivery unit {key}",
        )
        for dependency in item["depends_on"]:
            visit(dependency)
        active.remove(key)
        done.add(key)

    for key in units:
        visit(key)
    cases = spec["acceptance_cases"]
    ids = [item["id"] for item in cases]
    declared = [case for item in units.values() for case in item["acceptance_cases"]]
    require(
        len(ids) == len(set(ids)) == len(declared) == len(set(declared)) == 53,
        "Implementation acceptance count/duplicates",
    )
    require(set(ids) == set(declared), "Unmapped implementation acceptance")
    expected_ids = {
        f"{prefix}-{i:02}"
        for prefix in ("CAP", "CMP", "CTX", "ANA", "RUN")
        for i in range(1, 7)
    }
    expected_ids |= {f"E2E-{i:02}" for i in range(1, 15)} | {
        f"SKL-{i:02}" for i in range(1, 4)
    }
    expected_ids |= {f"LOOP-{i:02}" for i in range(1, 7)}
    require(set(ids) == expected_ids, "Missing required acceptance family")
    table = (HERE / "10-HARNESS-DELIVERY-AND-TESTS.md").read_text()
    for case in cases:
        require(
            case["unit"] in units
            and case["id"] in units[case["unit"]]["acceptance_cases"],
            f"Wrong acceptance owner {case['id']}",
        )
        require(
            case["status"] == "planned_not_run",
            "Future acceptance represented as executed",
        )
        require(
            all(
                case[field]
                for field in ("fixture", "action", "required_assertions", "test_target")
            ),
            f"Incomplete implementation acceptance {case['id']}",
        )
        row = (
            f"| {case['id']} / {case['unit']} | {case['fixture']} {case['action']} | "
            f"{case['required_assertions']} | `{case['test_target']}` |"
        )
        require(table.count(row) == 1, f"Implementation matrix drift: {case['id']}")
        require(
            case["test_target"].endswith(f"[{case['id']}]"),
            f"Missing individual evidence node: {case['id']}",
        )
    require(
        len(spec["coverage_families"]) == len(set(spec["coverage_families"])) == 14,
        "Coverage family count/duplicates",
    )
    require(
        {case["title"] for case in cases if case["unit"] == "HS-06"}
        == set(spec["coverage_families"]),
        "Missing family journey contract",
    )
    fixture_ids = [item["id"] for item in spec["fixtures"]]
    require(len(fixture_ids) == len(set(fixture_ids)) == 22, "Fixture IDs/count")
    for case in cases:
        require(
            bool(case["fixture_ids"]) and set(case["fixture_ids"]) <= set(fixture_ids),
            f"Unknown/missing concrete fixture: {case['id']}",
        )
        if case["id"].startswith("E2E-"):
            require(
                f"FX-J{case['id'][-2:]}" in case["fixture_ids"],
                f"Missing domain setup/oracle: {case['id']}",
            )
    paths = [item["path"] for item in spec["source_pins"]]
    require(len(paths) == len(set(paths)), "Duplicate implementation source pin")
    require(
        {path for item in units.values() for path in item["code_targets"]}
        <= set(paths),
        "Unpinned implementation code target",
    )
    for pin in spec["source_pins"]:
        require(
            ".." not in Path(pin["path"]).parts and not Path(pin["path"]).is_absolute(),
            "Unsafe pinned implementation path",
        )
        result = subprocess.run(
            ["git", "show", f"{sha}:{pin['path']}"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        require(
            result.returncode == 0,
            f"Fetch assessed source {sha} before validating pins",
        )
        require(
            hashlib.sha256(result.stdout).hexdigest() == pin["sha256"],
            "Implementation source hash drift",
        )
        require(
            pin["url"]
            == f"https://github.com/MLHaoChang/pypsa-eur/blob/{sha}/{pin['path']}",
            "Unpinned implementation source URL",
        )
    validation = spec["validation"]
    require(
        validation["runtime_acceptance"] == "not_run_not_implemented"
        and validation["paid_calls"] == 0
        and validation["reviewers_contacted"] is False,
        "Implementation-spec scope changed",
    )
    print(
        "Implementation specs passed: 7 existing-owner units, 53 PLANNED acceptance cases, 14 families; this PR changes no runtime."
    )


def check_current_master_review() -> None:
    review = read_json("current-master-reconciliation.json")
    sha = review["assessed_master"]
    require(re.fullmatch(r"[0-9a-f]{40}", sha) is not None, "Review source SHA")
    require(
        review["runtime_modified"] is False
        and review["paid_model_calls"] == 0
        and review["standing_reviewers_contacted"] is False,
        "Independent review exceeded scope",
    )
    require(
        review["new_acceptance_cases"] == 53
        and review["new_acceptance_execution"] == "planned_not_run",
        "Planned journeys misrepresented as executed",
    )
    require(
        review["review_verdict"] in {"pending_independent_rereview", "GO", "NO_GO"},
        "Unknown independent verdict",
    )
    require(
        {x["unit"] for x in review["unit_reconciliation"]}
        == {f"HS-{i:02}" for i in range(1, 8)},
        "Missing residual delivery disposition",
    )
    for pin in review["source_pins"]:
        path = Path(pin["path"])
        require(
            not path.is_absolute() and ".." not in path.parts, "Unsafe review source"
        )
        result = subprocess.run(
            ["git", "show", f"{sha}:{pin['path']}"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        require(
            result.returncode == 0
            and hashlib.sha256(result.stdout).hexdigest() == pin["sha256"]
            and pin["url"]
            == f"https://github.com/MLHaoChang/pypsa-eur/blob/{sha}/{pin['path']}",
            "Current-master source drift",
        )
    spec = read_json("harness-implementation-plan.json")
    for fixture in spec["fixtures"]:
        if not fixture["id"].startswith("FX-J"):
            continue
        require(
            bool(fixture["setup"]) and bool(fixture["oracle"]),
            "Incomplete domain fixture",
        )
        anchor = fixture["native_anchor"].split("::")
        result = subprocess.run(
            ["git", "show", f"{sha}:{anchor[0]}"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        require(result.returncode == 0, "Missing native fixture foundation")
        if len(anchor) > 1:
            require(
                f"def {anchor[1]}(" in result.stdout.decode(),
                "Missing native test anchor",
            )
    baseline = review["current_master_baseline_tests"]
    if baseline["status"] == "passed":
        report = (HERE / baseline["report"]).resolve()
        require(report.is_relative_to(HERE), "Unsafe current baseline report")
        require(
            hashlib.sha256(report.read_bytes()).hexdigest() == baseline["sha256"],
            "Current baseline report drift",
        )
        suite = ET.parse(report).getroot().find("testsuite")
        require(suite is not None, "Missing current baseline suite")
        for key in ("tests", "failures", "errors", "skipped"):
            require(int(suite.attrib[key]) == baseline[key], "Baseline count drift")
        require(
            baseline["source_commit"] == sha
            and baseline["failures"] == baseline["errors"] == baseline["skipped"] == 0,
            "Current baseline source/outcome mismatch",
        )
    later = review.get("later_master_review")
    if later:
        require(later["baseline_relabelled"] is False, "Historical tests relabelled")
        for pin in later["changed_source_pins"]:
            result = subprocess.run(
                ["git", "show", f"{later['sha']}:{pin['path']}"],
                cwd=ROOT,
                capture_output=True,
                check=False,
            )
            require(
                result.returncode == 0
                and hashlib.sha256(result.stdout).hexdigest() == pin["sha256"],
                "Later-master delta source drift",
            )
        if later["targeted_tests_status"] == "passed":
            evidence = later["targeted_tests"]
            path = (HERE / evidence["report"]).resolve()
            require(path.is_relative_to(HERE), "Unsafe later-master report")
            require(
                hashlib.sha256(path.read_bytes()).hexdigest() == evidence["sha256"],
                "Later-master test report drift",
            )
            suite = ET.parse(path).getroot().find("testsuite")
            require(suite is not None, "Missing later-master test suite")
            for key in ("tests", "failures", "errors", "skipped"):
                require(
                    int(suite.attrib[key]) == evidence[key], "Later test count drift"
                )
            require(
                evidence["source_commit"] == later["sha"]
                and evidence["failures"]
                == evidence["errors"]
                == evidence["skipped"]
                == 0,
                "Later-master test source/outcome mismatch",
            )
    if review["review_verdict"] == "GO":
        require(bool(review.get("reviewed_spec_files")), "Unbound GO verdict")
        for pin in review["reviewed_spec_files"]:
            path = (HERE / pin["path"]).resolve()
            require(path.is_relative_to(HERE), "Unsafe reviewed specification path")
            require(
                hashlib.sha256(path.read_bytes()).hexdigest() == pin["sha256"],
                "Reviewed specification changed after verdict",
            )
    print(
        f"Fresh-master reconciliation passed; independent design verdict: {review['review_verdict']}. No runtime/merge certification."
    )


def check_harness_supplement(plan: dict) -> None:
    supplement = read_json("harness-assessment.json")
    require(supplement["status"] == "research_only", "Harness research scope changed")
    sha = supplement["assessed_master"]
    require(
        re.fullmatch(r"[0-9a-f]{40}", sha) is not None, "Invalid harness source pin"
    )
    findings = {item["id"]: item for item in supplement["findings"]}
    checks = supplement["planned_checks"]
    require(
        set(findings) == {f"H{i:02}" for i in range(1, 9)}, "Harness findings changed"
    )
    declared = [key for item in findings.values() for key in item["checks"]]
    ids = [case["id"] for case in checks]
    require(
        len(ids) == len(set(ids)) == len(declared) == 24, "Foundation check IDs/count"
    )
    require(set(ids) == set(declared), "Foundation check mapping mismatch")
    slice_ids = {item["id"] for item in plan["slices"]}
    table = (HERE / "07-HARNESS-GAPS-AND-ROADMAP.md").read_text()
    for item in findings.values():
        require(
            item["status"] == "proposed_not_implemented", "Harness implementation claim"
        )
        require(
            set(item["supports_slices"]) <= slice_ids, "Unknown capability dependency"
        )
    for case in checks:
        require(case["status"] == "planned_not_run", "Foundation case reported as run")
        require(case["finding"] in findings, "Unknown finding")
        require(
            case["id"] in findings[case["finding"]]["checks"], "Wrong finding mapping"
        )
        require(
            table.count(f"| {case['id']} | {case['trigger']} | {case['expected']} |")
            == 1,
            f"Harness matrix drift: {case['id']}",
        )
    paths = [pin["path"] for pin in supplement["source_pins"]]
    require(len(paths) == len(set(paths)), "Duplicate harness source")
    for pin in supplement["source_pins"]:
        require(
            pin["path"].startswith(("pypsa-gui/", ".scratch/live-")),
            "Unsafe source path",
        )
        require(".." not in Path(pin["path"]).parts, "Unsafe source path traversal")
        result = subprocess.run(
            ["git", "show", f"{sha}:{pin['path']}"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        require(
            result.returncode == 0,
            f"Pinned source unavailable: fetch assessed commit {sha} before checking {pin['path']}",
        )
        require(
            hashlib.sha256(result.stdout).hexdigest() == pin["sha256"],
            "Harness source hash mismatch",
        )
        require(
            pin["url"]
            == f"https://github.com/MLHaoChang/pypsa-eur/blob/{sha}/{pin['path']}",
            "Unpinned harness source URL",
        )
    for ref in supplement["public_references"]:
        require(
            ref["http_status"] == 200
            and ref["url"].startswith("https://code.claude.com/docs/en/"),
            "Unofficial or failed public reference",
        )
        require(
            re.fullmatch(r"[0-9a-f]{64}", ref["sha256"]) is not None,
            "Public reference hash",
        )
    validation = supplement["validation"]
    require(
        validation["runtime_changed"] is False
        and validation["paid_model_calls"] == 0
        and validation["reviewers_contacted"] is False,
        "Harness research boundary changed",
    )
    for key in ("focused_baseline_tests", "offline_pilot"):
        info = validation[key]
        path = (HERE / info["report"]).resolve()
        require(
            path.is_relative_to(HERE) and path.is_file(),
            "Missing/unsafe harness report",
        )
        require(
            hashlib.sha256(path.read_bytes()).hexdigest() == info["sha256"],
            "Harness report drift",
        )
        require(info["result"] == "passed", "Harness baseline validation did not pass")
    baseline = validation["focused_baseline_tests"]
    suite = ET.parse(HERE / baseline["report"]).getroot().find("testsuite")
    require(suite is not None, "Missing baseline JUnit suite")
    for key in ("tests", "failures", "errors", "skipped"):
        require(
            int(suite.attrib[key]) == baseline[key], f"Harness JUnit count drift: {key}"
        )
    require(
        baseline["tests"] == 370
        and baseline["failures"] == baseline["errors"] == baseline["skipped"] == 0,
        "Unexpected baseline outcome",
    )
    pilot = read_json(validation["offline_pilot"]["report"])
    require(
        pilot["passed"] and pilot["required_cases"] == len(pilot["results"]) == 244,
        "Incomplete pilot report",
    )
    require(
        pilot["process_exit_code"] == 0
        and not pilot["live_requested"]
        and not pilot["source"]["dirty"]
        and pilot["source"]["commit"] == sha,
        "Pilot source/execution mismatch",
    )
    print(
        "Harness supplement passed: 8 findings, 24 PLANNED checks; baseline 370 passed, offline gate 244 cases."
    )


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
    check_harness_supplement(plan)
    check_implementation_spec()
    check_current_master_review()

    markdown = [
        *HERE.glob("*.md"),
        ROOT / "docs/research/gridspine-high-value-ai-overview.md",
        ROOT / "docs/research/gridspine-harness-gap-overview.md",
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

# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: CC0-1.0
"""Validate the active amendment without relabelling historical approvals/tests."""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from validate_specs import HERE, ROOT, read_json, require
from validate_specs import main as check_historical


def source(sha: str, path: str) -> bytes:
    require(
        not Path(path).is_absolute() and ".." not in Path(path).parts, "Unsafe source"
    )
    return subprocess.check_output(["git", "show", f"{sha}:{path}"], cwd=ROOT)


def main() -> None:
    check_historical()
    plan = read_json("active-master-plan.json")
    sha, prior = plan["source_commit"], plan["previous_reviewed_master"]
    require(plan["schema_version"] == 1, "Unknown active schema")
    require(
        plan["historical_go_not_relabelled"] is True
        and plan["runtime_changed"] is False
        and plan["paid_calls"] == 0
        and plan["standing_reviewers_contacted"] is False,
        "Active reassessment scope drift",
    )
    require(
        plan["planned_harness_cases"] == 53 and plan["planned_cases_executed"] == 0,
        "Planned acceptance misrepresented",
    )
    require(
        plan["next_slice"]["id"] == "HS-01a"
        and not plan["next_slice"]["new_endpoint"]
        and not plan["next_slice"]["new_tool"]
        and not plan["next_slice"]["new_effect_authority"],
        "Bounded next-slice scope changed",
    )
    require(
        {u["id"] for u in plan["residual_units"]}
        == {f"HS-{n:02}" for n in range(1, 8)},
        "Missing active unit disposition",
    )
    changes = subprocess.check_output(
        ["git", "diff", "--name-only", prior, sha], cwd=ROOT, text=True
    ).splitlines()
    pins = plan["changed_source_pins"]
    require(
        len(pins) == len({p["path"] for p in pins})
        and {p["path"] for p in pins} == set(changes),
        "Incomplete/duplicate current-source delta",
    )
    for pin in pins:
        require(
            hashlib.sha256(source(sha, pin["path"])).hexdigest() == pin["sha256"]
            and pin["url"]
            == f"https://github.com/MLHaoChang/pypsa-eur/blob/{sha}/{pin['path']}",
            "Active source hash/URL drift",
        )
    unchanged = plan["unchanged_central_sources"]
    require(len(unchanged) == len({p["path"] for p in unchanged}) == 9, "Central pins")
    for pin in unchanged:
        raw = source(sha, pin["path"])
        require(
            raw == source(prior, pin["path"])
            and hashlib.sha256(raw).hexdigest() == pin["sha256"],
            "Claimed unchanged foundation changed",
        )
    tree = ast.parse(source(sha, "pypsa-gui/backend/services/assistant_tools.py"))
    discovery = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "find_capabilities"
    )
    require(
        [a.arg for a in discovery.args.args] == ["goal", "limit", "overview"],
        "Discovery has changed; reassess next build before proceeding",
    )
    for key in ("amendment", "review_report"):
        path = (HERE / plan[key]).resolve()
        require(path.is_relative_to(HERE) and path.is_file(), "Missing active document")
    for fixture in read_json("harness-implementation-plan.json")["fixtures"]:
        if "native_anchor" not in fixture:
            continue
        path, *node = fixture["native_anchor"].split("::")
        raw = source(sha, path).decode()
        require(not node or f"def {node[0]}(" in raw, "Native fixture anchor changed")
    tests = plan["current_tests"]
    if tests["status"] == "passed":
        path = (HERE / tests["report"]).resolve()
        require(path.is_relative_to(HERE), "Unsafe active test evidence")
        require(
            tests["source_commit"] == sha
            and hashlib.sha256(path.read_bytes()).hexdigest() == tests["sha256"],
            "Active test source/report drift",
        )
        suite = ET.parse(path).getroot().find("testsuite")
        require(suite is not None, "Missing active test suite")
        for key in ("tests", "failures", "errors", "skipped"):
            require(int(suite.attrib[key]) == tests[key], f"Active count drift:{key}")
        require(tests["failures"] == tests["errors"] == 0, "Active regression failed")
        skipped = [
            t for t in suite.findall("testcase") if t.find("skipped") is not None
        ]
        require(
            len(skipped) == tests["skipped"]
            and all(
                t.attrib["name"] == "test_every_known_gap_is_still_a_gap[NOTSET]"
                and t.find("skipped").attrib["message"]
                == "got empty parameter set for (tool)"
                for t in skipped
            ),
            "Unexpected active regression skip",
        )
    require(plan["review_verdict"] in {"pending", "GO", "NO_GO"}, "Unknown verdict")
    if plan["review_verdict"] == "GO":
        review = json.loads((HERE / plan["review_record"]).read_text())
        require(
            review["verdict"] == "GO" and review["source_commit"] == sha,
            "Active verdict/source mismatch",
        )
        for pin in review["reviewed_files"]:
            path = (HERE / pin["path"]).resolve()
            require(
                path.is_relative_to(HERE)
                and hashlib.sha256(path.read_bytes()).hexdigest() == pin["sha256"],
                "Active specification changed after review",
            )
    print(
        f"Active {sha[:9]} reassessment passed: {len(pins)} changed-source pins; "
        f"next HS-01a; design verdict {plan['review_verdict']}; no runtime certification."
    )


if __name__ == "__main__":
    main()

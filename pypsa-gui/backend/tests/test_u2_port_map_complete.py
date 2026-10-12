"""
U2 WP2 acceptance: every test of the five GS engine test files has a row in
`tests/fixtures/u2_port_map.json` (a target test, or "dropped: <reason>"),
and every named target exists.

Plan: docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md WP2.
After WP10 deletes the source files, the map is the record and only the
target check runs.
"""
from __future__ import annotations

import ast
import json
import pathlib

import pytest

TESTS = pathlib.Path(__file__).resolve().parent
MAP = json.loads((TESTS / "fixtures" / "u2_port_map.json").read_text(encoding="utf-8"))
SOURCES = ("test_tariff_bill.py", "test_tariff_demand_charge.py",
           "test_demand_charge_absent_is_noop.py", "test_proforma_golden.py",
           "test_proforma_xlsx.py")


def _tests_in(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}


@pytest.mark.parametrize("source", SOURCES)
def test_every_source_test_has_a_row(source):
    path = TESTS / source
    assert source in MAP["map"], source
    if not path.exists():  # deleted in WP10: the map is the record
        return
    missing = sorted(_tests_in(path) - set(MAP["map"][source]))
    assert missing == [], f"{source}: no port-map row for {missing}"
    stale = sorted(set(MAP["map"][source]) - _tests_in(path))
    assert stale == [], f"{source}: rows for tests that do not exist: {stale}"


def test_every_target_exists_or_the_row_says_why_it_was_dropped():
    for source, rows in MAP["map"].items():
        for test, target in rows.items():
            if target.startswith("dropped: "):
                assert len(target) > len("dropped: ") + 10, (source, test)
                continue
            file, _, name = target.partition("::")
            path = TESTS / file
            assert path.exists(), (source, test, target)
            assert name in _tests_in(path), (source, test, target)


def test_the_workbook_port_drops_nothing():
    """
    Gate U2-S1 C8 (b), WP9: the pro forma workbook's tests, the formula
    evaluator's two included, each map to a real target, never "dropped".
    """
    rows = MAP["map"]["test_proforma_xlsx.py"]
    dropped = sorted(t for t, target in rows.items() if target.startswith("dropped: "))
    assert dropped == []


def test_no_target_still_waits_on_a_work_package_that_has_landed():
    """
    WP9 is the last package whose targets carried `pending()`; after it, a
    `pending("WP<n>")` mark may only name WP10 or later (C8 c makes WP10 the
    one that asserts none at all).
    """
    import re

    mark = re.compile(r"""\bpending\(\s*["']WP(\d+)["']""")
    early = sorted(f"{p.name}: WP{m.group(1)}" for p in TESTS.glob("test_*.py")
                   for m in mark.finditer(p.read_text(encoding="utf-8"))
                   if int(m.group(1)) <= 9)
    assert early == []

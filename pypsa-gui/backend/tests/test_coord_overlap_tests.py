"""`scripts/coord/overlap_tests.py` maps the registry bases and the chat tool
fragments to the tests that read them (RETRO-FRAG-1), so premerge can waive an
overlap in them only when those tests pass on the trial merge."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "coord_overlap_tests", ROOT / "scripts" / "coord" / "overlap_tests.py"
)
overlap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(overlap)


@pytest.mark.parametrize(
    "path",
    [
        "pypsa-gui/backend/harness/catalogue_tools/report_templates.py",
        "pypsa-gui/backend/harness/catalogue_tools/__init__.py",
        "pypsa-gui/backend/services/chat_dispatchers/report_templates.py",
        "pypsa-gui/backend/harness/catalogue_base.py",
    ],
)
def test_a_tool_fragment_maps_to_the_registry_tests(path):
    runner, tests = overlap.tests_for(ROOT, path)
    assert runner == "pytest"
    assert tests[: len(overlap.TOOL_REGISTRY_TESTS)] == overlap.TOOL_REGISTRY_TESTS


@pytest.mark.parametrize(
    "base,test",
    [
        (
            "pypsa-gui/backend/tests/write_surface_lock_policy",
            "tests/test_write_surface_lock_policy.py",
        ),
        (
            "pypsa-gui/backend/tests/route_chat_disposition",
            "tests/test_route_chat_disposition.py",
        ),
        ("pypsa-gui/backend/services/not_via_chat", "tests/test_not_via_chat.py"),
        ("pypsa-gui/backend/smoke/family_manifest", "tests/test_family_manifest.py"),
        (
            "pypsa-gui/backend/tests/golden_route_surfaces",
            "tests/test_golden_coverage.py",
        ),
    ],
)
def test_a_registry_base_and_its_fragments_map_to_their_tests(base, test):
    for path in (f"{base}.json", f"{base}.d/some-branch.json"):
        runner, tests = overlap.tests_for(ROOT, path)
        assert runner == "pytest" and test in tests, path


def test_every_mapped_test_exists():
    backend = ROOT / "pypsa-gui" / "backend"
    named = set(overlap.TOOL_REGISTRY_TESTS)
    for tests in overlap.SHARED_JSON.values():
        named |= set(tests)
    missing = sorted(t for t in named if not (backend / t).is_file())
    assert missing == []

# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""File-to-test mapping behind premerge's tested-overlap waiver."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "coord"))

import pytest  # noqa: E402
import overlap_tests  # noqa: E402

main = overlap_tests.main
map_tests = overlap_tests.tests_for


@pytest.fixture
def tree(tmp_path):
    for name in (
        "pypsa-gui/backend/tests/test_costs.py",
        "pypsa-gui/backend/tests/test_costs_extra.py",
        "pypsa-gui/backend/tests/test_other.py",
        "pypsa-gui/frontend/src/components/Kpi.test.tsx",
        "pypsa-gui/frontend/src/components/Kpi.card.test.tsx",
        "pypsa-gui/frontend/src/pages/__tests__/IssuesPanel.test.tsx",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    return tmp_path


def test_backend_module_maps_to_globbed_tests(tree):
    runner, tests = map_tests(tree, "pypsa-gui/backend/services/costs.py")
    assert runner == "pytest"
    assert tests == ["tests/test_costs.py", "tests/test_costs_extra.py"]


def test_frontend_component_maps_to_matching_test_files(tree):
    runner, tests = map_tests(tree, "pypsa-gui/frontend/src/components/Kpi.tsx")
    assert runner == "vitest"
    assert tests == [
        "src/components/Kpi.card.test.tsx",
        "src/components/Kpi.test.tsx",
    ]


def test_frontend_test_dir_is_found_from_a_different_directory(tree):
    _, tests = map_tests(tree, "pypsa-gui/frontend/src/pages/IssuesPanel.tsx")
    assert tests == ["src/pages/__tests__/IssuesPanel.test.tsx"]


@pytest.mark.parametrize(
    "path",
    [
        "pypsa-gui/backend/services/untested.py",
        "pypsa-gui/backend/services/__init__.py",
        "pypsa-gui/backend/tests/test_costs.py",
        "pypsa-gui/frontend/src/components/Untested.tsx",
        "pypsa-gui/frontend/src/components/Kpi.test.tsx",
        "scripts/anything.py",
        "Snakefile",
    ],
)
def test_no_matching_test_means_no_tests(tree, path):
    assert map_tests(tree, path)[1] == []


def test_cli_prints_one_line_per_test_and_none(tree, capsys):
    main([str(tree), "pypsa-gui/backend/services/costs.py", "Snakefile"])
    assert capsys.readouterr().out.splitlines() == [
        "pytest\tpypsa-gui/backend/services/costs.py\ttests/test_costs.py",
        "pytest\tpypsa-gui/backend/services/costs.py\ttests/test_costs_extra.py",
        "none\tSnakefile",
    ]


@pytest.mark.parametrize(
    "path, tests",
    [
        ("pypsa-gui/tool-error-kinds.json", ["tests/test_tool_error_kind_manifest.py", "tests/test_json_fragments.py"]),
        ("pypsa-gui/tool-error-kinds.d/claude-x.json",
         ["tests/test_tool_error_kind_manifest.py", "tests/test_json_fragments.py"]),
        ("pypsa-gui/backend/smoke/assistant_route_parity_declarations.d/claude-x.json",
         ["tests/test_assistant_route_parity.py", "tests/test_json_fragments.py"]),
    ],
)
def test_shared_json_and_its_fragments_map_to_their_tests(tree, path, tests):
    assert map_tests(tree, path) == ("pytest", tests)

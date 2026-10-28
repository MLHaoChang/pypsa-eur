"""LC-06: the family execution manifest maps every tool and every annex variant.

`smoke/family_manifest.py --check` is the public seam: each inventory tool sits
in exactly one of the 14 capability-matrix families, every LC-D01–24 suite has
its named variants, and a variant called covered names test functions that
exist. Pending and unavailable variants say why.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
COMMAND = ROOT / 'pypsa-gui/backend/smoke/family_manifest.py'
sys.path.insert(0, str(COMMAND.parent))

import family_manifest as fm  # noqa: E402


def run(*args):
    return subprocess.run([sys.executable, str(COMMAND), *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)


def test_the_check_command_passes_and_prints_the_denominators():
    result = run('--check')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'families=14' in result.stdout and 'suites=24' in result.stdout


def test_the_written_manifest_names_every_tool_and_variant(tmp_path):
    path = tmp_path / 'manifest.json'
    result = run('--write', '--output', str(path))
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(path.read_text())
    inventory = fm.inventory_tools()
    mapped = [t for tools in data['tools_by_family'].values() for t in tools]
    assert sorted(mapped + data['unmapped_tools']) == sorted(inventory)
    assert {v['suite'] for v in data['variants']} == {f'LC-D{i:02d}' for i in range(1, 25)}
    assert data['full_coverage'] == 'not_established'


def test_families_are_the_control_matrix_families():
    from assistant_control_matrix import FAMILY_JOURNEYS
    assert [j['journey_id'].removeprefix('pending:issue-05-') for j in FAMILY_JOURNEYS] == list(fm.FAMILIES)


@pytest.mark.parametrize('variant', fm.VARIANTS, ids=lambda v: v['id'])
def test_each_variant_has_an_explicit_disposition(variant):
    assert variant['status'] in ('covered', 'pending', 'unavailable')
    if variant['status'] == 'covered':
        assert variant['tests'] and variant['oracle']
    else:
        assert variant['reason']


def test_a_covered_variant_naming_a_missing_test_is_refused():
    bogus = {'id': 'LC-D04.bogus', 'suite': 'LC-D04', 'status': 'covered', 'oracle': 'x',
             'tests': ['pypsa-gui/backend/tests/test_capacity_cycles_assurance.py::test_no_such_test']}
    assert 'test_no_such_test' in ' '.join(fm.manifest_problems(variants=[*fm.VARIANTS, bogus]))


def test_a_tool_in_two_families_or_a_stale_tool_is_refused():
    families = {f: set(tools) for f, tools in fm.TOOLS_BY_FAMILY.items()}
    families['reports'] |= {'get_results', 'no_such_tool'}
    problems = ' '.join(fm.manifest_problems(tools_by_family=families))
    assert 'get_results' in problems and 'no_such_tool' in problems

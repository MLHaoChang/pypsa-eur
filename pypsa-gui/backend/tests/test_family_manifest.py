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
    assert f'/ {len(fm.inventory_tools())} (' in result.stdout


def test_the_written_manifest_names_every_tool_and_variant(tmp_path):
    path = tmp_path / 'manifest.json'
    result = run('--write', '--output', str(path))
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(path.read_text())
    inventory = fm.inventory_tools()
    mapped = [t for tools in data['tools_by_family'].values() for t in tools]
    assert sorted(mapped + data['unmapped_tools']) == sorted(inventory)
    assert set(data['covered_tools']) <= set(inventory) and data['counts']['tools'] == len(inventory)
    assert {v['suite'] for v in data['variants']} == {f'LC-D{i:02d}' for i in range(1, 25)}
    assert data['full_coverage'] == 'not_established'


def test_families_are_the_control_matrix_families():
    from assistant_control_matrix import FAMILY_JOURNEYS
    assert [j['journey_id'].removeprefix('manifest:') for j in FAMILY_JOURNEYS] == list(fm.FAMILIES)


@pytest.mark.parametrize('variant', fm.VARIANTS, ids=lambda v: v['id'])
def test_each_variant_has_an_explicit_disposition(variant):
    assert variant['status'] in ('covered', 'pending', 'unavailable')
    if variant['status'] == 'covered':
        assert variant['tests'] and variant['oracle'] and all('::' in t for t in variant['tests'])
        assert variant['tools'] or variant['seam'] == fm.CHECKED_STREAM
    else:
        assert variant['reason']
    if variant['status'] == 'unavailable':
        assert variant['pointer']


def test_a_covered_node_id_that_does_not_collect_is_refused():
    real = fm.VARIANTS[[v['status'] for v in fm.VARIANTS].index('covered')]['tests'][0]
    bogus = real.split('::')[0] + '::test_no_such_test'
    assert fm.uncollected_nodes([real, bogus]) == [bogus]
    variant = {'id': 'LC-D04.bogus', 'suite': 'LC-D04', 'status': 'covered', 'oracle': 'x',
               'tools': ['get_results'], 'tests': [bogus]}
    assert f'{bogus}: does not collect' in fm.manifest_problems(variants=[*fm.VARIANTS, variant])


def test_an_unavailable_row_needs_a_pointer_that_resolves():
    bare = {'id': 'LC-D07.bare', 'suite': 'LC-D07', 'status': 'unavailable', 'reason': 'x'}
    stale = {**bare, 'id': 'LC-D07.stale', 'pointer': 'pypsa-gui/backend/services/loadflow_summary.py NO_SUCH'}
    problems = ' '.join(fm.manifest_problems(variants=[*fm.VARIANTS, bare, stale], collect=False))
    assert 'LC-D07.bare: no pointer' in problems and 'NO_SUCH not in the file' in problems


def test_a_tool_has_one_primary_family_and_stale_names_are_refused():
    families = {f: set(tools) for f, tools in fm.TOOLS_BY_FAMILY.items()}
    families['reports'] |= {'get_results', 'no_such_tool'}
    problems = ' '.join(fm.manifest_problems(tools_by_family=families, collect=False))
    assert 'get_results is in both' in problems and 'no_such_tool is not an inventory tool' in problems


def test_a_new_tool_stays_in_the_denominator_as_unmapped():
    inventory = (*fm.inventory_tools(), 'brand_new_tool')
    assert fm.manifest_problems(inventory=inventory, collect=False) == []
    before, after = fm.build_manifest(), fm.build_manifest(inventory)
    assert after['counts']['tools'] == before['counts']['tools'] + 1
    assert after['unmapped_tools'] == ['brand_new_tool']
    assert after['counts']['covered_tools'] == before['counts']['covered_tools']
    assert 'brand_new_tool' in fm.report(after) and '(1 unmapped)' in fm.report(after)


def test_a_covered_row_names_its_tools_or_the_checked_stream():
    bare = {'id': 'LC-D04.bare', 'suite': 'LC-D04', 'status': 'covered', 'oracle': 'x', 'tools': [],
            'tests': [fm.VARIANTS[[v['status'] for v in fm.VARIANTS].index('covered')]['tests'][0]]}
    problems = fm.manifest_problems(variants=[*fm.VARIANTS, bare], collect=False)
    assert 'LC-D04.bare: covered needs its tools or the checked-stream seam' in problems

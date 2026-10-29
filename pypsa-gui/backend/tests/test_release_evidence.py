"""LC-08: release evidence per capability-matrix family, pinned to its source.

`smoke/release_evidence.py --check` is the public seam. It reads the LC-06
family manifest at run time, so a family with any pending variant stays
``partial`` (one journey never closes a family), a family with nothing covered
is never ``evidenced``, and the five statuses this package does not compute
stay ``not_established``.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMMAND = ROOT / 'pypsa-gui/backend/smoke/release_evidence.py'
sys.path.insert(0, str(COMMAND.parent))

import family_manifest as fm  # noqa: E402
import release_evidence as re_  # noqa: E402


def run(*args):
    return subprocess.run([sys.executable, str(COMMAND), *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=300)


def synthetic(*variants):
    """A one-family manifest shaped like ``build_manifest()``."""
    return {'families': {'demo': 'Demo'}, 'suites_by_family': {'demo': ['LC-D99']},
            'tools_by_family': {'demo': ['a', 'b']}, 'covered_tools': ['a'],
            'variants': [{'id': f'LC-D99.{name}', 'suite': 'LC-D99', 'status': status,
                          **({'tests': ['tests/x.py::test_x'], 'oracle': 'o', 'tools': ['a']}
                             if status == 'covered' else {'reason': 'why'})}
                         for name, status in variants]}


OWNED = {'LC-D99': 'test owner'}


def test_a_family_with_one_pending_variant_is_partial_and_names_it():
    built = synthetic(('one', 'covered'), ('two', 'unavailable'), ('three', 'pending'))
    family = re_.build_evidence(built, owners=OWNED, journeys={})['families']['demo']
    assert family['statuses']['fixture_testing'] == 'partial'
    assert [p['id'] for p in family['pending']] == ['LC-D99.three']
    assert family['pending'][0]['owner'] == 'test owner'
    assert family['variants'] == {'total': 3, 'covered': 1, 'pending': 1, 'unavailable': 1}


def test_an_all_unavailable_family_is_not_evidenced():
    built = synthetic(('one', 'unavailable'), ('two', 'unavailable'))
    family = re_.build_evidence(built, owners=OWNED, journeys={})['families']['demo']
    assert family['statuses']['fixture_testing'] == 'not_evidenced'


def test_a_fully_covered_family_is_evidenced_with_its_tests_and_oracles():
    built = synthetic(('one', 'covered'), ('two', 'unavailable'))
    family = re_.build_evidence(built, owners=OWNED, journeys={})['families']['demo']
    assert family['statuses']['fixture_testing'] == 'evidenced'
    assert family['covered'] == [{'id': 'LC-D99.one', 'oracle': 'o', 'tests': ['tests/x.py::test_x']}]
    assert family['tools'] == {'total': 2, 'covered': 1}


def test_the_five_statuses_this_package_does_not_compute_stay_not_established():
    evidence = re_.build_evidence(fm.build_manifest(), source={'git_head': 'x'})
    assert evidence['full_coverage'] == 'not_established'
    for family in evidence['families'].values():
        fixed = {k: v for k, v in family['statuses'].items() if k != 'fixture_testing'}
        assert set(fixed) == {'implementation', 'vendor_readiness', 'domain_review', 'calibration',
                              'production_enablement'}
        assert all(v == 'not_established' for v in fixed.values())
        assert all(family['status_reasons'][k] for k in fixed)


def test_families_and_denominators_come_from_the_manifest():
    built = fm.build_manifest()
    evidence = re_.build_evidence(built, source={'git_head': 'x'})
    assert list(evidence['families']) == list(built['families'])
    for name, family in evidence['families'].items():
        v = family['variants']
        assert v['covered'] + v['pending'] + v['unavailable'] == v['total']
        suites = set(built['suites_by_family'][name])
        assert v['total'] == sum(x['suite'] in suites for x in built['variants'])


def test_a_pending_variant_without_an_owner_is_a_problem():
    built = synthetic(('one', 'covered'), ('two', 'pending'))
    problems = re_.evidence_problems(built, owners={}, journeys={}, collect=False)
    assert problems == ['LC-D99.two: pending variant has no owner in OWNERS']
    assert re_.evidence_problems(built, owners=OWNED, journeys={}, collect=False) == []


def test_every_pending_variant_on_this_source_has_an_owner():
    assert re_.evidence_problems(fm.build_manifest(), collect=False) == []


def test_a_journey_node_that_does_not_exist_fails_the_check(tmp_path):
    journeys = {'demo': [{'kind': 'backend', 'name': 'OJ-00',
                          'nodes': ['pypsa-gui/backend/tests/test_release_evidence.py::test_no_such_test']}]}
    built = synthetic(('one', 'covered'))
    problems = re_.evidence_problems(built, owners=OWNED, journeys=journeys, collect=True)
    assert problems == ['OJ-00: pypsa-gui/backend/tests/test_release_evidence.py::test_no_such_test does not collect']
    journeys = {'demo': [{'kind': 'browser', 'name': 'OJ-00', 'script': 'pypsa-gui/frontend/scripts/nope.mjs'}]}
    problems = re_.evidence_problems(built, owners=OWNED, journeys=journeys, collect=False)
    assert problems == ['OJ-00: pypsa-gui/frontend/scripts/nope.mjs: no such file']


def test_the_written_evidence_is_pinned_to_the_checked_out_source(tmp_path):
    path = tmp_path / 'evidence.json'
    result = run('--write', '--output', str(path))
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(path.read_text())
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert data['source']['git_head'] == head
    pinned = data['source']['files']
    assert 'pypsa-gui/backend/smoke/family_manifest.py' in pinned
    assert all(len(digest) == 64 for digest in pinned.values())
    for family in data['families'].values():
        for journey in family['journeys']:
            assert 'result' not in journey  # mapped, never an unobserved result
    assert data['limitations'] and all(item['source'] for item in data['limitations'])

"""Issue 08 — editable-control / family-journey / AR matrix contracts."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / 'pypsa-gui/backend/smoke/assistant_control_matrix.py'
COVERAGE_DOC = ROOT / '.scratch/live-voice/application-coverage.md'

EXPECTED_FAMILIES = {
    'Projects/scenarios/templates',
    'Network/topology/asset parameters',
    'Time series/horizon/multi-period',
    'Solver/simulation',
    'All results',
    'Reliability/health/adequacy',
    'GridSpine/campus electrical',
    'Economics/investment/Library',
    'Experiments/sensitivity',
    'Import/export/delivery',
    'Reports/templates/edit round trip',
    'Audit/undo/checkpoints',
    'Harness/workflows/providers',
    'UI/preferences/monitoring',
}


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SMOKE), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _gen():
    sys.path.insert(0, str(SMOKE.parent))
    try:
        import assistant_control_matrix as gen
    finally:
        sys.path.remove(str(SMOKE.parent))
    return gen


@pytest.fixture(scope='module')
def matrix() -> dict:
    # Built in memory: the matrix is not committed (RETRO-COST-1).
    return json.loads(json.dumps(_gen().build_matrix()))


def test_control_matrix_builds(matrix):
    assert matrix['format_version'] == 1
    assert matrix['counts']['creation_controls'] >= 80
    assert matrix['counts']['unmapped_palette_ids'] == 0


def test_every_creation_form_field_maps_to_tool_or_declared_gap(matrix):
    for row in matrix['controls']:
        assert row['tool'] or row['gap'], row
        if row['tool']:
            assert row['tool'] in {'create_component', 'update_component', 'bulk_update_components'}
            assert row['component_class']
            assert row['attrs_path'] or row['gap']


def test_catalog_editability_overrides_appear_in_matrix(matrix):
    targets = {r['ui_target'] for r in matrix['editability_overrides']}
    assert 'catalog:Bus.control' in targets
    assert 'catalog:Generator.committable' in targets
    bus = next(r for r in matrix['editability_overrides'] if r['ui_target'] == 'catalog:Bus.control')
    assert bus['editability'] == 'editable'
    assert bus['tool'] == 'update_component'


def test_closed_sets_carry_enum_values(matrix):
    bus = next(r for r in matrix['closed_sets'] if r['ui_target'] == 'catalog:Bus.control')
    assert bus['parameter_type'] == 'enum'
    assert bus['values'] == ['PQ', 'PV', 'Slack']


def test_ar_alias_resolves_to_inventory_ids_not_ardat(matrix):
    ar = matrix['ar_resolution']
    assert ar['status'] == 'mapped'
    assert 'Ardat' not in json.dumps(ar)
    from smoke.assistant_inventory import build_inventory
    inventory = build_inventory()
    tool_names = {t['name'] for t in inventory['tools']}
    tab_ids = {t['id'] for t in inventory['result_tabs']}
    for name in ar['maps_to']['tools']:
        assert name in tool_names
    for tab in ar['maps_to']['result_tabs']:
        assert tab in tab_ids
    assert ar['expansion'] == 'Asset Reliability'


def _manifest():
    sys.path.insert(0, str(SMOKE.parent))
    try:
        import family_manifest
    finally:
        sys.path.remove(str(SMOKE.parent))
    return family_manifest


def test_every_coverage_family_journey_is_a_family_manifest_reference(matrix):
    fm = _manifest()
    rows = matrix['family_journeys']
    assert {r['family'] for r in rows} == EXPECTED_FAMILIES == set(fm.FAMILIES.values())
    assert len(rows) == 14
    by_suite: dict[str, dict[str, list[str]]] = {}
    for v in fm.VARIANTS:
        by_suite.setdefault(v['suite'], {}).setdefault(v['status'], []).append(v['id'])
    for row in rows:
        slug = row['journey_id'].removeprefix('manifest:')
        assert row['journey_id'] == f'manifest:{slug}' and fm.FAMILIES[slug] == row['family']
        suites = [f'LC-D{i:02d}' for i in fm.SUITES_BY_FAMILY[slug]]
        assert row['suites'] == suites
        for status in ('covered', 'pending', 'unavailable'):
            assert row['variants'][status] == [i for s in suites for i in by_suite.get(s, {}).get(status, [])]
        assert row['status'] == ('pending' if row['variants']['pending'] else 'covered')
    assert 'pending:issue-05' not in json.dumps(matrix)


def test_ineligible_ui_targets_mark_project_requirement(matrix):
    for row in matrix['controls']:
        elig = row['eligibility']
        assert elig['requires_project'] is True
        assert 'capacity_expansion' in elig['project_kinds']
        assert 'expert' in elig['ui_modes'] or 'guided' in elig['ui_modes']


def test_check_builds_in_memory_and_never_touches_a_matrix_file(tmp_path):
    path = tmp_path / 'capability-matrix.json'
    proc = _run('--check', '--output', str(path))
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert not path.exists()


# ── issue 08 close-out: explicit full-coverage limitation block ─────────

def test_full_coverage_is_explicitly_not_claimed_while_manifest_variants_are_pending(matrix):
    block = matrix['full_coverage']
    pending = [r for r in matrix['family_journeys'] if r['variants']['pending']]
    assert pending, 'update this guard together with the claim once every manifest variant is covered'
    assert block['claimed'] is False and block['status'] == 'not_established'
    assert block['journeys_total'] == 14 and block['journeys_pending'] == len(pending)
    assert 'Tested subsets only' in block['permitted_statement']
    blockers = {b['id']: b for b in block['blockers']}
    r3 = {f'LC-D{i:02d}' for i in (1, 2, 20, 21, 22, 23, 24)}
    for row in pending:
        lc06 = [v for v in row['variants']['pending'] if v.split('.')[0] not in r3]
        if lc06:
            assert blockers[f'family-manifest:{row["journey_id"].removeprefix("manifest:")}']['pending_variants'] == lc06
    # The families LC-06 leaves out (R3) keep an explicit blocker each, owned by LC-32.
    for suite in sorted(r3):
        assert blockers[f'r3-uncovered:{suite}']['issue'] == 'LC-32'
    assert 'behavioral-fixtures-pending' not in blockers
    assert {'ui-local-control-parity-unaudited', 'hosted-gates-pending', 'manifest-tools-uncovered'} <= set(blockers)
    parity = matrix['route_parity']
    assert parity['counts']['unclassified'] == 0
    for family, routes in parity['needs_tool_by_family'].items():
        assert routes and f'route-parity:needs-tool:{family}' in blockers
    for blocker in block['blockers']:
        assert blocker['issue'] in {'05', '08', 'LC-06', 'LC-32'} and blocker['summary']


def test_a_variant_going_pending_reopens_its_family_and_the_claim_reads_the_manifest():
    gen, fm = _gen(), _manifest()
    covered = [dict(v, status='covered') for v in fm.VARIANTS]
    journeys = gen.family_journeys(covered)
    block = gen._full_coverage(journeys)
    assert block['journeys_pending'] == 0
    assert not any(b['id'].startswith(('family-manifest:', 'r3-uncovered:')) for b in block['blockers'])
    one = [dict(v, status='pending') if v['id'] == 'LC-D19.single_case' else v for v in covered]
    block = gen._full_coverage(gen.family_journeys(one))
    assert block['journeys_pending'] == 2       # LC-D19 is in projects and experiments
    assert {b['id'] for b in block['blockers'] if b['id'].startswith('family-manifest:')} == {
        'family-manifest:projects', 'family-manifest:experiments'}


def test_matrix_never_asserts_whole_application_coverage_elsewhere(matrix):
    assert matrix['full_coverage']['claimed'] is False
    text = json.dumps(matrix).lower()
    for phrase in ('whole-application coverage is complete', 'full coverage established',
                   'every tool is covered'):
        assert phrase not in text
    doc = COVERAGE_DOC.read_text(encoding='utf-8')
    assert 'Whole-application coverage is NOT claimed.' in doc
    # Variant counts change with every LC-06 slice, so the doc points at the build instead.
    assert 'family_manifest.py' in doc and 'assistant_control_matrix.py --write' in doc
    assert not re.search(r'Pending family journeys: \d+ of', doc)
    # Route counts change with every tool PR, so the doc points at the build instead.
    assert 'assistant_route_parity.py --write' in doc
    assert not re.search(r'\d+ of \d+ GUI-reached routes', doc)


@pytest.mark.parametrize('mutate, expected', [
    (lambda m: m['full_coverage'].update(claimed=True, status='established'), 'claimed is true'),
    (lambda m: m['full_coverage'].update(claimed=True), 'claimed is true'),
    (lambda m: m['full_coverage'].update(status='established'), 'claimed=false/not_established'),
    (lambda m: m['full_coverage'].update(journeys_pending=0), 'journeys_pending disagrees'),
    (lambda m: m.pop('full_coverage'), 'missing'),
])
def test_ci_rejects_a_full_coverage_claim_without_passing_journeys(matrix, mutate, expected):
    tampered = json.loads(json.dumps(matrix))
    mutate(tampered)
    assert expected in (_gen().matrix_problem(tampered) or '')


@pytest.mark.parametrize('mutate, expected', [
    (lambda m: m['ar_resolution'].update(status='unresolved'), 'AR alias'),
    (lambda m: m['family_journeys'].pop(), 'all 14'),
    (lambda m: m['controls'][0].update(tool=None, gap=None), 'tool or an explicit gap'),
])
def test_matrix_guards_reject_each_broken_invariant(matrix, mutate, expected):
    assert _gen().matrix_problem(matrix) is None
    tampered = json.loads(json.dumps(matrix))
    mutate(tampered)
    assert expected in (_gen().matrix_problem(tampered) or '')


def test_claim_guard_rejects_claim_even_if_artifact_was_regenerated():
    gen = _gen()
    built = gen.build_matrix()
    assert gen._full_coverage_problem(built) is None
    forged = json.loads(json.dumps(built))
    forged['full_coverage'].update(claimed=True, status='established')
    assert 'claimed is true' in gen._full_coverage_problem(forged)
    covered = [dict(v, status='covered') for v in _manifest().VARIANTS]
    block = gen._full_coverage(gen.family_journeys(covered))
    assert block['journeys_pending'] == 0
    # Covered variants alone are not enough: standing blockers need an explicit edit.
    assert block['claimed'] is False
    assert {b['id'] for b in gen.STANDING_BLOCKERS} <= {b['id'] for b in block['blockers']}


def test_claim_guard_reads_pending_from_the_manifest_not_the_matrix_copy(matrix):
    # Editing the matrix's own journey rows cannot hide a pending manifest variant.
    forged = json.loads(json.dumps(matrix))
    for row in forged['family_journeys']:
        row['variants']['pending'] = []
        row['status'] = 'covered'
    forged['full_coverage']['journeys_pending'] = 0
    assert 'journeys_pending disagrees with family_manifest' in (_gen().matrix_problem(forged) or '')


def test_claim_guard_ignores_the_module_family_journeys(matrix, monkeypatch):
    # Pending is recomputed from the manifest's variants, never from FAMILY_JOURNEYS.
    gen = _gen()
    cleared = [dict(r, variants=dict(r['variants'], pending=[]), status='covered') for r in gen.FAMILY_JOURNEYS]
    monkeypatch.setattr(gen, 'FAMILY_JOURNEYS', cleared)
    forged = json.loads(json.dumps(matrix))
    forged['family_journeys'] = cleared
    forged['full_coverage']['journeys_pending'] = 0
    assert 'journeys_pending disagrees with family_manifest' in (gen.matrix_problem(forged) or '')


def test_unavailable_variants_are_not_blockers_but_their_pointers_resolve(matrix):
    fm = _manifest()
    unavailable = {i for r in matrix['family_journeys'] for i in r['variants']['unavailable']}
    assert unavailable, 'pick another unavailable variant once these are covered'
    listed = {i for b in matrix['full_coverage']['blockers'] for i in b.get('pending_variants', [])}
    assert not unavailable & listed
    for v in fm.VARIANTS:
        if v['id'] in unavailable:
            assert fm._pointer_problem(v['pointer']) is None, v['id']

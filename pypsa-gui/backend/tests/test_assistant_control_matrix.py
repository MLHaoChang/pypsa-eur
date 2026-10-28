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


def test_every_coverage_family_has_journey_assignment_row(matrix):
    rows = matrix['family_journeys']
    assert {r['family'] for r in rows} == EXPECTED_FAMILIES
    assert len(rows) == 14
    for row in rows:
        assert row['status'] == 'assigned'
        assert row['journey_id'].startswith('pending:issue-05-')


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

def test_full_coverage_is_explicitly_not_claimed_while_journeys_are_pending(matrix):
    block = matrix['full_coverage']
    pending = [r for r in matrix['family_journeys'] if r['journey_id'].startswith('pending:')]
    assert pending, 'update this guard together with the claim once issue 05 journeys pass'
    assert block['claimed'] is False and block['status'] == 'not_established'
    assert block['journeys_total'] == 14 and block['journeys_pending'] == len(pending)
    assert 'Tested subsets only' in block['permitted_statement']
    blocker_ids = {b['id'] for b in block['blockers']}
    for row in pending:
        assert f'family-journey:{row["journey_id"]}' in blocker_ids
    assert {'behavioral-fixtures-pending', 'ui-local-control-parity-unaudited',
            'hosted-gates-pending'} <= blocker_ids
    parity = matrix['route_parity']
    assert parity['counts']['unclassified'] == 0
    for family, routes in parity['needs_tool_by_family'].items():
        assert routes and f'route-parity:needs-tool:{family}' in blocker_ids
    for blocker in block['blockers']:
        assert blocker['issue'] in {'05', '08'} and blocker['summary']


def test_matrix_never_asserts_whole_application_coverage_elsewhere(matrix):
    assert matrix['full_coverage']['claimed'] is False
    text = json.dumps(matrix).lower()
    for phrase in ('whole-application coverage is complete', 'full coverage established',
                   'every tool is covered'):
        assert phrase not in text
    doc = COVERAGE_DOC.read_text(encoding='utf-8')
    assert 'Whole-application coverage is NOT claimed.' in doc
    assert (f'Pending family journeys: {matrix["full_coverage"]["journeys_pending"]} of '
            f'{matrix["full_coverage"]["journeys_total"]}') in doc
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
    journeys = [dict(r, journey_id=r['journey_id'].replace('pending:', '')) for r in built['family_journeys']]
    block = gen._full_coverage(journeys)
    assert block['journeys_pending'] == 0
    # Passing journeys alone are not enough: standing blockers need an explicit edit.
    assert block['claimed'] is False
    assert {b['id'] for b in block['blockers']} == {b['id'] for b in gen.STANDING_BLOCKERS}

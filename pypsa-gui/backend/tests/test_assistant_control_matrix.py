"""Issue 08 — editable-control / family-journey / AR matrix contracts."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / 'pypsa-gui/backend/smoke/assistant_control_matrix.py'
MATRIX = ROOT / '.scratch/live-voice/capability-matrix.json'
INVENTORY = ROOT / '.scratch/live-voice/capability-inventory.json'

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
        [sys.executable, '-S', str(SMOKE), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope='module')
def matrix() -> dict:
    written = _run('--write')
    assert written.returncode == 0, written.stderr + written.stdout
    checked = _run('--check')
    assert checked.returncode == 0, checked.stderr + checked.stdout
    return json.loads(MATRIX.read_text(encoding='utf-8'))


def test_control_matrix_check_passes_committed_artifact(matrix):
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
    inventory = json.loads(INVENTORY.read_text(encoding='utf-8'))
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


def test_control_matrix_reader_needs_no_site_packages():
    proc = _run('--check')
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_matrix_rejects_stale_palette_digest(tmp_path, matrix):
    stale = dict(matrix)
    stale['controls'] = list(matrix['controls']) + [{
        'family': 'Network/topology/asset parameters',
        'ui_surface': 'creation_form',
        'ui_target': 'palette:ghost.fake',
        'palette_id': 'ghost',
        'field_key': 'fake',
        'label': 'Fake',
        'parameter_type': 'text',
        'unit': None,
        'component_class': 'Bus',
        'tool': 'create_component',
        'attrs_path': 'attrs.fake',
        'eligibility': {
            'project_kinds': ['capacity_expansion', None],
            'ui_modes': ['expert'],
            'requires_project': True,
        },
        'gap': None,
        'behavioral_fixture': 'pending:issue-05-network-topology',
    }]
    path = tmp_path / 'capability-matrix.json'
    path.write_text(json.dumps(stale, indent=2) + '\n', encoding='utf-8')
    proc = _run('--check', '--output', str(path))
    assert proc.returncode != 0
    assert 'stale' in (proc.stdout + proc.stderr).lower()

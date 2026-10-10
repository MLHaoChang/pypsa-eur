"""Public inventory CLI prevents silent tool/GUI-navigation coverage drift."""
import json
import subprocess
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
COMMAND = ROOT / 'pypsa-gui/backend/smoke/assistant_inventory.py'


def run(*args):
    return subprocess.run([sys.executable, str(COMMAND), *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=20)


def test_committed_inventory_passes_the_public_check_command():
    result = run('--check')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'navigation' in result.stdout.lower()


def test_written_inventory_is_complete_and_explicit_about_unverified_behavior(tmp_path):
    path = tmp_path / 'inventory.json'
    result = run('--write', '--output', str(path))
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(path.read_text())
    assert data['tool_count'] >= 220
    assert len({t['name'] for t in data['tools']}) == data['tool_count']
    component = next(t for t in data['tools'] if t['name'] == 'list_components')
    assert component['required'] == ['component_class']
    assert component['safety_tier'] == 'read'
    assert ['GET', '/api/network/lines'] in component['route']
    assert all(t['behavioral_fixture'] == 'pending' for t in data['tools'])
    assert data['result_tabs_not_in_tool_enum'] == []
    assert data['slide_panels_not_in_tool_enum'] == []
    assert {t['id'] for t in data['result_tabs']} >= {'adequacy', 'fmea', 'loadflow', 'investment'}
    assert len(data['result_tabs']) == 14 and len(data['slide_panels']) == 17
    assert 'pending' in data['status']


@pytest.mark.parametrize('damage', ['schema', 'route', 'toolset', 'tab', 'panel', 'missing_tool'])
def test_check_rejects_stale_inventory_without_rewriting_it(tmp_path, damage):
    path = tmp_path / 'inventory.json'
    assert run('--write', '--output', str(path)).returncode == 0
    data = json.loads(path.read_text())
    if damage == 'schema': data['tools'][0]['schema_sha256'] = 'stale'
    elif damage == 'route': data['tools'][0]['route'] = [['GET', '/wrong']]
    elif damage == 'toolset': data['tools'][0]['toolsets'] = []
    elif damage == 'tab': data['result_tabs'].pop()
    elif damage == 'panel': data['slide_panels'].pop()
    else: data['tools'].pop()
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    result = run('--check', '--output', str(path))
    assert result.returncode == 1 and 'stale' in result.stdout.lower()
    assert path.read_bytes() == before


def test_check_reports_missing_inventory_actionably(tmp_path):
    result = run('--check', '--output', str(tmp_path / 'missing.json'))
    assert result.returncode == 1
    assert '--write' in result.stdout


def test_inventory_cli_needs_no_site_packages_or_application_startup():
    result = subprocess.run([sys.executable, '-S', str(COMMAND), '--check'],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def isolated_reader(tmp_path, results, panels):
    for relative in (
        'pypsa-gui/backend/smoke/assistant_inventory.py',
        'pypsa-gui/backend/harness/__init__.py',
        'pypsa-gui/backend/harness/catalogue.py',
        'pypsa-gui/backend/harness/toolsets.py',
        'pypsa-gui/backend/models/energy_hub.py',
        'pypsa-gui/backend/services/adequacy/mc.py',
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    for relative, source in (
        ('pypsa-gui/frontend/src/pages/Results.tsx', results),
        ('pypsa-gui/frontend/src/store/uiStore.ts', panels),
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    return subprocess.run([
        sys.executable, str(tmp_path / 'pypsa-gui/backend/smoke/assistant_inventory.py'), '--write',
    ], capture_output=True, text=True, timeout=20)


def test_inventory_reads_multiline_and_mixed_quote_frontend_target_declarations(tmp_path):
    result = isolated_reader(tmp_path, '''
const TABS: Array<{id: string; label: string}> = [
  { id: "fmea", label: "FMEA" },
  { id: 'adequacy', label: 'Adequacy' },
]
''', '''
export type SlidePanel =
  | 'results'
  | "reports"
''')
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads((tmp_path / '.scratch/live-voice/capability-inventory.json').read_text())
    assert data['result_tabs'] == [{'id': 'fmea', 'label': 'FMEA'}, {'id': 'adequacy', 'label': 'Adequacy'}]
    assert data['slide_panels'] == ['results', 'reports']


def test_inventory_refuses_unrecognized_targets_instead_of_silently_omitting_them(tmp_path):
    result = isolated_reader(tmp_path, '''
const TABS: Array<{id: string; label: string}> = [
  { id: 'fmea', label: 'FMEA' },
  { id: DYNAMIC_TAB, label: 'Needs reader update' },
]
''', "export type SlidePanel = 'results'\n")
    assert result.returncode == 1
    assert 'enumerate' in result.stdout.lower()
    assert not (tmp_path / '.scratch/live-voice/capability-inventory.json').exists()

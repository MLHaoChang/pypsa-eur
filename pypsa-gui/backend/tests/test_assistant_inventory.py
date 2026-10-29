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


def test_inventory_passes_the_public_check_command():
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
    assert {t['id'] for t in data['result_tabs']} >= {'summary', 'adequacy', 'fmea', 'loadflow', 'investment'}
    # 19 panels: Economics in Expert added 'economics' (plan 2026-10-14-economics-in-expert.md).
    # One tab per registry view with a tab, so each slice that adds a view needs no bump here.
    from services import result_views as rv
    assert len(data['result_tabs']) == len([v for v in rv.RESULT_VIEWS if v.panel == 'results']) and len(data['slide_panels']) == 23
    assert 'pending' in data['status']


def test_check_builds_in_memory_and_never_touches_an_inventory_file(tmp_path):
    # The inventory is not committed (RETRO-COST-1): tool PRs never regenerate it.
    path = tmp_path / 'inventory.json'
    result = run('--check', '--output', str(path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert not path.exists()


def test_inventory_cli_needs_no_site_packages_or_application_startup():
    result = subprocess.run([sys.executable, '-S', str(COMMAND), '--check'],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def isolated_reader(tmp_path, results, panels, edits=()):
    for relative in (
        'pypsa-gui/backend/smoke/assistant_inventory.py',
        'pypsa-gui/backend/harness/__init__.py',
        'pypsa-gui/backend/harness/catalogue.py',
        'pypsa-gui/backend/harness/toolsets.py',
        'pypsa-gui/backend/models/energy_hub.py',
        'pypsa-gui/backend/services/adequacy/mc.py',
        'pypsa-gui/backend/services/asset_schema/attrs_doc.py',
        'pypsa-gui/backend/harness/assurance_coverage.py',
        'pypsa-gui/backend/harness/assurance.py',
        'pypsa-gui/backend/harness/navigation.py',
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    for relative, old, new in edits:
        target = tmp_path / relative
        text = target.read_text()
        assert old in text, f'{old!r} not in {relative}'
        target.write_text(text.replace(old, new, 1))
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


def test_check_rejects_a_gui_target_the_tool_schema_does_not_offer(tmp_path):
    result = isolated_reader(tmp_path, """
const TABS: Array<{id: string; label: string}> = [
  { id: 'fmea', label: 'FMEA' },
  { id: 'not_offered', label: 'Not offered' },
]
""", "export type SlidePanel = 'results' | 'not_a_panel'\n")
    assert result.returncode == 1
    assert 'absent from shared tool schema: not_offered, not_a_panel' in result.stdout


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


COVERAGE = 'pypsa-gui/backend/harness/assurance_coverage.py'
TABS = "const TABS: Array<{id: string; label: string}> = [\n  { id: 'fmea', label: 'FMEA' },\n]\n"
PANELS = "export type SlidePanel = 'results' | 'reports'\n"


def test_every_tool_target_and_channel_maps_to_a_declared_adapter(tmp_path):
    """A0.2: the map is complete, and only the strict pilot's tools carry a checked adapter."""
    path = tmp_path / 'inventory.json'
    assert run('--write', '--output', str(path)).returncode == 0
    assurance = json.loads(path.read_text())['assurance']
    adapters = assurance['adapters']
    tools = json.loads(path.read_text())['tools']
    assert all(t['assurance_adapter'] in adapters for t in tools)
    assert set(assurance['result_tabs']) == {t['id'] for t in json.loads(path.read_text())['result_tabs']}
    assert set(assurance['slide_panels']) == set(json.loads(path.read_text())['slide_panels'])
    assert {*assurance['result_tabs'].values(), *assurance['slide_panels'].values(),
            *assurance['output_channels'].values()} <= set(adapters)
    checked = {t['name'] for t in tools if adapters[t['assurance_adapter']]['status'] == 'checked'}
    assert checked == set(assurance['checked_tools']) == {
        'get_aggregate_load', 'get_component', 'get_run_evidence', 'get_timeseries',
        'list_components', 'update_component', 'ui_open_panel'}
    assert all(a['status'] in {'checked', 'unvalidated'} for a in adapters.values())
    assert all(a.get('pending') for a in adapters.values() if a['status'] == 'unvalidated')
    assert set(assurance['output_channels']) >= {'stream_tokens', 'history_replay', 'tts', 'answer_export'}
    assert all(v == 'results_view_unvalidated' for v in assurance['result_tabs'].values())


def test_runtime_checked_tools_are_exactly_the_mapped_checked_adapters():
    from harness import assurance, assurance_coverage as coverage
    assert assurance.CHECKED_TOOLS == frozenset(coverage.CHECKED_TOOL_ADAPTERS)
    assert not coverage.checked_label_allowed('unknown_adapter')
    assert not coverage.checked_label_allowed('read_unvalidated')


@pytest.mark.parametrize('edit', [
    # An adapter id nobody declared.
    (COVERAGE, '"read": "read_unvalidated"', '"read": "read_magic"'),
    # A tool given a checked label the strict gate does not run.
    (COVERAGE, '"ask_user": "user_choice"', '"ask_user": "static_input_evidence"'),
    # The checked map and the runtime gate disagree.
    (COVERAGE, '"ui_open_panel": "navigation_ack",', '"ui_open_panel": "navigation_ack", "get_component_schema": "static_input_evidence",'),
    # A safety tier with no adapter.
    (COVERAGE, '    "execution_long_running": "job_unvalidated",\n', ''),
    # An output channel bound to an undeclared contract.
    (COVERAGE, '"tts": "approved_answer"', '"tts": "spoken_raw"'),
])
def test_unknown_or_unmapped_adapter_cannot_reach_a_checked_label(tmp_path, edit):
    result = isolated_reader(tmp_path, TABS, PANELS, edits=[edit])
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'adapter' in result.stdout.lower() or 'checked' in result.stdout.lower()
    assert not (tmp_path / '.scratch/live-voice/capability-inventory.json').exists()

"""Audit-only expectations for dependable restore; not production regression tests.

Copy temporarily into backend/tests under a unique test filename, execute it
through normal pytest fixture discovery, then remove that temporary copy. Do
not load tests.conftest twice. Every project/file uses isolated test fixtures. Failures demonstrate limits in
current behavior; this file does not weaken or replace repository tests.
"""
import pypsa


def _snapshot(client, name):
    r = client.post(f'/api/projects/{name}/snapshots', json={'label': 'audit-original'})
    assert r.status_code == 200, r.text
    return r.json()['id']


def _restore(client, name, sid):
    r = client.post(f'/api/projects/{name}/snapshots/{sid}/restore')
    assert r.status_code == 200, r.text


def test_restore_safety_checkpoint_preserves_unsaved_network_edit(
    client, api_project, project_storage_dir
):
    name = api_project('audit-unsaved')
    sid = _snapshot(client, name)
    bus = client.get('/api/network/buses').json()[0]
    changed_x = float(bus.get('x') or 0) + 25.0
    r = client.put(f"/api/network/buses/{bus['name']}", json={**bus, 'x': changed_x})
    assert r.status_code == 200, r.text
    live = client.get('/api/network/buses').json()
    assert next(b for b in live if b['name'] == bus['name'])['x'] == changed_x
    _restore(client, name, sid)
    rows = client.get(f'/api/projects/{name}/snapshots').json()
    safety = next(s for s in rows if s['label'] == 'before-restore')
    captured = pypsa.Network(project_storage_dir(name) / 'snapshots' / safety['id'] / 'network.nc')
    assert float(captured.buses.loc[bus['name'], 'x']) == changed_x, (
        'The advertised safety snapshot did not capture the unsaved live edit')


def test_restore_removes_stress_settings_absent_at_checkpoint(
    client, api_project, project_storage_dir
):
    name = api_project('audit-new-sidecar')
    before = client.get(f'/api/projects/{name}/stress_scenarios').json()['scenarios']
    assert not (project_storage_dir(name) / 'adequacy_stress_scenarios.json').exists()
    sid = _snapshot(client, name)
    r = client.put(f'/api/projects/{name}/stress_scenarios', json={'scenarios': [{
        'id': 'audit-new', 'name': 'Added after checkpoint', 'kind': 'parametric',
        'frequency_per_year': 2.0, 'electrical_load_multiplier': 1.2}]})
    assert r.status_code == 200, r.text
    _restore(client, name, sid)
    after = client.get(f'/api/projects/{name}/stress_scenarios').json()['scenarios']
    assert after == before, 'Post-checkpoint settings survived restoring the old checkpoint'


def test_restore_removes_upload_directory_absent_at_checkpoint(
    client, api_project, project_storage_dir
):
    name = api_project('audit-new-directory')
    dest = project_storage_dir(name)
    assert not (dest / 'uploads').exists()
    sid = _snapshot(client, name)
    later = dest / 'uploads' / 'audit-only.txt'
    later.parent.mkdir()
    later.write_text('Created after checkpoint; no private data')
    _restore(client, name, sid)
    assert not later.exists(), 'Post-checkpoint upload survived restoring the old checkpoint'

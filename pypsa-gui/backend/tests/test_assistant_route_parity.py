"""Issue 08 slice A: GUI-route to typed-tool parity contracts (V1-V6).

The real matrix is built in memory by ``assistant_route_parity.py --check``
in the assistant-route-parity workflow; these tests pin each fail-closed rule on
small fixture apps and scans.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / 'pypsa-gui/backend/smoke'
FRONTEND = ROOT / 'pypsa-gui/frontend'
sys.path.insert(0, str(SMOKE))
import assistant_control_matrix as control  # noqa: E402
import assistant_route_parity as parity  # noqa: E402

sys.path.remove(str(SMOKE))

EMPTY = {'service_matches': [], 'limitations': [], 'needs_tool': [], 'non_mutating_routes': [],
         'binding_overrides': [], 'acknowledged_shadows': []}


def _decl(**kw) -> dict:
    return {**json.loads(json.dumps(EMPTY)), **kw}


def _scan(*bindings: tuple[str, str], problems: list[dict] | None = None) -> dict:
    return {'bindings': [{'file': 'api/x.ts', 'kind': 'client', 'method': m, 'url': u} for m, u in bindings],
            'problems': problems or []}


def _app(*routes: tuple[str, str], endpoint=None) -> FastAPI:
    app = FastAPI()
    for method, path in routes:
        app.add_api_route(path, endpoint or (lambda: None), methods=[method])
    return app


def _build(app, decl, scan):
    matrix, problems = parity.build(app=app, declarations=decl, frontend=scan)
    return {(r['method'], r['path']): r for r in matrix['rows']}, problems


# ─── V1: unresolvable bindings fail ─────────────────────────────────────────


@pytest.fixture(scope='module')
def scanner_ready() -> None:
    has_ts = (FRONTEND / 'node_modules/typescript').is_dir() and shutil.which('node')
    if not has_ts:
        if os.environ.get('REQUIRE_TS_SCANNER') == '1':
            pytest.fail('REQUIRE_TS_SCANNER=1 but node or pypsa-gui/frontend/node_modules is missing')
        pytest.skip('TypeScript scanner needs node and npm ci in pypsa-gui/frontend')


def test_scanner_reports_unresolvable_fetch_eventsource_and_resolves_href(tmp_path, scanner_ready):
    (tmp_path / 'api').mkdir()
    (tmp_path / 'api/client.ts').write_text(
        "import axios from 'axios'\nexport const client = axios.create({ baseURL: '/api' })\n")
    (tmp_path / 'a.tsx').write_text(
        "import { client } from './api/client'\n"
        "export const ok = (n: string) => client.post(`/projects/${encodeURIComponent(n)}/uploads`)\n"
        "export const f = (u: string) => fetch(u)\n"
        "export const es = (u: string) => new EventSource(u)\n"
        "const blob = (n: string, id: string) => `/api/projects/${n}/uploads/${id}/blob`\n"
        "export const l = (n: string, id: string) => <a href={blob(n, id)}>x</a>\n")
    scan = parity.scan_frontend(tmp_path)
    got = {(b['method'], b['url']) for b in scan['bindings']}
    assert ('POST', '/api/projects/{}/uploads') in got
    assert ('GET', '/api/projects/{}/uploads/{}/blob') in got
    reasons = {p['call']: p['reason'] for p in scan['problems'] if p['file'] == 'a.tsx'}
    assert 'cannot resolve' in reasons['fetch(u)']
    assert 'cannot resolve' in reasons['new EventSource(u)']


def test_unresolved_binding_fails_until_an_override_attributes_it():
    problem = {'file': 'api/x.ts', 'call': 'fetch(url)', 'reason': 'cannot resolve URL expression url'}
    app = _app(('GET', '/api/things'))
    _, problems = _build(app, _decl(), _scan(problems=[problem]))
    assert any("'fetch(url)': cannot resolve" in p for p in problems)
    override = {'file': 'api/x.ts', 'call': 'fetch(url)', 'routes': [['GET', '/api/things']], 'reason': 't'}
    rows, problems = _build(app, _decl(binding_overrides=[override],
                                       needs_tool=[{'method': 'GET', 'path': '/api/things', 'family': 'projects'}]),
                            _scan(problems=[problem]))
    assert problems == [] and rows[('GET', '/api/things')]['class'] == 'needs-tool'


def test_override_to_a_missing_route_and_an_unused_override_fail():
    override = {'file': 'api/x.ts', 'call': 'fetch(url)', 'routes': [['GET', '/api/gone']], 'reason': 't'}
    problem = {'file': 'api/x.ts', 'call': 'fetch(url)', 'reason': 'cannot resolve'}
    _, problems = _build(_app(('GET', '/api/things')), _decl(binding_overrides=[override]),
                         _scan(problems=[problem]))
    assert any('does not exist' in p for p in problems)
    _, problems = _build(_app(('GET', '/api/things')), _decl(binding_overrides=[override]), _scan())
    assert any('stale binding override' in p for p in problems)


# ─── V2: segment matching, ambiguity, wrong method ─────────────────────────


ROUTES = [('GET', '/api/p/library'), ('GET', '/api/p/{sid}'), ('POST', '/api/q/{x}')]


def test_no_route_and_wrong_method_fail():
    assert parity.match_binding({'method': 'GET', 'url': '/api/z/{}'}, ROUTES)[1] == 'no route matches'
    hit, why = parity.match_binding({'method': 'GET', 'url': '/api/q/{}'}, ROUTES)
    assert hit is None and "['POST'] but GUI uses GET" in why


def test_earlier_literal_route_shadowing_a_gui_parameter_is_ambiguous():
    hit, why = parity.match_binding({'method': 'GET', 'url': '/api/p/{}'}, ROUTES)
    assert hit is None and why.startswith('ambiguous')
    assert parity.match_binding({'method': 'GET', 'url': '/api/p/library'}, ROUTES)[0] == ROUTES[0]


def test_acknowledged_shadow_must_name_exactly_the_shadowing_set():
    b = {'method': 'GET', 'url': '/api/p/{}'}
    assert parity.match_binding(b, ROUTES, frozenset({'/api/p/library'}))[0] == ROUTES[1]
    hit, why = parity.match_binding(b, ROUTES, frozenset({'/api/p/library', '/api/p/other'}))
    assert hit is None and why.startswith('ambiguous')
    hit, why = parity.match_binding({'method': 'POST', 'url': '/api/q/{}'}, ROUTES, frozenset({'/api/q/old'}))
    assert hit is None and 'stale acknowledged shadows' in why


def test_unused_acknowledged_shadow_is_stale():
    decl = _decl(acknowledged_shadows=[{'method': 'GET', 'url': '/api/p/{}', 'shadows': ['/api/p/library'],
                                        'reason': 't'}])
    _, problems = _build(_app(('GET', '/api/p/library')), decl, _scan())
    assert any('stale acknowledged_shadows' in p for p in problems)


# ─── V3: service-call matches only through the allowlist ────────────────────


def _shares_campus_state(name: str):
    from services import campus_electrical_service  # noqa: PLC0415
    return campus_electrical_service.get_state(name)


def test_service_call_match_needs_the_allowlist():
    app = _app(('GET', '/api/test/{name}/state'), endpoint=_shares_campus_state)
    scan = _scan(('GET', '/api/test/{}/state'))
    rows, problems = _build(app, _decl(), scan)
    assert rows[('GET', '/api/test/{name}/state')]['class'] == 'unclassified'
    assert any('unclassified GUI route' in p for p in problems)
    allow = [{'service': 'services.campus_electrical_service:get_state'}]
    rows, problems = _build(app, _decl(service_matches=allow), scan)
    row = rows[('GET', '/api/test/{name}/state')]
    assert problems == [] and row['class'] == 'tool' and row['via'] == 'service_call' and row['tools']


async def _shares_campus_state_off_the_loop(name: str):
    from starlette.concurrency import run_in_threadpool  # noqa: PLC0415

    from services import campus_electrical_service  # noqa: PLC0415
    return await run_in_threadpool(campus_electrical_service.get_state, name)


def test_a_service_handed_to_run_in_threadpool_is_the_routes_call():
    app = _app(('GET', '/api/test/{name}/state'), endpoint=_shares_campus_state_off_the_loop)
    allow = [{'service': 'services.campus_electrical_service:get_state'}]
    rows, problems = _build(app, _decl(service_matches=allow), _scan(('GET', '/api/test/{}/state')))
    row = rows[('GET', '/api/test/{name}/state')]
    assert problems == [] and row['class'] == 'tool' and row['via'] == 'service_call' and row['tools']


def test_unused_service_match_is_stale():
    _, problems = _build(_app(), _decl(service_matches=[{'service': 'services.nowhere:fn'}]), _scan())
    assert any('stale service_matches entry services.nowhere:fn' in p for p in problems)


# ─── V4: a read-tier tool never covers a mutating route ─────────────────────


def test_read_tier_tool_does_not_cover_a_mutating_route_unless_declared_non_mutating():
    # validate_network is read-tier on the preflight POST (it solves nothing).
    # solve_queue_clear_finished was the example until B3 made it write-tier.
    key = ('POST', '/api/simulation/preflight')
    from harness.catalogue import safety_tier_for  # noqa: PLC0415
    assert safety_tier_for('validate_network') == 'read'
    rows, _ = _build(_app(key), _decl(), _scan(key))
    assert rows[key]['class'] == 'unclassified'
    rows, problems = _build(_app(key), _decl(non_mutating_routes=[{'method': key[0], 'path': key[1]}]), _scan(key))
    assert problems == [] and rows[key]['tools'] == ['validate_network']
    _, problems = _build(_app(), _decl(non_mutating_routes=[{'method': key[0], 'path': key[1]}]), _scan())
    assert any('stale non_mutating_routes' in p for p in problems)


# ─── V5: closed reasons/families and stale declarations ─────────────────────


@pytest.mark.parametrize('decl, expected', [
    ({'limitations': [{'method': 'GET', 'path': '/api/things', 'reason': 'too hard'}]}, "reason 'too hard'"),
    ({'needs_tool': [{'method': 'GET', 'path': '/api/things', 'family': 'misc'}]}, "family 'misc'"),
    ({'limitations': [{'method': 'GET', 'path': '/api/things', 'reason': 'admin'}],
      'needs_tool': [{'method': 'GET', 'path': '/api/things', 'family': 'projects'}]}, 'both needs-tool and limitation'),
    ({'needs_tool': [{'method': 'GET', 'path': '/api/other', 'family': 'projects'}]}, 'no GUI binding reaches it'),
    ({'needs_tool': [{'method': 'GET', 'path': '/api/gone', 'family': 'projects'}]}, 'route gone'),
])
def test_invalid_or_stale_declarations_fail(decl, expected):
    app = _app(('GET', '/api/things'), ('GET', '/api/other'))
    _, problems = _build(app, _decl(**decl), _scan(('GET', '/api/things')))
    assert any(expected in p for p in problems), problems


@pytest.mark.parametrize('name, row', [
    ('needs_tool', {'family': 'projects'}),
    ('limitations', {'reason': 'admin'}),
])
def test_declaration_for_a_tool_covered_route_is_stale_but_passes(name, row):
    # The PR that serves a route leaves its declaration (no two PRs edit the same
    # lines); the matrix lists it for the sweep, and --check still passes.
    key = ('GET', '/api/projects/{name}/uploads')  # list_uploads declares it
    decl = _decl(**{name: [{'method': key[0], 'path': key[1], **row}]})
    matrix, problems = parity.build(app=_app(key), declarations=decl, frontend=_scan(('GET', '/api/projects/{}/uploads')))
    rows = {(r['method'], r['path']): r for r in matrix['rows']}
    assert rows[key]['class'] == 'tool' and 'list_uploads' in rows[key]['tools']
    assert not problems
    assert matrix['stale_declarations'] == [{'method': key[0], 'path': key[1], 'tools': rows[key]['tools']}]


def test_a_route_declared_twice_fails():
    row = {'method': 'GET', 'path': '/api/things', 'family': 'projects'}
    _, problems = _build(_app(('GET', '/api/things')), _decl(needs_tool=[row, dict(row, note='again')]),
                         _scan(('GET', '/api/things')))
    assert any('declared more than once' in p for p in problems), problems


def test_declarations_merge_fragments_from_the_d_directory(tmp_path):
    base = tmp_path / 'decl.json'
    base.write_text(json.dumps(_decl(needs_tool=[{'method': 'GET', 'path': '/a', 'family': 'projects'}])))
    (tmp_path / 'decl.d').mkdir()
    (tmp_path / 'decl.d/claude-x.json').write_text(json.dumps(
        {'needs_tool': [{'method': 'GET', 'path': '/b', 'family': 'projects'}]}))
    assert [d['path'] for d in parity.load_declarations(base)['needs_tool']] == ['/a', '/b']


def test_families_match_the_capability_matrix_journeys():
    slugs = {j['journey_id'].removeprefix('manifest:') for j in control.FAMILY_JOURNEYS}
    assert set(parity.FAMILIES) == slugs


def test_committed_declarations_use_closed_reasons_and_families():
    decl = parity.load_declarations()
    assert {d['reason'] for d in decl['limitations']} <= set(parity.LIMITATION_REASONS)
    assert {d['family'] for d in decl['needs_tool']} <= set(parity.FAMILIES)


# ─── V6: deterministic output, side-effect-free import ──────────────────────


def test_output_is_deterministic():
    app = _app(('GET', '/api/things'), ('GET', '/api/projects/{name}/uploads'))
    decl = _decl(needs_tool=[{'method': 'GET', 'path': '/api/things', 'family': 'projects'}])
    scan = _scan(('GET', '/api/things'), ('GET', '/api/projects/{}/uploads'))
    first, _ = parity.build(app=app, declarations=decl, frontend=scan)
    second, _ = parity.build(app=app, declarations=decl, frontend=json.loads(json.dumps(scan)))
    assert parity._dump(first) == parity._dump(second)
    assert 'generated_at' not in first


def test_importing_the_generator_does_not_import_the_app():
    code = ('import sys; sys.path.insert(0, sys.argv[1]); import assistant_route_parity; '
            "assert 'main' not in sys.modules and 'fastapi' not in sys.modules")
    proc = subprocess.run([sys.executable, '-c', code, str(SMOKE)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr

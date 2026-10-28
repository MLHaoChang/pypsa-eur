"""Every live method/path has one explicit chat disposition (LC-22/task E).

SERVED_BY_TOOL records shared services, not identical payloads or permission
requirements: tools still enforce their own ACL, lock, confirmation and limits.
No prefix-based exemption or auto-populated gap hides a newly added route.
"""
from collections import defaultdict

import main
from harness.catalogue import TOOLS, TOOL_ROUTES
from routers import network_library_reference as reference_routes
from services.not_via_chat import NOT_VIA_CHAT
from tests.test_write_surface_lock_policy import _walk_routes

# D declares preview_router when moving this one known read route. Select the
# expected contract from that declaration, never from live route discovery.
# Both exact source states are supported; the identity tripwire below rejects
# missing, duplicate, mis-mounted or unrelated handlers at these paths.
_LIBRARY_PREVIEW_PATH = (
    '/api/library_reference/preview' if hasattr(reference_routes, 'preview_router')
    else '/api/network/library_reference/preview'
)

# Audited against chat_tools dispatchers and routers/gridspine.py: same service,
# target project and arguments. Bundle delivery returns a link to the same file.
# Campus wrappers share campus_electrical_service/campus_island_* and grid_readiness.
# Cuts status is a field in campus_island_cuts; GFM card is the native edit preview.
# get_milp versus milp_job_status are NOT the same service; keep that route a gap.
# Result readers share the native payload builders; tool outputs are bounded.
# Preview tools use the same plan_active; apply_library_reference's confirmation
# uses the same edit_card.preview as the HTTP preview (preview_library_reference).
# Settings selects an existing profile only; secret editing stays NOT_VIA_CHAT.
# get_file_delivery authorizes the upload and returns its exact native blob link.
SERVED_BY_TOOL: dict[tuple[str, str], str] = {
    ('GET', '/api/profiles/schema'): 'get_profile_parameters',
    ('GET', '/api/campus-electrical/{name}'): 'campus_get_study',
    ('GET', '/api/campus-electrical/{name}/grid-codes'): 'campus_list_grid_codes',
    ('GET', '/api/campus-electrical/{name}/grid-readiness'): 'campus_grid_readiness',
    ('GET', '/api/campus-electrical/{name}/island'): 'campus_island_config',
    ('GET', '/api/campus-electrical/{name}/island/cuts'): 'campus_island_cuts',
    ('GET', '/api/campus-electrical/{name}/island/cuts/apply'): 'campus_island_cuts',
    ('GET', '/api/campus-electrical/{name}/island/gfm-settings'): 'campus_island_gfm_settings',
    ('GET', '/api/campus-electrical/{name}/library'): 'campus_get_library',
    ('GET', '/api/gridspine/{name}/bundles/{hour}'): 'gridspine_export_handoff_bundle',
    ('GET', '/api/gridspine/{name}/capacity'): 'gridspine_get_capacity',
    ('GET', '/api/gridspine/{name}/config'): 'gridspine_get_config',
    ('GET', '/api/gridspine/{name}/connection'): 'gridspine_get_connection_assessments',
    ('GET', '/api/gridspine/{name}/figures/{hour}/{figure}'): 'gridspine_fetch_result_figure',
    ('GET', '/api/gridspine/{name}/ledger'): 'gridspine_get_assumption_ledger',
    ('GET', '/api/gridspine/{name}/readback'): 'gridspine_get_readback',
    ('GET', '/api/gridspine/{name}/snapshots'): 'gridspine_list_ranked_snapshots',
    ('GET', '/api/gridspine/{name}/status'): 'gridspine_get_stage_status',
    ('GET', '/api/projects/{name}/uploads/{file_id}/blob'): 'get_file_delivery',
    ('GET', '/api/results/asset/{component_class}/{name}'): 'get_asset_results',
    ('GET', '/api/results/asset/{component_class}/{name}/export.xlsx'): 'export_asset_results',
    ('POST', '/api/campus-electrical/{name}/draft'): 'campus_draft_campus',
    ('POST', '/api/campus-electrical/{name}/grid-codes/documents/{document_id}/extract'): 'campus_extract_grid_code',
    ('POST', '/api/campus-electrical/{name}/island/cuts/apply'): 'campus_apply_island_cuts',
    ('POST', '/api/campus-electrical/{name}/island/gfm-settings'): 'campus_recommend_gfm_settings',
    ('POST', '/api/campus-electrical/{name}/island/gfm-settings/apply'): 'campus_apply_gfm_settings',
    ('POST', '/api/campus-electrical/{name}/island/study'): 'campus_run_island_study',
    ('POST', '/api/campus-electrical/{name}/run'): 'campus_run_study',
    ('POST', '/api/chat/settings/llm/active'): 'set_active_profile',
    ('POST', '/api/gridspine/projects'): 'gridspine_create_study',
    ('POST', '/api/gridspine/{name}/capacity'): 'gridspine_compute_capacity',
    ('POST', '/api/gridspine/{name}/connection'): 'gridspine_assess_connection',
    ('POST', '/api/gridspine/{name}/dispatch-source'): 'gridspine_set_dispatch_source',
    ('POST', '/api/gridspine/{name}/run'): 'gridspine_run_pipeline',
    ('PUT', '/api/campus-electrical/{name}/island'): 'campus_set_island_config',
    ('PUT', '/api/campus-electrical/{name}/library'): 'campus_set_library',
    ('PUT', '/api/gridspine/{name}/config'): 'gridspine_update_config',
    ('PUT', '/api/gridspine/{name}/templates/{unit_id}/{param}'): 'gridspine_edit_template_param',
}

UI_INTERNAL: dict[str, dict] = {
    'spa': {
        "reason": "Serves the built frontend's pages.",
        "routes": frozenset({
            ('GET', '/{full_path:path}'),
        }),
    },
    'health': {
        "reason": 'Liveness probe for the desktop shell and CI.',
        "routes": frozenset({
            ('GET', '/api/health'),
        }),
    },
    'chat_panel': {
        "reason": 'The chat panel itself: stream, cards, history, checked-answer records and Activity.',
        "routes": frozenset({
            ('GET', '/api/chat/access'),
            ('GET', '/api/chat/activity'),
            ('GET', '/api/chat/answers/{answer_id}'),
            ('GET', '/api/chat/answers/{answer_id}/export'),
            ('GET', '/api/chat/decisions/{decision_id}'),
            ('GET', '/api/chat/evidence/{evidence_id}'),
            ('GET', '/api/chat/export'),
            ('GET', '/api/chat/external/pending'),
            ('GET', '/api/chat/health'),
            ('GET', '/api/chat/history'),
            ('GET', '/api/chat/metrics'),
            ('GET', '/api/chat/navigation/{action_id}'),
            ('GET', '/api/chat/operations/{operation_id}'),
            ('GET', '/api/chat/profiles'),
            ('GET', '/api/chat/reliability/capabilities'),
            ('GET', '/api/chat/reliability/context'),
            ('GET', '/api/chat/reliability/contracts'),
            ('GET', '/api/chat/skills'),
            ('GET', '/api/chat/workflows'),
            ('GET', '/api/chat/{session_id}/continuations'),
            ('GET', '/api/chat/{session_id}/progress'),
            ('POST', '/api/chat/import'),
            ('POST', '/api/chat/operations/{operation_id}/reconcile'),
            ('POST', '/api/chat/skills/reload'),
            ('POST', '/api/chat/stream'),
            ('POST', '/api/chat/ui-ack'),
            ('POST', '/api/chat/{session_id}/abort'),
            ('POST', '/api/chat/{session_id}/confirm'),
            ('POST', '/api/chat/{session_id}/continuations/{job_id}/dismiss'),
            ('POST', '/api/chat/{session_id}/goal'),
            ('POST', '/api/chat/{session_id}/rewind'),
        }),
    },
    'durable_conversations': {
        "reason": "The chat panel's own thread persistence and rebinding.",
        "routes": frozenset({
            ('GET', '/api/durable-conversations/{thread_id}'),
            ('POST', '/api/durable-conversations'),
            ('POST', '/api/durable-conversations/{thread_id}/checkpoint'),
            ('POST', '/api/durable-conversations/{thread_id}/rebind'),
            ('POST', '/api/durable-conversations/{thread_id}/resume'),
        }),
    },
    'mcp': {
        "reason": 'MCP protocol endpoint; external agents use the same catalogue through it.',
        "routes": frozenset({
            ('DELETE', '/api/mcp'),
            ('GET', '/api/mcp'),
            ('POST', '/api/mcp'),
        }),
    },
    'editor_lock': {
        "reason": "The browser tab's editing lock; the assistant acts under that tab's lock.",
        "routes": frozenset({
            ('DELETE', '/api/projects/{project_id}/lock'),
            ('POST', '/api/projects/{project_id}/lock'),
            ('POST', '/api/projects/{project_id}/lock/heartbeat'),
        }),
    },
    'log_feeds': {
        "reason": 'Solver log feeds for the log panel; the assistant follows jobs with wait_for_job.',
        "routes": frozenset({
            ('GET', '/api/simulation/log_stream'),
            ('GET', '/api/simulation/queue/{job_id}/log_stream'),
        }),
    },
    'dictation': {
        "reason": "The composer's voice input; its words arrive as the user's message (LC-07).",
        "routes": frozenset({
            ('GET', '/api/dictation/config'),
            ('POST', '/api/dictation/transcribe'),
        }),
    },
    'browser_upload': {
        "reason": "The browser's file upload and signature; the assistant uses uploads by file ID.",
        "routes": frozenset({
            ('GET', '/api/projects/{name}/uploads/{file_id}/signature'),
            ('POST', '/api/projects/{name}/uploads'),
        }),
    },
    'series_json_put': {
        "reason": 'JSON series write no screen calls now; chat writes series with upload_timeseries, which posts the file upload.',
        "routes": frozenset({
            ('PUT', '/api/network/timeseries/{component}/{attribute}'),
        }),
    },
}

# Each gap names its existing manual surface; this is an explicit reviewed baseline.
KNOWN_GAPS: dict[tuple[str, str], str] = {
    ('DELETE', '/api/projects/{name}/profiles/weather'): 'Time series panel',
    ('GET', '/api/campus-electrical/{name}/owner-assets'): 'Campus electrical panel',
    ('GET', '/api/guides'): 'Help, guides panel',
    ('GET', '/api/projects/{name}/reports/{report_id}/figures/{figure_id}'): 'Reports panel',
    ('GET', '/api/projects/{name}/studies/{study_id}/ledger.csv'): 'Decision studies panel',
    ('GET', '/api/projects/{name}/studies/{study_id}/options/{option_id}/case.xlsx'): 'Decision studies panel',
    ('GET', '/api/results/energy_flows.csv'): 'Results panel',
    ('GET', '/api/results/investment_case/export.xlsx'): 'Results panel',
    ('GET', '/api/results/process_uc_check'): 'Results panel',
    ('GET', '/api/results/process_view'): 'Results panel',
    ('POST', '/api/campus-electrical/{name}/island/preset'): 'Campus electrical panel',
    ('POST', '/api/projects/{name}/flow/ack'): 'Projects page / Workspace panel',
    ('POST', '/api/projects/{name}/profiles/shape'): 'Time series panel',
}
_KNOWN_GAPS_CEILING = 70


def _routes():
    return {(method, path): endpoint for path, methods, endpoint in _walk_routes(main.app.routes)
            for method in methods if method not in {'HEAD', 'OPTIONS'}}


def _buckets():
    buckets = defaultdict(set)
    for rows in TOOL_ROUTES.values():
        for row in rows:
            if isinstance(row, tuple) and len(row) == 2:
                buckets[row].add('tool_routes')
    for route in SERVED_BY_TOOL:
        buckets[route].add('served_by_tool')
    for entry in NOT_VIA_CHAT:
        for route in entry['routes']:
            buckets[route].add('not_via_chat:' + entry['id'])
    for name, entry in UI_INTERNAL.items():
        for route in entry['routes']:
            buckets[route].add('ui_internal:' + name)
    for route in KNOWN_GAPS:
        if not buckets[route] & {'tool_routes', 'served_by_tool'}:
            buckets[route].add('known_gap')
    return buckets


def stale_known_gaps() -> list[tuple[str, str]]:
    """KNOWN_GAPS rows a tool now serves. They pass: the PR that serves a route
    leaves its row, so two such PRs never edit the same lines. The coordinator's
    sweep deletes them and lowers the ceiling (smoke/prune_stale_declarations.py)."""
    served = {row for rows in TOOL_ROUTES.values() for row in rows
              if isinstance(row, tuple) and len(row) == 2} | set(SERVED_BY_TOOL)
    return sorted(set(KNOWN_GAPS) & served)


def _unclassified(routes):
    buckets = _buckets()
    return sorted(r for r in routes if not buckets[r])


def _classification_message(missing, routes):
    rows = []
    for method, path in missing:
        endpoint = routes.get((method, path))
        where = f'{endpoint.__module__}.{endpoint.__name__}' if endpoint else 'planted endpoint'
        rows.append(f'{method} {path} ({where})')
    return '\n'.join(rows) + '''
Give each route exactly one chat disposition:
  a chat tool calls it over HTTP      -> its tool's entry in harness/catalogue.py TOOL_ROUTES
  a _service_call_ tool reaches it    -> SERVED_BY_TOOL in tests/test_route_chat_disposition.py
  chat must refuse or point elsewhere -> an entry's routes in services/not_via_chat.py (reason + page)
  only the app's own screens call it  -> UI_INTERNAL in tests/test_route_chat_disposition.py (one-line reason)
KNOWN_GAPS only shrinks: a new route without a tool is declared in services/not_via_chat.py as "unbuilt".
'''


def test_the_route_enumeration_is_complete():
    methods = {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS', 'TRACE'}
    operations = {(method.upper(), path) for path, ops in main.app.openapi()['paths'].items()
                  for method in ops if method.upper() in methods - {'HEAD', 'OPTIONS'}}
    routes = _routes()
    assert operations <= routes.keys(), operations - routes.keys()
    assert len(routes) > 400


def test_every_route_has_exactly_one_chat_disposition():
    routes = _routes()
    missing = _unclassified(routes)
    assert not missing, _classification_message(missing, routes)
    duplicate = {r: sorted(_buckets()[r]) for r in routes if len(_buckets()[r]) > 1}
    assert not duplicate, f'Routes in more than one chat disposition: {duplicate}'


def test_no_disposition_names_a_missing_route():
    declared = set(SERVED_BY_TOOL) | set(KNOWN_GAPS)
    declared.update(r for entry in NOT_VIA_CHAT for r in entry['routes'])
    declared.update(r for entry in UI_INTERNAL.values() for r in entry['routes'])
    assert not declared - _routes().keys(), f'Stale route dispositions: {sorted(declared - _routes().keys())}'


def test_served_by_names_real_tools():
    assert set(SERVED_BY_TOOL.values()) <= {t['name'] for t in TOOLS}


def test_ui_internal_stays_small():
    assert len(UI_INTERNAL) <= 11 and 'series_json_put' in UI_INTERNAL
    assert all(20 <= len(entry['reason']) <= 160 for entry in UI_INTERNAL.values())


def test_known_gaps_only_shrink():
    assert len(KNOWN_GAPS) <= _KNOWN_GAPS_CEILING
    assert list(KNOWN_GAPS) == sorted(KNOWN_GAPS), 'keep KNOWN_GAPS sorted by (method, path)'


def test_a_known_gap_a_tool_now_serves_passes_as_stale(monkeypatch):
    route = next(iter(KNOWN_GAPS))
    monkeypatch.setitem(SERVED_BY_TOOL, route, TOOLS[0]['name'])
    assert _buckets()[route] == {'served_by_tool'}
    assert route in stale_known_gaps()
    routes = _routes()
    assert not _unclassified(routes)


def test_a_planted_route_is_reported():
    routes = {**_routes(), ('POST', '/api/planted/route'): None}
    missing = _unclassified(routes)
    assert missing == [('POST', '/api/planted/route')]
    message = _classification_message(missing, routes)
    assert 'POST /api/planted/route' in message
    assert 'services/not_via_chat.py' in message and 'UI_INTERNAL' in message


def test_library_preview_matches_the_declared_native_handler():
    approved_paths = {'/api/network/library_reference/preview', '/api/library_reference/preview'}
    # Inspect walker rows before a dict could collapse duplicate registrations.
    candidates = [(path, endpoint) for path, methods, endpoint in _walk_routes(main.app.routes)
                  if 'POST' in (methods or ()) and path in approved_paths]
    assert len(candidates) == 1, 'Mount exactly one approved library-reference preview route'
    assert candidates[0][0] == _LIBRARY_PREVIEW_PATH
    assert candidates[0][1] is reference_routes.preview_library_reference

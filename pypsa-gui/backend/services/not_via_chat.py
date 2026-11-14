"""Unsupported chat operations: advisory guidance, never dispatch or access authority.

Routes are explicit so the live-route disposition test detects drift. Broad
provider names are not keywords: switching an existing profile is supported.
"""
from __future__ import annotations

import re

DISPOSITIONS = ("out of chat", "manual", "unbuilt", "unbuilt; owner to decide later")

NOT_VIA_CHAT: tuple[dict, ...] = (
    {
        'id': 'admin_users_and_orgs',
        'title': 'Admin accounts and organizations',
        'disposition': 'out of chat',
        'reason': 'Account and organization administration is outside chat for security. An authorized administrator must use the Admin page.',
        'page': 'Admin page (/admin)',
        'panel': None,
        'keywords': frozenset({
            'account',
            'accounts',
            'admin',
            'colleague',
            'colleagues',
            'invite',
            'org',
            'organisation',
            'organisations',
            'organization',
            'organizations',
            'smtp',
            'users',
        }),
        'routes': frozenset({
            ('GET', '/api/admin/email/status'),
            ('GET', '/api/admin/legacy-projects'),
            ('GET', '/api/admin/organizations'),
            ('GET', '/api/admin/users'),
            ('POST', '/api/admin/email/test'),
            ('POST', '/api/admin/legacy-projects/{legacy_name}/claim'),
            ('POST', '/api/admin/organizations'),
            ('POST', '/api/admin/users'),
            ('POST', '/api/admin/users/{user_id}/resend-set-password'),
        }),
    },
    {
        'id': 'legacy_project_import',
        'title': 'Claim or import a legacy project folder',
        'disposition': 'out of chat',
        'reason': 'Claiming a legacy folder changes project ownership and is outside chat. Use the Projects page to review and import it.',
        'page': 'Projects page (/projects)',
        'panel': None,
        'keywords': frozenset({
            'claim',
            'folder',
            'legacy',
            'unclaimed',
        }),
        'routes': frozenset({
            ('GET', '/api/projects/unclaimed'),
            ('POST', '/api/projects/import-folder'),
            ('POST', '/api/projects/unclaimed/{legacy_name}/import'),
        }),
    },
    {
        'id': 'sign_in_and_password',
        'title': 'Sign-in and password changes',
        'disposition': 'out of chat',
        'reason': 'Sign-in and password changes handle authentication secrets and must be completed on the Sign-in page, outside chat.',
        'page': 'Sign-in page',
        'panel': None,
        'keywords': frozenset({
            'login',
            'logout',
            'password',
            'passwords',
            'sign',
            'signin',
        }),
        'routes': frozenset({
            ('GET', '/api/auth/me'),
            ('POST', '/api/auth/forgot-password'),
            ('POST', '/api/auth/login'),
            ('POST', '/api/auth/logout'),
            ('POST', '/api/auth/reset-password'),
            ('POST', '/api/auth/set-password'),
        }),
    },
    {
        'id': 'project_members',
        'title': 'Project sharing and membership',
        'disposition': 'unbuilt; owner to decide later',
        'reason': 'Sharing a project is not available from chat yet. Add or remove its members in the Workspace panel.',
        'page': 'Workspace panel, project members',
        'panel': 'workspace',
        'keywords': frozenset({
            'collaborator',
            'collaborators',
            'colleague',
            'colleagues',
            'member',
            'members',
            'share',
            'shared',
            'sharing',
        }),
        'routes': frozenset({
            ('GET', '/api/auth/org-members'),
            ('GET', '/api/projects/{name}/members'),
            ('PUT', '/api/projects/{name}/members'),
        }),
    },
    {
        'id': 'assistant_keys_and_profiles',
        'title': 'Assistant API credentials and profile settings',
        'disposition': 'out of chat',
        'reason': 'API credentials and profile editing handle secrets and stay in Settings. Chat can switch an existing profile, but cannot reveal or edit its credentials.',
        'page': 'Settings panel',
        'panel': 'settings',
        'keywords': frozenset({
            'api',
            'apikey',
            'reveal',
            'secret',
            'secrets',
        }),
        'routes': frozenset({
            ('DELETE', '/api/chat/settings/api-key'),
            ('DELETE', '/api/chat/settings/llm/profiles/{profile_id}'),
            ('DELETE', '/api/chat/settings/llm/profiles/{profile_id}/key'),
            ('GET', '/api/chat/settings/api-key'),
            ('GET', '/api/chat/settings/llm'),
            ('GET', '/api/chat/settings/mcp/servers'),
            ('PUT', '/api/chat/settings/mcp/servers/{server_id}'),
            ('DELETE', '/api/chat/settings/mcp/servers/{server_id}'),
            ('POST', '/api/chat/settings/mcp/servers/{server_id}/test'),
            ('PUT', '/api/chat/settings/mcp/servers/{server_id}/keys/{key_name}'),
            ('DELETE', '/api/chat/settings/mcp/servers/{server_id}/keys/{key_name}'),
            ('GET', '/api/local-settings'),
            ('POST', '/api/chat/settings/llm/profiles/{profile_id}/test'),
            ('POST', '/api/local-settings/reveal-log'),
            ('PUT', '/api/chat/settings/api-key'),
            ('PUT', '/api/chat/settings/llm/profiles/{profile_id}'),
            ('PUT', '/api/chat/settings/llm/profiles/{profile_id}/key'),
            ('POST', '/api/chat/settings/llm/roles'),
            ('PUT', '/api/local-settings/anthropic-key'),
        }),
    },
    {
        'id': 'assistant_access_rule',
        'title': 'Chat access restrictions',
        'disposition': 'out of chat',
        'reason': 'Chat cannot change its own access restrictions. Review and edit your personal access rule in the Chat panel access settings.',
        'page': 'Chat panel, access settings',
        'panel': 'chat',
        'keywords': frozenset({
            'autoapprove',
            'permission',
            'permissions',
            'readonly',
            'restriction',
            'restrictions',
        }),
        'routes': frozenset({
            ('PUT', '/api/chat/restrictions'),
        }),
    },
    {
        'id': 'solve_queue_instance_controls',
        'title': 'Pause or resume the solve queue',
        'disposition': 'out of chat',
        'reason': 'Pausing or resuming the whole solve queue affects the instance and needs super-admin access. Use the Solve queue panel; chat can pause its own goal.',
        'page': 'Solve queue panel',
        'panel': 'solveQueue',
        'keywords': frozenset({
            'halt',
            'pause',
            'paused',
            'unpause',
        }),
        'routes': frozenset({
            ('POST', '/api/simulation/queue/pause'),
            ('POST', '/api/simulation/queue/resume'),
        }),
    },
    {
        'id': 'map_layout_and_sites',
        'title': 'Map layout and site thumbnails',
        'disposition': 'manual',
        'reason': 'Arrange thumbnails and map geometry in Map. Chat can geocode a place, read and replace the site document and fetch a site\'s context, but map layout and thumbnails are manual operations.',
        'page': 'Map',
        'panel': 'map',
        'keywords': frozenset({
            'layout',
            'thumbnail',
            'thumbnails',
        }),
        'routes': frozenset({
            ('DELETE', '/api/projects/{name}/sites/{site_id}/context'),
            ('GET', '/api/geocode/status'),
            ('GET', '/api/projects/{name}/map_layout'),
            ('GET', '/api/projects/{name}/sites/{site_id}/thumbnail'),
            ('PUT', '/api/projects/{name}/map_layout'),
            ('PUT', '/api/projects/{name}/sites/{site_id}/thumbnail'),
        }),
    },
    {
        'id': 'accept_assumed_defaults',
        'title': 'Keep all assumed defaults',
        'disposition': 'unbuilt',
        'reason': 'Confirming every assumed default at once is not available from chat yet. Use Keep all defaults on the Assumptions card of the section in Guided.',
        'page': 'Guided sections',
        'panel': None,
        'keywords': frozenset({
            'assumptions',
            'defaults',
        }),
        'routes': frozenset({
            ('POST', '/api/projects/{name}/flow/ack-many'),
        }),
    },
    {
        'id': 'business_case',
        'title': 'Business case preset',
        'disposition': 'out of chat',
        'reason': 'The preset fills cost defaults from a pack, so it is applied in the panel; chat sets the lines only from your figures. Use the Business case view on the Results page.',
        'page': 'Results (Investment tab)',
        'panel': None,
        'keywords': frozenset({
            'business',
            'preset',
        }),
        'routes': frozenset({
            ('GET', '/api/results/business_case/preset'),
        }),
    },
    {
        'id': 'intent_comparison',
        'title': 'Design intent comparison',
        'disposition': 'unbuilt',
        'reason': 'The design intent comparison (AS-10) runs two to five sizing loops and is not available from chat yet. Start it from the Intent comparison section of the Grid study tab.',
        'page': 'Grid study',
        'panel': None,
        'keywords': frozenset({
            'intent',
            'intents',
        }),
        'routes': frozenset({
            ('GET', '/api/projects/{name}/intent-comparison'),
            ('POST', '/api/projects/{name}/intent-comparison'),
            ('POST', '/api/projects/{name}/intent-comparison/abort'),
        }),
    },
    {
        'id': 'dc_backup',
        'title': 'Data-centre backup: battery in place of gensets',
        'disposition': 'unbuilt',
        'reason': 'The data-centre backup setting, its outage proof, cost delta and storage reserve revenue are not available from chat yet. Set and assess it in the Backup section of the Grid study tab.',
        'page': 'Grid study',
        'panel': None,
        'keywords': frozenset({
            'backup',
            'genset',
        }),
        'routes': frozenset({
            ('PUT', '/api/campus-electrical/{name}/dc-backup'),
            ('POST', '/api/campus-electrical/{name}/dc-backup/assess'),
            ('GET', '/api/results/reserves'),
        }),
    },
    {
        'id': 'dc_architecture',
        'title': 'Data-centre power architecture comparison',
        'disposition': 'unbuilt',
        'reason': 'The data-centre power-architecture comparison (AC, MV UPS, 800 VDC, solid-state transformer) is not available from chat yet. Run it in the Power architecture section of the Grid study tab.',
        'page': 'Grid study',
        'panel': None,
        'keywords': frozenset({
            'architecture',
            'vdc',
            'sst',
            'transformer',
        }),
        'routes': frozenset({
            ('POST', '/api/campus-electrical/{name}/dc-architecture/compare'),
        }),
    },
    {
        # Owner decision DC25-POINTER-1: chat points to the Plan vs operation
        # card; no typed read tool.
        'id': 'dc_operational_gap',
        'title': 'Plan-vs-operation gap of the operational run',
        'disposition': 'manual',
        'reason': 'The gap between the operational (rolling) run and the full-horizon dispatch on the same sizes is not available from chat. Open Results › Summary: the Plan vs operation card shows it.',
        'page': 'Results › Summary',
        'panel': 'results',
        'keywords': frozenset({
            'foresight',
            'lookahead',
        }),
        'routes': frozenset({
            ('GET', '/api/results/operational_gap'),
        }),
    },
    {
        # ui-modern 03: the strip is a screen layout over figures chat already
        # reads (get_results cost_breakdown and lost_load, the checked answers).
        'id': 'results_headline_strip',
        'title': 'Results headline strip',
        'disposition': 'manual',
        'reason': "The Results headline strip's own card list is not read from chat. Chat reads its figures one by one and compares a case with its base on other measures. Open Results › Summary to see the strip.",
        'page': 'Results › Summary',
        'panel': 'results',
        # 'headline' alone is in six tool descriptions (compare-runs headline
        # KPIs, …) and 'strip' alone in the chat file strip's: `matches` also
        # needs `_asks_about_the_strip`.
        'keywords': frozenset({
            'strip',
        }),
        'routes': frozenset({
            ('GET', '/api/results/headline_kpis'),
        }),
    },
    {
        'id': 'site_scene',
        'title': 'Site 3D: legacy Site adoption and results overlay',
        'disposition': 'unbuilt',
        'reason': 'Chat reads and changes 3D site buildings (site_scene_read, site_scene_prepare, site_scene_apply). Adopting a Site saved before Site 3D, and the 3D results overlay, stay in Site 3D for now.',
        'page': 'Site 3D',
        'panel': 'map',
        'keywords': frozenset({
            'scene',
        }),
        'routes': frozenset({
            ('POST', '/api/projects/{name}/sites/{site_id}/scene/adopt'),
            ('GET', '/api/projects/{name}/sites/{site_id}/scene/results'),
        }),
    },
    {
        'id': 'site_scene_export',
        'title': 'Exporting the 3D site for Blender',
        'disposition': 'manual',
        'reason': 'Exporting the 3D site downloads a file (a 3D model file and a manifest) to your computer. Use "Export for Blender" on the Site canvas.',
        'page': 'Site 3D',
        'panel': 'map',
        'keywords': frozenset({
            'blender',
            'glb',
        }),
        'routes': frozenset({
            ('POST', '/api/projects/{name}/sites/{site_id}/scene/exports'),
        }),
    },
    {
        'id': 'library_uploads_and_tariff_edit',
        'title': 'Library series sent as JSON values',
        'disposition': 'manual',
        'reason': 'A series sent as JSON values is entered in the Commercial library. From chat, upload a file and import it with import_library_series or import_meter_data.',
        'page': 'Commercial library',
        'panel': 'library',
        'keywords': frozenset({
            'json',
        }),
        'routes': frozenset({
            ('POST', '/api/library/series'),
        }),
    },
    {
        'id': 'report_chart_snapshots',
        'title': 'Saving a chart for the report',
        'disposition': 'manual',
        'reason': 'Only you can pin a chart you are looking at in Results for the report, list the pinned ones or remove one. Use "Add to report" on the chart; chat reports never carry saved charts.',
        'page': 'Results (chart "Add to report")',
        'panel': 'results',
        'keywords': frozenset({
            'pin',
            'pinned',
        }),
        'routes': frozenset({
            ('DELETE', '/api/projects/{name}/report-charts/{chart_id}'),
            ('GET', '/api/projects/{name}/report-charts'),
            ('GET', '/api/projects/{name}/report-charts/{chart_id}.png'),
            ('POST', '/api/projects/{name}/report-charts'),
        }),
    },
    {
        'id': 'power_trace_spectrum',
        'title': 'Power-trace spectrum and smoothing-tier split',
        'disposition': 'unbuilt',
        'reason': 'Reading a power trace into a PSD, f_c and the smoothing-tier split is not available from chat or a screen yet; it is an API route for now.',
        'page': 'API: POST /api/spectrum/trace',
        'panel': None,
        'keywords': frozenset({
            'psd',
            'spectrum',
        }),
        'routes': frozenset({
            ('POST', '/api/network/spectrum/tier_split'),
            ('POST', '/api/spectrum/trace'),
        }),
    },    {
        'id': 'report_comment_revisions',
        'title': 'Report comments and comment revisions',
        'disposition': 'unbuilt',
        'reason': 'Commenting on a saved report and revising it from a comment is not available from chat yet. No report screen offers it yet either; only the report comments API does.',
        'page': 'Reports',
        'panel': None,
        'keywords': frozenset({
            'comment',
            'comments',
        }),
        'routes': frozenset({
            ('GET', '/api/projects/{name}/reports/{report_id}/comments'),
            ('POST', '/api/projects/{name}/reports/{report_id}/comments'),
            ('POST', '/api/projects/{name}/reports/{report_id}/comments/{comment_id}/address'),
            ('POST', '/api/projects/{name}/reports/{report_id}/comments/{comment_id}/withdraw'),
        }),
    },
    {
        'id': 'memory_page_edits',
        'title': 'Editing, retiring or sharing saved memory',
        'disposition': 'out of chat',
        'reason': ('The assistant only proposes memory (on a card you decide) and reads it. Editing, '
                   'retiring, restoring and sharing items with your organisation are done on the '
                   'Memory page.'),
        'page': 'Memory page (account menu)',
        'panel': None,
        'keywords': frozenset({'forget', 'unremember', 'memories'}),
        'routes': frozenset({
            ('POST', '/api/memory/dismissals'),
            ('PATCH', '/api/memory/items/{item_id}'),
            ('POST', '/api/memory/items/{item_id}/{action}'),
            ('GET', '/api/memory/items/{item_id}/versions'),
        }),
    },
    {
        'id': 'memory_dream_review',
        'title': 'Reviewing your recent work for lessons',
        'disposition': 'out of chat',
        'reason': ('A review of your past chats and runs proposes lessons for you to confirm. It is '
                   'started, and its proposals confirmed one by one, on the Memory page, so it never '
                   'runs from inside a chat turn.'),
        'page': 'Memory page (account menu)',
        'panel': None,
        'keywords': frozenset({'dream', 'lessons', 'consolidate'}),
        'routes': frozenset({
            ('GET', '/api/memory/dream'),
            ('POST', '/api/memory/dream'),
            ('GET', '/api/memory/dream/settings'),
            ('PUT', '/api/memory/dream/settings'),
        }),
    },
    {
        'id': 'memory_parameter_suggestions',
        'title': 'Saving a field value for reuse, or applying a saved one',
        'disposition': 'out of chat',
        'reason': ('Save a value with the Save for reuse button beside its field. A saved value is '
                   'applied only when you pick its chip beside the same field and approve the card. '
                   'The assistant never applies one.'),
        'page': 'Asset card (Properties)',
        'panel': None,
        # 'saved' and 'reuse' alone are in project, scenario and snapshot asks:
        # `matches` also needs `_asks_for_a_saved_value` (GK B N4).
        'keywords': frozenset({'reusable', 'chip', 'chips', 'saved', 'save', 'reuse', 'remember', 'keep', 'store'}),
        'routes': frozenset({
            ('GET', '/api/memory/suggestions'),
            ('POST', '/api/memory/parameters'),
            ('POST', '/api/memory/suggestions/{item_id}/preview'),
            ('POST', '/api/network/memory_parameter/apply'),
        }),
    },
)

NOTE = ("not_via_chat lists features you cannot do from chat. Tell the user the reason and the page; "
        "if panel is set you may open it with ui_open_panel. Do not look for another tool or an API for them.")


# A saved field value. Three asks reach it:
# - a chip, or a value made "reusable";
# - using one ("saved"/"reuse" with a value or a field), unless the saved thing is a
#   scenario, run, snapshot, tariff or project;
# - keeping one: "save" or "remember" with a value or a field AND words that say the
#   value is kept for reuse: "later", "elsewhere", "reuse", or "next/another/other"
#   with "project" or "time". A bare "again", "next", "other" or "another" is
#   ordinary workflow ("solve again", "the next one", "another carrier"), and an ask
#   that also names a table, network, report or results stores that, not a value
#   (GK B N2). "Store" is a component, "keep" a constraint ("keep the capacity under
#   50 MW"), and "future" and "use ... again" describe model time, so none of them
#   counts (GK B N2 of 46).
_SAVED_VALUE_NAMES = frozenset({'reusable', 'chip', 'chips'})
_USE_WORDS = frozenset({'saved', 'reuse'})
_VALUE_WORDS = frozenset({
    'value', 'values', 'parameter', 'parameters', 'efficiency', 'capex', 'opex',
    'cost', 'costs', 'price', 'prices', 'capacity', 'lifetime',
})
_SAVED_ELSEWHERE = frozenset({
    'scenario', 'scenarios', 'run', 'runs', 'snapshot', 'snapshots', 'tariff',
    'tariffs', 'project', 'projects',
})
_KEEP_VERBS = frozenset({'save', 'remember'})
# Phrases that say a value is kept for reuse. Single words cannot: "later" and
# "next time" are also model time and plain workflow.
_REUSE_PHRASE = re.compile(
    r'\b(?:for (?:later|next time)|for (?:my |the |our )?(?:next|another|other|future) projects?'
    r'|to reuse|so i can (?:reuse|use (?:it|this|that) again)|as (?:a|the) default)\b')
# "Store" is a component, so it counts only as the opening verb, with a project or default phrase.
_STORE_PHRASE = re.compile(
    r'\b(?:for (?:my |the |our )?(?:next|another|other|future) projects?|as (?:a|the) default)\b')
_KEPT_ELSEWHERE = (_SAVED_ELSEWHERE - {'project', 'projects'}) | {
    'table', 'tables', 'network', 'networks', 'report', 'reports', 'result', 'results',
    'csv', 'chart', 'charts', 'series', 'file', 'files',
}


def _asks_for_a_saved_value(words: set[str], goal: str = '') -> bool:
    if words & _SAVED_VALUE_NAMES:
        return True
    if not words & _VALUE_WORDS:
        return False
    if words & _USE_WORDS and not words & _SAVED_ELSEWHERE:
        return True
    if words & _KEPT_ELSEWHERE:
        return False
    text = ' '.join(re.findall(r'[a-z0-9]+', goal.lower()))
    if words & (_KEEP_VERBS | {'keep'}):
        return bool(_REUSE_PHRASE.search(text))
    return text.startswith('store ') and bool(_STORE_PHRASE.search(text))


# The headline strip entry answers only what the strip itself owns: what it
# shows, why a card is missing, adding or hiding a card. Exporting, reporting
# or comparing the same figures is a chat tool's job and must not be refused.
_STRIP_NAMES = frozenset({'headline', 'kpi', 'kpis'})
_STRIP_ASKS = frozenset({'why', 'what', 'which', 'read', 'add', 'hide'})
# A destination or an action that puts the figures elsewhere: a tool does that.
_STRIP_ELSEWHERE = frozenset({
    'report', 'export', 'exported', 'excel', 'word', 'csv', 'pdf', 'workbook', 'document', 'bundle',
    'save', 'download', 'compared',
})


def _asks_about_the_strip(words: set[str]) -> bool:
    return bool(words & _STRIP_NAMES and words & _STRIP_ASKS and not words & _STRIP_ELSEWHERE)


# Only the business case's preset is out of chat (GK B (54) Q3, D8): its run, read and
# lines are tools (`run_business_case`, `get_business_case`, `set_business_case_lines`),
# so "show the business case for …" must reach them. The entry hits only on "business
# case" with a preset word.
_BUSINESS_CASE_NOUN = frozenset({'case', 'cases'})
_BUSINESS_CASE_ASKS = frozenset({'preset', 'presets', 'template', 'defaults'})


# "Business as usual" (BAU) is a scenario, not this feature (GK B (54) D3).
_BUSINESS_AS_USUAL = frozenset({'usual', 'bau'})


def _asks_for_the_business_case(words: set[str]) -> bool:
    return bool('business' in words and words & _BUSINESS_CASE_NOUN
                and words & _BUSINESS_CASE_ASKS and not words & _BUSINESS_AS_USUAL)


def matches(words: set[str], limit: int = 2, goal: str = '') -> list[dict]:
    """Keyword hits, ranked by count then declaration order; return public copies.

    A plan/goal pause is supported; do not describe it as an instance queue pause.
    As with the brief's omission of bare `resume`, this guidance is best effort.
    """
    ranked = []
    for index, entry in enumerate(NOT_VIA_CHAT):
        if (entry['id'] == 'solve_queue_instance_controls' and words & {'goal', 'task', 'plan'}
                and 'queue' not in words):
            continue
        if entry['id'] == 'report_chart_snapshots' and not words & {'chart', 'charts', 'graph', 'plot'}:
            continue  # bare `pin` also means "pin a bus or a capacity"
        if entry['id'] == 'memory_parameter_suggestions' and not _asks_for_a_saved_value(words, goal):
            continue
        if entry['id'] == 'results_headline_strip' and not _asks_about_the_strip(words):
            continue
        if entry['id'] == 'business_case' and not _asks_for_the_business_case(words):
            continue
        hits = len(words & entry['keywords'])
        if hits:
            ranked.append((-hits, index, entry))
    return [{key: e[key] for key in ('title', 'disposition', 'reason', 'page', 'panel')}
            for _, _, e in sorted(ranked)[:max(0, limit)]]

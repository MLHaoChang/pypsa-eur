"""Unsupported chat operations: advisory guidance, never dispatch or access authority.

Routes are explicit so the live-route disposition test detects drift. Broad
provider names are not keywords: switching an existing profile is supported.
"""
from __future__ import annotations

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
        'title': 'Map layout and site placement',
        'disposition': 'manual',
        'reason': 'Arrange sites, thumbnails and map geometry in Map. Chat can edit model parameters, but map layout and geocoding are manual operations.',
        'page': 'Map',
        'panel': 'map',
        'keywords': frozenset({
            'geocode',
            'geocoding',
            'layout',
            'thumbnail',
            'thumbnails',
        }),
        'routes': frozenset({
            ('DELETE', '/api/projects/{name}/sites/{site_id}/context'),
            ('GET', '/api/geocode'),
            ('GET', '/api/geocode/status'),
            ('GET', '/api/projects/{name}/map_layout'),
            ('GET', '/api/projects/{name}/sites'),
            ('GET', '/api/projects/{name}/sites/{site_id}/context'),
            ('GET', '/api/projects/{name}/sites/{site_id}/thumbnail'),
            ('POST', '/api/projects/{name}/sites/{site_id}/context'),
            ('PUT', '/api/projects/{name}/map_layout'),
            ('PUT', '/api/projects/{name}/sites'),
            ('PUT', '/api/projects/{name}/sites/{site_id}/thumbnail'),
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
        'id': 'grid_code_publish',
        'title': 'Grid-code review and publishing',
        'disposition': 'manual',
        'reason': 'Confirm limits and publish grid-code profiles in Campus electrical, Grid codes. Chat can list profiles and extract a draft from a document, but cannot publish it.',
        'page': 'Campus electrical, Grid codes',
        'panel': 'campusElectrical',
        'keywords': frozenset({
            'publish',
            'published',
        }),
        'routes': frozenset({
            ('DELETE', '/api/campus-electrical/{name}/grid-codes/documents/{document_id}'),
            ('DELETE', '/api/campus-electrical/{name}/grid-codes/drafts/{profile_id}'),
            ('DELETE', '/api/campus-electrical/{name}/grid-codes/published/{profile_id}'),
            ('GET', '/api/campus-electrical/{name}/grid-codes/drafts/{profile_id}'),
            ('GET', '/api/campus-electrical/{name}/grid-codes/published/{profile_id}'),
            ('POST', '/api/campus-electrical/{name}/grid-codes/documents'),
            ('POST', '/api/campus-electrical/{name}/grid-codes/drafts'),
            ('POST', '/api/campus-electrical/{name}/grid-codes/drafts/{profile_id}/confirm'),
            ('POST', '/api/campus-electrical/{name}/grid-codes/drafts/{profile_id}/publish'),
            ('PUT', '/api/campus-electrical/{name}/grid-codes/drafts/{profile_id}'),
        }),
    },
)

NOTE = ("not_via_chat lists features you cannot do from chat. Tell the user the reason and the page; "
        "if panel is set you may open it with ui_open_panel. Do not look for another tool or an API for them.")


def matches(words: set[str], limit: int = 2) -> list[dict]:
    """Keyword hits, ranked by count then declaration order; return public copies.

    A plan/goal pause is supported; do not describe it as an instance queue pause.
    As with the brief's omission of bare `resume`, this guidance is best effort.
    """
    ranked = []
    for index, entry in enumerate(NOT_VIA_CHAT):
        if (entry['id'] == 'solve_queue_instance_controls' and words & {'goal', 'task', 'plan'}
                and 'queue' not in words):
            continue
        hits = len(words & entry['keywords'])
        if hits:
            ranked.append((-hits, index, entry))
    return [{key: e[key] for key in ('title', 'disposition', 'reason', 'page', 'panel')}
            for _, _, e in sorted(ranked)[:max(0, limit)]]

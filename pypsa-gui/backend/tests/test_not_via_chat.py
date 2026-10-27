"""Declared unsupported operations augment discovery without granting permissions."""
import json
import re

import pytest

from harness.catalogue import SAFETY_PANEL_ENUM, TOOLS
from harness.session import ChatSession
from services import chat_tools
from tests.test_workflow_tools import helpers_state  # noqa: F401

MESSAGE = 'Create a user account for my colleague.'
PUBLIC = {'title', 'disposition', 'reason', 'page', 'panel'}
BANNED = set('create get list run project projects result results network component settings data file change set show load flow user key keys token code pdf address coordinates tariff tariffs upload uploads import series profile profiles resume'.split())


@pytest.fixture
def discovery(client):
    session = ChatSession()
    chat_tools.set_chat_session(session)
    return session


def test_oj46_goal_returns_the_admin_entry(discovery):
    out = chat_tools.find_capabilities(MESSAGE)
    assert out.get('not_via_chat'), out
    from services.not_via_chat import NOT_VIA_CHAT, NOTE

    assert out['not_via_chat'][0]['title'] == NOT_VIA_CHAT[0]['title']
    assert all(set(e) == PUBLIC for e in out['not_via_chat'])
    assert '/api/' not in json.dumps(out['not_via_chat'])
    assert out['not_via_chat_note'] == NOTE
    assert len(json.dumps(out)) <= 3800


def test_every_entry_is_well_formed():
    from services.not_via_chat import DISPOSITIONS, NOT_VIA_CHAT, NOTE

    ids = [e['id'] for e in NOT_VIA_CHAT]
    assert len(ids) == len(set(ids))
    assert not set(ids) & ({t['name'] for t in TOOLS} | set(chat_tools.DISPATCHERS))
    assert len(NOTE) <= 240
    for e in NOT_VIA_CHAT:
        assert e['disposition'] in DISPOSITIONS
        assert 0 < len(e['title']) <= 80 and 40 <= len(e['reason']) <= 200 and 0 < len(e['page']) <= 80
        assert e['panel'] is None or e['panel'] in SAFETY_PANEL_ENUM
        assert e['keywords'] and all(re.fullmatch('[a-z0-9]+', k) for k in e['keywords'])
        assert not e['keywords'] & BANNED
        assert e['routes'] and all(isinstance(r, tuple) and len(r) == 2 and r[0].isupper()
                                   and r[1].startswith('/api/') for r in e['routes'])


def test_the_issue_rows_are_declared():
    from services.not_via_chat import NOT_VIA_CHAT

    entries = {e['id']: e for e in NOT_VIA_CHAT}
    expected = {
        'admin_users_and_orgs': ('out of chat', None), 'legacy_project_import': ('out of chat', None),
        'sign_in_and_password': ('out of chat', None),
        'project_members': ('unbuilt; owner to decide later', 'workspace'),
        'assistant_keys_and_profiles': ('out of chat', 'settings'), 'assistant_access_rule': ('out of chat', 'chat'),
        'solve_queue_instance_controls': ('out of chat', 'solveQueue'), 'map_layout_and_sites': ('manual', 'map'),
        'library_uploads_and_tariff_edit': ('manual', 'library'), 'grid_code_publish': ('manual', 'campusElectrical'),
    }
    assert {k: (e['disposition'], e['panel']) for k, e in entries.items()} == expected
    assert entries['project_members']['reason'] == 'Sharing a project is not available from chat yet. Add or remove its members in the Workspace panel.'
    assert entries['solve_queue_instance_controls']['routes'] == frozenset({
        ('POST', '/api/simulation/queue/pause'), ('POST', '/api/simulation/queue/resume'),
    })
    assert 'series_file_upload' not in entries


@pytest.mark.parametrize('goal,entry_id', [
    ('Share this project with Anna', 'project_members'), ('Change my password', 'sign_in_and_password'),
    ('Set my Anthropic API key', 'assistant_keys_and_profiles'), ('Pause the solve queue', 'solve_queue_instance_controls'),
    ('Unpause the solve queue', 'solve_queue_instance_controls'),
    ('Resume the paused solve queue', 'solve_queue_instance_controls'), ('Replace the site thumbnail', 'map_layout_and_sites'),
])
def test_refusal_goals_find_their_entry(discovery, goal, entry_id):
    from services.not_via_chat import NOT_VIA_CHAT

    expected = next(e for e in NOT_VIA_CHAT if e['id'] == entry_id)
    assert chat_tools.find_capabilities(goal)['not_via_chat'][0]['title'] == expected['title']


@pytest.mark.parametrize('goal', [
    'get_component_schema', 'run the simulation', 'show the load flow results', 'parameter changes chart delivery',
    'Change the storage cost to 150', 'Read the capex from this PDF datasheet', 'Set the coordinates of bus 3',
    'What are the key cost drivers', 'Add a user-defined carrier', 'Attach the TOU tariff to the grid connection',
    'Import a URDB tariff', 'List the tariffs in the library', 'Upload this CSV as the load profile',
    'Set the load profile from the uploaded file', 'Resume my task', 'Resume the plan',
    'Switch to my OpenAI profile', 'Switch to my Anthropic profile', 'Pause my goal', 'Pause the plan',
    'Extract the grid code from this PDF', 'Geocode the site address',
])
def test_ordinary_goals_get_no_refusal(discovery, goal):
    assert 'not_via_chat' not in chat_tools.find_capabilities(goal)


def test_entries_never_become_discovered_tools(discovery):
    from services.not_via_chat import NOT_VIA_CHAT

    out = chat_tools.find_capabilities(MESSAGE)
    assert discovery.discovered_tools == tuple(i['name'] for i in out['items'])
    assert set(discovery.discovered_tools) <= {t['name'] for t in TOOLS}
    assert not set(discovery.discovered_tools) & {e[k] for e in NOT_VIA_CHAT for k in ('id', 'title')}


def test_entries_never_displace_a_tool(discovery, monkeypatch):
    from services import not_via_chat

    before = {n: chat_tools.find_capabilities(MESSAGE, limit=n) for n in (3, 8)}
    monkeypatch.setattr(not_via_chat, 'NOT_VIA_CHAT', ())
    for n, out in before.items():
        without = chat_tools.find_capabilities(MESSAGE, limit=n)
        assert out['items'] == without['items']
        assert len(json.dumps(out)) <= 3800 and len(json.dumps(without)) <= 3800
    assert 'not_via_chat' in before[3]


def test_overview_is_unchanged(discovery, monkeypatch):
    from services import not_via_chat

    before = chat_tools.find_capabilities(overview=True)
    monkeypatch.setattr(not_via_chat, 'NOT_VIA_CHAT', ())
    assert before == chat_tools.find_capabilities(overview=True)
    assert 'not_via_chat' not in before


def test_matching_is_bounded_ranked_and_returns_detached_public_records():
    from services.not_via_chat import NOT_VIA_CHAT, matches

    out = matches({'account', 'colleague'})
    assert out[0]['title'] == NOT_VIA_CHAT[0]['title']
    assert len(out) <= 2 and matches({'account'}, limit=0) == []
    out[0]['reason'] = 'changed by a caller'
    assert matches({'account'})[0]['reason'] != 'changed by a caller'

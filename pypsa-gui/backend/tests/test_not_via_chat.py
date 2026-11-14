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
        'memory_page_edits': ('out of chat', None), 'memory_dream_review': ('out of chat', None),
        'memory_parameter_suggestions': ('out of chat', None),
        'sign_in_and_password': ('out of chat', None),
        'project_members': ('unbuilt; owner to decide later', 'workspace'),
        'assistant_keys_and_profiles': ('out of chat', 'settings'), 'assistant_access_rule': ('out of chat', 'chat'),
        'solve_queue_instance_controls': ('out of chat', 'solveQueue'), 'map_layout_and_sites': ('manual', 'map'),
        'site_scene': ('unbuilt', 'map'),
        'accept_assumed_defaults': ('unbuilt', None),
        'intent_comparison': ('unbuilt', None),
        'dc_backup': ('unbuilt', None),
        'dc_architecture': ('unbuilt', None),
        'dc_operational_gap': ('manual', 'results'), 'results_headline_strip': ('manual', 'results'),
        'library_uploads_and_tariff_edit': ('manual', 'library'),
        'power_trace_spectrum': ('unbuilt', None),
        'report_comment_revisions': ('unbuilt', None),
        'report_chart_snapshots': ('manual', 'results'),
    }
    assert 'grid_code_publish' not in entries  # GRIDCODE-CHAT-1: chat tools behind a card
    assert {k: (e['disposition'], e['panel']) for k, e in entries.items()} == expected
    assert entries['project_members']['reason'] == 'Sharing a project is not available from chat yet. Add or remove its members in the Workspace panel.'
    assert entries['solve_queue_instance_controls']['routes'] == frozenset({
        ('POST', '/api/simulation/queue/pause'), ('POST', '/api/simulation/queue/resume'),
    })
    assert 'series_file_upload' not in entries
    # ui-modern 03 S3 (GK B S8): the strip's changes are not chat's comparison.
    assert 'compares a case with its base on other measures' in entries['results_headline_strip']['reason']


@pytest.mark.parametrize('goal,entry_id', [
    ('Share this project with Anna', 'project_members'), ('Change my password', 'sign_in_and_password'),
    ('Set my Anthropic API key', 'assistant_keys_and_profiles'), ('Pause the solve queue', 'solve_queue_instance_controls'),
    ('Unpause the solve queue', 'solve_queue_instance_controls'),
    ('Resume the paused solve queue', 'solve_queue_instance_controls'), ('Replace the site thumbnail', 'map_layout_and_sites'),
    ('Make this capex reusable in my next project', 'memory_parameter_suggestions'),
    ('Apply my saved value to this generator', 'memory_parameter_suggestions'),
    ('Use my saved efficiency', 'memory_parameter_suggestions'),
    ('Reuse a parameter I saved', 'memory_parameter_suggestions'),
    ('Apply my saved lifetime', 'memory_parameter_suggestions'),
    ('Save this value for later', 'memory_parameter_suggestions'),
    ('Save the lifetime of this battery for my next project', 'memory_parameter_suggestions'),
    ('Save this capex so I can reuse it', 'memory_parameter_suggestions'),
    ('Save the efficiency for another project', 'memory_parameter_suggestions'),
    ('Remember this capex for later', 'memory_parameter_suggestions'),
    ('Remember this value for next time', 'memory_parameter_suggestions'),
    ('Save this capex so I can use it again', 'memory_parameter_suggestions'),
    ('Keep this value for next time', 'memory_parameter_suggestions'),
    ('Store this capex for future projects', 'memory_parameter_suggestions'),
    ('Save the capex as a default', 'memory_parameter_suggestions'),
    ('Save the capex to reuse', 'memory_parameter_suggestions'),
    ('Save the cost for my next project', 'memory_parameter_suggestions'),
    ('Save the capex for future projects', 'memory_parameter_suggestions'),
    ('Save this capex for later use', 'memory_parameter_suggestions'),
    ('Save this capex so I can use this again', 'memory_parameter_suggestions'),
    ('Read me the headline strip', 'results_headline_strip'), ('What is in the KPI strip', 'results_headline_strip'),
    ('Which KPIs are in the strip', 'results_headline_strip'),
    ('Why is the LCOE KPI not shown in the strip?', 'results_headline_strip'), ('Add a KPI to the strip', 'results_headline_strip'),
    ('Hide the headline strip', 'results_headline_strip'), ('What does the headline strip show?', 'results_headline_strip'),
    # BC 08b (GK B (54) D8): only the preset is out of chat.
])
def test_refusal_goals_find_their_entry(discovery, goal, entry_id):
    from services.not_via_chat import NOT_VIA_CHAT

    expected = next(e for e in NOT_VIA_CHAT if e['id'] == entry_id)
    assert chat_tools.find_capabilities(goal)['not_via_chat'][0]['title'] == expected['title']


@pytest.mark.parametrize('goal', [
    'get_component_schema', 'run the simulation', 'show the load flow results', 'parameter changes chart delivery',
    # Business case 14 (GK B (56) B7): the preset is a chat tool now (apply_business_case_preset).
    'Apply the business case preset', 'Fill the business case from the template preset',
    'Change the storage cost to 150', 'Read the capex from this PDF datasheet', 'Set the coordinates of bus 3',
    'What are the key cost drivers', 'Add a user-defined carrier', 'Attach the TOU tariff to the grid connection',
    'Import a URDB tariff', 'List the tariffs in the library', 'Upload this CSV as the load profile',
    'Set the load profile from the uploaded file', 'Resume my task', 'Resume the plan',
    'Switch to my OpenAI profile', 'Switch to my Anthropic profile', 'Pause my goal', 'Pause the plan',
    'Extract the grid code from this PDF', 'Geocode the site address',
    'Pin the slack bus to bus 3', 'Pin the battery capacity at 50 MW', 'Which pinned settings does the scenario use',
    'List my saved snapshots', 'Reuse the last run settings', 'Any suggestion for the battery size', 'Save the project', 'Saved project list', 'Show saved scenarios',
    'Reuse my saved scenario parameters', 'Show saved scenario costs', 'What did the last saved run cost',
    'Compare capacity with the saved snapshot', 'Reuse the price from the saved tariff',
    'Set the capacity to 50 MW and save', 'Save my changes to the capex', 'Save the cost table as csv',
    'Export costs and save the report', 'Save results with prices', 'Save the price series I uploaded',
    'Save the scenario costs for later', 'Save this run with its prices and solve again',
    'Show my saved charts', 'Save the report for later',
    'Size the store capacity for our next project', 'Use the cost as the default for the hydrogen store',
    "Use this store's capex as a default when I add stores", 'Set the capex as a default for the next project', 'Save the capacity and check the next time step', 'Save the cost and solve it later',
    'Remember the price rises later in the year', 'Remember that the capex is lower in another project',
    'Save the price and solve again', 'Save the price again',
    'Set capacity to 50 and save, then do the next one', 'Save the capacity, then try another carrier',
    'Save the network and costs for later', 'Save the cost table for later', 'Save the other values',
    'Keep the capacity at 50 MW', 'Keep the cost for the next run',
    'Add a store and set its capacity for future years', 'Increase the store capacity next time step',
    'Store the hydrogen at a lower cost later in the year',
    'Store the excess energy for later use and lower the cost',
    'Keep the capacity under 50 MW for future years', 'Keep the price fixed and use it again',
    'Remember to use the price again in the next solve', 'Save the capacity for future years',
    # ui-modern 03 (GK B F3): 'headline' and 'strip' each belong to real tools.
    'Headline KPIs for run A vs run B', 'Show the investment case headlines', 'List the files in the chat file strip',
    'Export the headline results to the file strip', 'Compare headline KPIs of run A and run B and export to the file strip',
    'Put the KPI report in the file strip',
    # GK B R1: export, report, compare and the verb "strip" are chat tools' work.
    'Export the headline strip to Excel', 'Export the KPI strip', 'Download the KPI strip as CSV',
    'Put the headline strip in the report', 'Add the headline strip KPIs to the Word report',
    'Compare the headline strip of two runs', 'Compare the KPI strip of run A and run B',
    'Strip the headline from the report', 'Strip KPI names of units in the export', 'Save the KPI strip to my files',
    'Add the KPI strip to the export', 'What is in the chat file strip',
    # GK B C1: destinations and actions that put the figures elsewhere.
    'Add the headline strip to the Excel workbook', 'Add the KPI strip to the Word document',
    'Add the headline strip to the exported bundle', 'Read the headline strip and save it as CSV',
    'What is the KPI strip showing for system cost compared with the last run?',
    # One word each, so no word of the set is carried by another.
    'Add the KPI strip to Excel', 'Add the KPI strip to the workbook', 'Add the headline strip to Word',
    'Add the KPI strip to the document', 'Add the KPI strip to the bundle', 'Which KPIs in the strip were exported',
    'Add the KPI strip as CSV', 'Which strip KPIs can I save', 'Add the KPI strip to the PDF',
    'Which strip KPIs can I download', 'Add the KPI strip to the report',
    # BC 08a (GK B (54) D3): EBITDA and break-even asked of the investment case are its tools'.
    'what is the island breakeven in the investment case', 'show ebitda by year from the investment case',
    'export the investment case workbook with the ebitda rows',
    # The guard, one part each: "business as usual" is a scenario; no case noun; no ask.
    'What is the business as usual case?', 'Show the business-as-usual case results',
    'change the business as usual case demand', 'Show the BAU business case',
    'Run the business hours report', 'the business case for adding a battery',
    # BC 08b (GK B (54) D8): the business case's tools answer these, so no refusal.
    'show the business case for adding a battery', 'Run the business case',
    'Edit the business case lines', 'What is the break-even price in the business case?',
    # The guard re-pinned, one part each: BAU only; no case noun; no preset word.
    'Apply the business as usual case preset', 'Apply the business preset',
    'Show the business case',
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


def test_saved_report_charts_are_human_only_and_found_by_their_goal(discovery):
    from services.not_via_chat import NOT_VIA_CHAT

    entry = next(e for e in NOT_VIA_CHAT if e['id'] == 'report_chart_snapshots')
    assert entry['disposition'] == 'manual'
    assert {('POST', '/api/projects/{name}/report-charts'),
            ('DELETE', '/api/projects/{name}/report-charts/{chart_id}')} <= entry['routes']
    out = chat_tools.find_capabilities('Pin this chart for the report.')
    assert [e['title'] for e in out.get('not_via_chat', [])][:1] == [entry['title']]


def test_a_real_chart_pin_phrase_is_still_refused(discovery):
    out = chat_tools.find_capabilities('Which charts are pinned to the report?')
    assert out['not_via_chat'][0]['title'] == 'Saving a chart for the report'

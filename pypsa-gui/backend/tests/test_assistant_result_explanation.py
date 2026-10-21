"""W10 H2a: "Explain these results" for one card (card-workspaces.md §6, ROI-B).

The explanation runs the explain-results workflow on the card the browser has
open. While it runs, a task cap lowers the turn's limits (512 output tokens,
3 tool calls, 2 rounds) and never raises them. The cap keys on harness state
only (the workflow and a validated `results_card`), and hitting it is said,
never silent. Numbers come from the views' own native reads; under strict
checking only the bounded profiles publish, and everything else is withheld or
refused. Cases RI07–RI10, RI12 (#558) and D12, D13 (#567 HV-D3). Only provider
HTTP is mocked in the journey tests; checked turns script the model only.
"""
# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
import json

import pytest

from harness import budget, calculations
from services import chat_service, result_views as rv
from tests.assurance_acceptance.support import approval, public_text, text_turn
from tests.journey_wire import run_journey
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider, solved  # noqa: F401
from tests.test_oj01_latest_loadflow_journey import oj_grid  # noqa: F401
from tests.test_workflow_provider_parity import PROVIDERS
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401

WIRE = PROVIDERS[1]
EXPLAIN = {'workflow': {'id': 'explain-results', 'step': 'explain'}}


def _explain(card):
    return {**EXPLAIN, 'panel': 'results', 'results_card': card}


def _session(workflow='explain-results', card='capacity'):
    session = chat_service.ChatSession()
    session.workflow = {'id': workflow, 'step': 'explain'} if workflow else None
    session.turn_ui_context = {'results_card': card}
    return session


# ── the cap keys on harness state and only lowers ────────────────────────────

def test_the_cap_needs_the_workflow_and_a_valid_card():
    assert budget.task_cap(_session()) == budget.EXPLAIN_CARD
    assert budget.task_cap(_session(workflow=None)) is None
    assert budget.task_cap(_session(workflow='run-study')) is None
    assert budget.task_cap(_session(card='frequency')) is None
    assert budget.task_cap(_session(card=None)) is None


def test_the_cap_is_the_roi_b_bound():
    cap = budget.EXPLAIN_CARD
    assert (cap.output_tokens, cap.tool_calls, cap.rounds) == (512, 3, 2)


def test_a_cap_only_lowers(monkeypatch):
    cap = budget.EXPLAIN_CARD
    assert budget.output_limit(None, 8192) == 8192
    assert budget.output_limit(cap, 8192) == 512
    assert budget.output_limit(cap, 256) == 256          # a stricter profile wins
    assert budget.tool_call_limit(cap) == 3
    monkeypatch.setattr(budget, 'MAX_TOOL_CALLS_PER_TURN', 2)
    assert budget.tool_call_limit(cap) == 2              # a stricter global wins
    assert budget.tool_call_limit(None) == 2


def test_every_checked_profile_a_view_names_fits_twelve_facts():
    """ROI-B's 12 fact blocks: one checked answer admits one profile's facts."""
    named = {p for v in rv.RESULT_VIEWS for p in v.checked_profiles if p in calculations.PROFILES}
    assert named
    assert all(len(calculations.PROFILES[p]) <= 12 for p in named)


# ── journeys: the explanation on a solved grid ───────────────────────────────

def _read(view_id, i=0):
    read = rv.view(view_id).reads[i].call()
    return lambda r: (read['tool'], read['args'])


def _answer():
    """End the turn; run_journey's `answer` is the text."""
    return lambda r: (None, {})


def _caps_seen(kind, model, base_url, auth, steps, card, **kwargs):
    """The max_tokens of every provider request in the turn."""
    import httpx

    seen = []
    send = httpx.MockTransport.handle_request

    def spy(self, request):
        body = json.loads(request.content)
        seen.append(body.get('max_tokens') or body.get('max_completion_tokens'))
        return send(self, request)

    with pytest.MonkeyPatch.context() as m:
        m.setattr(httpx.MockTransport, 'handle_request', spy)
        out = run_journey(kind, model, base_url, auth, steps, 'Explain these results.',
                          ui_context_extra=_explain(card), **kwargs)
    return seen, out


def test_ri07_the_card_block_names_the_cards_views_and_their_reads(oj_grid):
    session = chat_service.ChatSession()
    _, _, frames, _ = run_journey(*WIRE, [_answer()], 'Explain these results.',
                                  session=session, ui_context_extra=_explain('capacity'),
                                  answer='Gas covers the night.')
    user = session.messages[0]['content']
    text = user if isinstance(user, str) else '\n'.join(b.get('text', '') for b in user)
    assert 'results card: capacity' in text
    for v in rv.RESULT_VIEWS:
        if v.card == 'capacity' and v.live():
            assert f'[{v.view_id}]' in text, v.view_id
    assert '[loadflow]' not in text and '[adequacy]' not in text
    assert any(e == 'turn_done' for e, _ in frames)


def test_ri07_reads_are_native_and_a_view_without_data_is_not_zero(oj_grid):
    calls, results, frames, _ = run_journey(
        *WIRE, [_read('dispatch'), _answer()], 'Explain these results.',
        ui_context_extra=_explain('capacity'), answer='Gas covers the night.')
    assert calls == ['get_results', None]
    assert results[0] and 'error' not in results[0]
    # The card listing gives statuses only: no-data views say so, never 0.
    from services import chat_tools
    chat_tools.set_chat_session(chat_service.ChatSession())
    try:
        rows = rv.list_result_views(card='capacity')['views']
    finally:
        chat_tools.set_chat_session(None)
    assert not any(isinstance(x, (int, float)) and not isinstance(x, bool)
                   for row in rows for x in row.values())
    assert {row['data'] for row in rows} <= set(rv.DATA_STATUSES)


def test_the_explanation_turn_asks_for_at_most_512_tokens(oj_grid):
    seen, _ = _caps_seen(*WIRE, [_read('dispatch'), _answer()], 'capacity',
                         answer='Gas covers the night.')
    assert seen and all(t is not None and t <= 256 for t in seen)  # the journey profile is 256


def test_the_cap_is_off_without_the_card(oj_grid, monkeypatch):
    monkeypatch.setattr(budget, 'EXPLAIN_CARD', budget.TaskCap(output_tokens=100, tool_calls=3, rounds=2))
    seen, _ = _caps_seen(*WIRE, [_answer()], 'not-a-card', answer='ok')
    assert seen == [256]
    seen, _ = _caps_seen(*WIRE, [_answer()], 'capacity', answer='ok')
    assert seen == [100]


def test_a_third_round_stops_at_the_step_limit_and_keeps_pairing(oj_grid):
    calls, results, frames, session = run_journey(
        *WIRE, [_read('dispatch'), _read('prices'), _read('emissions'), _answer()],
        'Explain these results.', ui_context_extra=_explain('capacity'), answer='done')
    assert calls == ['get_results', 'get_results']       # round 2's read is never run
    assert len(results) == 1
    errors = [d for e, d in frames if e == 'error']
    assert errors and errors[-1]['error_kind'] == 'task_step_limit'
    assert errors[-1]['message'] == budget.TASK_STEP_LIMIT
    assert [d['reason'] for e, d in frames if e == 'session_done'] == ['task_step_limit']
    used = {b['id'] for m in session.messages if m['role'] == 'assistant' and isinstance(m['content'], list)
            for b in m['content'] if b.get('type') == 'tool_use'}
    answered = {b['tool_use_id'] for m in session.messages if m['role'] == 'user' and isinstance(m['content'], list)
                for b in m['content'] if b.get('type') == 'tool_result'}
    assert used and used <= answered


def test_the_model_cannot_lift_the_cap_by_ending_the_workflow(oj_grid):
    calls, _, frames, _ = run_journey(
        *WIRE, [lambda r: ('end_workflow', {}), _read('prices'), _answer()],
        'Explain these results.', ui_context_extra=_explain('capacity'), answer='done')
    assert calls == ['end_workflow', 'get_results']
    assert [d['reason'] for e, d in frames if e == 'session_done'] == ['task_step_limit']


def test_ri12_a_failing_read_keeps_the_turn_and_is_not_rerun(oj_grid, monkeypatch):
    from services import chat_tools

    attempts = []

    def broken(**kwargs):
        attempts.append(kwargs)
        raise RuntimeError('reader moved')

    monkeypatch.setitem(chat_tools.DISPATCHERS, 'get_results', broken)
    calls, results, frames, _ = run_journey(
        *WIRE, [_read('dispatch'), _answer()], 'Explain these results.',
        ui_context_extra=_explain('capacity'), answer='Dispatch is unavailable right now.')
    assert len(attempts) == 1 and calls == ['get_results', None]
    assert 'error' in results[0]
    assert any(e == 'turn_done' for e, _ in frames)


# ── strict checking: native facts only, or an explicit refusal ───────────────

def _checked_explain(checked, provider, card, **options):
    return checked.run(provider, message='Explain these results.',
                       ui_context={'ui_mode': 'expert', **_explain(card)}, **options)


def _scope(ctx, profile, entity):
    return {'version': 1, 'profile': profile, 'project_id': ctx.project_uuid,
            'entity_id': entity, 'retain': False}


def test_ri09_a_card_without_checked_profiles_is_an_explicit_scoped_refusal(baseline, checked):
    assert not any(v.checked_profiles for v in rv.RESULT_VIEWS if v.card == 'control')
    frames = _checked_explain(checked, [text_turn(json.dumps({'schema_version': 1, 'blocks': []}))],
                              'control')
    record = approval(frames)
    assert record['receipt']['checked_block_ids'] == []
    assert 'No checked result is available for this request.' in public_text(frames)


def test_d12_a_right_number_with_the_wrong_unit_is_withheld(solved, checked):
    def wrong_unit(ledger):
        ledger['blocks'][0]['unit'] = 'kWh'

    frames = _checked_explain(checked, NumericalProvider(mutate=wrong_unit), 'capacity',
                              checked_scope=_scope(solved, 'generator-dispatch/1', 'G1'))
    assert not any(e == 'answer_approved' and d['verdict'] == 'passed' for e, d in frames)
    assert "couldn't verify" in public_text(frames).lower() or not public_text(frames)


def test_d13_a_thirteenth_fact_is_withheld(solved, checked):
    def pad(ledger):
        first = ledger['blocks'][0]
        ledger['blocks'] += [{**first, 'id': f'extra-{i}', 'metric': f'extra_{i}'} for i in range(12)]

    frames = _checked_explain(checked, NumericalProvider(mutate=pad), 'capacity',
                              checked_scope=_scope(solved, 'generator-dispatch/1', 'G1'))
    assert not any(e == 'answer_approved' and d['verdict'] == 'passed' for e, d in frames)


def test_ri08_a_checked_answer_cut_at_the_cap_is_still_checked(solved, checked):
    """An answer that reaches the output cap is committed like any other: an
    injected number is withheld, then the turn says it stopped."""
    class Truncated(NumericalProvider):
        def stream(self, request):
            for event in super().stream(request):
                if event.type == 'message_done':
                    event.usage['output_tokens'] = budget.EXPLAIN_CARD.output_tokens
                yield event

    def inject(ledger):
        ledger['blocks'][0]['value'] += 1

    frames = _checked_explain(checked, Truncated(mutate=inject), 'capacity',
                              checked_scope=_scope(solved, 'generator-dispatch/1', 'G1'))
    assert not any(e == 'answer_approved' and d['verdict'] == 'passed' for e, d in frames)
    assert [d['error_kind'] for e, d in frames if e == 'error'][-1:] == ['task_step_limit']


def test_ri10_energy_balance_totals_do_not_certify_the_flow_rows():
    flows = rv.view('flows')
    assert flows.card == 'capacity' and flows.checked_profiles == ('energy-balance/1',)
    assert 'unavailable, not zero' in flows.claim_limits
    metrics = calculations.PROFILES['energy-balance/1']
    assert not any('via' in m or 'route' in m or 'path' in m for m in metrics)


def test_four_reads_in_one_round_dispatch_none(oj_grid, monkeypatch):
    from harness.protocol import LLMEvent
    from harness.providers.fake import FakeProvider
    from services import chat_tools

    attempts = []
    monkeypatch.setitem(chat_tools.DISPATCHERS, 'get_results',
                        lambda **kwargs: attempts.append(kwargs) or {'status': 'ok'})
    reads = [{'type': 'tool_use', 'id': f'r{i}', 'name': 'get_results',
              'input': {'result_kind': kind}}
             for i, kind in enumerate(('generators', 'prices', 'emissions', 'curtailment'))]
    provider = FakeProvider([{
        'events': [LLMEvent(type='tool_use_start', tool_name='get_results', tool_use_id=b['id'])
                   for b in reads],
        'blocks': reads, 'usage': {'input_tokens': 1, 'output_tokens': 1}}])
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    frames = list(chat_service.run_turn(session, 'Explain these results.', provider=provider,
                                        ui_context={'ui_mode': 'expert', **_explain('capacity')}))
    assert attempts == []
    assert [d['reason'] for e, d in frames if e == 'session_done'] == ['task_step_limit']
    results = [b for m in session.messages if m['role'] == 'user' and isinstance(m['content'], list)
               for b in m['content'] if b.get('type') == 'tool_result']
    assert {b['tool_use_id'] for b in results} == {'r0', 'r1', 'r2', 'r3'}

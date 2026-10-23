"""OJ-46: a scripted provider consumes native guidance without admin execution.

This tests transport and harness integration, not a real model's refusal rate.
"""
import pytest

from harness.catalogue import TOOLS
from services.not_via_chat import NOT_VIA_CHAT
from tests.journey_wire import run_journey
from tests.test_workflow_provider_parity import PROVIDERS
from tests.test_workflow_tools import helpers_state  # noqa: F401

MESSAGE = 'Create a user account for my colleague.'


@pytest.mark.parametrize('mode', ['expert', 'guided'])
@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS, ids=[row[0] for row in PROVIDERS])
def test_a_feature_chat_must_refuse_names_the_reason_and_the_page(
    client, kind, model, base_url, auth, mode,
):
    steps = [lambda r: ('find_capabilities', {'goal': MESSAGE}), lambda r: (None, {})]
    calls, results, frames, session = run_journey(
        kind, model, base_url, auth, steps, MESSAGE, mode=mode,
        answer=lambda r: r[0]['not_via_chat'][0]['reason'] + ' ' + r[0]['not_via_chat'][0]['page'],
    )
    assert calls == ['find_capabilities', None]
    entry = results[0]['not_via_chat'][0]
    assert entry['title'] == NOT_VIA_CHAT[0]['title']
    assert not [e for e, _ in frames if e in {'tool_error', 'error', 'tool_pending_confirmation', 'ui_event'}]
    text = ''.join(d.get('delta', '') for e, d in frames if e == 'token')
    assert entry['page'] in text and entry['reason'] in text
    assert set(session.discovered_tools) <= {t['name'] for t in TOOLS}

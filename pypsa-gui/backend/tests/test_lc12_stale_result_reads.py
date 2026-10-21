"""LC-12 item 4: after a chat edit, result reads say the results are stale.

A parameter-only edit keeps the dispatch axes aligned, so the axis check
alone can still say "fresh". Result reads now also compare the solve-input
fingerprint, as the load-flow summary already does, and report
`inputs_changed_since_solve`. Real HiGHS solve on `oj-grid`; only provider HTTP
is mocked.
"""
import pytest

from services import chat_tools
from tests.journey_wire import run_journey
from tests.test_oj05_selected_line_edit_journey import _decide, oj_grid  # noqa: F401
from tests.test_workflow_provider_parity import PROVIDERS
from tests.test_workflow_tools import helpers_state  # noqa: F401


READ = 'How loaded are the lines? Check dispatch_status, then get_results for lines.'


def _reads():
    return [lambda r: ('dispatch_status', {}),
            lambda r: ('get_results', {'result_kind': 'lines'}),
            lambda r: (None, {})]


def test_reads_are_current_right_after_the_solve(oj_grid):  # noqa: F811
    calls, results, frames, _ = run_journey(*PROVIDERS[1], _reads(), READ)
    assert calls == ['dispatch_status', 'get_results', None]
    status, lines = results
    assert status['state'] == 'fresh' and status['inputs_changed_since_solve'] is False
    assert lines['inputs_changed_since_solve'] is False


@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS, ids=[row[0] for row in PROVIDERS])
def test_after_a_chat_edit_the_reads_say_stale(oj_grid, kind, model, base_url, auth):  # noqa: F811
    edit = [lambda r: ('update_component', {'component_class': 'Line', 'name': 'L2', 'attrs': {'s_nom': 400}}),
            lambda r: (None, {})]
    _, results, _, session = run_journey(kind, model, base_url, auth, edit, 'Set L2 s_nom to 400.',
                                         on_frame=_decide('approve', []))
    assert results[0]['_edit_receipt']['results_stale'] is True
    calls, results, frames, _ = run_journey(kind, model, base_url, auth, _reads(), READ,
                                            session=session)
    status, lines = results
    assert status['inputs_changed_since_solve'] is True
    assert lines['inputs_changed_since_solve'] is True
    assert 'stale' in lines['freshness_note']


def test_an_unsolved_network_has_no_claim_either_way(client, session_ctx, install_network):
    from services.pypsa_service import PyPSAService
    from tests.oj_grid import NAME, build_oj_grid

    install_network(build_oj_grid())
    assert client.post(f'/api/projects/{NAME}').status_code == 200
    binding = PyPSAService.bind_request_context(session_ctx(client))
    try:
        assert chat_tools.dispatch_status()['inputs_changed_since_solve'] is None
    finally:
        PyPSAService.reset_request_context(binding)

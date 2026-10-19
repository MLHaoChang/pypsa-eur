"""Public ordinary-harness schema discovery over real native schema sources."""
import json

import pytest

from harness.catalogue import COMPONENT_CLASS_ENUM
from services import chat_service, chat_tools
from services.pypsa_service import PyPSAService
from tests.assurance_acceptance.support import approval, parse_sse, text_turn, tool_turn
from tests.test_assistant_capabilities import dispatch, output
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_chat_stream_session_ownership import fake_provider  # noqa: F401
from tests.test_chat_restrictions import save_rule
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401
from tests.test_workflow_provider_parity import PROVIDERS


@pytest.fixture
def session(request):
    request.getfixturevalue('baseline')
    value = chat_service.ChatSession()
    chat_tools.set_chat_session(value)
    return value


def test_storage_duration_schema_is_discoverable_through_ordinary_dispatch(session):
    result = output(dispatch(session, 'get_component_schema', {
        'component_class': 'StorageUnit', 'query': 'max_hours',
    }))
    assert result['version'] == 1
    assert result['component_class'] == 'StorageUnit'
    assert result['returned'] == result['total_count'] == 1
    field = result['items'][0]
    assert field['name'] == 'max_hours'
    assert field['unit_storage'] == 'h'
    assert field['editable'] is True and field['derived'] is False
    assert field['validation_schema']['type'] == 'number'


def schema(session, component_class, **kwargs):
    return output(dispatch(session, 'get_component_schema', {'component_class': component_class, **kwargs}))


@pytest.mark.parametrize('component_class', COMPONENT_CLASS_ENUM)
def test_every_native_field_can_be_paged_without_losing_its_schema(session, client, component_class):
    api_models = client.get('/openapi.json').json()['components']['schemas']
    expected = set(api_models[component_class + 'Create']['properties'])
    assets = client.get('/api/network/asset_schema/' + component_class)
    assert assets.status_code == 200
    expected.update(field['key'] for group in assets.json()['groups'] for field in group['fields'])
    if component_class == 'GlobalConstraint':
        expected.update({'mu', 'bus'})  # Native dual and bus selector.
    else:
        native = client.get('/api/network/catalog/' + component_class)
        assert native.status_code == 200
        expected.update(field['name'] for field in native.json()['attributes'])
    names, digests, offset = [], set(), 0
    for _ in range(100):
        page = schema(session, component_class, offset=offset, limit=50)
        assert not page.get('_truncated')
        assert len(json.dumps(page, allow_nan=False)) <= 3800
        assert page['offset'] == offset and page['returned'] == len(page['items'])
        assert page['total_count'] == len(expected)
        names.extend(row['name'] for row in page['items'])
        digests.add(page['schema_sha256'])
        offset += page['returned']
        if not page['has_more']:
            break
        assert page['returned'] > 0
    else:
        pytest.fail('Schema pagination did not terminate')
    assert names == sorted(expected)
    assert len(digests) == 1 and len(next(iter(digests))) == 64
    end = schema(session, component_class, offset=offset)
    assert end['items'] == [] and end['returned'] == 0 and not end['has_more']


@pytest.mark.parametrize('component_class,key,unit,display', [
    ('StorageUnit', 'inv_power_overnight', 'EUR/MW', 'EUR/kW'),
    ('StorageUnit', 'inv_energy_overnight', 'EUR/MWh', 'EUR/kWh'),
    ('Store', 'overnight_cost', 'EUR/MWh', 'EUR/kWh'),
    ('Line', 'inv_per_km_overnight', 'EUR/MVA/km', 'EUR/MVA/km'),
])
def test_cost_bases_are_native_metadata_and_never_display_scaled_values(session, component_class, key, unit, display):
    field = schema(session, component_class, query=key)['items'][0]
    assert field['name'] == key and field['unit_storage'] == unit and field['unit_display'] == display
    assert field['display_scale'] == (1.0 if unit == display else 0.001)
    assert field['editable'] is True and 'value' not in field


def test_native_bounds_enums_and_infinite_defaults_remain_explicit(session):
    outage = schema(session, 'Generator', query='outage_rate_value')['items'][0]
    numeric = next(row for row in outage['validation_schema']['anyOf'] if row['type'] == 'number')
    assert numeric['minimum'] == 0 and numeric['exclusiveMaximum'] == 1
    basis = schema(session, 'Generator', query='outage_rate_basis')['items'][0]
    assert basis['options'] == ['FOR', 'EFORd']
    maximum = schema(session, 'Generator', query='p_nom_max')['items'][0]
    assert maximum['default'] == {'value': None, 'text': 'inf', 'non_finite': True}


@pytest.mark.parametrize('component_class,key,reason', [
    ('StorageUnit', 'capital_cost', 'derived'),
    ('Line', 'fom_cost', 'derived'),
    ('Generator', 'p_nom_opt', 'native_output'),
])
def test_computed_outputs_are_not_presented_as_editable(session, component_class, key, reason):
    field = schema(session, component_class, query=key)['items'][0]
    assert field['name'] == key and field['editable'] is False
    assert field['editability_reason'] == reason


def test_unknown_queries_do_not_export_custom_columns_or_model_values(session, client):
    network = PyPSAService.get_network()
    network.generators['secret_fixture_column'] = 'DO-NOT-EXPORT'
    page = schema(session, 'Generator', query='secret_fixture_column')
    assert page['items'] == [] and page['total_count'] == 0 and not page['has_more']
    encoded = json.dumps(schema(session, 'Generator'))
    assert 'DO-NOT-EXPORT' not in encoded and 'secret_fixture_column' not in encoded and 'G1' not in encoded
    assert client.get('/api/network/generators').json()[0]['marginal_cost'] == 10


def test_metadata_revision_tracks_schema_not_live_values_or_paging(session, client):
    first = schema(session, 'Generator', query='p_nom')
    assert schema(session, 'Generator', query='MARGINAL_COST')['schema_sha256'] == first['schema_sha256']
    # Change an actual value through its existing public write, not schema composition.
    output(dispatch(session, 'update_component', {'component_class': 'Generator', 'name': 'G1', 'attrs': {'p_nom': 25}}))
    assert client.get('/api/network/generators').json()[0]['p_nom'] == 25
    assert schema(session, 'Generator')['schema_sha256'] == first['schema_sha256']
    defaults = PyPSAService.get_network().components['Generator'].defaults
    previous = defaults.at['p_nom', 'unit']
    try:
        defaults.at['p_nom', 'unit'] = 'fixture alternate unit'
        changed = schema(session, 'Generator', query='p_nom')
        assert changed['schema_sha256'] != first['schema_sha256']
    finally:
        defaults.at['p_nom', 'unit'] = previous


@pytest.mark.parametrize('args', [
    {'component_class': 'ProcessUnit'}, {'component_class': '../../settings'},
    {'component_class': 'Generator', 'query': 'x' * 81},
    {'component_class': 'Generator', 'query': {}},
    *[{'component_class': 'Generator', 'offset': x} for x in (-1, True, 1.5, 1000001)],
    *[{'component_class': 'Generator', 'limit': x} for x in (0, True, 1.5, 51)],
])
def test_invalid_discovery_requests_refuse_without_metadata(session, args):
    frames = dispatch(session, 'get_component_schema', args)
    assert any(event == 'tool_error' for event, _ in frames)
    assert not any(event == 'tool_result' for event, _ in frames)


def test_discovery_does_not_grant_writes_under_personal_read_only_rule(session, client):
    project_id = client.get('/api/chat/access').json()['scope']['project_id']
    save_rule(client, project_id, 'read_only')
    assert schema(session, 'Generator', query='marginal_cost')['items'][0]['editable'] is True
    frames = dispatch(session, 'update_component', {'component_class': 'Generator', 'name': 'G1', 'attrs': {'marginal_cost': 99}})
    assert any(data.get('error_kind') == 'user_restriction' for _, data in frames)
    assert not any(event in {'tool_pending_confirmation', 'tool_running'} for event, _ in frames)
    assert client.get('/api/network/generators').json()[0]['marginal_cost'] == 10


def test_strict_checked_policy_does_not_silently_admit_schema_tool(session, request):
    gate = request.getfixturevalue('checked')
    frames = gate.run([tool_turn('get_component_schema', {'component_class': 'Generator'}), text_turn('No checked adapter.')])
    assert any(data.get('error_kind') == 'capability_unsupported' for _, data in frames)
    assert not any(event == 'tool_result' and data.get('tool_name') == 'get_component_schema' for event, data in frames)
    assert approval(frames)['verdict'] == 'limited'


def test_authenticated_chat_stream_delivers_the_native_schema_then_completes(session, client, request):
    install_provider = request.getfixturevalue('fake_provider')
    provider = install_provider([tool_turn('get_component_schema', {
        'component_class': 'StorageUnit', 'query': 'max_hours',
    }), text_turn('Schema inspected.')])
    session_id = 'schema-http-fixture'
    try:
        response = client.post('/api/chat/stream', json={
            'session_id': session_id, 'message': 'Inspect get_component_schema StorageUnit max_hours.',
        })
        assert response.status_code == 200
        frames = parse_sse(response.text)
        result = next(data['result'] for event, data in frames if event == 'tool_result')
        assert result['items'][0]['name'] == 'max_hours' and result['items'][0]['unit_storage'] == 'h'
        assert not any(event in {'error', 'tool_error', 'tool_pending_confirmation'} for event, _ in frames)
        assert any(event == 'turn_done' for event, _ in frames)
        assert 'Schema inspected.' in ''.join(data.get('delta', '') for event, data in frames if event == 'token')
        assert len(provider.requests) == 2
        assert client.get('/api/network/generators').json()[0]['marginal_cost'] == 10
    finally:
        chat_service.drop_session(session_id)


def test_a_future_oversized_field_refuses_instead_of_clipping_validation_or_looping(session):
    defaults = PyPSAService.get_network().components['Generator'].defaults
    previous = defaults.at['p_nom_opt', 'description']
    try:
        defaults.at['p_nom_opt', 'description'] = '長い説明' * 2000
        frames = dispatch(session, 'get_component_schema', {'component_class': 'Generator', 'query': 'p_nom_opt'})
        assert any(event == 'tool_error' and 'schema_field_too_large' in data.get('message', '') for event, data in frames)
        assert not any(event == 'tool_result' for event, _ in frames)
    finally:
        defaults.at['p_nom_opt', 'description'] = previous


def test_networkless_project_can_inspect_class_metadata_without_previous_project_values(session):
    original = schema(session, 'Generator')['schema_sha256']
    study = output(dispatch(session, 'gridspine_create_study', {
        'name': 'Schema-only planning project', 'config': {'hours': 24, 'k': 1, 'screen': True},
    }))
    output(dispatch(session, 'activate_project', {'project_id': study['id']}))
    metadata = schema(session, 'Generator', query='marginal_cost')
    assert metadata['schema_sha256'] == original
    assert metadata['items'][0]['name'] == 'marginal_cost'
    assert metadata['note'].startswith('Class metadata only.')
    frames = dispatch(session, 'get_component', {'component_class': 'Generator', 'name': 'G1'})
    assert any(event == 'tool_error' for event, _ in frames)
    assert not any(event == 'tool_result' for event, _ in frames)


@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS, ids=[row[0] for row in PROVIDERS])
def test_discover_schema_then_read_values_through_real_provider_wires(session, client, kind, model, base_url, auth):
    from importlib import import_module

    import httpx

    from harness.providers.anthropic import AnthropicProvider
    from harness.providers.openai_compat import OpenAICompatProvider
    from services import llm_config
    from tests.test_workflow_provider_parity import _anthropic_stream, _compatible_stream, _last_result

    native = kind == 'anthropic'
    transport = httpx
    if native:
        import anthropic as sdk
        transport = import_module(next(cls.__module__.split('.')[0] for cls in sdk.DefaultHttpxClient.__mro__
                                  if cls.__module__.split('.')[0] in ('httpx', 'httpx2')))
    profile = llm_config.LLMProfile(id='schema-parity', label='Schema fixture', preset='custom',
                                  wire='anthropic' if native else 'openai', base_url=base_url,
                                  model=model, tools=True, vision=False, auth=auth,
                                  fallback_model=None, max_output_tokens=256)
    llm_config.save_profiles([profile], profile.id)
    session.profile_id, session.bound_wire, session.model = profile.id, profile.wire, model
    calls, results, failures = [], [], []
    sequence = [('use_toolset', {'domain': 'results'}),
                ('find_capabilities', {'goal': 'get_component_schema', 'limit': 1}),
                ('get_component_schema', {'component_class': 'StorageUnit', 'query': 'max_hours'}),
                ('use_toolset', {'domain': 'network'}),
                ('get_component', {'component_class': 'Generator', 'name': 'G1'}), (None, {})]

    def respond(request):
        try:
            body = json.loads(request.content)
            index = len(calls)
            if index:
                call_id, content, error = _last_result(body, native)
                assert call_id == f'schema-{index-1}' and not error, content
                parsed, _ = json.JSONDecoder().raw_decode(content[content.index('{'):])
                assert not parsed.get('_truncated')
                results.append(parsed)
            name, args = sequence[index]
            offered = {tool['name'] if native else tool['function']['name'] for tool in body['tools']}
            assert 'get_component_schema' in offered and (name is None or name in offered)
            if kind == 'openai':
                assert len(offered) <= 128
            calls.append(name)
            stream = _anthropic_stream(model, f'schema-{index}', name, args) if native else _compatible_stream(f'schema-{index}', name, args)
            return transport.Response(200, headers={'content-type': 'text/event-stream'}, content=stream)
        except Exception as error:
            failures.append(repr(error))
            raise

    with transport.Client(transport=transport.MockTransport(respond)) as http:
        if native:
            provider = AnthropicProvider(sdk.Anthropic(api_key='synthetic', base_url=base_url,
                                                     http_client=http, max_retries=0))
        else:
            provider = OpenAICompatProvider(base_url, api_key='synthetic' if auth == 'bearer' else None,
                                            http_client=http, token_param='max_tokens')
        frames = list(chat_service.run_turn(session, 'find_capabilities get_component_schema then get_component Generator G1', provider=provider))
    assert not failures, failures
    assert calls == [row[0] for row in sequence]
    assert frames[-1][0] == 'turn_done'
    assert not [data for event, data in frames if event in {'error', 'tool_error', 'tool_pending_confirmation'}]
    assert results[1]['items'][0]['name'] == 'get_component_schema'
    assert results[1]['items'][0]['eligible_for_project'] is True
    assert results[1]['items'][0]['in_current_toolset'] is True
    assert results[2]['items'][0]['name'] == 'max_hours' and results[2]['items'][0]['unit_storage'] == 'h'
    assert results[4]['name'] == 'G1' and results[4]['marginal_cost'] == 10
    assert client.get('/api/network/generators').json()[0]['marginal_cost'] == 10

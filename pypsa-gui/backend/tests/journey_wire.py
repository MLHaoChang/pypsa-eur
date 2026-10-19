"""Tier A owner-journey driver: a scripted model over a real provider wire.

Only the provider's HTTP is mocked (an in-process MockTransport). The turn runs
through the real provider adapter, stream parser, dispatch, handlers and
frames. Each step is a function of the parsed results so far, so arguments
come from native results the way a model's would; `None` as the tool name
ends the turn with `answer` as the text.
"""
import json
from importlib import import_module

from services import chat_service, chat_tools


def run_journey(kind, model, base_url, auth, steps, message, *, mode='expert',
                on_frame=None, session=None, answer='', **turn_kwargs):
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
    profile = llm_config.LLMProfile(id='journey', label='Journey fixture', preset='custom',
                                    wire='anthropic' if native else 'openai', base_url=base_url,
                                    model=model, tools=True, vision=False, auth=auth,
                                    fallback_model=None, max_output_tokens=256)
    llm_config.save_profiles([profile], profile.id)
    if session is None:
        session = chat_service.ChatSession(model=model)
        session.profile_id, session.bound_wire = profile.id, profile.wire
    chat_tools.set_chat_session(session)
    calls, results, failures = [], [], []

    def respond(request):
        try:
            body = json.loads(request.content)
            index = len(calls)
            if index:
                call_id, content, error = _last_result(body, native)
                assert call_id == f'oj-{index-1}', call_id
                parsed = None
                if not error and '{' in content:
                    parsed, _ = json.JSONDecoder().raw_decode(content[content.index('{'):])
                results.append(parsed if parsed is not None else {'error': content})
            name, args = steps[min(index, len(steps) - 1)](results)
            calls.append(name)
            if name is None:
                stream = (_text_anthropic(model, answer) if native else _text_compatible(answer))
            else:
                stream = (_anthropic_stream(model, f'oj-{index}', name, args) if native
                          else _compatible_stream(f'oj-{index}', name, args))
            return transport.Response(200, headers={'content-type': 'text/event-stream'}, content=stream)
        except Exception as error:
            failures.append(repr(error))
            raise

    frames = []
    with transport.Client(transport=transport.MockTransport(respond)) as http:
        if native:
            provider = AnthropicProvider(sdk.Anthropic(api_key='synthetic', base_url=base_url,
                                                       http_client=http, max_retries=0))
        else:
            provider = OpenAICompatProvider(base_url, api_key='synthetic' if auth == 'bearer' else None,
                                            http_client=http, token_param='max_tokens')
        for event, data in chat_service.run_turn(session, message, provider=provider,
                                                 ui_context={'ui_mode': mode}, **turn_kwargs):
            frames.append((event, data))
            if on_frame is not None:
                on_frame(event, data)
    assert not failures, failures
    return calls, results, frames, session


def _with_text(stream: bytes, text: str) -> bytes:
    """The parity helpers' final text frame, carrying `text` instead of 'Finished.'."""
    return stream if not text else stream.replace(b'Finished.', json.dumps(text)[1:-1].encode())


def _text_anthropic(model, text):
    from tests.test_workflow_provider_parity import _anthropic_stream

    return _with_text(_anthropic_stream(model, 'end', None, {}), text)


def _text_compatible(text):
    from tests.test_workflow_provider_parity import _compatible_stream

    return _with_text(_compatible_stream('end', None, {}), text)

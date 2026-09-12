# Chat changes are not covered by the test suite and need a live-API probe

No test in `backend/tests/` constructs a real Anthropic client — the chat suites
drive the SSE and settings paths with monkeypatched seams
(`tests/test_chat_sse.py`, `tests/test_chat_api_key_settings.py`). A change to
the chat path is therefore unverified by a green suite, and must additionally be
exercised against the live API before it is called done.

## Consequences

This is a real gap, recorded rather than closed: a fully green suite once
shipped a total chat outage. The suite proves the plumbing around the client,
never the client. Treat "tests pass" as necessary and not sufficient on any diff
touching chat, and say in the report which live probe was run.

Closing the gap properly would mean either a contract test against a recorded
transcript or a gated live smoke run. Neither exists yet; until one does, the
probe is manual and its absence is a defect in the change, not in the suite.

## Addendum 2026-09-09 — the gated live run now exists

The `feature/llm-provider-config` line added one per wire, in
`backend/tests/test_llm_provider_seam.py`: `test_live_probe_anthropic_wire`
and `test_live_probe_openai_wire_through_a_saved_profile`. They construct a
real client and drive the production path (profile store →
`_provider_for_profile` → `run_turn`), so the opening sentence above is no
longer true of that branch.

Both have been run and passed — openai against a local Ollama on 2026-09-04,
anthropic against the vendor on 2026-09-09. Runbooks:
`docs/superpowers/runbooks/local-openai-wire-probe.md` and
`anthropic-wire-probe.md`.

**This narrows the decision; it does not retire it.** The probes are opt-in and
skip by default, deliberately: an always-on live call would make the suite cost
credit and fail on a network outage. So a green suite still does not verify a
chat change, and the rule stands — exercise the live path and say in the report
which probe was run. What has changed is that "the probe" is now a named test
rather than something each change improvises.

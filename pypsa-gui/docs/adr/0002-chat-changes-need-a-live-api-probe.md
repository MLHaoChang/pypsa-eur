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

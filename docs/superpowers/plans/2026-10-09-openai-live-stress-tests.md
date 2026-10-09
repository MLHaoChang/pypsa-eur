# Budgeted chatbot API stress tests

Approved budget: **20,000 total input + completion tokens**, including reasoning
tokens in the API's completion usage. Use synthetic, disposable networks only.
No Anthropic credential is configured; retain its offline regression coverage.

## Planned matrix and oracles

| Case | Live path | Expected evidence |
| --- | --- | --- |
| Production prompt | Luna, original system prompt, read-tool subset | Streamed tool execution and factual continuation; no harness error |
| Nested Unicode batch | Luna, real batch-create handler | Array/object/numeric arguments preserve quoted and Unicode names; both rows created with correct voltages |
| Multiple read calls | Luna, unrestricted choice within a read subset | Both requested components fetched, distinct call/result IDs, factual summary |
| Handler error recovery | Luna, missing component then valid component | Real 404 becomes a paired tool error; model corrects the name and continues |
| Untrusted tool data | Luna, malicious text in a bus name | Counts rows without obeying the instruction embedded in the name |
| Guided denial | Luna, create request with simulated UI denial | Confirmation card emitted; no row created; continuation acknowledges denial |
| Guided approval | Luna, create request with simulated UI approval | Confirmation card, actual write, then a separate read-back |
| Structured choice | Luna, nested `ask_user` options | UI choice card preserves recommendation and disables free text |
| Atomic write recovery | Luna, duplicate-name batch then corrected batch | Rejected batch leaves the network unchanged; corrected batch succeeds |
| Streaming cancellation | Luna, abort on tool preparation | Stream stops before dispatch; no component created |
| Sol tools | gpt-6.1-sol | Real read tool, paired result, factual streamed continuation |
| Earlier Sol / Astra tools | gpt-6-sol / gpt-6-astra | Same tool/continuation contract; inspect each model's actual API errors |
| Astra switching | gpt-6-astra, history from a Luna turn | Saved profile/model switch preserves history; final response uses the earlier tool result |
| Invalid authentication | Deliberately invalid key | One request, unauthorized error, no retry or tool execution |
| Full production catalogue | Offline size measurement | 186 tools, about 30,988 tool tokens + 2,601 system tokens: live request blocked by this budget |

Forced first-tool choices establish adapter/handler contracts. Unforced read,
recovery, and injection cases evaluate model decisions. Keep those results
separate: a forced successful call is not proof of autonomous tool selection.
Deterministic oracles check tool arguments, IDs, network state, error frames,
and required facts; these are diagnostics, not a general intelligence score.

## Controls and execution order

1. Add an opt-in runner; ordinary pytest runs must make zero live requests.
2. Verify budget reservation/refund, missing-usage accounting, and request caps
   offline before making paid requests. Tokenize serialized payloads locally
   with `tiktoken` (`o200k_base`), add a 25% input margin plus 512 tokens, and
   reserve the full output limit before each send. This is a conservative
   estimate, not an API-guaranteed input-token limit.
3. Record server usage after every completion. On failures after a send with
   missing usage, keep the entire reservation charged. Stop on quota/auth
   failures, maximum requests, or insufficient remaining reservation.
4. Run the matrix once; leave approximately 3,000 tokens for targeted retests.
   Do not spend spare budget on duplicate successful cases. Persist sanitized
   results, responses, tool frames, errors, and token counts after each case.
5. Reproduce confirmed implementation defects offline, fix them, then rerun
   only affected live cases if budget remains. Report behavior failures even
   when no code fix is justified.
6. Run relevant backend/provider/Anthropic/confirmation regressions and frontend
   tests/build. Report budget skips and infrastructure blockers explicitly.

The runner uses the configured proxy and CA trust. Credentials never enter
source files or reports. The previous failed environment remains untouched.

## Reproduction

Install `tiktoken` in the test environment (optional for ordinary tests), then
run from the backend directory using the configured credential:

```sh
OPENAI_API_KEY="${OPENAI_API_KEY:-${PYPSA_GUI_OPENAI_API_KEY}}" \
  PYPSA_GUI_TEST_LIVE_OPENAI_STRESS=1 \
  PYPSA_GUI_TEST_LIVE_TOKEN_CAP=20000 \
  python -m pytest tests/test_openai_live_stress.py -k live_harder -s
```

`PYPSA_GUI_TEST_LIVE_CASES` selects comma-separated case IDs. For a targeted
rerun, set `PYPSA_GUI_TEST_LIVE_INITIAL_CHARGED` to the previous report's
`charged_tokens`, so the budget covers both invocations. Set
`PYPSA_GUI_TEST_LIVE_RESERVE=0` to use the reserved retest allowance, and
`PYPSA_GUI_TEST_LIVE_REPORT` to a separate JSON path. The request cap is per
invocation. Do not reset carried usage during a funded test run.

The first production case retains the complete system prompt but narrows tools.
All other cases use a short diagnostic prompt and unchanged catalogue entries.
Only the first choices are forced where listed; multiple reads and untrusted
data use autonomous first-tool selection. Confirmation decisions simulate the
UI through the real session API; product confirmation rules are unchanged.

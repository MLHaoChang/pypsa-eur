# Comprehensive OpenAI harness coverage

The owner expanded the run to the remaining $5 with a 1,000,000-token ceiling,
then raised the total ceiling to 5,000,000 tokens for the autonomous workflow.
Both limits include carried usage from the diagnostic phase. Standard pricing
was verified on 2026-10-09 at https://developers.openai.com/api/docs/pricing.

Use `gpt-6-luna` for the exhaustive matrix. Its standard short-context rates
are $0.10 input / $0.01 cached input / $0.125 cache writes / $0.50 output per
million tokens. Long-context rates are $0.20 / $0.02 / $0.25 / $0.75. Reserve
all input at $0.25 and output at $0.75 per million, with no caching discount.
Carry $0.05 conservatively for previous testing. This leaves room within $5
even if the complete token allowance is used. Unknown model prices are refused.

## Coverage levels

1. **Every registered tool's API contract:** real streamed API function call,
   schema validation, real handler-signature binding, harness dispatch into a
   recording double, confirmation lifecycle, matching call/result IDs, and a
   real API continuation. Doubles prevent 201 unrelated project/solver/report
   side effects; this level is not successful execution of the real handler.
2. **Offline actual handler coverage:** instrument existing passing tests to
   record which real wrappers ran successfully, which tested errors, and their
   reusable argument samples. Route/service mocks may exist in these tests, so
   label this offline coverage rather than live external-service success.
3. **Real multi-turn conversations:** unchanged handlers and real disposable
   networks for read/write/read-back, rename/delete, errors, snapshots and time
   series, choices, skills, workflows, UI control, and injected data. Assert
   network state and frame contracts after every step. Preserve confirmation
   gates, with test UI decisions recorded on the real session API.
4. **Production catalogue:** send the real complete prompt/tool catalogue once
   within the expanded budget. Diagnose any upstream catalogue/schema limit.
   The live endpoint rejected 186 declarations because its maximum is 128.
   Wiring now selects at most 128 per turn, prioritizing explicit names,
   harness controls, recent calls, and query vocabulary. All 201 tools remain
   eligible; preserve original order for prefix caching and enforce the
   selected dispatch allowlist. Anthropic and local endpoints keep their
   existing catalogues. The bounded production request passed live.
5. **Negative and reliability regressions:** retained offline tests cover
   authentication, truncation, fragmented/malformed JSON, disconnects, retries,
   provider switching, budget refusal, and Responses continuation.
6. **Autonomous project-to-GridSpine workflow:** actual production prompts and
   selected tools, with no forced tool choices. Create IEEE39, parameterize
   every generator, solve and save; branch a cost sensitivity, solve and compare;
   create and bind a study, choose the saved source, queue its pipeline; read
   ranked snapshots/capacity and assess a connection; raise k, rerun, compute
   AC capacity and interpret the refined results. Filter stored assessments by
   hour to recover data truncated in the whole-study output. Assert saved
   network objectives, unchanged baseline, exact cost delta, stage artifacts,
   ranked coverage, actual assessment counts and stated scope limitations.

## Execution and caching

Generate an inventory from `TOOLS`, never a fixed tool count. Derive contract
arguments from successful offline samples when schema-compatible; otherwise
construct a minimal schema-valid object. Test generator/schema/signature parity
offline first. Flag unsupported schemas and missing real-handler fixtures.

Keep system prompts and catalogue ordering stable. Group tool declarations in
fixed groups of eight so successive contract cases can reuse the API prefix.
Reuse model discovery, parsed pricing, argument samples, and fixture metadata
locally. Track the API's reported cached input tokens. Long conversations reuse
one session; do not restate previously established facts in each user message.
Retain normal harness trimming/budget rules rather than replacing the context.

Persist accounting after every test. Reserve estimated input with a 25% margin
plus 512 tokens and the entire output limit before every HTTP attempt. Keep full
reservations for interrupted streams with missing usage. Stop on either limit,
unknown pricing, quota/authentication failure, or insufficient next reservation.
Run targeted retests with carried token and dollar accounting, never a reset.

Responses authentication currently fails through the managed credential proxy;
do not repeatedly retry it. No Anthropic key is available. Tools requiring
external credentials, proprietary engines, or unavailable fixtures receive an
explicit coverage blocker, not a fabricated success. Do not spend credit merely
to hit $5 after the matrix is complete or blocked.

## Reproduce

From the repository root, use the configured test interpreter and install
`pypsa-gui/backend/tests/live-api-requirements.txt` when running outside Pixi.
Set `OPENAI_API_KEY` through the environment's secret binding. Never put a key
in a command literal, transcript, report, or source file.

```sh
PYPSA_GUI_TEST_LIVE_COMPREHENSIVE=1 python -m pytest \
  pypsa-gui/backend/tests/test_openai_comprehensive_live.py \
  pypsa-gui/backend/tests/test_openai_conversations_live.py -s -q
```

`PYPSA_GUI_COMPREHENSIVE_REPORT` selects the JSON ledger (default
`/tmp/openai-comprehensive.json`). Preserve it between retries. It carries
usage, reservations, successful cases, and exact conversation checkpoints.
Completed contracts and conversations skip on rerun; partial conversations
rebuild their disposable network locally and reuse the recorded API history.
Accounting checkpoints before every send and after every usage settlement.
Do not delete the ledger to get around a budget refusal.

The added autonomous suite is
`pypsa-gui/backend/tests/test_openai_project_workflow_live.py`. Its offline
fixture runs real engines without API calls. Paid turns let the model choose
and sequence tools, and resume completed phases with a local engine replay.
Fresh fixture project UUIDs are remapped from recorded names. A changed budget
requires explicit owner authorization; this run used
`PYPSA_GUI_COMPREHENSIVE_TOKEN_CAP=5000000` and retained every prior charge.

Export per-tool coverage without copying full transcripts:

```sh
python pypsa-gui/backend/smoke/report_openai_coverage.py \
  --output /tmp/openai-tool-coverage.json
```

The offline handler capture is obtained by loading `tests.tool_coverage_capture`
as a pytest plugin with the backend first on `sys.path`. Both ledger and
capture file are required for the export. The versioned assessment contains
the final counts and the sanitized per-tool result from this run.

# OpenAI harness test results — 2026-10-09

The comprehensive live suite passed for `gpt-6-luna`: all 201 registered tool
contracts, the production prompt with 128 selected tools, and four real
conversations totaling 56 user turns, plus an eight-phase autonomous project,
sensitivity and GridSpine workflow. All five harness controls executed live.
The previous failed environment was untouched.

## What the tests establish

| Check | Result | Scope |
| --- | --- | --- |
| Every registered tool | 201 passed | Streamed function arguments, schema validation, real signature binding, dispatch double, matching result IDs, real API continuation |
| Production catalogue | Passed after fix | Actual system prompt, 128 offered tools, real bus handler, streamed 110 kV answer |
| Network conversation | 20 turns passed | CRUD, batches, bulk updates, read-back, denied write, deletion, carrier, memory |
| Time-series conversation | 13 turns passed | Snapshots, weightings, download metadata, generation, CSV upload, read-back, deletion |
| Harness/UI conversation | 13 turns passed | Skills, workflows, choices and selection, UI events, expected errors, memory |
| Solver conversation | 10 turns passed | Actual HiGHS optimization, objective 200, status and result retrieval |
| Autonomous project/GridSpine workflow | 8 phases passed, 61 API completions | Production prompt/catalogue, model-chosen tool sequences, real saved projects, sensitivity, queued GridSpine runs, ranked snapshots, AC capacity, connection assessments and interpretation |
| Earlier hard diagnostics | 11 distinct cases passed | Unicode/escaped arguments, multiple calls, recovery, atomic batch, untrusted names, confirmations, abort, Astra text history |
| Instrumented offline handlers | 95 observed successful wrappers | Existing passing tests; service/route doubles may be involved |
| Broad backend regressions | 2,880 passed, 214 skipped, one fixture failure | Chat, providers, harness, projects, session recovery, queue, GridSpine; skipped probes require explicit opt-in or prerequisites |
| Upload suite after fixture correction | 44 passed | Sole broad-run failure corrected using a real DOCX package; application MIME guards unchanged |
| Study binding and recovery recheck | 131 passed | Activation, cold hydration, tenant boundaries, user time series, chat rebinding and layout |
| Frontend tests | 3,416 passed across 292 files | Complete frontend suite, including new study binding/refusal behavior |
| Frontend build | Passed | TypeScript and Vite; existing chunk warnings |

The 201 contract cases use a recording dispatch double: they prove that the
API and harness can call every declared tool and continue, not that every
domain engine completed a real operation. The conversations verified real
successful execution for 54 distinct handlers and expected error/denial paths
for three. The [per-tool JSON](2026-10-09-openai-tool-coverage.json) records
these separate levels and identifies the remaining live execution checks.

## Fixes found through testing

- OpenAI rejected the production catalogue with `array_above_max_length`:
  186 offered tools exceeded its 128 maximum. Selection now prioritizes names,
  harness controls, recent calls and query terms, preserves declaration order,
  and caches parsed vocabulary. Every eligible tool can be selected by name.
  The dispatch allowlist and `session_init.tool_count` use the exact selection.
  Anthropic and other compatible endpoints retain their original catalogues.
- GridSpine tool filtering was passed no context on the production path, so
  study tools stayed hidden. It now resolves the active project context.
  A second gap prevented that context from ever being bound: study activation
  required `network.nc`, although studies are config/run directories. Activation
  and cold session resolution now build an empty context for a registered study
  with its config present, retaining ACLs and ordinary missing-network refusals.
  Frontend study switching and creation bind the backend before opening the
  study. Tenant, cold recovery, missing-config and refusal checks pass.
- Two download tools returned HTTP response objects, producing opaque Python
  representations in model context. They now return filename, media type,
  and an authenticated download URL. Actual CSV/XLSX route checks passed.
- Official Sol/Astra tools require the Responses transport. The new adapter
  translates function calls/results, parses streaming completions, reuses
  server response IDs to retain private reasoning, handles errors, and drops
  the continuation pointer when model/history changes. Offline regressions
  pass; live authentication to this endpoint remains blocked below.
- Both transports expose reported cached input usage without subtracting it
  from total input accounting. Stable tool groups and prompts permit prefix
  reuse. Paid test checkpoints cache successful cases and conversation state;
  retries do not replay completed API calls.
- The pre-existing DOCX upload test built a one-entry fake ZIP, which some
  libmagic builds correctly identify as generic binary data. Its fixture now
  uses a real Word OOXML document. The entire 44-test upload suite passed
  afterward, including renamed executable rejection; MIME guards were unchanged.

## Autonomous workflow evidence

The model created IEEE39, read all 15 generators, set marginal costs to 10,
solved and saved the baseline, then branched a sensitivity with costs of 20.
Independent saved-network checks measured objectives **1,198,935.891** and
**2,397,871.782**, with unchanged baseline and dispatch. The model reported
both values and correctly explained the doubled operating cost.

It created and activated a GridSpine study, selected the saved baseline as
dispatch source, and queued the actual pipeline. All stages completed. The
k=1 run selected four hours; the k=2 refinement selected seven, all converged,
with screening and handoff artifacts. It computed and read back AC connection
capacity at BUS_16 and assessed a 1 MW load. Its initial interpretation stated
the assessment-output truncation; the follow-up read each hour separately and
correctly counted **six connection passes and one failure**. It identified the
hour-11 outage of BUS_10-BUS_11-1 and high voltage at BUS_28, suggested checking
that contingency, and retained the steady-state/FRT/compliance limitations.

These are backend harness and API checks with simulated confirmation decisions
on real sessions. They are not a manually exercised browser journey.

## Usage and caching

The final limits were 5,000,000 total tokens and $5, including conservative
carry from the earlier diagnostic phase. Standard model pricing was confirmed at
https://developers.openai.com/api/docs/pricing before this phase.

The comprehensive phase made 577 HTTP requests, including the rejected
catalogue request (400, zero usage). It reported 3,159,974 input and 14,009
output tokens. Cached input totaled 2,536,817, approximately 80% of input.
Adding the 19,744-token diagnostic carry gives **3,193,727 budgeted tokens**.
The conservative cost upper bound is **$0.85050025**, including a $0.05 carry;
it charges all input at $0.25/million and output at $0.75/million, assumes no
cache discount, and is not the billing ledger. No calls were added just to
consume remaining credit.

## Remaining blockers and limits

- The managed credential proxy returns 401 `invalid_api_key` on
  `/v1/responses` while Chat Completions succeeds. Sol/Astra live tool loops
  remain unverified. The endpoint was not repeatedly retried or bypassed.
- Live invalid-key testing is inconclusive: managed credential injection
  accepted the invalid-header Chat Completions probe. Mock HTTP 401/403,
  missing-key, no-retry, and redaction checks cover application behavior.
- No Anthropic credential is available for a live regression. Existing
  Anthropic transport, thinking, tool and profile regressions run offline.
- Astra/Sol are available API IDs, not display labels guessed into requests:
  earlier authenticated discovery returned `gpt-6-astra`, `gpt-6.1-sol`, and
  `gpt-6-sol`. Astra text continuation passed live. Discovery alone does not
  establish working tool support through the managed proxy. Pricing for
  `gpt-6-sol` was not confirmed, so it was excluded from the expanded paid run.
- Successful real live execution of the other 147 handlers needs domain
  fixtures and, for some tools, external engine installations or credentials.
  Their API contracts passed; unsupported prerequisites are not counted as
  successful operations.
- Selection is heuristic and limited to 128 declarations per turn. Explicit
  tool names and recent calls are retained; ambiguous natural-language
  queries may omit a needed tool. The full registry remains unchanged.

See the [plan and reproduction instructions](../plans/2026-10-09-openai-comprehensive-tests.md).

# Chat workflow tools — 2026-10-09

Five provider-neutral tools extend the existing project, queue, simulation and
GridSpine handlers. No existing handler or Anthropic adapter was replaced.

| Tool | Behavior |
| --- | --- |
| `project_readiness` | Authorized project kind, validation, solver availability, saved/dirty state, time-series readability and study freshness; suggested next tools |
| `wait_for_job` | Locally wait up to 25 seconds for an authorized queue job, with existing progress frames and cancellation; no model polling calls |
| `get_study_evidence` | Filtered, paginated GridSpine evidence with counts computed before status filtering/paging, freshness checks and a bounded fingerprint cache; saved simulation headlines and comparisons |
| `run_sensitivity_sweep` | Preflight 1–4 typed cases, create isolated saved children, enqueue existing solves, preserve baseline/active binding and reuse matching solved cases |
| `use_toolset` | Stable ordered catalogue views for project/network/simulation/results/GridSpine; retain controls, project navigation and exact dispatch allowlists |

The catalogue now refreshes between model requests after an intentional project
activation or toolset switch in the same turn. The previous batch is checked
against the tools actually offered to that batch. Execution-tier sensitivity
sweeps still use the normal confirmation gate. A new `project-refinement` skill
describes baseline → solve → save → sensitivity → wait → compare → GridSpine
refinement, including the engineering limits below.

## Verification

- Final affected backend regression recheck: **390 passed**. Coverage
  includes real HiGHS solves, two-case reuse, baseline preservation, invalid
  case preflight, partial queue failure, confirmation denial, cross-organization
  refusals, job timeout/cancellation/failure/interruption, progress frames,
  same-turn study activation, evidence count/page/cache invalidation, long-row
  clipping with readable counts/cursors, active-job reuse refusal, investment
  period weighting, partial save identification, full Anthropic catalogue/view
  parity, case-folded name conflicts, context recovery, and a real
  24-hour GridSpine ranked-snapshot/capacity/connection pipeline.
- Broad backend regressions: **2,867 passed, 220 skipped** across 120 files.
  One existing source-shape tripwire expected the old timeout expression. It
  now checks the shared deadline and remaining-time expression; its entire
  chat end-to-end suite passes in the final 390-test recheck. No unresolved
  test failure remains. Paid/prerequisite-gated tests explain the skips.
- Complete frontend suite: **3,416 passed across 292 files**; TypeScript/Vite
  production build passed.
- Bounded real OpenAI conversation: **passed**, using the production prompt,
  eligible catalogue and real handlers, with unforced `gpt-6-luna` tool choice.
  Sixteen API completions exercised all five new tools, baseline solve/save,
  two queued scenarios, local waits, saved comparisons and solved-case reuse.
  The baseline objective was **100**, and scenario objectives were **200** and
  **400**. The original network's generator marginal cost remained **10**.
  Both cases were reused on the second sweep without extra solve jobs.

Two earlier paid diagnostic runs also solved the three models. They exposed a
mistyped model-generated project identifier (recovered by the model) and an
oversized reuse result. Reused cases now return compact objectives; simulation
comparisons return saved headline deltas rather than entire economic tables.
The regression checks verify that these fields survive the harness's existing
4,000-character result cap. Detailed tables remain available from existing tools.

This implementation phase used **55 API requests**, **796,488 input/output
tokens** and an upper cost estimate of **$0.20068900**, including diagnostic
attempts and the final validation after the additional edge-case fixes.
The carried aggregate ledger is **632 requests**, **3,990,215 charged
tokens**, **3,112,680 reported cached input tokens**, and an upper estimated cost
of **$1.05118925**, below the authorized 5-million-token/$5 caps. Accounting
conservatively charges all input at confirmed uncached rates and is not an
OpenAI billing statement. No calls were added to exhaust remaining credit.
Successful unchanged live cases are checkpointed by implementation digest.
An immediate unchanged replay skipped the paid case and left request, token
and cost totals unchanged.

## Limits and remaining prerequisites

- A timed-out wait returns pending; cancelling the wait does not cancel its job.
  The existing queue abort action remains separate.
- Sweeps change static component parameters, retain time-series overrides and
  support at most four cases per call. GridSpine configuration refinement uses
  its existing configuration and pipeline tools. Large preparations remain
  subject to the harness's normal per-tool deadline; partial saved work is
  reported and retained.
- Evidence freshness uses saved configuration and artifact/source timestamps.
  GridSpine screens establish steady-state evidence, not dynamic or regulatory
  compliance. Simulation evidence and comparisons use last saved results.
- The earlier managed-proxy 401 on `/v1/responses` still blocks live Sol/Astra
  tool loops; no repeated blocked-endpoint probes were made in this phase.
  Anthropic live checks still require an Anthropic credential. The existing
  offline provider tests cover those adapters and error paths.
- This phase verifies the new tools and selected real domain workflows; it does
  not establish successful live execution of every external/domain handler.
  See the [earlier coverage report](2026-10-09-openai-comprehensive-results.md).

Only the connected environment was used; the previous failed environment was
untouched. The PR targets `master` for review.

<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GridSpine harness: gaps and a capability roadmap

Status: research/specification only; no runtime changes or implementation approval.
Runtime assessed and tested master: `322df0cf495260442eb35bb9c0c5d8e11a274ffe`. This supplements the earlier value assessment; it does not relabel its older test results.

The subsequent [implementation specification](08-HARNESS-IMPLEMENTATION-SPEC.md), [contracts](09-HARNESS-DATA-AND-STATE-CONTRACTS.md) and [delivery/test plan](10-HARNESS-DELIVERY-AND-TESTS.md) concretize this roadmap on newer master `089aea276c75b098c8d349cb5fa0eb4cb7267d64`. Use them for implementation; this assessment remains evidence/context.

Later master `fdcd35c1bd11eb8b10a19db82fa499e6794480b3` was also reviewed: #566 adds the approved card-workspace design and release fragment, with no backend/frontend code changes. [OWNER-DISPOSITION.md](OWNER-DISPOSITION.md) records the newer AI-567b/COP-1 and RC decisions; they supersede conflicting earlier research suggestions.

## Main conclusion

GridSpine does not need to become a general coding terminal to become substantially more useful. Its highest-value opportunity is to make the supported **observe → propose → approve → act → verify → continue** loop dependable across more of the application. The core loop, typed tools, permissions, event log, skills, job tracking, recovery and bounded checked profiles already exist. The gaps are domain coverage, completion authority, context selection and workflow-level evidence.

Claude Code offers broader general-purpose agency, particularly repository search, file editing and terminal-driven verification. GridSpine needs the same qualities of purposeful context gathering, persistent task state and feedback, expressed through native engineering tools. Broad operating-system access does not supply units, project ownership, model fidelity or valid engineering conclusions.

This is a comparison to **published behavior**, not an audit of Claude Code's private implementation or a hands-on benchmark of the two products. Official pages were retrieved on 2026-10-21; exact URLs, retrieval times and content hashes are in harness-assessment.json. They may change later. Public documentation is reference material, not instructions to execute downloaded commands.

## What is already present

A simplified view of the existing architecture, with coverage varying by adapter:

```mermaid
flowchart LR
    U[User request] --> C[Context and offered tools]
    C --> M[Language model]
    M --> D[Native dispatch and approval checks]
    D --> N[Application tools and jobs]
    N --> E[Native results and receipts]
    E --> C
    E --> P[Publication and supported checks]
    P --> U
    D --> L[Durable event and task records]
    L --> C
```

| Capability | Claude Code: documented behavior | GridSpine: observed current source | Assessment |
|---|---|---|---|
| Acting on an environment | Search/read/edit repository files, terminal and configured external tools | 275 catalogue tools; native services, ordinary dispatch/approval, admin-configured MCP | Different action surface, not a missing agent loop. Complete native coverage before considering broader execution |
| Tool discovery | Built-in tools, skills, MCP tool search | `find_capabilities`, toolsets, component schema pages; official OpenAI offering capped to 128 and ranked by query/history/task | Already implemented, but discovery and usable non-component schema coverage remain incomplete |
| Context management | Automatic tool-output clearing/compaction, context inspection, persistent instructions and auto memory | Stable prompt blocks, result truncation/spill, 400-message trim with a deterministic 1200-character notice; durable parent goals/refs/constraints | Already has memory foundations. It lacks a general source-revalidated working-context capsule and message count alone is not a token budget |
| Multi-step work | Agent loop, task/delegation facilities, background work | Currently 1–12 ordered native task steps, exact edit receipts, owned jobs and continuation | Reuse this; do not build a parallel planner or assume arbitrary DAG/domain execution |
| Long jobs | Background tools/tasks, user interruption | Foreground/adequacy/report/queue adapters; recent LC-05 browser continuation holds for draft text/cards and can dismiss waiting | Recently improved; not a wholly absent feature. Job observation is broader than automatic verified task settlement |
| Procedures and extensions | Skills, configured MCP, lifecycle hooks; hooks can include model-based behavior | On-demand packaged/install-owned skills, workflow registry, central dispatch checks, external MCP | Extend trusted domain procedures and shared validation. Arbitrary project hooks/scripts are not required |
| Verification | Read diffs, run tests/commands, inspect outputs | Source-bound result readers, checked calculations, edit readback and finite pilot evaluation | Strong foundations but bounded scope. A passing unit test, mounted tab or model review is not full engineering validation |
| Continuity/undo | Saved sessions and file checkpoints; remote effects not universally reversible | Event log, durable threads/tasks, snapshots, effect receipts and reconciliation | Preserve separate Safe restore work; do not promise universal undo from snapshots |
| Delegation | Scoped subagents with separate contexts/tool access | One bounded read-only interpretation reviewer; no general study-specialist agent team in the inspected harness | Optional future optimization, not the first missing feature or evidence of correctness |

The source inventory check currently reports **275 tools, 17 result tabs and 20 panels**. The matrix reports **170 creation controls, 14 family assignments and full_coverage=not_established**. Every per-tool behavioral fixture and all 14 family-journey links are pending in those generated records. This does **not** mean there are no passing journeys in the repository: OJ-07 and other subset tests exist. It means the overall ledger has not connected and certified the required coverage. Historical prose in application-coverage.md still contains older counts; use the generated current checks and source, not those numbers.

## Prioritized gaps and recommended changes

### H01 — capability and evidence coverage (foundation priority)

**Observed:** component field discovery describes types/units/defaults, but README explicitly excludes non-component service/domain schemas and warns that metadata does not prove project instances or engine support. Result views expose partial checked profiles/gaps; only load flow has the full displayed-data browser adapter. The matrix is mostly creation-field mapping, not a complete audit of every editor/filter/action.

**User impact:** “Can you update this parameter?” or “Walk through this result” may work in one domain and be unavailable in another, while a long tool list suggests broader ability.

**Change:** extend the existing capability/control/result registries with real domain schema, allowed read/write/preview actions, project/card eligibility, run requirements, units/basis, job/evidence adapter and an explicit unsupported/manual/security disposition. Bind actual passing journeys to those rows. Discovery should distinguish supported, needs-input, unavailable-here, manual-only and prohibited rather than invent an endpoint. Maintain ordinary offered-tool admission; discovery never grants access. Avoid another registry or blanket exposure of administrative/secrets operations.

**Owners:** LC-01/LC-06/LC-22, S7, W0/W9/W10 and existing inventory/matrix tools. Supports HV-S1, HV-D1 and HV-I1. Prioritize common asset/series inputs and per-card result questions, then fill domains according to user demand.

### H02 — completion must distinguish acknowledgment from native proof (foundation priority)

**Observed:** normal edit steps require a verified `_edit_receipt`; solve steps require exact identity-bound job/run evidence. But `resolve_task_step` can set completed using a caller-supplied result after ordinary destructive confirmation. It checks ownership and originating-worker quiescence; it does not independently reconstruct every submitted result. Confirmation records an authorized decision, not necessarily the truth of a scientific claim.

**User impact:** a manually reconciled task can look complete even when the evidence needed for “this simulation succeeded with these inputs” is absent.

**Change:** in existing task/evidence handling, distinguish user-acknowledged/reconciled disposition from natively verified outcome. Derive verification from the exact effect/job/source adapter before dependent operations consume it as proof or checked prose claims completion. If the adapter cannot establish the outcome, retain Needs review. Legacy manual completion remains an observation; do not retroactively certify it. Retry still needs current ordinary authorization and positive safe native conditions; uncertain effects never auto-replay.

**Owners:** LC-05/LC-12/LC-19, existing task/effects/reconciliation adapters; S7. Extends HV-S2/S3. This assessment identifies a product assurance boundary, not an exploited authorization bypass.

### H03 — more useful context with less repeated history (next priority)

**Observed:** trim retains up to eight short lines naming old user requests and tools. It is deliberately deterministic and pairing-aware. Durable parent storage already has goals, constraints, units, source refs and task/job IDs, so “no persistent memory” would be incorrect. Resume explicitly rehydrates discussed-run refs; there is no general verified reconstruction of all relevant study assumptions and constraints into every model request. A 400-message cap does not express the actual token footprint of long schemas/results.

**User impact:** old requirements may become hard to resolve, while irrelevant history and tool definitions increase cost and distract the model.

**Change:** assemble a compact native working-context capsule from existing owner-scoped records: reviewed goal and constraints, selected cards/periods, authoritative source IDs/revisions, exact pending action and unresolved assumptions. Distinguish user decisions from model suggestions and imported text. Revalidate source/access/freshness on use; keep raw artifacts on demand through existing readers/spill. Add token-aware packing to existing limits without breaking tool-use/result pairs. Preserve stable prompt/schema order where possible. A missing or obsolete capsule prompts reread/clarification, never silently grants authority.

Start with deterministic structured state, not an always-running paid summarizer. Narrative summaries may later help retrieval but cannot overwrite budgets, source bindings, native facts or approvals. Do not restore deferred answer caches, task-profile endpoints or price-accounting work. Measure existing usage/cache fields and context composition before claiming savings.

**Owners:** durable conversation, history/compose/provider wiring, W9/S7/LC-05. Supports HV-S1/S3 and all long studies.

### H04 — wider verified analysis and honest confidence (next priority)

**Observed:** strict checked turns admit only mapped tools/profiles and otherwise refuse. Several result views remain partly or entirely outside checked scope. BESS decision logic is domain-specific; report number matching alone is weaker than source/semantic verification. Current schema and dispatch success cannot establish physical validity or the correct interpretation.

**User impact:** users need comparable, explainable results across capacity, grid, reliability, commercial and control; they should not mistake a planning proxy for certified compliance or a successful solve for meeting every objective.

**Change:** extend native metric/formula/units/basis/coverage adapters per real question, with independent numerical fixtures and exact current/retained source binding. Fold plausibility rules into LC-20 and native report chapters into LC-21; distinguish not evaluated, outside scope, unsupported and failed. Add source-bound browser adapters where needed. Use existing inline source/status explanations for input-quality, model-scope, freshness and verification gaps; no separate evidence map/advisor interface or unexplained confidence percentage. An unmapped request stays unavailable until its adapter is validated.

**Native-engine limitation:** merged #566 records a Link-connected AC-PF dispatch bug and gates the relevant load-flow work on its fix. Source inspection of `_fix_dispatch_for_ac_pf` confirms it copies generators/storage/stores but not Link dispatch into `p_set`. This assessment did not reproduce the numerical impact; the merged design/gatekeeper records it. Track and resolve that native defect in its gated engine slice before claiming validated Link-connected AC results. Exact provenance can faithfully identify a result without proving its underlying physics is correct.

**Owners:** checked-results/S7, native engines, LC-20/LC-21 and W10. Supports HV-D1/D2/D3; do not add a generic LLM calculator or resurrect removed tool stubs.

### H05 — study execution across admitted domains (next priority)

**Observed:** ordered tasks, generic job observation and normal foreground solve continuation exist. Job adapters cover adequacy/FMEA/MC/frontier/coupling/EH/investment and reports as well as foreground/queue work. That is not equivalent to a uniform multi-domain verified completion/continuation/cancellation contract. Current tasks are bounded ordered calls, not an arbitrary branching study graph, and project-rebinding steps are excluded.

**User impact:** larger goals still require coordination, particularly when a failed prerequisite, partial study, model edit or missing result changes what should happen next.

**Change:** extend the existing task/job adapters one domain at a time with exact outcome, evidence, dependency and cancellation semantics. Use the existing plan card for a cross-card brief and bounded phases; LC-05 raises its finite step cap in the owning slice. Every write/run needs its own card click, each card boundary pauses at Continue to card, and reads proceed without an extra stop (COP-1). On new assumptions/failures, propose a revised phase and invalidate stale previews/confirmations instead of silently replanning effects. Distinguish stop speaking, dismiss continuation, cancel future task steps and actual native job cancellation. Do not label budget-exhausted/unreachable/unknown as successful completion simply because it is terminal.

**Owners:** LC-05/LC-12/LC-19 and current native adapters, S5/S6/S7. This is the enabling work for HV-S, not a second orchestrator. Preserve the newly merged browser continuation and its unsent-draft/card hold behavior.

### H06 — prove user journeys and expose useful diagnostics (cross-cutting priority)

**Observed:** events, replay, budgets, aggregate latency/tokens, inventory and a finite adversarial checked gate already exist. They do not establish all-domain agent success or calibrated error rates, and the generated full-coverage ledger still declines that claim.

**Change:** connect current passing tests to a versioned coverage ledger and add missing representative journeys across all 14 families. Cover the six-card combinations through supported matrix rules, both Guided/Expert, source navigation, owner isolation, long/restarted conversations, partial effects, user interruption and provider tool-offering limits. Use deterministic/fake providers for control tests; separately evaluate actual language/tool selection only when authorized. Record task completion, wrong-tool selection, missing evidence, invalidated previews, false completion, native errors, latency and provider-reported token/cache usage using existing event/metric surfaces. Replay effectful turns only in isolated synthetic fixtures or read-only mode; never reexecute production writes merely to compare providers.

Do not use a model committee vote, inventory count or path-filtered green job as full functional proof. New live measurements and independent domain review remain pending; no paid evaluation or reviewer contact occurs here.

**Owners:** issue-05/08 evidence, owner journeys, eventlog/replay/metrics and CI. Supplies release evidence for every HV capability.

### H07 — richer trusted domain procedures (subsequent)

**Observed:** skills/workflows and MCP already exist. Their declarative text cannot override dispatch policy. Install-owned skills are deliberately separate from untrusted project/upload content. MCP can execute configured external processes; this is not evidence that arbitrary model-authored Python is sandboxed.

**Change:** add concise, tested procedures for actual supported goals (e.g. critical-service design trade-off, tariff completeness, production shortfall), loading schemas/evidence only when needed. Use existing shared dispatch/validation points for non-negotiable preconditions and postconditions. Version procedures with native tool/schema support and runnable fixtures. Keep project-specific notes as fenced data, never automatically executable skills. Do not copy Claude Code's arbitrary hook/command surface into a multi-user simulation app.

**Owners:** existing skill/workflow registries and domain adapters, W9/W10. A procedure is useful only if its named tools and evidence are real.

### H08 — optional specialist reads and analysis workspaces (later, conditional)

**Observed:** the current interpretation reviewer is one bounded read-only call with no tools/new facts. It is not a generic specialist workforce. There is no inspected native general-purpose sandbox for arbitrary model-authored engineering code. Claude Code's configured OS sandbox cannot be assumed to exist here merely because both products use tools.

**Possible addition:** if measured tasks justify it, first prototype a scoped read-only specialist that receives a small exact source packet and returns cited observations/gaps. It gets no write tools, approvals, new project binding or authority over publication. Parent budgets include all child work; no recursive spawning, unbounded concurrency or full-history cloning. Disagreement is surfaced and resolved with native evidence, not majority vote.

A computation workbench is a separate conditional design: prefer named native queries/calculations first. Any later free-form code execution needs a genuinely isolated optional worker, immutable admitted data, restricted network/files/resources and reviewed artifact promotion. No in-process `eval`, generic shell access or “analysis” that can mutate live inputs. Such a worker needs a separate approved native-engine contract and deployment review; it is not part of this PR's delivery or already approved by the existing engine-worker roadmap.

**Owners:** no new team/workbench package authorized. Reuse the reviewer/optional-worker infrastructure only after an accepted narrow design. Preserve AI-558's deferred price-accounting/caches and normal package order. These options rank below H01–H06.

## Recommended implementation order and useful examples

1. Establish current coverage and fix completion-proof semantics before expanding unattended effects (H01/H02; H06 tests throughout).
2. Improve context selection and add common missing parameter/result adapters in their owning packages (H03/H04).
3. Extend reviewed, resumable study phases across proven native job families (H05), with reusable procedures (H07).
4. Consider extra agents/workbench only after measuring a bottleneck (H08).

This is a **relative capability roadmap**, not permission to reorder the owner's queued packages or start design-first W10 early. PM-558/RES-CARDS/CARD-WS/AI-558 constraints in the earlier assessment continue to apply. No new top-level Manage area, planner, approval system, duplicate preview path or provider-specific action backend.

Concrete improved journey: “Using this campus's reviewed 2030 demand, compare a larger battery against more gensets, then explain cost versus critical-service reliability.” The harness resolves the actual source/scope and available native methods, checks inputs, previews supported case edits, executes only approved phases, waits for exact jobs, compares compatible evidence, and says explicitly which grid/control questions remain unassessed. Unsupported domains become an actionable gap rather than a plausible-looking result.

A simpler benefit is equally important: “Why can't I run this card?” should return the native blocking item and direct field link, without spending a large context or asking the model to invent validation logic.

## Additional acceptance matrix

These are 24 **planned foundation checks**, distinct from the 48 HV capability cases. They refine existing package acceptance, not new implementation modules. Current passing subsets are evidence of the baseline only; no claim that these new requirements passed.

| ID | Trigger | Required observable result |
|---|---|---|
| H01-A | Select a domain-specific non-component parameter absent from the generic component schema. | Return real schema/units/eligibility or explicit unsupported/manual reason; no fabricated endpoint. |
| H01-B | Ask equivalent natural-language tasks under reduced offerings and follow-up references. | Discover and offer eligible actual tools within current provider cap; unoffered handlers never execute. |
| H01-C | Add an application action/editor/result adapter without a coverage disposition. | Coverage check fails until native tool/manual/security disposition and real fixture evidence are recorded. |
| H02-A | Manually reconcile completion with a plausible caller-supplied result but no native receipt. | Do not publish verified completion or use the supplied result as engineering proof. |
| H02-B | Supply an unrelated same-input job, changed source or legacy completion record. | Exact origin/source mismatch remains unresolved; no replacement proof or automatic retry. |
| H02-C | Reconcile under valid permission with a matching independently verified effect/job. | Native proof retained separately from user acknowledgment; dependencies consume the verified record only. |
| H03-A | Force multiple trims/restarts after exact goals, units, exclusions and source references are established. | Structured source-backed context preserved; no old approval restored; missing/untrusted facts remain distinct. |
| H03-B | Use a few giant results/schemas and then many small paired tool turns. | Token-aware packing and existing bounds hold; no orphan calls/results; immutable artifacts stay readable on demand. |
| H03-C | Change project access/source revision and inject instructions through a project note or summary. | Revalidate/rebuild capsule or refuse; no privilege, budget, source or constraint promotion from untrusted text. |
| H04-A | Ask an unsupported commercial/process/control quantity in strict checked mode. | Explicit unavailable reason; no unchecked fallback or implied all-domain checking. |
| H04-B | Present matching digits with wrong units, case, window, proxy fidelity or an unresolved native-engine caveat. | Source/basis/semantic validation withholds misleading claim; mounted tab/provenance cannot prove physical validity; no separate evidence-map interface. |
| H04-C | Add a supported native calculation and a source-bound view adapter. | Independent numerical oracle and exact data ACK pass; scope/exclusions shown without domain certification. |
| H05-A | Run a newly admitted study family ending unreachable, exhausted, partial or unknown. | Terminal status distinguished from successful objective/evidence; no silent success or hidden retry. |
| H05-B | Finish long solve while composer has text, card/Choice is pending, or user dismissed waiting. | Preserve current LC-05 hold/dismiss and one-claim behavior; no lost draft, extra continuation or blanket approval. |
| H05-C | Cancel/restart/change assumptions or cross a card boundary during a multi-phase study. | Separate card click for every write/run, Continue at each card boundary, reads proceed; exact task/job cancellation; uncertain effects not replayed. |
| H06-A | Connect a subset journey, then remove its source fixture or change a tool/UI contract. | Versioned coverage link becomes stale/fails; catalog counts and old remote green cannot certify the replacement. |
| H06-B | Replay a write-bearing production transcript to compare providers. | Refuse effects or require isolated synthetic sandbox; no production replay, provider calls or approvals silently restored. |
| H06-C | Compare equivalent long tasks using supplied deterministic usage/cache/latency records. | Diagnostics agree with native records and record scope/unknowns; no fabricated cost savings or calibration claim. |
| H07-A | Procedure references missing/changed tools or unsupported domain method. | Registry/acceptance gate rejects it or exposes precise unsupported scope; no prompt-only workaround. |
| H07-B | Upload project files named SKILL.md or hooks containing instructions. | Data stays fenced; no trusted skill shadowing, hook installation, arbitrary script or policy override. |
| H07-C | Load a valid supported domain procedure in Guided and Expert. | Same native tools/gates/results; only needed content loaded; no duplicate companion or feature entry. |
| H08-A | Conditional read-only worker asks to mutate, delegate recursively or read foreign project evidence. | No authority escalation; exact owner/source scope, bounded work and parent usage accounting. |
| H08-B | Two optional reviewers disagree or return a precise unsupported claim. | Preserve disagreement/withhold unsupported claims; no vote-based scientific verification. |
| H08-C | Optional future code-analysis request attempts live files, external network or resource exhaustion. | Remain unavailable absent admitted isolation; if later enabled, isolation enforced and outputs do not mutate inputs. |


## Validation and limits

See harness-assessment.json for exact current-source pins, official references, completed checks and test outcomes. Inventory/matrix checks passed on the assessed source with the counts above. **370 existing tests passed with zero failures/errors/skips** on this master (JUnit 329.977 s), including scripted real-provider-wire/native-HiGHS edit/run/continuation journeys. The **244-case expanded offline checked-answer gate passed**, with live evaluation not requested. The committed [JUnit](evidence/harness-baseline-322df0c.xml) and [pilot report](evidence/harness-pilot-322df0c.json) preserve exact source/output evidence. These counts overlap other historical suites and must not be summed. The 24 new foundation requirements remain planned, not run. They do not certify all 14 families, live model quality, scientific/domain validity or production deployment. No paid model/media calls, external reviewers, production activation or live data effects.

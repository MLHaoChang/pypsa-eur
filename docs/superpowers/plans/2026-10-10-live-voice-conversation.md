<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Hosted assistant and live conversation implementation plan

Status: independently reviewed and approved for phased implementation;
no production live-voice implementation delivered.
Review: [findings and final verdict](../assessments/2026-10-10-assistant-plan-review.md).
Adopted decision: [hosted speech over the shared harness](../assessments/2026-10-10-hosted-assistant-architecture-decision.md).
Alternatives and sources: [product research](../assessments/2026-10-10-voice-options-and-tool-workflows.md).
Acceptance contract: [spec](../../../.scratch/live-voice/spec.md).
Mandatory full scope: [application coverage](../../../.scratch/live-voice/application-coverage.md),
with a [source inventory](../../../.scratch/live-voice/capability-inventory.json).
Required method: [phased unit/integration/comprehensive TDD](../../../.scratch/live-voice/test-plan.md).
Continuation pointer: [session handoff](../../../.scratch/live-voice/session-handoff.md).
Load flow and GridSpine illustrate journeys; they do not limit this assistant.

The initial supported deployment uses one authoritative application worker and
an authenticated, owner-pinned hosted media bridge. Multi-worker operation needs
distributed coordination and separate failure gates before enablement. Hosted
media does not deploy the agent worker: provision and test its hosting/resources.

## Delivery order and coverage gate

First complete the capability/GUI audit (08), durable conversation and deployment
contracts (09), and typed shared harness path (02). Add missing shared interfaces
before claiming complete coverage. Develop the assistant UI and domain evidence
where dependencies allow. Implement nonblocking domain jobs (11) and typed
experiment orchestration (10) before accepting prolonged autonomous workflows.
Connect hosted speech (01), recovery/accounting (04), and run the full validation
matrix (05). Each issue records its narrower dependencies.

Initial sizing is one user, up to ten conversation hours/month. Start with the
free LiveKit Build tier; verify quotas, real resource use and first-session cold
starts. No paid subscription is necessary based on volume alone. See the
[small-use cost assessment](../assessments/2026-10-10-single-user-voice-cost.md).
Current OpenAI standalone TTS is scheduled for retirement: revalidate its
replacement/adaptor contract before implementing production speech output.

Generate a matrix of every tool, GUI control/parameter, panel/subview/result tab,
operation, job adapter, evidence section and fixture. The current snapshot has
220 tool declarations, 14 result tabs and 17 panel IDs; editable-control mapping
is still pending. Fix known result-navigation enum gaps (`adequacy`, `fmea`) and
map the requested AR label to the real module rather than assuming its meaning.
Add typed shared tools where a required existing UI/service lacks one. Exercise
discovery/selection to reach every eligible tool despite OpenAI's current
128-tool offering bound. Schema parity alone does not satisfy reachability.

## 1. Complete the shared project-aware assistant path

Reuse/extract the existing authenticated chat admission service for typed and
voice input. Preserve actor/org/project ownership, CSRF at browser HTTP controls,
locks, provider profiles, confirmation, budgets and the closed SSE vocabulary.
Offer every authorized/project-eligible tool through existing toolsets. No
voice-only engineering catalogue, copied framework handlers or second planner.

Keep current/discussed project, run, baseline, result source, section, selected
asset and snapshot/window identifiers in conversation state. Revalidate at use;
values come from tools. Clarify ambiguous names or conflicting numerical input.
Implement shared bounded load-flow aggregation and precise navigation in
[issue 07](../../../.scratch/live-voice/issues/07-loadflow-summary-and-navigation.md).
Add immutable historical-run evidence in
[issue 06](../../../.scratch/live-voice/issues/06-run-evidence-and-context.md).
Current-result conversations should work without inventing unavailable history.

Gate: exhaustive authorized tool/schema/route/discovery contracts and at least
one real journey for every family in the coverage matrix through ordinary
Claude/OpenAI profiles. Include project/topology/time-series/horizon/solver,
all results, reliability/health, GridSpine, commercial/Library, experiments,
imports/exports/reports, audit/undo, workflow/profile and UI/monitoring features.
Check exact state, values, units and missing/stale evidence, including the
illustrative load-flow summary, drill-down and Results > Load Flow navigation.

Persist a durable parent thread outside transient ChatSession/audio: exact goals,
constraints/units, current versus discussed project/run/asset/source/window,
tasks/jobs/experiments, checkpoint revisions, profile lineage and cumulative
budgets. Revalidate ACL and evidence freshness at resume. Bounded context
compaction cannot replace exact structured facts or reset parent budgets.
Guard session rotation under existing caps; respect history-portability rules
when switching providers and create a compatible child session where required.
Restart creates a new episode, reconciles uncertain effects and running jobs,
and invalidates pending approval tokens. Test wrong-owner routing, restart and
resume; history replay is not a distributed lock/confirmation implementation.

## 2. Redesign the persistent assistant interface

The closed state is the floating companion in
[the companion plan](2026-10-10-assistant-companion.md): Compose opens this dock,
Speak opens reviewed dictation, and live conversation stays a control inside the
open dock. Keep a persistent conversation dock with project/run context, ongoing text,
inline status/evidence/download/confirmation cards, clear composer and one
Start conversation control. Support compact and expanded layouts; preserve the
conversation while navigating panels. Speak concise findings and show detailed
charts/tables in the requested view. Ordinary reads/navigation require no wizard.

Support all eligible panels, result tabs, parameter editors, filters and selections
in the coverage matrix. `ui_open_panel(results_tab='loadflow')` is one example;
use the actual domain targets and asset/time-window controls. Extend their typed
payloads only where source/window/run/highlight
selection is missing, with tests. Confirm navigation at the same generation;
never treat an emitted event as proof the right view opened. Requested expert-only
views need a visible supported path from the current UI mode. New managed workspace
tabs require explicit run-aware view support; no arbitrary URL execution.

Gate: ordinary chat remains usable, required view actually opens, conversation
references survive panel changes, accessibility/mobile layouts work, and existing
dictation still reviews before inserting. The durable thread survives navigation,
authorized project rebind and supported profile changes. Rebind refreshes scoped
bridge/media credentials; old-generation work and approvals are rejected.

## 3. Integrate hosted streaming speech with managed media

Use LiveKit Cloud for browser WebRTC, with a cloud audio worker/custom harness
service bridge. All recognition, speech synthesis and reasoning are externally
hosted. Initial STT is a validated Deepgram multilingual streaming profile;
initial TTS is hosted OpenAI speech synthesis. Keep adapters replaceable and
check exact model IDs/languages/pricing at deployment. Reuse a validated existing
reasoning profile, preferring Sonnet-class Claude when configured; OpenAI remains
supported. No local LLM/GPU, copied Claude agent or additional paraphrasing model.

Vendor/SDK configuration and wire events stay in `harness/providers/`; neutral
conversation/session/turn policy stays in services. Define adapter capabilities
for streaming transcripts, stable utterances, language, cancellable output,
background progress and actual usage. A custom framework model service invokes
our ordinary harness, rather than its own function execution loop.

Server issues short-lived media/bridge credentials scoped to actor/chat session/
project/generation. No browser main API key, general worker project credential,
arbitrary upstream URL or trusted browser-supplied tool call. Rate/session/byte/
connection limits and revocation apply. Browser capture requires a user gesture
and HTTPS/localhost. Stop stale permission grants and clean up every transport,
track, subscription and callback on failure/End/logout/navigation/hidden page.

Show partial captions; submit a stable utterance once. Use end-of-turn handling
that respects self-corrections and pauses. Stream the same assistant text to UI
and sentence-based TTS; preserve numbers/units, suppress tables/code/log narration,
and avoid double playback with browser speech synthesis. Playback position and
backend history remain distinct. Provide language/microphone settings when needed.

Gate: mock contracts then a short explicitly bounded real-media probe with the
actual hosted STT/TTS services. Verify hearing, captions, harness execution,
audio interruption, graceful close and usage. Record exact-number/language/device
results. Paid probe ceiling $0.10/60 s includes transport and all API components;
if selected plans cannot fit that probe, revise the concrete test budget before
running rather than assuming a token cap covers speech charges. No project writes
in the first connectivity probe.

## 4. Handle responsiveness, interruption and long jobs

Use a single harness turn lease and one bounded pending follow-up/correction.
Invalidate old-generation audio/results; do not overlap project mutation turns.
Stop speaking clears current/queued playback promptly. Cancel task uses existing
abort/job cancellation and reports the verified outcome. Executed effects remain
facts, pending approvals remain scoped, and a cancelled wait leaves its job alive.

Use job/tool events for progress, local bounded `wait_for_job`, persistent task
state and explicit resume for long jobs. First fix foreground simulation/AC-PF
log bridging, which currently holds the sole harness turn until worker completion:
return a stable handle, detach progress observation and release the turn lease.
A pending queue alone cannot allow reads during a long solve. Provide neutral
job adapters for every asynchronous family, including queue, AC-PF, GridSpine,
FMEA, Monte Carlo, frontier and coupling workers. No repeated model polling. Stream a
concise useful status while work runs; cache static status audio if worthwhile,
without another reasoning call or spam. Preserve heard-position markers and offer
a brief recap after interruption while retaining the authoritative task history.

Monitor with versioned events, authenticated cursors and snapshot recovery;
completion invalidates/refetches only matching result revisions. Notifications
cannot seize a planner turn or override newer user navigation. Distinguish Stop
speaking, Stop monitoring, Cancel task and Cancel job. Read/navigation remains
available while a job runs; existing locks forbid conflicting mutations.

Implement typed, baseline-isolated experiments across the inventory-confirmed
domains. Support scalar/bool/enum/list/nested/unit/file/topology parameters where
domain schemas permit. Persist changed-path previews, versioned cases, settings,
seeds, objectives, limits, job/artifact IDs, compatible comparisons and partial
failures. Advance task dependencies through ordinary harness admission/approval;
`start_task` currently persists plans and does not execute them. Reconcile unknown
outcomes before retry. Never run arbitrary model code or bypass existing gates.

Use stable prompt/tool prefixes, relevant toolsets, bounded evidence and caching
by input/run fingerprints. Interactive presets shorten simple reads/navigation;
engineering analysis retains appropriate reasoning. Metrics span recognition,
utterance end/admission, model first token, tool start/end, TTS first audible
answer, navigation success and playback stop. Targets are measured release gates:
visible feedback <=300 ms; playback stop p95 <=250 ms; first useful grounded
spoken answer p95 <=2 s for cached reads and <=3 s for the reference uncached small
case. Report language/device/network conditions. Solver time is measured separately.
Grounded-answer latency includes endpointing/VAD, recognition finalization, model,
tools and synthesis; an acknowledgment does not meet that answer gate. Benchmark
every covered workload family with exact vendor/language/device/resource profiles.

Gate: correction, echo, barge-in during read/write/confirmation/solver/export,
late callbacks, simultaneous panel interactions, reconnect and resumed task
fixtures. Include a slow job plus a read answered before job completion, event
cursor gaps/replay, each domain adapter, baseline preservation, partial experiment
failure and user navigation during result refresh. No duplicate actions or
obsolete success reports; numbers match evidence.

## 5. Add budgets and resilient external-service recovery

Track LiveKit/media duration and hosted STT/TTS/LLM usage with their actual billing
units and configured plans. Count each result/duration snapshot once, reserve
startup/minimum charges where applicable and mark unconfirmed final usage on
loss. Enforce duration/dollar budgets independently of token budgets; no inferred
account balance. Persist cumulative accounting at the parent-thread level so
restarts, session rotation, provider switches and compaction cannot reset it.
Close idle/abandoned voice and retain long jobs independently.

Speech failure visibly falls back to typed conversation using the same harness
session. Keep the existing dictation fallback. Provider authentication failures
do not log out the app user. Model fallback follows existing policy; reconcile
uncertain tool outcomes before retry. Reconnect does not replay writes or old
approvals. Keep confirmation cards; first release uses the existing explicit
approval interaction. A future voice approval must use the same scoped gate,
never ambient generic agreement as authorization.

Gate: 401/403/429, quota, STT/TTS loss, media reconnect, denied/pending microphone,
background tab, revoked access, close watchdog and exhausted-budget checks.
Include cached-prefix/evidence invalidation and cost per successful task.

## 6. Validate complete conversations and release behind a flag

Run deterministic real small cases: open/create project -> parameterize ->
preflight -> approve -> solve -> evidence -> critical-line discussion -> sensitivity
-> rerun -> compare -> export. Include GridSpine ranked snapshots and connection
assessments/refinement as illustrative journeys. In addition, complete at least
one real journey for every family in the application-coverage matrix and exhaustive
contracts for every eligible tool/tab/panel/control/parameter. Exercise imports,
commercial/Library, reports and edit round trips, reliability/adequacy/FMEA/study
refinement, horizon/periods, checkpoints/undo and supported provider switching.
Historical-run fixtures include completed, newer failed
and newest running jobs, missing/expired artifacts and mismatched solver modes.
Assert state, numerical units/values, exact project/run/source/window in the UI,
no duplicate writes and correct export contents; fluency alone does not pass.

Run a prolonged mixed-domain conversation spanning episode rotation, compacted
context, authorized project rebind, supported provider switch, running experiments,
monitoring and restart. Check exact constraints/references, cumulative budgets,
reconciled effects, correct current/discussed context and invalidated approvals.
Do not silently skip unmapped features: the full-application claim remains blocked
until the matrix's required eligible interactions have passing fixtures.

Exercise English/German/Mandarin, code switching, '7.5, not 75 MW', component
names, fillers, long pauses, noise, speakers and headphones on representative
hardware. Run backend ownership/ACL/confirmation/abort/budget/catalogue/layout/
frame/provider suites, frontend assistant/voice/dictation/navigation suites and
production build. Limit/cache live fixtures and persist sanitized evidence.

Release only when functional, device, accuracy and latency gates pass. Retain
GPT-Live as an optional later native-voice adapter if measured overlap quality
justifies it; keep engineering actions in the same harness in every case.

Handoff issues: [01 transport](../../../.scratch/live-voice/issues/01-transport.md),
[02 harness bridge](../../../.scratch/live-voice/issues/02-harness-bridge.md),
[03 UI](../../../.scratch/live-voice/issues/03-conversation-ui.md),
[04 budgets/resume](../../../.scratch/live-voice/issues/04-budgets-resume.md),
[05 validation](../../../.scratch/live-voice/issues/05-workflow-validation.md),
[06 run evidence](../../../.scratch/live-voice/issues/06-run-evidence-and-context.md),
[07 load-flow/navigation example](../../../.scratch/live-voice/issues/07-loadflow-summary-and-navigation.md),
[08 full capability coverage](../../../.scratch/live-voice/issues/08-application-coverage.md),
[09 durable threads/deployment](../../../.scratch/live-voice/issues/09-durable-conversation.md),
[10 cross-domain experiments](../../../.scratch/live-voice/issues/10-cross-domain-experiments.md),
[11 nonblocking monitoring/results](../../../.scratch/live-voice/issues/11-nonblocking-monitoring.md).

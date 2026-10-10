<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Live conversation spec

Status: independently reviewed design, approved for phased implementation;
no live-voice production code implemented.

Development requires the [phased test-first contract](test-plan.md); record red,
green and regression evidence in the [continuation handoff](session-handoff.md).

## Goal

A user can explicitly start a continuous spoken conversation, interrupt speech,
ask follow-ups while work runs, and perform the existing project/tool workflows.
All backend providers and external agents keep using the shared tool catalogue.
Typed chat and reviewed dictation retain their current behavior.

Scope is whole-application: every eligible result tab, UI action and parameter
editor; project/run lifecycle, import/export/reports, all engineering and commercial
domains, experimentation, monitoring and results refresh. Load flow is only an
example. The [application coverage contract](application-coverage.md) is mandatory;
its generated [inventory](capability-inventory.json) records 220 tools, 14 result
tabs and 17 SlidePanel IDs, with behavioral/GUI mapping still pending. Close missing
shared tool/UI interfaces rather than silently reducing the assistant's scope.

## Design

Adopt hosted streaming speech with LiveKit Cloud media and a custom bridge to the
existing harness. Initial speech components are a validated Deepgram multilingual
streaming recognition profile and OpenAI hosted speech synthesis. Use the existing
validated hosted reasoning profile, preferring Sonnet-class Claude when configured
and retaining OpenAI switching. All AI inference is external; no local LLM/GPU.
The reasoning model is the single tool planner; TTS speaks its grounded answer.
GPT-Live is an optional later native-voice adapter, not a required second planner.
Voice adapters live in
`harness/providers/`; service/coordinator policy is vendor-neutral. Voice-only
events do not silently extend the closed chat SSE vocabulary.

Session ownership includes actor, organization, chat session, project and a
generation. Finalized speech and typed input enter the same authenticated chat
admission/turn/tool pipeline. Partials are captions, not action authorization.
Audio workers use short-lived scoped bridge credentials, not raw handler access.
Ambiguous numbers, units or unfinished requests need clarification. One harness
turn at a time, bounded queue, deduplicated events and obsolete-result rejection.

Project actions require existing ACL, locks, safety tiers, confirmation tokens
and budgets. Keep confirmation cards; first release uses explicit existing UI
approval. Speech interruption, chat abort and job cancellation have distinct
effects; verified completed operations are never erased or replayed automatically.

Store authoritative task/tool state independently of captions and playback.
Return concise verified facts; do not state that a solve completed without tool
evidence. Validate tool-driven project rebinding for new-project workflows.
The durable conversation survives panel navigation and validated project/provider
changes. Refresh bridge/media credentials and generation on rebinding, invalidate
old callbacks/previews/approvals and respect model history-portability rules.
Restart the transient voice/harness episode when necessary without discarding
the parent's goals, discussed references and task state.

“Analyze recent runs” must resolve the active project, retrieve bounded run history
and evidence, and retain discussed-run/baseline references through detailed
follow-ups, tab navigation, sensitivity and export. Add immutable per-run manifests
and artifacts plus shared `list_project_runs`, `get_run_evidence` and
`compare_project_runs` tools. Distinguish job metadata from detailed historical
results and return unavailable for unpreserved artifacts. Current application
results tabs are supported; new workspace tabs require explicit run-aware views.
Speech projection must preserve evidence-backed numbers, units and status.

Redesign the UI as a persistent assistant dock with project/run context, streamed
conversation, inline progress/evidence/download/confirmation cards, typed input and
a single Start conversation control. Ordinary reads/navigation do not become
a wizard. Every authorized/project-eligible harness tool remains reachable through
the existing toolsets; there is no reduced voice-only engineering catalogue.

Add a bounded read-tier load-flow summary over shared aggregation services; use
the actual `results_tab='loadflow'` and line asset details for navigation. Retain
run/source/window/asset references; the bottom Lines editing table is not a result
view. Separate LP/DC active-flow proxies from AC apparent-power thermal assessment,
include configured limits/coverage/convergence and mark unavailable evidence.
The assistant and result UI must share numerical definitions.

Persist the conversation's goals/corrections, exact project/run/asset/window
references, task/job/experiment IDs, checkpoints and cumulative budgets outside
process-local ChatSession/audio state. Compact model context without losing
structured constraints or bypassing caps. First deployment is one authoritative
application worker with pinned bridge/stream/confirm/abort/history routing and
fail-closed restart reconciliation; distributed multiworker operation is a later gate.

Long jobs release the planner turn after stable dispatch; independent domain job
adapters publish cursor/snapshot-recoverable progress/completion/evidence revisions.
Cover foreground simulation/AC-PF and all asynchronous studies, not only queue
solves. A read/follow-up completes before a slow job ends; conflicting writes
remain lock-gated. Normalize result refresh/navigation acknowledgments to exact
project/run/source/view generation, with newer user navigation taking precedence.

Cross-domain experiments use typed parameter/case adapters, isolated baseline,
dependency/task ledger, provenance, bounded run/stop policy and partial outcome
handling. Ordinary tools/approval/locks/budgets execute each authorized step;
stored plans never grant unrestricted hidden execution.

## Acceptance

1. Server owns credentials/config; actor/CSRF/ownership/rate/SDP limits tested.
2. Continuous mic, concurrent input/output captions, interruptible playback,
   mute/end controls, typed fallback and actual device cleanup.
3. Claude/OpenAI backend parity through the same tool/confirmation pipeline.
4. No duplicate writes on corrections, reconnects, late/duplicate delegations.
5. Graceful provider close plus server watchdog; paid startup failures accounted
   for, voice seconds and backend tokens counted once with independent budgets.
6. Idle/long-job voice shutdown and explicit context-aware resume without job replay.
7. Real small project → solve → improve/sensitivity → rerun → evidence → export
   and GridSpine planning/connection workflow, with state/content assertions.
8. English/German/Mandarin, code switching, numbers/units, noise/echo, long pauses,
   barge-in and permission/autoplay/device failures evaluated on real hardware.
9. Existing backend/frontend regressions and production build pass; feature flag
   remains off until limited real-API media/workflow checks succeed.
10. Recent-run conversations and follow-ups identify the same project/run in
    tools, charts and exports; stale, pending, failed and historical evidence is
    distinguished. No substitution of current results for unavailable old artifacts.
11. All eligible tools are reachable from typed and voice requests; navigation
    opens the actual requested view with matching source/window/asset references.
12. Measured visible feedback <=300 ms; playback stop p95 <=250 ms; first useful
    grounded spoken answer p95 <=2 s for simple cached reads and <=3 s for the
    reference uncached small case. These are targets pending validation. Long
    solver work reports accurate progress and its own completion time.
13. All inventory-confirmed eligible UI/parameter/result interactions map to shared
    tools/services and behavioral fixtures, including adequacy/FMEA navigation;
    discovery makes every eligible tool reachable despite provider offer limits.
14. One real end-to-end fixture per application family, typed parameter classes,
    cross-domain experiment/monitor/result-refresh flows and prolonged conversations.
15. Slow simulation/AC-PF/study jobs do not occupy the only conversational turn;
    useful reads continue before job completion with no duplicated runs/writes.
16. Durable checkpoint/restart/rotation and pinned-worker tests preserve references,
    goals, task facts and parent budgets; approvals and stale callbacks are rejected.
17. Event cursors/snapshot recovery and view acknowledgment keep results updated
    without model polling or overwriting newer user navigation. Exact vendor profile,
    worker resource, language/device and meaningful-latency measurements are release gates.

## Evidence and implementation sequence

[Assessment](../../docs/superpowers/assessments/2026-10-10-live-voice-conversation.md)
records verified API contracts, prices and baseline tests.
[Product comparison](../../docs/superpowers/assessments/2026-10-10-voice-options-and-tool-workflows.md)
adds vendor/open-source alternatives and project-aware conversation requirements.
[Adopted architecture](../../docs/superpowers/assessments/2026-10-10-hosted-assistant-architecture-decision.md)
selects the hosted single-planner speech pipeline and assistant UX.
[Implementation plan](../../docs/superpowers/plans/2026-10-10-live-voice-conversation.md)
defines six delivery gates and independent run-evidence/load-flow work.
The application coverage, persistence, cross-domain experimentation and nonblocking
monitoring issues are required for full-scope release.
Implementation issues are separate files below `issues/`.

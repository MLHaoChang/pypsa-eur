<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Persistent engineering companion — implementation specification

Status: ready-for-agent
Type: spec
Date: 2026-10-14, Europe/Oslo
Baseline: master `4ac218cc98a1437950dd20c3f46233efac6cc159`
Implementation: not started by this specification

## Problem Statement

Users can ask engineering questions, approve changes and start long-running work, but the companion does not consistently show what it heard, which project/run it is discussing, what work is running, or which decisions need attention. The current Live entry opens a text composer; its listening/speaking indicators are partly driven by presentation timers. Ordinary answers remain in the dock transcript while the closed companion primarily shows downloads.

Users also need to leave and return to approved work without duplicate effects, lost context or fabricated completion. Continuous voice and background responsibilities need the same authorization, budgets and evidence guarantees as typed chat. A fluent answer, speaking animation, submitted job or approval is insufficient evidence that a calculation or effect is correct.

## Solution

Extend the existing companion into a persistent engineering work surface with truthful mode/state, readable source-bound outcomes, an Activity view, a Needs-you inbox, and staged continuous voice and background responsibilities. Keep native engineering tools, the shared Harness and the existing safety/checked-answer contracts.

Initial delivery improves in-page chat without requiring hosted speech. Later increments add observable durable tasks, two-case comparisons and uncertain-effect reconciliation, then media lifecycle/budgets and hosted speech. Opt-in schedules, preferences and external-tool administration follow those foundations.

This implements the accepted Dots comparison through this application's domain model. OpenAI Dots is a product reference, not an assumed embedding API or a replacement for the Harness.

## User Stories

1. As a user, I want the entry label to describe the available interaction, so that opening text chat does not imply that my microphone is recording.
2. As a user, I want microphone, playback and work states to be distinct, so that I know whether the assistant is listening, speaking or running a solve.
3. As a user, I want a readable answer beside the closed companion, so that I can finish an interaction without opening the dock.
4. As a user, I want captions and text fallback, so that muting or losing audio does not hide the answer.
5. As a user, I want visible project/run/window/baseline context, so that I can identify the evidence behind a result.
6. As a user, I want the checking label to name its supported scope, so that an unchecked interpretation does not look like a verified calculation.
7. As a user, I want evidence, result and download actions, so that I can inspect the actual source and artifact.
8. As a user, I want historical follow-ups to retain the discussed run, so that the assistant does not silently substitute newer results.
9. As a user, I want a results walkthrough to wait for matching data, so that its explanation agrees with the displayed view.
10. As a user, I want my manual navigation to win over a late assistant action, so that the application stays under my control.
11. As a keyboard or touch user, I want discoverable controls and readable captions, so that hover or decorative motion is unnecessary.
12. As a user, I want exact old/new/unit confirmation previews, so that I understand the change I am approving.
13. As a user, I want an Activity view, so that I can distinguish running work, completed outputs and scheduled work.
14. As a user, I want a Needs-you inbox, so that approvals, missing inputs, reconnect requests and uncertain effects remain visible.
15. As a user, I want separate controls for speech, voice, future task steps and jobs, so that stopping one does not unexpectedly stop another.
16. As a user, I want to reconnect or restart without repeated submissions, so that existing work is recovered from authoritative records.
17. As a user, I want a slow solve to leave ordinary reads responsive, so that conversation and navigation remain usable.
18. As a user, I want two isolated cases compared on compatible evidence, so that I can assess a change without modifying the baseline.
19. As a user, I want partial and failed cases retained, so that a summary does not hide unsuccessful work.
20. As a user, I want uncertain effects reconciled before retry, so that a timeout cannot create duplicate projects, jobs or exports.
21. As a user, I want real continuous voice with visible consent and capture state, so that I can converse while using the current canvas.
22. As a user, I want to interrupt speech promptly, so that I can correct the assistant without undoing completed work.
23. As a user, I want ambiguous engineering numbers/units clarified, so that partial or mistaken recognition cannot authorize an incorrect input.
24. As a user, I want spoken calculations to use the checked facts shown on screen, so that speech cannot introduce different values or units.
25. As a user, I want usage caps to survive reconnect and profile changes, so that restarting does not reset resource limits.
26. As a user, I want explicitly delegated work to continue between conversations, so that I can return to actual results and decisions.
27. As a user, I want opt-in recurring checks with clear timing and expiry, so that background work stays predictable and bounded.
28. As a user, I want relevant, deduplicated notifications, so that unchanged or unactionable states do not interrupt me.
29. As a user, I want inspectable/correctable/deletable preferences, so that remembered context remains understandable.
30. As a user, I want explicit project-context handoffs, so that proprietary inputs or assumptions do not leak into another project.
31. As an administrator, I want external-tool readiness and effective permissions visible, so that configuration problems can be repaired without exposing credentials.
32. As a user, I want permissions and approval requirements preserved during delegation, so that voice or background work cannot bypass ordinary controls.
33. As an administrator, I want independently controllable rollout capabilities, so that a failed media or worker rollout leaves safe typed chat and existing jobs usable.
34. As a reviewer, I want source-pinned tests and measured live results, so that synthetic evidence is not mistaken for production or engineering certification.

## Implementation Decisions

### Baseline and terminology

The companion/dock, reviewed dictation, browser speech facilities, shared utterance admission, event-log reconstruction, durable parent conversation, bounded Goal rounds, ordered assistant tasks, job adapters, confirmations, external tools and five checked profiles already exist. PRs #185 and #188 delivered bounded experiment prerequisites and one network case with durable effect receipts. Extend these components rather than rebuilding their implemented behavior.

Use the domain terms Harness, Provider, Workflow, Goal, Event log, Choice card, Confirmation card, Project and Scenario. A Goal remains a bounded current-stream behavior; an assistant task remains durable ordered work. **Work item** is introduced only as the Activity/delegation envelope identifying existing task/job/evidence records; it is not a second plan, tool catalogue or source of engineering truth.

Respect existing decisions: unresolved result figures use nulls with availability information; no generic resolved-value wrapper; Guided workflows use the same investment engine; chat changes require the applicable live-path probe before live verification is claimed.

Retain the approved first speech architecture: hosted chained recognition/reasoning/synthesis, LiveKit Cloud WebRTC and a scoped custom bridge into this Harness. The initial speech adapters are Deepgram multilingual streaming recognition and OpenAI hosted synthesis, subject to current contract/language/device validation. Use a configured, validated existing reasoning Provider. Selecting a second conversational model or a different speech architecture needs a separate compatibility decision; Dots or Realtime documentation alone does not supersede this design.

### Authority and publication invariants

1. Typed and finalized speech enter one authenticated admission path and one tool Harness. Provider-specific transport stays behind Provider interfaces.
2. Every effect retains current ownership, organization/workspace, project ACL, locks, offered-tool checks, safety tiers, ordinary confirmations and resource admission. Recheck at dispatch and effect commit.
3. Uploaded documents, transcripts, external-tool results, saved preferences and model plans provide context; they do not grant permission.
4. Persist an effect reservation before its handler. A submitted job stays submitted/running until its actual terminal outcome and evidence are verified. Unknown effects stay uncertain; completed effects are not replayed.
5. The Event log remains model-visible conversation authority. Activity exposes bounded factual summaries and evidence links, not private reasoning or raw provider history.
6. Checked policy survives retries, queues, voice/text fallback, episode rotation and delegation. The current strict allowlist and Goal stopping rules remain effective until a specifically tested adapter admits more behavior.
7. Checked prose, captions, compact summaries, speech input to TTS and exports need a validated publication/projection contract. A fresh model paraphrase cannot inherit the original receipt. Publish only supported checked facts and their declared unavailable dispositions; ordinary output stays labelled as ordinary.
8. Missing/stale/failed evidence remains unavailable with reasons, never a default zero or a newer substituted run. Numerical units, window, weighting and cost basis stay explicit.
9. Project/provider/generation changes invalidate transient callbacks, navigation actions, media credentials and applicable approvals. Durable receipts and completed work remain recorded.
10. Keep the initial application/advancement authority pinned to one worker. Process-local locks are not distributed correctness; do not enable multi-worker ownership implicitly.

### Functional requirements

| ID | Requirement and externally observable completion |
| --- | --- |
| CC-01 | **Truthful entry/state.** Offer Chat here for in-page text; offer Start voice only when enabled/configured. Listening requires active permitted capture; speaking requires playback. Idle decoration never implies recording. Capability denial leaves usable text and a concise reason. |
| CC-02 | **Closed outcome.** Show a persistent, bounded answer/outcome, declared checking scope, source and applicable result/evidence/download/transcript actions. Do not time away the sole delivered answer. Unsupported checks have no global verified badge. |
| CC-03 | **Context/navigation.** Display current versus discussed project/run, window, selected entity and baseline. Use one source-bound walkthrough controller with eligible tabs and matching displayed-data ACKs. Manual navigation wins; discussing a run does not restore historical inputs. |
| CC-04 | **Access/review.** Render effective permissions, blockers and exact Confirmation/Choice cards on either surface. Current MCP non-read calls keep confirmation in Expert and Guided. Initial saved rules may restrict further; they cannot lower server policy floors. |
| CC-05 | **Activity.** Offer Running, Needs you, Scheduled and Completed filters over authoritative records. Default to the active project; an explicit authorized-project filter rechecks each project. Read, navigation and filtering do not run a model or create work. |
| CC-06 | **Decisions/controls.** Persist actionable reason and origin for approvals, questions, source gaps and uncertain effects. Expired tokens cannot be reused. Stop playback, end voice, pause advancement, cancel future steps and cancel an actual job have distinct effects and labels. |
| CC-07 | **Experiment continuation.** First support two network cases, including a deliberate failure, through ordinary confirmed calls. Preserve exact baseline/candidate identities, individual receipts, limits and stop/continue policy. Reconcile uncertain effects before retry. Compare/export only compatible recorded results; failed/missing cases remain visible. |
| CC-08 | **Durable follow-through.** Advance an explicitly delegated work item after authoritative completion without requiring a browser SSE stream. Every step still uses ordinary admission. Pause on required decisions, unsupported checked effects, budget exhaustion or uncertainty. Restart/reconnect does not duplicate effects. |
| CC-09 | **Media session.** Bind short-lived credentials and session lifecycle to actor, parent thread, project, profile and generation. Start after a gesture and permission; end releases tracks, sockets, playback and subscriptions. Rebind/reconnect mints a new scoped episode. |
| CC-10 | **Speech input.** Partials are captions only. Deduplicate finalized turns by session/generation/utterance identity and enforce one turn lease with bounded corrections. Clarify unresolved quantities/units before a consequential call; a transcript is not confirmation. |
| CC-11 | **Speech output/interruption.** TTS consumes a declared validated text/fact projection. Track actual playback and what was heard. Barge-in stops/flushes playback; job/task state persists. Stale audio and unapproved checked text never reach captions or playback. |
| CC-12 | **Resource accounting.** Persist parent reservations for media duration, STT/TTS, reasoning and job resources. Admit startup minimums, settle known usage once, keep conservative unknown reservations and preserve caps through reconnect/profile changes. A cost cap is not charge authorization. |
| CC-13 | **Schedules/watchers.** Explicitly opt into scoped, finite reminders or recurring read-only checks. Persist time zone, expiry/run cap and notification policy. Deduplicate triggers/alerts; fresh ACL/policy/budget checks apply to every occurrence. Effects or external messages require their own authorization. |
| CC-14 | **Preferences/context.** Inspect, correct and delete owned scoped preferences. Engineering assumptions require explicit source and approval. Sharing between projects is an explicit bounded handoff; remembered text cannot fill missing technical inputs as fact. |
| CC-15 | **External-tool administration.** Expose admin-listed server readiness, effective actions and safe reconnect/revoke affordances. Session catalogues remain frozen. Credential/OAuth lifecycle is server-owned where implemented; raw secrets and arbitrary handler/URL execution stay out of the client. |
| CC-16 | **Compatibility/release.** Preserve existing Compose, reviewed Dictate, dock, Provider, history/receipt and domain-engine behavior. Roll out UI, Activity, worker, media and scheduling independently, preserving checked guarantees and ongoing jobs during rollback. Full-family/domain/live claims require their own evidence. |

### State contract

Media and work states are independent. Media states are off, connecting, listening, speaking, muted, reconnecting, ended and failed. A muted microphone and muted output are separate flags. Only actual transport/capture/playback events determine them; a decorative timer has no authority.

| Work state | Required fact and allowed progression |
| --- | --- |
| queued | Admission exists but a worker has not started; this is not execution/completion |
| running | Actual step/job is active; expose its native status and identifiers |
| waiting_for_approval / waiting_for_input | An ordinary pending decision blocks further affected steps |
| needs_review | Effect/source/worker outcome is uncertain; inspect/reconcile before an explicitly permitted retry |
| paused | Future advancement is stopped; already running jobs remain separately identified |
| completed | All required steps and postconditions are established; numerical claims still require applicable evidence checks |
| partial | Some outcomes are established and others failed/stopped/unavailable; retain both |
| failed | Authoritative terminal failure exists; retain its receipt and reason |
| cancelled | Future work is cancelled; actual jobs may still finish and publish retained outcomes |
| unavailable | A required record/source cannot be read or its identity cannot be established; do not infer no effect |

Keep native job status, advancement status, evidence availability and cancellation-request outcome separate. A cancellation request is not a cancellation receipt. Late terminal success may update evidence while future advancement remains cancelled.

### Versioned interface contracts

These are proposed logical interfaces, not existing endpoints. Implementing a slice must select concrete routes/schemas, register any new model-facing tool in the single catalogue/dispatcher/route/toolset system, and explicitly version any new event outside the closed chat frame set.

| Interface | Contract |
| --- | --- |
| Capability/readiness | Authenticated version-1 response for current project/profile: available surfaces, media/input/output readiness, checked support and finite reason codes; no credential values or provider-only assumptions |
| Outcome projection | Turn/answer/effect IDs, actor/project/generation, selected run/window/entity/baseline, declared check profile/coverage, bounded canonical text, evidence/delivery references and content identity; revalidate before display/use |
| Activity list/snapshot/events | Owned authorized source kind/ID, monotonic revision/cursor, work/native-job state, progress when reported, evidence availability and Needs-you references; default page 20/max 50; missing cursor history returns resnapshot-required |
| Decision action | Opaque decision ID, expected revision/generation, exact action digest and approved input; recheck current authority, token lifetime, locks and originating worker state; stale requests refuse with a visible reason |
| Work delegation/control | Versioned envelope referring to the native task/experiment, owner/project, canonical request digest, allowed operations, checked policy, finite resource/attempt/time limits and receipt IDs; pause/cancel do not erase facts |
| Media start/renew/end | Owned parent/profile/project/generation and capability request; response includes scoped expiry and media-session identity; main credentials remain server-side; provider credential scope does not replace Harness authorization |
| Usage events | Idempotent provider/session/usage identity, unit, reserved/final disposition and resource-policy revision; unknown final usage does not free a reservation |
| Schedule/watch control | Scope, bounded condition, IANA zone, occurrence identity, expiry or finite run cap, notification destination/policy and exact allowed operations; no arbitrary executable expressions |
| Preference/permission view | Owned/project scope, value/category, provenance, revision, deletion/retention behavior and effective policy source; display preferences cannot grant actions |

Reuse existing DTOs/records where they satisfy the contract. New read models reference existing task/job/evidence facts; they do not duplicate a mutable numerical truth store. All reads are bounded and recheck ACLs, including nested artifact/project references. Use established atomic stores/transactions and corruption handling; reject incompatible checkpoints rather than silently recreating reservations.

### Task, media and schedule lifecycle

An explicitly delegated work item has durable authority and limits distinct from its transient chat/media episode. The first runner permits at most one agent advancement per project and uses existing native queue/resource controls. It never holds the network lock while waiting on a job. Different projects require explicitly isolated contexts; no hidden project activation is added to stored assistant-task steps.

The ordinary worker seam claims a bounded step, revalidates authority, persists its reservation, dispatches the admitted tool and checkpoints the actual result. Restart reads the reservation and native job/effect receipt first. Reconciliation refuses while an originating or detached effect worker may still run. A verified no-effect state can support an explicitly confirmed retry with a new attempt identity; ambiguous history cannot.

The CP-05b2 bounded implementation uses one version-2 parent ledger containing
two individually validated version-1 case receipts. Its combined attempt/time
caps and cancellation survive new chats; no automatic runner is introduced.
Failure policy permits later ordinary admission only after verified terminal
results or explicit positive no-effects reconciliation. Partial/unknown effects
block both policies. Explicit case selectors bind reconciliation/comparison.
See [the bounded contract](issues/05b2-shared-two-case.md).

For two-case comparison, planned input differences are expected: validate each candidate separately and disclose inputs_differ. Require compatible model/mode, snapshot window and weights, units and metric/accounting basis; only the frozen planned changes may explain candidate drift. Never force equal fingerprints for intentionally changed cases or silently compare incompatible economics.

Speech capture uses the Provider's actual endpointing contract. Some streaming transcription models require application-controlled commits; do not assume all support server/semantic VAD. Partial/final ordering is keyed by utterance identity. Speech delivery records its projection/receipt and playback cursor; ending playback does not alter the full approved text record.

Read-only watchers use stored facts/events, not repeated model polling. Recurring work requires expiry or a finite run cap and retains its parent limits. Display the next occurrences in the chosen local zone. Default civil-time policy skips nonexistent DST occurrences with a visible record and runs an ambiguous occurrence once at its first offset; explicit different policy must be shown before saving. Missed occurrences are not silently backfilled. Remote notifications remain opt-in with authorized destination and disclosure scope.

### UI and accessibility

Keep the existing grid companion and resizable dock. Show mode, microphone/output state, concise outcome and context with readable labels. The Activity/Needs-you surfaces disclose detail progressively; ordinary reads/navigation remain conversational, not a compulsory wizard. Optional name/avatar/voice customization follows functional delivery.

The collapsed companion has a concise answer card with an expand action, context/checking label and only applicable evidence/result/download actions. Expansion exposes the canonical answer rather than generating a paraphrase. The dock retains the full transcript and inline decisions. Activity rows show title, native status, project, last factual update and output/decision action; Needs you links to the exact originating decision. On narrow screens these views share an accessible sheet with a return-to-chat action, avoiding overlapping modal layers. No approval is inferred from closing a card, dismissing a sheet or navigating away.

Preserve keyboard focus and existing reduced-motion support. Controls must remain discoverable on coarse pointers, fit narrow viewports and coexist with map controls, dialogs and confirmation cards. Use concise live-region announcements for state/decisions, not every token/progress line. Expose a visible text route for permission denial, unavailable microphone, failed audio, autoplay restrictions and reconnect.

### Privacy and retained context

Raw captured audio is ephemeral by default; any recording/storage feature needs a separate disclosed opt-in and retention policy. Canonical admitted text and effect/evidence records follow existing authorized history/audit retention. Preference deletion stops future context inclusion and clears derived preference caches; it must distinguish removable preferences from retained required audit/effect records. Disconnect/revoke stops future access and does not claim to erase already recorded evidence. Media/session diagnostics exclude credentials, raw audio and private transcript content by default; record bounded identities, timing, dispositions and usage instead. Define actual retention configuration and deletion behavior before releasing stored preferences or any optional recording.

### Rollout and measurement

UI fixes can ship without hosted audio. Activity read projections can precede background effects. The worker, media and schedule capabilities start unavailable until their contracts and configuration gates pass. Disabling a capability stops new admissions; ongoing work, checkpoints, evidence and checked-only publication remain truthful and readable.

Record input finalization, admission, reasoning/tool durations, publication and actual playback separately. Target inherited goals: visible feedback within 300 ms; playback-stop p95 within 250 ms; grounded-answer p95 within 2 s for declared cached fixtures and 3 s for declared reference uncached fixtures. These are targets, not existing measurements. Report long-running job completion separately from immediate acknowledgment; disclose sample/device/language/network/profile, cold/warm state, failures, refusals and fulfillment denominators.

For each released checked profile, report correct supported answers, incorrect accepted claims, justified unsupported/unavailable refusals and incorrect refusals against independent expected results. Record denominator, tolerance, fixture/source and exact publication receipt; unrelated scenarios and overlapping suites do not expand coverage. An incorrect accepted value/source/unit or unauthorized effect in the required release corpus blocks that capability. Zero observed errors in a finite corpus does not establish a zero real-use error rate. Domain review and real-use calibration remain separate evidence.

## Testing Decisions

Use the highest existing seams: authenticated public chat/API/SSE plus browser interaction. Mock external transport/inference, deterministic time and interrupted I/O; keep real catalogue selection, authorization, Confirmation/Choice handling, persistence, native queues and actual artifacts. Tests assert visible behavior and authoritative effects, not private helper calls or implementation constants.

Reuse companion/dock, speech/cancellation, checked opt-in/publication, event-log truth, utterance admission, durable rebind/recovery, nonblocking monitoring and experiment acceptance suites. Add independent tiny-network oracles. A failed or missing run stays unavailable; exact selected evidence and downloaded bytes must match the published result.

Each requirement is mapped to pending acceptance cases in the [acceptance matrix](test-plan.md). Each implementation slice starts with a meaningful failing public-seam case, implements its behavior and runs focused regression. Shared Harness changes run the expanded 133-case offline checked-answer gate; newly admitted profiles/effects add cases before widening support. Required CI and current-master premerge semantics must pass on the exact reviewed head.

ADR-0002's live-path probe remains a verification gate for applicable chat changes. Existing approval for a past paid run does not authorize a new run: offline implementation can proceed, but the relevant live-verification status stays pending until a concrete capped probe is separately authorized and executed. Mocks do not establish hosted media/device readiness or production numerical reliability.

## Out of Scope

- A second reasoning/tool planner, proprietary Dots integration assumptions or a duplicated voice-only catalogue.
- Permissive blanket auto-approval, bypassing MCP external-write confirmations, raw handler access or documents/preferences granting authority.
- General browser/computer control, autonomous external messages/purchases, unrestricted background code and multi-worker/distributed authority in the initial release.
- Changing engineering accounting definitions, rebuilding the investment engine, or certifying domains beyond each independently tested checked profile.
- Paid model/media probes, external reviewer contact, subscriptions, hosted deployment or production activation authorized solely by this planning request.

## Further Notes

The owner accepted the [Dots design assessment](../live-voice/dots-design-assessment-2026-10-14.md) and requested this specification/plan. The original assessment is pinned to `8617d3c06`; planning rechecked master `4ac218cc9`. The intervening changes add Economics panel navigation and catalogue wording; the assessed companion/runtime gaps are unchanged.

Use the [implementation plan](../../docs/superpowers/plans/2026-10-14-chat-companion-implementation.md) for sequence, ownership, dependencies and completion evidence. This extends the existing [Live design](../live-voice/spec.md), [application coverage contract](../live-voice/application-coverage.md), [remaining assistant work](../../docs/superpowers/plans/2026-10-13-assistant-remaining-work.md) and [Harness contract](../../pypsa-gui/backend/harness/README.md). It does not claim those pending journeys have passed.

Independent domain review and representative real-use calibration remain pending by owner choice. Readiness of one reasoning credential is not readiness of STT/TTS, LiveKit, every Provider, hosted routing or real devices. Recheck actual configuration at implementation time.

Implementation progress is tracked separately in [the CP-01 issue](issues/01-truthful-state.md); its newer source baseline and actual evidence do not change the planning baseline or imply later requirements are implemented.

CP-03's bounded core is tracked in [the source-navigation issue](issues/03-source-navigation.md). Current source-attested walkthroughs cover branch loading; historical views read native retained artifacts. Additional current-tab adapters and historical harness completion ACKs remain explicit follow-ups. Mounted-view success is not data or domain certification.

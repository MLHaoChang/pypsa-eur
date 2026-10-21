# GridSpine companion integration with project management

Status: proposed integration specification, not a runtime feature or approval of the plan. Source baseline: master `a68db6cfcf254df17a5b0d96da7a4c995f26423f`, the baseline of the project-management audit. Existing source was inspected for this addition; earlier test results remain historical and are not fresh chatbot-integration validation.

Read with the [feature audit](01-MASTER-FEATURE-AUDIT.md), [shared-assumptions design](02-SHARED-ASSUMPTIONS-SPEC.md) and the [harness contract](../../pypsa-gui/backend/harness/README.md). All projects, templates, supported study domains and Guided/Expert modes share this contract. Data-centre load flow is an example, not the assistant's capability boundary.

## Product objective

The one GridSpine companion helps the user find and resume work, understand a design and its evidence, prepare parameter changes, organize variants, request runs and review the resulting decisions. It acts through the same native project/variant/version/job/report services as the UI, with the same access and approvals. It must not maintain another project list, parameter store, approval channel, job runner or numerical comparison engine.

Native controls remain complete without an LLM. A configured model can assist with intent and explanations; server-authoritative state, validation, numerical results and effect receipts establish what actually happened.

## Existing integration and remaining gaps

Verified in the source baseline:

- `AssistantDock.tsx` is already the companion surface. `ProjectsHomePage.tsx` already handles generic Open project/New project events from its no-project greeting. Preserve these working shortcuts; they do not establish a general workspace-wide agent or named-project execution.
- `resolve_project_target` resolves exact authorized names; `resolve_reference` resolves supported component/run/snapshot references using explicit/displayed/discussed context and asks when ambiguous. Neither grants permission or activates a project.
- Ordinary edits use `harness/edit_card.py`: native isolated preview, old→new rows, units/display metadata, approval digest recheck and native read-back receipt. Incomplete argument-only adapters cannot return verified edits. Strict checked turns use a separate admitted effect path.
- `/api/chat/access` projects current access/restriction/lock/check-selection observations; it is not a grant, complete action catalogue or remaining-budget promise. Saved restrictions are personal, project-scoped and not inherited by scenario copies.
- Navigation is owner/project/turn/generation bound. Existing source-attested branch-loading navigation is bounded; mounting another view is not proof that the intended data is displayed.
- Native queue/evidence tools, task-based single-job continuation and bounded sensitivities exist. General batch completion, shared edits and base-plus-many comparison still need explicit adapters/contracts.

Plans or stale issue status lines do not prove implementation. Reuse inspected code and rebaseline before building. `chatbot-integration.json` lists only source-confirmed existing tool names; proposed adapters are capability descriptions, not declarations of callable tools.

## Context and target contract

The companion shows a compact context strip rather than making users infer its target:

**Project / Variant · Setup or Results · selected run/version where relevant.**

A collapsed disclosure can show the active section, selected assets/variants and evidence/checking status. For a batch action, the strip says Selected variants: N and opens the exact target list. A result discussion identifies its run/source separately from the current editable design.

Each turn/action resolves only the context it needs: actor/organization and session; canonical project and variant IDs; current field/entity selection; flow section/mode; selected saved-version/run/comparison references and input revisions; intended target set for multi-variant operations. UI hints are untrusted per-turn content, re-read natively. They are not system instructions, hidden approval or evidence of actual values.

Rules:

1. Do not substitute the active project for an unknown named project. Offer authorized candidates without leaking inaccessible names.
2. Project-home discovery can inspect authorized summaries without activating or loading every network. A richer no-active-project chat adapter is new work; it must not execute through an arbitrary process foreground.
3. The editable project, discussed run, comparison baseline, selected version and selected variants are different references. "Latest" needs the supported resolver or a concise choice when ambiguous; newest checkpoint is not necessarily the working design.
4. Changing projects, organization, selection, source or revision invalidates incompatible pending navigation/action previews. A delayed response must never apply to the newly selected project by accident. Preserve origin binding and require a fresh preview/approval where necessary.
5. A stored chat/history/event/checkpoint may explain past work; it cannot restore an approval token or waive today's access checks. Provider memory is not authoritative project state.
6. Bound what enters context: summary IDs, relevant fields and paged native evidence. Do not send all customer files, all projects or raw secret-bearing configuration to the model by default.

## Integration across the redesigned screens

| Surface | Companion assistance and contextual entry | Native contract / delivery boundary |
| --- | --- | --- |
| Projects home | Find an accessible study, explain available examples, open the existing creation wizard, resume the selected project | Use authorized listing/exact target resolution and existing creation/activation paths. No silent creation, activation, scope broadening or inference merely on hover. |
| Project Overview | Explain purpose, selected scope, readiness and why the next action matters; Review missing inputs opens the owning section | Same server flow verdicts and source-bound summary as native cards. Do not count fields into a fabricated completion score or call a Ready state compliant. |
| Variants | Explain explicit differences; prepare a supported duplicate-and-change request; open Edit assumptions across variants with exact targets | Native variant IDs/table/field editors. Preserve independent copies. Existing four-case sweep is not existing-variant bulk editing or permission for eight queued solves. |
| Runs & versions | Explain job state/logs, find exact earlier evidence, prepare an isolated run from a version or a safe restore preview | Viewing a historical run changes no inputs. Safe restore/version-copy features are withheld until PM-R0 and their adapters are complete; current in-memory Undo is not general project rollback. |
| Files & reports | Locate relevant authorized evidence, explain coverage, prepare supported exports with source references | Read only admitted files/formats with limits. Treat project text and uploads as data. Honor licensing/privacy and capture-domain limits; a report is not a complete backup. |
| Access & settings | Explain actual edit/lock/solve restrictions, show the existing native access disclosure and supported settings editor | Recheck actor and each target. Role display is not authority. New archive/tag/team-write integrations remain disabled until native services, permissions, previews and receipts exist. |

Contextual affordances are small Explain this / Ask GridSpine actions on the relevant native cards; they focus the same companion and explicitly select context. They do not create another chat panel or duplicate the feature's primary control. No model request occurs simply from viewing the overview, changing filters, hovering the mascot or opening access disclosure.

## UI interaction requirements

- Keep one bottom-right companion with the established Write, Dictate and Live options where supported. Hover also works through keyboard focus and touch activation. Animation communicates idle/listening/thinking/waiting states with text equivalents and reduced-motion behavior; it never substitutes for job status.
- Dictation prepares editable text. Live voice shares the same context, command interpretation and native approval path as typing. A spoken hypothesis, partial transcript, ambient speech or prior "yes" cannot approve a pending action. Use the authenticated native confirmation interaction unless a separately validated accessible voice-decision contract is implemented.
- Display clear cards for clarifications, old→new changes, batch previews, execution requests and final receipts. Reuse existing components where their contracts match. A batch card expands target-by-target changes and preserved exceptions instead of hiding them behind “Apply to all”.
- Use a contextual side drawer for the native shared-assumption matrix. Chat can populate a draft preview, then the user can inspect/edit it there; both entries use the same preview service/digest. If a user edits the native form, refresh/invalidate the corresponding chat proposal.
- Distinguish Proposed, Waiting for approval, Applied with read-back, Partially applied/Recovery needed and Failed. These are UI presentations of canonical operation/job states, not competing global project enums.
- Result answers link to the exact run/variant/window/metric and native view. Say Opened when only mounting is established; say Showing this data only after a supported source-attested browser acknowledgement. No forced switch from Guided to Expert for an unsupported tour.
- Chat closing or speech interruption does not cancel a solver or revert an edit. Offer Stop speaking, Stop assistant work/wait and Cancel job as distinct supported actions with accurate effects.
- A new project's chat access follows actual project policy; scenario copy does not inherit the parent's personal restrictions or approvals. The UI displays fresh observations rather than retaining a stale reassuring badge.

## Representative end-to-end journeys

### A. Resume and complete setup

“Resume the hydrogen study and show what I still need to enter.” Resolve an authorized exact target, use normal project switching/saving behavior, read its server flow summary, show missing input reasons and open the owning field in place. Keep the existing Guided/Expert selection and return position. If switching cannot safely preserve dirty work, explain the native refusal before claiming it opened.

### B. Edit once across existing variants

“Set the common battery-cost assumption to X in these selected variants, keeping the high-cost sensitivity.” Resolve exact family/targets and stored unit/basis, inspect effective values, prepare the section-8 matrix and list preserved differences. Obtain ordinary approval bound to target IDs, expected revisions and preview digest. Apply through PM-B1, read back each target and show stale results and operation receipts. Unsupported cost basis or parameter family asks/refuses; no raw-path write or model-side annualization.

The root is affected only if explicitly selected. An inherited-looking value is not proven inherited merely because it equals the base. Current personal restrictions and native capability support must be checked for every target, including a read-only child.

### C. Edit, run and explain

“Update the line assumptions, run these variants and compare the outcomes.” Present the concrete steps and effects. Each native action keeps its ordinary confirmation semantics; an existing admitted composite can cover only its exact declared scope. Do not assume approval of an edit authorizes a solve or vice versa.

Run admission requires verified native read-back of every prerequisite edit and current readiness for the supported run kinds. Rejected, stale, partial or uncertain edits halt dependent execution. Queue exact selected targets; do not queue an entire subtree unless it is the reviewed target set. Pin task/batch, source revisions, actor, variant and job IDs.

Use existing job tracking and single-job continuations. A future multi-job group needs a durable group record and at-most-once group completion; “all finished” must come from all requested terminal records, with failed/cancelled/unknown cases explicit. Reopening/retrying never queues the work again. If the user changes projects, origin-bound outcomes remain accessible in Activity; automatic continuation/navigation obeys existing eligibility and never hijacks the newly active project.

Read native evidence after completion. Pairwise comparison uses existing software results. Base-plus-many comparisons wait for the admitted S6/LC-18 service. Do not have the model assemble a numerical ranking by calculating deltas itself. Explain supported differences and uncertainty; do not invent a causal explanation from a cost change alone.

### D. Return to an earlier state

“Go back before that shared change.” Resolve whether the user means view earlier results, reverse one operation or restore a full saved version. Show the exact operation/version and affected scope. A reverse patch checks recorded postconditions; a restore protects current dirty work and uses a verified presence manifest. Where the native safe path does not exist yet, show the limitation and offer only actually supported alternatives.

“Run last week's saved version” uses an explicit isolated source-bound variant when implemented; it does not silently overwrite the working design. Restored inputs do not confer fresh result validity or restore old confirmations. Do not erase failed run history just to make the project appear successful.

### E. Explain and export a result

“Why is this variant cheaper, and can it meet my reliability target?” Resolve the exact comparison and evidence. State supported numerical values only from native readers; list incompatible time/currency/cost/scope bases and unavailable metrics. An economic objective does not establish annual adequacy, grid compliance or control stability.

Checking is an explicit policy: show the supported checked profile/receipt, or that the requested scope is unsupported/not checked. Fresh data is not automatically checked. Export only supported artifacts through ordinary native tools, with their source and restrictions; never retype numbers into a stronger-looking report without the same provenance.

## Harness and backend implementation contract

- Use the existing provider-neutral catalogue/dispatch/route structure, toolsets and safety tiers. No separate OpenAI-only action path, hidden agent shell, arbitrary code executor, network filesystem writer or provider-specific numerical engine.
- Native project-management services own their operations. Add thin read/preview/execute/status/recovery adapters only as those services become available. Proposed capability IDs PMAI01–PMAI12 are planning labels, not callable tool names.
- Register any new ordinary edit adapter with complete native preview/read-back, classify every write and retain route/handler ACL and lock enforcement for in-process chat calls. Incomplete argument cards do not certify success.
- Multi-target effects require the shared operation journal, source/target digests, read-back and recovery from PM-B1. Do not loop existing active-project tools while silently switching projects, accept untrusted tool-supplied IDs, or assume parent access/approval policies cover every child.
- Strict checked requests remain limited to the existing admitted tools/profiles. New PM tools need explicitly validated checked read/effect adapters before use in that path. Do not disable checking, bypass allowlists or downgrade to ordinary execution automatically to complete a request.
- Recheck actor, target access, personal restrictions, tool safety policy, revisions, lock/busy state and approval binding at actual admission/worker pickup/commit as the current service requires. A UI access summary is observation only.
- Reuse durable event logging, approvals, task/job origins and Activity/Needs you records. Never save approval credentials inside project bundles, snapshots, reports or inherited scenario metadata. An old decision observation is not renewed authority.
- Honor configured resource ceilings and existing sweep/job limits. A proposed eight-target matrix does not raise the four-case sensitivity tool cap. Read-only/model calls also consume resources; nothing in this spec authorizes automatic background model use or paid calls.
- Bound request size, targets, parameter count, output rows, file bytes, tool attempts and elapsed work. Unknown outcomes require inspection/recovery, not automatic replay. No recursive “optimize until good” run loop.
- Native UI actions and chatbot actions produce interoperable references: operation ID, exact target list and revisions, job IDs, selected result IDs, requested scope, native outcomes and evidence/check state. Numerical facts are read from native services rather than copied into a competing chat database.

## Delivery slices

| ID | Deliverable | Dependency and evidence |
| --- | --- | --- |
| PM-AI0 | Context strip, exact project/variant/run/version selection and existing access disclosure across management screens | PM-V0; existing resolvers and context/navigation guards. Source-only/mounted states labelled accurately. No new write authority. |
| PM-AI1 | Read-only project/variant summary and owning-field navigation adapters | Authorized bounded native summary; preserve existing no-project shortcuts and section/mode. All-tabs source verification remains conditional. |
| PM-AI2 | Shared-assumption preview/apply/reverse-patch adapters and reusable cards | PM-R0/PM-B0/PM-B1 complete; exact revisions, policy checks, journal/read-back, denied and uncertain cases. Strict support separately gated. |
| PM-AI3 | Exact-target run requests and durable multi-job outcome integration | PM-B2 plus supported orchestrator; reuse LC-12 single-job continuation and extend only its admitted group scope. No duplicate submissions or revived approval. |
| PM-AI4 | Evidence-backed comparison/explanation/report navigation | Existing native A/B first; S6/LC-18 for base+N, appropriate source adapters and checked profiles per metric. |
| PM-AI5 | Safe-version actions, additional PM metadata writes and voice parity | Each native feature must exist first. Restore safety F01–F03 remain release blockers; voice changes no authority and adds no live equipment control. |

Read-only integration can progress alongside the visual redesign. Safe restore, shared writes and their chat controls wait for the corresponding native contracts. No separate chatbot-only implementation should get ahead of the UI's own reliable behavior.

## Acceptance and validation matrix

These are required future acceptance cases, not executed tests for new features. Use deterministic fake providers and mocked external provider transports while keeping native authenticated HTTP/dispatch/read-back real. Representative browser integration should exercise actual native endpoints in an isolated environment; pure UI mock tests are identified separately. Paid model/voice tests and real-use/domain calibration remain pending unless separately authorized.

| ID | Scenario | Required outcome |
| --- | --- | --- |
| AI01 | Project-home generic open/new action without model key | Existing native shortcut works once; no model call, duplicate wizard or silent named-target claim. |
| AI02 | Named project, partial name, UUID-shaped name or foreign-org collision | Exact authorized resolution or scoped choice/not-found; no active-project substitution or name leak. |
| AI03 | Ambiguous “this”, “latest”, “previous” across variant/run/version | Resolve distinct canonical source or ask; no newest-equals-current assumption. |
| AI04 | Project/org switch while model response, preview or approval is pending | Origin remains bound; stale preview/action cannot mutate the newly active project. |
| AI05 | Overview readiness/next action | Same server verdict and owning field as native UI; context/mode/return position preserved. |
| AI06 | Single edit in Guided/Expert | Same unit/basis, complete native old→new card, approval digest and read-back; computed/read-only fields refused. |
| AI07 | Batch common-value edit with intentional variant difference | Exact selected targets, differing assumption preserved by default, explicit overwrite preview when requested; no root edit unless selected. |
| AI08 | Target missing/renamed asset, profile override or incompatible money basis | Explain/refuse unsupported mapping/effective value; no guessed name, silent scalar/profile substitution or model-side conversion. |
| AI09 | Child is read-only/restricted, ACL revoked, lock acquired or policy revision changes | Recheck every target at native admission/commit; stale approval cannot authorize it. |
| AI10 | Native form changed after chat preview, including change-and-restore | Revalidation rejects stale version/digest; fresh preview required under current revision rules. |
| AI11 | Denied edit or partial/uncertain batch effect before a dependent run | No solve submitted; show actual per-target receipt/recovery state and preserve historical evidence. |
| AI12 | Approve edit but no run authorization, or ask only for preview | No implicit queue submission or extra project writes. Existing approved composites stay within their exact declared scope. |
| AI13 | Run exact selected set with one failure/cancellation | Only reviewed jobs; per-job native terminal results, unknowns explicit; no invented all-success or unintended descendant run. |
| AI14 | Long job exceeds turn wait, tab closes, application restarts | Existing durable origin tracking and at-most-once eligible continuation; no duplicate run or revived approval. Group completion waits for its explicit group contract. |
| AI15 | Origin job completes while user works elsewhere | Activity retains source; no active-project hijack, unsolicited result navigation or wrong-project numerical answer. |
| AI16 | Pairwise or base+N request with different horizons/currencies/scope | Native comparison supports or refuses exact request; no model-generated deltas/ranking and no false like-for-like winner. |
| AI17 | Requested annual reliability/compliance metric missing or outside checked scope | Explicit unavailable/not checked or strict refusal; no inference from objective/job completion or fallback to unverified execution. |
| AI18 | Source-bound native result display unavailable | Honest Opened/mounted-only status; no claim displayed data was verified, no forced Expert mode or current-result substitution. |
| AI19 | Ask to undo vs view historical result vs restore whole version | Clarify exact intent/scope; historical viewing changes no inputs; unavailable safe restore refuses. |
| AI20 | Reverse shared change after later conflicting edit | Native conflict check preserves later work; no blind multi-project snapshot restore. |
| AI21 | Run saved version | Exact supported isolated copy/source manifest and ordinary job request; current working design unchanged. |
| AI22 | Restored/copied chat history contains prior approvals | No restored tokens or policy inheritance; current access and native effect admission required. |
| AI23 | Malicious project title/upload/report contains tool or policy instructions | Treat as untrusted data; bounded reader cannot execute instructions, reveal secrets or select foreign targets. |
| AI24 | Type/dictate/live command, interrupted speech or stale transcript | Same intent/target/approval/receipt pipeline; no background/partial speech approval; distinct speech/wait/job cancellation. |
| AI25 | Model unavailable, quota exhausted or user chooses native UI | Management, previews, edits and jobs remain usable through native controls; no hidden provider fallback or paid call. |
| AI26 | Export, replay/retry, double-click or uncertain completion | Exact supported source/export scope; same request returns same receipt; no duplicate effects or stronger checked claims in reports. |

Before implementation, run the existing relevant target/reference, edit-card, access/restriction, navigation, continuation/evidence, project isolation and provider-parity suites, then add meaningful failing journeys for each newly implemented slice. Standard catalogue/inventory, closed-frame, tool-schema/route, lock/ACL and semantic checks remain required. The project-management audit's older passing suites do not certify these additions.

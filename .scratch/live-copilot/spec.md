<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Whole-application copilot specification

Status: ready-for-agent
Baseline: `c75ca5584b873418f10f1bca42f868255d445b7b`
Implementation: remaining capabilities below are proposed, not enabled here.

Planning review: [approved at the exact recorded revision](review-approval.md).
This covers specification/test-requirement completeness, not execution or
domain release certification.

## Product outcome

The companion stays beside the workbench. A user can explore an authorized
model, understand any supported result, edit supported parameters, initiate
ordinary tools and compose an ordered workflow in natural language. The same
conversation works through typed input, reviewed dictation and later continuous
speech. Load flow is one example; the scope is all application families in the
existing capability matrix, with capability-specific release evidence.

“Any tool/parameter” means every eligible capability the application actually
exposes under current authentication, project, role, policy and schema. Missing
tools, read-only/computed fields, unsupported checked operations and inaccessible
projects remain explicit. Discovery must not expose secret values or confer new
authority. Generic reachability and independent domain validation are distinct.

The [domain acceptance annex](domain-coverage.md) is normative for LC-C1/C3/C5/
C6/C10. It maps all 14 inventory families to 24 suites and distinct mathematical
engines/control models. A generic wrapper and one journey per family do not
establish that coverage. The source-review agent did not execute those suites
or provide independent domain release approval.

## User journeys

| Request | Required behavior |
| --- | --- |
| “Summarize the results of sensitivity X” | Resolve exact owned project/case/run and requested result family; clarify multiple matches. Bind source/window/baseline, retrieve available native evidence, compute a bounded summary, open matching views and acknowledge displayed data. Missing retained sections stay unavailable; never use current results for a different run |
| “Show the important results, then explain this chart” | Offer supported applicable sections, expose current/discussed source, walk only matching verified views, and preserve selected entity/time/window for follow-ups. Manual navigation wins; the copilot explains what is actually displayed |
| “Update the storage costs to X, Y, Z” | Resolve Store/StorageUnit and exact entities, discover real editable cost fields and units/basis, clarify missing currency/price-year/annualization where material, show complete changes, request ordinary approval, execute once and verify native read-back. Never guess which cost X means |
| “Update line parameters X, Y, Z, then start the simulation” | Resolve fields/values/entities; prepare ordered steps; apply only admitted confirmed edits; verify every required postcondition; recheck project/input revision and request ordinary execution approval before starting one job. Refused/partial/uncertain writes block the dependent solve |
| “Run a sensitivity and compare/export it” | Reuse existing isolated cases, common budgets, receipts, reconciliation and native comparisons; expose failure/partial outcomes. Every new recipe needs its own adapter. Creating a plan is not executing it |
| “What is running? Stop speaking / stop the job” | Report native observed work and freshness without a model polling loop. Playback interruption and actual job cancellation are separate operations; display the eventual native cancellation/effect receipt |
| “Open project X / inspect its parameters” | Resolve an authorized exact target. Do not turn a named-project request into an unlabelled picker or a tour of the previous project. Preserve current/discussed scope and ordinary project-binding policy |

## Required contracts

**LC-C1 Capability discovery.** Generate a bounded versioned view from actual
catalogue/routes/toolsets, entity schemas and UI/result adapters: stable
capability/operation IDs, family, applicable scope, schema revision, eligibility
and refusal reason, read/write/execution classification, parameter names,
types/units/bounds/enums/editability, and current checking support. Page and
filter large models; retrieve values on demand. Support dynamic tool discovery
within provider offer limits; never send a whole network or secret settings as
unbounded prompt context. Existing server schemas remain validation authority.

Declare engine/mode/fidelity and supported claim limits as capability metadata.
Resolve “simulation” before execution: optimization, static AC/security analysis
and actual aggregate frequency dynamics are distinct operations. Existing
single-bus RK4 frequency screening is supported within its native limits; it
does not model voltage dynamics, inter-unit oscillations or detailed inverter
current-limit dynamics. Unsupported EMT/PLL/transient-voltage claims refuse
explicitly rather than substituting hourly dispatch or a static handoff.

**LC-C2 Scope resolution.** Resolve names to owned canonical IDs before
navigation or execution. Bind actor, current project, discussed project/case/run,
baseline, result source/mode, time window/period, entity and generation/revisions.
Server checks every use. Display these refs beside the answer. Natural-language
labels and retrieved documents cannot become trusted IDs or instructions.

**LC-C3 Results/evidence.** Use a registry of typed native adapters rather than
hard-coded tours for one result. Each adapter declares eligible current and
retained sections, completeness, supported filters/entities, provenance,
units/conventions, bounded fact rows, unavailable reasons and checking profile.
Persist immutable historical evidence at job completion with manifest/hash/
retention rules; legacy runs without a section remain unsupported. Reuse a
shared native numerical source for API, UI, chat and exported facts. Separately
verify correctness using independent analytical/hand-calculated/benchmark
fixtures per engine as specified in LC-D01–24; agreement among surfaces sharing
one wrong calculation is insufficient. No global
“checked” label may be inferred from a successful tool or navigation.

For the copilot's engineering summaries, numerical claims must reference
admitted native fact slots with source, unit and validation disposition. The
model may explain those facts but must not introduce a new unsupported number,
convert a missing value to zero or silently downgrade a requested checked
answer. Calculated/checked coverage must expand through separately tested
adapters; unsupported claims remain unavailable. Legacy ordinary chat retains
its explicit unchecked distinction and is not evidence for this requirement.

**LC-C4 Navigation.** Reuse current typed UI actions and navigation epochs.
Extend per-adapter source-bound ACKs to current/historical pages, filters,
selected entities and times. Mounted/submitted and displayed-source-verified
are separate outcomes. Recheck native source at ACK; stale, late or wrong-view
responses do not claim success. Manual user actions and newer turns supersede
older navigation. Unavailable pages remain clearly unavailable.

**LC-C5 Parameter edits.** A proposed generic edit envelope references native
operation/entity IDs and schema/input revisions, plus typed changes. A native
adapter validates units/basis/editability and produces the exact complete
old/new/unit preview and canonical digest. Ordinary dispatch owns confirmation,
locks, personal rules and checked policy. Verify postconditions through native
reads and durable effect receipts where required; HTTP 200 or model prose is
insufficient. A generic envelope does not authorize arbitrary code or URLs.

**LC-C6 Ordered execution.** Use a bounded versioned operation graph over
allowlisted native capabilities, not arbitrary scripts. Persist dependencies,
preconditions, approval origin, input/schema/policy revisions, resource/attempt/
time caps and native receipts. Separate planned, admitted, waiting, running,
verified, partial, refused and uncertain states. Dependent steps require verified
native postconditions and current admission. Reserve once and reconcile before
any retry; ambiguous effects never replay automatically after browser loss,
restart, timeout or profile changes. Reuse native jobs/experiment mechanisms.

**LC-C7 Answer UX.** Show what was understood; resolved scope; compact action
plan; exact decisions; progress with native IDs; answer/fact provenance; applied
versus pending changes; and explicit unavailable/partial results. Keep transcript
and floating views consistent. Choice and Confirmation must be usable in either
surface. Fix current composer pointer overlap; test actual keyboard, touch,
narrow/short screens and reduced motion. Do not use animations as work evidence.

**LC-C8 Speech.** Preserve the approved hosted design and existing parent/lease
mechanisms. Only finalized utterances may request admission. Bind generation,
deduplicate corrections, clarify ambiguous engineering quantities and retain
caption/typed fallback. STT is input, not an approval credential. Initial effects
keep the ordinary explicit confirmation cards. TTS consumes the same validated
text/facts; barge-in cancels playback, not a running solver. Actual capture/
transport/playback events control media state; all exits release resources.

**LC-C9 Resources/recovery.** Wire existing parent reservations/settlement into
reasoning, media, STT/TTS and jobs. Preserve unknown reservations; deduplicate
late usage. Pin the initial bridge to one authoritative worker. Rebind rotates
credentials/generation and refreshes refs; it does not restore old approvals,
replay effects or reset budgets. No current paid/live authorization is created
by this specification.

**LC-C10 Compatibility and release.** Regenerate capability mappings when
schemas/tools change; retain provider parity, current Guided/Expert rules,
ordinary/checked distinctions and native numerical conventions. Incoming
profile-generation/process-unit APIs get adapters only as those features merge.
Release per capability/family with independent expected results and executable
evidence. Full-family behavior, vendor readiness and domain approval remain
separate statuses. Never promise zero hallucinations from a finite suite.

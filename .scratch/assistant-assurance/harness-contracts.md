<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Harness reliability API, version 1

These are implemented, bounded contracts under `/api/chat`. They do not approve
all domains or certify engineering validity. Read [implementation status](implementation-status.md)
for coverage and limitations. Authentication, ownership, project ACLs and existing
CSRF checks apply; client scope and model drafts are data, never proof.

## Rollout

| Server setting | Behavior |
|---|---|
| `PYPSA_GUI_ANSWER_ASSURANCE_MODE=disabled` (default) | Ordinary chat; explicit strict requests refused |
| `...=pilot` | Strict only for client `assurance: {"version":1,"mode":"strict"}` |
| `...=strict` | All normal turns use checked publication |
| `...=shadow` | Normal chat plus bounded nonpublic observations; no approval |
| `PYPSA_GUI_ANSWER_ASSURANCE_PROFILES` | Absent: five implemented profiles; comma-separated exact IDs restrict admission; empty disables all |
| `PYPSA_GUI_ANSWER_ASSURANCE_EFFECTS=checked-input-update/1` | Enables the narrow checked write contract; default disabled |
| `PYPSA_GUI_ANSWER_REVIEW_MODE=required` | Enables a single bounded review of qualitative interpretations; default disabled/withheld |

`GET /reliability/capabilities` lists enabled profiles, units/formulas, limits and
unsupported categories. `GET /reliability/contracts` returns request/draft JSON
schemas. These capabilities do not declare independent domain approval.

The chat **Check answers** control consumes capabilities and sends the existing
explicit version-1 strict option in pilot mode. It defaults off for new chats,
project bindings and application reload; server strict mode is always checked
and cannot be disabled. Disabled/shadow/invalid/unavailable configuration cannot
advertise available opt-in. A failed/rolled-back request never automatically
retries as ordinary chat; an explicit opt-out applies to new pilot messages.

The browser arms publication before dispatch and requires a version-1 strict
`session_init` acknowledgement with bounded session/turn identifiers and
`state: "checking"` before accepting other frames. Missing/downgraded/duplicate
acknowledgement or a turn ending without valid approval withholds output.
Native digest verification remains mandatory before display or speech.

Checked retry/edit/clarification policy survives the user's opt-out for new
turns. Public history projection adds optional `requires_checked: true` for known
checked answer references, including unavailable ones. This is a conservative
retry requirement, never an approval/evidence flag; unavailable history still
omits its answer identity/receipt. Imported flags cannot grant checked facts or
tool permission. Asynchronous rewinds recheck the browser binding generation
before changing local history or resending. Queued, Live and dictated requests
use the same dispatcher. Refused queued requests are dropped, not replayed when
availability recovers; a typed draft is preserved. Checked writes and qualitative
review remain independently controlled. See the [pilot runbook](checked-opt-in-runbook.md).

## Calculations

A `/stream` body can include the following, alongside ordinary `session_id`,
`message` and strict assurance options:

```json
{
  "checked_scope": {
    "version": 1,
    "profile": "generator-cost/1",
    "project_id": "authorized-project-id",
    "entity_id": "G1",
    "metrics": ["dispatch_energy", "marginal_expenditure", "allocated_fixed_cost", "total_cost"],
    "cost_basis": {"currency": "EUR", "price_year": 2026, "annual_fixed_cost_fraction": 1},
    "retain": true
  }
}
```

Profiles: `generator-dispatch/1` (three dispatch metrics), `generator-cost/1`
(six default dispatch/cost metrics), `network-linear-cost/1` (same six metrics
for `entity_id: "network"`). Omit `metrics` to use profile defaults. Additional
unsupported quantities remain unavailable slots. `start` and `end` must both
be supplied and match actual snapshots exactly; boundaries are inclusive.
No inferred timezone or interpolation is applied. Dispatch energy uses
`generators` weights; marginal expenditure uses `objective` weights. Fixed
charges use installed capacity times assumed annual capital charge, multiplied
by the explicitly declared annual fraction for total cost. A price year is an
assumption, not a currency or inflation conversion.

Whole-message shortcuts include:

```
Give me the key results for generator G1
Show me total cost for generator G1; price year 2026; annual fixed cost fraction 1
Show me key results for network; from 2026-01-01T00:00:00 to 2026-01-01T01:00:00; price year 2026; annual fixed cost fraction 1
```

An explicit API scope with a missing cost basis remains unavailable. A recognized
natural-language cost request without that basis prompts clarification; no
solver objective or assumed price year silently substitutes for it.
Arbitrary prose instructions are not parsed into complete scientific requests.

An approved answer's receipt carries `request_scope`, `result_slots` and
`request_complete`. Only an answered disposition satisfies a required slot.
The original dispatch profile labels valid subsets explicitly; cost scopes
with invalid dispatch do not present a subset as full cost. A failed draft
cannot retain answered dispositions. `passed` certifies the listed bounded
facts; it does not cover unexpressed user goals.

To replay history, supply `evidence_id` with the exact original retained scope
and `retain: true`. Retained raw evidence is recalculated without reading
foreground outputs; ownership/ACLs and checksum validation still apply.
For signed comparisons, request `cost_delta` and/or `cost_change_percent` and
supply `comparison_evidence_id` from a compatible retained baseline. Profile,
project, entity, snapshot window and cost basis must match. Delta is candidate
minus baseline; percentage is delta divided by the absolute baseline times 100.
A zero denominator is unavailable. Historical retention is 24 hours, not an
indefinite archive.

## Checked static-input writes

`GET /reliability/context` returns the active authorized `project_id` and
`input_sha256` under a bounded lock. Send an exact contract:

```json
{
  "checked_operation": {
    "version": 1,
    "operation_id": "8f6dcfc6-50fa-4dcb-8873-d18a1fa45d4c",
    "project_id": "authorized-project-id",
    "tool_name": "update_component",
    "arguments": {"component_class": "Generator", "name": "G1", "attrs": {"p_nom": 25}},
    "before_sha256": "the-64-character-current-input-sha256"
  }
}
```

Scope and operation contracts cannot be combined or attached to uploads. The
supported attributes are Load `p_set/q_set`, Generator/StorageUnit `p_nom`,
Line/Transformer `s_nom`, Store `e_nom`. Values must be finite and physically
nonnegative except signed Load reactive power. All other strict effects are
blocked. Completion describes a static input write; time-series values can still
override those settings. Existing confirmation remains mandatory, including Expert mode.

Reservation happens before execution. Exact completed repeats return the stored
receipt; live/uncertain/conflicting repeats are refused. Publication requires
actual model-input read-back to match the server-prepared candidate, including
absence of unintended input changes. Missing/wrong handler replies and exceptions
cannot report verified completion. A timed-out worker may still finish, so the
turn withholds approval and never silently starts another write.

`GET /operations/{operation_id}` reads owned state. `POST /operations/{id}/reconcile`
accepts exactly `{"version":1,"contract_sha256":"stored-contract-digest"}` and
performs read-back only. A live worker blocks reconciliation. Expected after-state
can settle completed; unchanged before-state settles failed; another partial
state stays uncertain. Reconciliation does not execute a retry. Unresolved
reservations are retained beyond public TTL to prevent ID reuse; expired ones
need operator recovery. The runtime remains one project application worker.

## Navigation

The harness adds an issued `acknowledgement` to supported full-page `ui_event`
frames. `binding_generation` is the client's nonnegative integer generation.
The browser posts exact version/action/turn/generation/target fields to `/ui-ack`
after the expected visible page/tab mounts. It checks current browser project,
turn and generation before posting and before rendering completion. Unsupported
popups/complex navigation and networkless study contexts remain submitted
without an issued network-revision ACK. `GET /navigation/{id}` reads the
owned outcome. Issued ACKs expire after 30 seconds; later turns and newer actions
supersede them. Mounted-view completion is not displayed-result numerical proof.

## Review and delivery

A final typed draft may have at most two qualitative interpretations, each
with a unique ID and citations to checked block IDs. Quantitative claims belong
in computed blocks; the numeric-prose guard rejects digits, common English number words and
engineering/currency units before review. This lexical guard is not a proof
against every possible numerical paraphrase; reviewed interpretations remain
model judgments, separate from deterministically verified facts. One optional
read-only call can pass or withhold all interpretations; it cannot change facts,
use tools, fetch evidence, repair drafts or publish its own prose. The call is
bounded to 512 output tokens, 128 events, a 16 KiB packet, a 4 KiB verdict and
5 seconds caller wait, with two worker slots. Usage must explicitly include
nonnegative integer `input_tokens` and `output_tokens`; an omitted count is not
zero, and cache-only counts cannot authorize an interpretation. Valid reported
counts are still charged when the other required count is missing. Missing
usage/schema, exhausted known budgets, tools or timeout preserve deterministic
facts with a limitation.
This uses the selected provider/model, not an independent expert certificate.
Unknown/late usage is not a durable cross-restart billing reservation.

`GET /answers/{id}` and `/evidence/{id}` revalidate public records.
`GET /answers/{id}/export?format=json|zip` uses that same answer, facts, scope,
limitations and evidence. ZIP includes `answer.json`, `facts.csv`, `manifest.json`
and SHA-256/byte bindings. Chart/report/import/general exports do not acquire
checked status from this API. Existing checked PNG integrity remains separate.
The evidence card offers these authenticated downloads. No new SSE frame type
or provider-specific reliability path is introduced.


## Bounded conversational scope (2026-10-11)

Strict/pilot checked turns also recognize whole-message follow-ups.

This resolver runs on attachment-free turns without a checked operation contract,
under the existing shared-loop selection boundary. Attached-input workflows and
effect contracts do not use conversational result carryover.

Examples:

```
Show those results again.
Show those results from 2026-01-01T01:00:00 to 2026-01-01T01:00:00.
Compare that with the previous run.
Use price year 2026; annual fixed cost fraction 0.5.
```

A completed checked result preserves its exact project, entity, required metrics,
time window, cost basis and current/retained mode in the **live server session**.
An original all-current-snapshots default becomes the actual explicit boundaries
for follow-ups, so later snapshot additions cannot silently extend the window.
Repeat of a comparison retains its baseline and delta slots; repeat of a retained
run retains that evidence ID even when current inputs have changed. Changing a
historical/comparison window or basis requires a new explicit scope.

`previous run` means the previous distinct checked source for the same profile,
entity, exact window and declared basis in this chat, not the previous answer.
If current source inputs/results have changed since the last checked answer,
that last checked source is the baseline; if they match it, the preceding distinct
checked source is the baseline. At most two distinct references are kept, without
searching older runs or substituting another basis. Snapshots use the existing
owned immutable raw-evidence store, its 24-hour retention, 128 KiB limit and
512-record quota. Live references expire no later than their retained evidence;
retention failure clears carryover and cannot revive an older scope.

`Show me cost for generator G1` (or `network`, optionally with `; from ... to ...`)
asks a software-generated choice between **Marginal dispatch expenditure** and
**Total declared-basis cost**. A missing required cost basis prompts a question;
`Price year YYYY; annual fixed cost fraction F` supplies it. Generator dispatch
expenditure uses its existing dispatch profile; network accounting requires its
explicit EUR basis. No model is called for these clarifications. Ordinary auth,
project membership, cancellation, profile selection, turn/session/daily budgets
and durable answer/history publication still apply. There is no effect grant.

Clarification receipts are `limited`, with `request_complete: false`, empty
checked-block/evidence IDs and `needs_clarification` dispositions/reasons for all
required slots. The existing `choice_request` frame is admitted only for the
exact software-generated cost choice after checked publication. Provider-authored
choice/tool payloads remain suppressed. The UI labels the question **Clarification
needed**, hides checked-value downloads and rejects contradictory completion
receipts. No new frame name or provider SDK is introduced.

Context is private server state, bound to owner/project/browser generation and
the latest approved assistant message. It is never rebuilt from model prose,
imported transcripts or caller-supplied histories. Rewind, missing/expired context,
conflicting generator selection or a changed binding require clarification.
A later unverified/unavailable answer cannot seed or restore an earlier result.
Network scope does not shrink when one generator is selected in the UI.
A new chat requires an explicit scope or owned retained evidence IDs. After a
backend restart, authenticated native-history recovery may restore the exact
bounded scope under the private checkpoint contract below. Whole-message parsing
rejects added constraints on recognized follow-ups instead of dropping them.
This does not certify understanding of arbitrary language or cover new domains.


## Owned conversation checkpoints (2026-10-11)

The shared harness stores at most one private version-1 conversation checkpoint
per owner/project/chat using the existing owned record store. It carries the exact
scope, pending software cost clarification, latest approved answer anchor and at
most two distinct retained run references. Its owner/project/chat key is software
generated, not a client field. The mutable checkpoint grants no effect authority;
evidence and approved answer records remain immutable. Existing atomic writes,
owner-store transactions, 24-hour retention, 128 KiB record limit and 512-record
owner quota apply. Checkpoint expiration never exceeds its approved anchor or
latest retained source. No model prose, imported history or client scope flags
can construct a checkpoint.

Only authenticated `GET /api/chat/history` that freshly recreates the recorded
chat may recover context, in strict/pilot mode. Its latest owned native turn must
carry the exact saved answer anchor. The public history must include that anchor
as its last assistant message, with no unreadable-history gap. Software checks
owner/project/chat/schema/expiry, the still-valid approved answer, exact scope and
all retained raw references by recalculation, including their declared adapter
versions. Missing, expired, corrupt, mismatched, duplicate or unavailable state
requires explicit scope; there is no earlier-checkpoint/transcript fallback.
An imported record lacks the native author binding even if it copies a real owned
answer ID. Copied projects/chats and older restored transcripts cannot adopt the
original checkpoint. Current source drift still invalidates current answers;
historical retained answers remain replayable against their immutable source.

Recovery grants a new live browser-generation binding once, at the first strict
turn. The original owner/project and ordinary selection/authorization/profile
checks still apply. Later live generation or selection changes require explicit
scope. Existing live sessions are not recovered or rebound by history reads.
History rebuild and scope adoption recheck the session under its lock; a stream
that starts/completes while this GET prepares recovery is never overwritten.
Checkpoint/evidence validation takes the network lock before the session lock.
No new public payload field, SSE frame, provider path or SDK is introduced;
private checkpoint IDs/state are not exposed by history/export/evidence APIs.

A strict turn first durably revokes any previous checkpoint before scope
preparation, the pending-turn WAL and model/tool execution. Failure to persist a
required revocation emits `assurance_context_persistence_failed` and stops before
execution. Only checked, durably saved final publication replaces it with a new
checkpoint; aborted/interrupted/failed publication cannot revive older scope.
Optional final checkpoint retention failure preserves independently checked facts
but clears carryover. Rewind commits revocation before changing messages; storage
failure returns a typed error with messages/context intact. Audit history remains
an honest append-only record and cannot undo this revocation after restart.

Durable parent resume/rebind also revokes the bound episode checkpoint before
advancing generation or dropping its runtime session. This works when the old
episode is absent after registry restart. A busy turn or foreign owned/project
binding refuses the control; revocation write failure preserves parent generation
and episode. Resume deliberately starts a fresh episode without checked scope;
rebind requires a fresh explicit scope on the existing episode. Parent goals,
refs and caller-provided checkpoint fields cannot create assurance authority.

The merged detached-solver path preserves start receipts for ordinary foreground
calls. An explicitly started sequential durable task retains a bounded solver
outcome wait (the shared per-tool deadline and cancellation apply), so downstream
chart/export steps cannot run on a start receipt. A still-running, missing-queue,
cancelled or contradictory completion receipt leaves the task uncertain instead
of completed. Full asynchronous durable-task step resumption remains pending.

Promoted queued utterances do not inherit the preceding request's explicit scope.
They resolve their own text against validated conversation context. A safe repeat
can retain its exact window/basis, but an unsupported added constraint requires
software clarification without a provider call or checked numerical blocks. Such
a refusal revokes the prior checkpoint instead of retaining the original answer
as the correction's authority.

This recovers bounded checked results and pending software clarification only.
Arbitrary-language understanding, general task/effect resumption, persistent
billing reservations and distributed project workers remain outside this scope.
Default deployment activation is unchanged; independent release/domain approval
is not claimed.

## StorageUnit dispatch profile contract

`storage-unit-dispatch/1` binds one named StorageUnit, the authorized project and
an exact inclusive datetime snapshot window (at most 512 ordered unique steps).
Default slots: `charged_energy`, `discharged_energy`, `net_storage_energy`,
`peak_charging_power`, `peak_discharging_power`, `final_state_of_charge`.
Energy uses `snapshot_weightings.stores` as storage-operation durations, never
generator or objective weights. Net energy is discharged minus charged grid-side
energy; it is not the change in stored energy. Peaks use the separate raw charging
and discharging power, including simultaneous operation, rather than clipped net
power. Final state is the last requested snapshot's raw stored energy.

Admission requires a matching completed optimal LP source, unique aligned raw
`p_store`, `p_dispatch`, `p` and `state_of_charge` populations, finite positive
durations, nonnegative capacities/power, applicable time-varying power bounds,
valid efficiencies and standing losses. Software validates power splitting,
capacity/energy bounds and the chronological storage energy recurrence from the
declared initial state. Missing/invalid prior state propagates into later windows;
checking a suffix cannot hide a broken trajectory. Invalid selected steps never
produce complete energy or peak claims, including when only the final metric is
requested. Formula version, masks, source identity and disclosures are retained
in the same existing evidence/publication/replay/export/recovery path.

Version 1 supports noncyclic StorageUnits with zero inflow/spill and ordinary
single-period datetime snapshots. Stores, cyclic/periodic boundary conditions,
inflow/spill, multi-investment periods, storage costs, round-trip efficiency
estimation, degradation and engineering certification remain unsupported.
Unsupported requested metrics receive unavailable slots. No model numeric prose,
alternate tool response, imported history or caller flags can grant approval.
Deployment remains opt-in and profile rollback applies. This contract records
the editing agent's bounded design; independent domain review remains pending.

Power splitting and state recurrence use absolute tolerance `1e-7` and relative
tolerance `1e-8`; capacity inequalities use absolute tolerance `1e-7`. No missing
dispatch/state output is replaced with zero. Absent spill is a structural zero
only within the zero-inflow supported model. Explicit storage cost-basis and
comparison scopes are rejected before provider execution.

Whole-message example: `Give me the key results for storage unit Battery.`
An optional exact window is `; from 2026-01-01T00:00:00 to 2026-01-01T02:00:00`.
The same profile may be supplied through version-1 `checked_scope`.

A whole-message `Give me the key results` also selects this profile when the
current UI identifies a StorageUnit. Its name binds only scope; authorization,
source provenance and the complete raw trajectory still establish the facts.
Additional requested quantities or suffix instructions cannot silently collapse
into that generic key-results grammar.

Entity class is determined by the requested profile, never by the spelling of
an asset name. A generator or StorageUnit named `network` stays an asset during
clarification, follow-ups and rendering; only the network-cost profile selects
network-wide calculations.

## StorageUnit direct-cost profile contract

`storage-unit-cost/1` extends the same bounded noncyclic zero-inflow/spill
StorageUnit trajectory model; it does not expand the network-cost adapter.
All cost claims require a current matching completed optimal solve, the valid
requested trajectory (including predecessors), and an explicit `cost_basis`.
The EUR price year and annual nature of `capital_cost` are declared assumptions.
No annual allocation is inferred from timestamps, durations or objective weights.

Default metrics are the six storage-operation metrics plus:

| Metric | Formula ID | Unit | Checked calculation |
| --- | --- | --- | --- |
| `storage_discharge_expenditure` | `storage-discharge-expenditure/1` | EUR | Sum of `p_dispatch * marginal_cost * objective_weight` |
| `storage_holding_expenditure` | `storage-state-holding-expenditure/1` | EUR | Sum of end-of-snapshot `state_of_charge * marginal_cost_storage * objective_weight` |
| `storage_operating_expenditure` | `storage-linear-operation-cost/1` | EUR | Discharge plus holding expenditure |
| `annual_fixed_cost` | `annual-capacity-cost/1` | EUR/year | Installed power capacity times annual `capital_cost` |
| `allocated_fixed_cost` | `allocated-capacity-cost/1` | EUR | Annual fixed cost times declared fraction |
| `total_cost` | `storage-direct-total-cost/1` | EUR | Operating expenditure plus allocated fixed cost |

Installed power capacity uses `p_nom_opt` for extendable units and `p_nom`
otherwise; it is not `max_hours * p_nom`. Dynamic prices override static ones.
Operating prices may be negative and must be finite; objective weights must be
positive and finite. Fixed charges must be nonnegative and finite. Nonzero
quadratic costs and modular/inactive units make cost claims unavailable. An
explicit overnight cost or separate FOM term prevents direct fixed/total claims;
this adapter does not infer annuities or silently ignore their precedence.
Missing dependencies and numeric overflow never become a complete total.

The total is direct asset accounting. It excludes charging-electricity
purchases, discharge revenues and other assets' costs; it does not establish
profitability, network cost, the solver objective or NPV. Requested unsupported
quantities remain unavailable slots and make `request_complete` false.
Storage energy continues to use storage-duration weights, independently of
objective-weighted costs. Profile-specific formula definitions are available
under each profile in `/reliability/capabilities`.

Whole-message examples:

```text
Show total cost for storage unit Battery; price year 2026; annual fixed cost fraction 0.5.
Show key results for storage unit Battery; price year 2026; annual fixed cost fraction 0.5.
Show total cost for storage unit Battery; from 2026-01-01T01:00:00 to 2026-01-01T01:00:00; price year 2026; annual fixed cost fraction 0.5.
```

`Show cost for storage unit Battery` asks in software for **Direct StorageUnit
operating expenditure** or **Total declared-basis cost**, then for the missing
basis. No provider is called for those clarifications. Ordinary storage key
results without a declared basis retain the dispatch profile. Explicit API
cost scopes without a basis can answer supported operation slots, but cost slots
remain unavailable. Same-basis retained comparisons, bounded follow-ups,
native restart recovery, JSON/ZIP exports and profile rollback use shared guards.
The [bounded plan and independent arithmetic](storage-economics-plan.md) define
the acceptance boundary; full-domain review and release calibration remain open.

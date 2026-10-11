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
| `PYPSA_GUI_ANSWER_ASSURANCE_PROFILES` | Absent: three implemented profiles; comma-separated exact IDs restrict admission; empty disables all |
| `PYPSA_GUI_ANSWER_ASSURANCE_EFFECTS=checked-input-update/1` | Enables the narrow checked write contract; default disabled |
| `PYPSA_GUI_ANSWER_REVIEW_MODE=required` | Enables a single bounded review of qualitative interpretations; default disabled/withheld |

`GET /reliability/capabilities` lists enabled profiles, units/formulas, limits and
unsupported categories. `GET /reliability/contracts` returns request/draft JSON
schemas. These capabilities do not declare independent domain approval.

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

Ambiguous cost basis is unavailable, never silently replaced with the objective.
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
blocked. Existing confirmation remains mandatory, including Expert mode.

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
popups/complex navigation remain submitted. `GET /navigation/{id}` reads the
owned outcome. Issued ACKs expire after 30 seconds; later turns and newer actions
supersede them. Mounted-view completion is not displayed-result numerical proof.

## Review and delivery

A final typed draft may have at most two qualitative interpretations, each
with a unique ID and citations to checked block IDs. Quantitative claims belong
in computed blocks; numeric prose is withheld before review. One optional
read-only call can pass or withhold all interpretations; it cannot change facts,
use tools, fetch evidence, repair drafts or publish its own prose. The call is
bounded to 512 output tokens, 128 events, a 16 KiB packet, a 4 KiB verdict and
5 seconds caller wait, with two worker slots. Missing usage/schema, exhausted
known budgets, tools or timeout preserve deterministic facts with a limitation.
This uses the selected provider/model, not an independent expert certificate.
Unknown/late usage is not a durable cross-restart billing reservation.

`GET /answers/{id}` and `/evidence/{id}` revalidate public records.
`GET /answers/{id}/export?format=json|zip` uses that same answer, facts, scope,
limitations and evidence. ZIP includes `answer.json`, `facts.csv`, `manifest.json`
and SHA-256/byte bindings. Chart/report/import/general exports do not acquire
checked status from this API. Existing checked PNG integrity remains separate.
The evidence card offers these authenticated downloads. No new SSE frame type
or provider-specific reliability path is introduced.

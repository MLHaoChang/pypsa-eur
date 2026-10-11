<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Checked publication, bounded numerical results, and chart integrity

Status: implementation on `feat/assistant-assurance-calculations`, building on
static-publication commit `eaeae8ad` and documentation commit `f533feda`. This is a bounded pilot, not completion of P0–P7
or approval of the proposed reliability additions. The editing agent reviewed
the implementation; no new independent reviewer verdict is claimed.

## Implemented behavior

- Strict admission validates version/mode before calling a provider. A server
  configured strict cannot be bypassed by omitted options or scripted frames.
- Provider text, thinking and intermediate assistant prose stay private. Final
  drafts must contain only bounded, typed observed or profile-owned computed blocks. Duplicate fields/IDs,
  booleans as numeric values, extra trust flags and arbitrary narrative fail.
- `get_component` captures finite configured input facts from the original
  network under its lock, before tool truncation. Handler values must match raw
  data; claims must match the exact entity, metric, value and unit. Missing data
  cannot be promoted to zero. Time-series overrides are explicitly unchecked.
- Software renders the facts, or publishes an honest, limited fallback. No
  additional model/reviewer/repair call is made. Normal shared budgets remain.
- Owned answer/evidence records have checksums, atomic replacement with fsync,
  a 128 KiB record cap, 512 records per owner and 24-hour retention. Expired files
  are reclaimed at the next write. These are current-input records, not immutable
  historical-run evidence. One application worker is the supported topology.
- Answer/receipt and history commit before approval/text. Evidence and ACLs are
  checked again before publication, lookup, history and export. Changed, missing
  or corrupt evidence prevents replay. Repeated lookup does not call the model
  or execute effects. Legacy history is explicitly unvalidated in strict mode;
  strict prompt seeding excludes legacy claims and rechecks approved references.
- The UI buffers approved tokens and verifies the exact UTF-8 digest and turn
  identity before display or TTS. Unapproved tool cards/links and thinking are
  withheld. Noncompleted tool outcomes do not show a completion checkmark.
- A required, fail-closed tool allowlist runs before handler execution. Only
  supported component reads and existing panel navigation are admitted. Panel
  navigation is submitted, not verified complete; no expected ACK is issued,
  and the ACK endpoint refuses unissued acknowledgements.

## Activation and supported scope

`PYPSA_GUI_ANSWER_ASSURANCE_MODE` defaults to `disabled`, preserving ordinary
chat behavior. `strict` gates every normal turn; `pilot` requires the client to
send `assurance: {"version": 1, "mode": "strict"}`. `disabled` and the reserved
`shadow` setting refuse explicit strict requests with 503. Shadow evaluation
itself is not implemented. Invalid server modes also refuse admission.

Supported input metrics: Load `p_set`/`q_set`, Generator and StorageUnit `p_nom`,
Line/Transformer `s_nom`, Store `e_nom`. A whole-message request such as
“Give me the key results for generator G1” (or a selected Generator in UI context)
activates `generator-dispatch/1`. Other numerical profiles and arbitrary
explanations remain unsupported by checked mode.
The production deployment is not switched to strict automatically.

## Remaining reliability work

The proposed [reliability contract](reliability-contract.md) still needs its
observable schemas reviewed/frozen. In particular, a `passed` v1 receipt in this
pilot means its listed static facts matched evidence; it does **not** establish
that the user's entire request was fulfilled. Request-slot coverage is implemented only for the explicit three-slot generator profile.

## Numerical profile and artifact boundary

The editing-agent candidate profile admits exactly generator dispatch energy
(MWh), marginal dispatch expenditure (EUR), and peak dispatch (MW) over all
current snapshots. It supports 1–512 ordered unique datetime snapshots, a
positive-sign Generator, a completed optimal LP status, matching solve-input
provenance, aligned raw dispatch and weights, and finite capacity bounds.
Energy uses `generators` weights; expenditure uses `objective` weights. Per-metric
raw masks reject missing/nonfinite values and nonpositive required weights.
Invalid intervals make the full-horizon slot unavailable; any valid subset is
labelled explicitly with its coverage and cannot supply a full-horizon claim.
Numeric overflow is unavailable. All three required slots receive a disposition.

Formula IDs and evidence pointers are server-owned. The provider draft must
include every available metric exactly once with the exact recalculated value,
unit, entity and formula. Fabrication, omission and duplicate/extra fields fail
closed; failed drafts cannot retain answered-slot badges. Software authors
coverage, partial values and cost-basis disclosures. Changes to outputs, inputs,
solve status or authorization invalidate public lookup/history/export. Dynamic
ledgers ride fenced per-turn content; the system prompt remains stable.

The independently specified real-LP oracle is 5 MW × 2 h + 7 MW × 1 h = 17 MWh,
170 EUR marginal expenditure at 10 EUR/MWh and equal objective weights, peak
7 MW. Separate objective weights [1,1] yield 120 EUR expenditure while energy
remains 17 MWh. Fixed capital cost is excluded. This does **not** implement the
proposed 230 EUR same-basis total-cost example: annual fixed-cost allocation,
price-year mappings and a full cost-basis registry still need domain review.
A matching fingerprint establishes current source provenance, not a scientific
certificate of whole-network feasibility or independent solver verification.

Charts now capture the exact ordered numeric dataset/units, render settings,
source/input fingerprint and PNG SHA-256. A server-written upload marker binds
the complete provenance sidecar before bytes become downloadable. Sources and
access are rechecked after rendering. Downloads and delivery bundles refuse
missing/altered sidecars or bytes and carry the same manifest. Sending the exact
validated bytes avoids a second-open race. Legacy/generic uploads have no checked
chart claim; other export formats are not chart-certified. The three original
P4 RED cases now run by default, with additional corruption/race regressions.
Chart integrity does not certify engineering validity and chart creation remains
outside the strict-chat tool allowlist.

Still pending: immutable retained runs/restart journeys, all-domain metric and
operation mappings, arbitrary request completeness, effect read-back/idempotent
recovery, positive view ACKs, report/import/general-export manifests, independent
reviewer checks, browser/accessibility journeys, multi-worker storage,
load/calibration measurements and rollout gates. Production defaults remain
unchanged; whole phase tickets remain open. The new computed/slot contract has
not inherited the original tranche's independent approval.

## Verification

All providers are fake or HTTP-mocked; no paid model calls. Current commands,
counts and limitations are recorded in [coverage ledger](coverage-ledger.md) and
[handoff](session-handoff.md). Neither an initial RED baseline nor a skipped test
counts as implemented coverage. Whole phase tickets remain open.

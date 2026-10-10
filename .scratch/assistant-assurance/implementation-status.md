<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# First implementation: checked static inputs and publication

Status: local implementation on `feat/assistant-assurance-publication`, based on
documentation commit `f533feda`. This is a bounded pilot, not completion of P0–P7
or approval of the proposed reliability additions. The editing agent reviewed
the implementation; no new independent reviewer verdict is claimed.

## Implemented behavior

- Strict admission validates version/mode before calling a provider. A server
  configured strict cannot be bypassed by omitted options or scripted frames.
- Provider text, thinking and intermediate assistant prose stay private. Final
  drafts must contain only bounded, typed observed blocks. Duplicate fields/IDs,
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
Line/Transformer `s_nom`, Store `e_nom`. Queries requiring other tools, calculations,
solved outputs or arbitrary explanations currently receive a limited fallback.
The production deployment is not switched to strict automatically.

## Remaining reliability work

The proposed [reliability contract](reliability-contract.md) still needs its
observable schemas reviewed/frozen. In particular, a `passed` v1 receipt in this
pilot means its listed static facts matched evidence; it does **not** establish
that the user's entire request was fulfilled. Request-slot coverage is pending.

Next numerical tracer: a server-owned energy/cost key-results profile, weighted
arithmetic from raw validity masks, independently specified 17 MWh/230 EUR fixture,
and unavailable/partial behavior for invalid intervals. Do not enable general
calculation answers based on matching an existing sanitized API value.

Also pending: immutable retained runs and restart journeys, computed blocks,
all-domain metric/operation mappings, effect read-back and idempotent recovery,
positive view ACKs, chart/report/import/export manifests and download admission,
read-only reviewer checks, accessibility/browser journeys, multi-worker storage,
load/calibration measurements and rollout gates. Existing artifact APIs remain
outside this pilot; suppressing links in strict chat does not secure direct
artifact downloads. Three P4 artifact acceptance cases intentionally remain RED.

## Verification

All providers are fake or HTTP-mocked; no paid model calls. Current commands,
counts and limitations are recorded in [coverage ledger](coverage-ledger.md) and
[handoff](session-handoff.md). Neither an initial RED baseline nor a skipped test
counts as implemented coverage. Whole phase tickets remain open.

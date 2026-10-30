<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LG-16: Define the event/control evidence admission and adapter gate

Status: needs-info
Type: research-and-implementation
Priority: P2
Group: R5
Blocked by: LG-01
Baseline: master `aba252d2384f55116bbd15516a5b025951ba265e`; recheck before claiming.

## Observed gap

Millisecond workload transients cannot use the whole-second generic series store. Public controller settings lack guaranteed site applicability.

## Research assignment

Which trace/model/operating-point evidence supports a bounded native rapid-load screen, and is a separate event adapter truly required?

Candidate starting points, not import approval:

- Research PR #527 as background only; owner decision RLR-527 is current workflow authority.
- Exact measured/OEM/open model event traces with sample rate, model/controller revision, operating point and source rights. No universal preset library assumed.

Apply [the source protocol](../research-protocol.md). Deliver a field/source map, exact resource/rights evidence, bounded permitted fixture or extraction recipe, independent numerical/time/identity oracle, and a GO/NEEDS INFO/NO GO record for a specified consumer. Do not mark a candidate verified from a landing page. Desk research can prepare before dependencies; implementation completion needs all listed dependencies and any ticket-specific gates.

## Native anchors and bounded scope

- `docs/agents/owner-decisions.md`
- `pypsa-gui/backend/services/library/series_store.py`
- `pypsa-gui/backend/harness/README.md`
- `.scratch/project-workflow/modular-cards.md`

These are inspected anchors, not permission to rewrite whole directories. Before code, record an exact file allowlist including relevant tests, permitted new reference/fixture files and one release fragment. If a new route, item kind, engine, migration or out-of-scope path is needed, return a scope amendment for review. Check the outsourced register and open PR overlaps first.

## Implementation steps after admission and required plan gates

1. Reconcile #527 with RLR-527: smoothing screen in adequacy, one backup/smoothing battery reserve, after-battery values beside existing grid rows, and Control 2 Island, ride-through & load smoothing. Do not revive the research PR as new tickets wholesale.
2. Map trace sampling, MW/kW normalization, PoC/rack boundary, ramp/event duration, uncertainty and controller/asset limits to current native screens. If subsecond evidence is needed, first propose an isolated event adapter with separate schema/time precision, consumer and independent validation; do not modify generic series canonicalization.
3. Keep unsupported high-frequency dynamics, grid-forming certification and parameter tuning as specialist handoff. Only after an explicit adapter/engine plan GO may a follow-on runtime PR use the evidence.

## Planned acceptance tests — write first

| ID | Trigger | Required observation |
|---|---|---|
| LG-16-01 | A trace has 1 ms samples. | Generic series still rejects it; no rounding/aliasing into whole seconds. |
| LG-16-02 | A bounded rectangular load step needs smoothing energy. | Independent power-times-duration/reserve oracle matches the screen with unit/efficiency limits. |
| LG-16-03 | Backup and smoothing request the same battery reserve. | Combined allocation cannot exceed the one declared reserve; no double count. |
| LG-16-04 | Operating point/controller or sample rate is missing. | No dynamic performance/certification claim or automatic operational setting. |
| LG-16-05 | User deselects the optional screen. | No new job, required field, background simulation or changed base result. |

Regression anchors (locate renamed/equivalent tests during the current-master audit; do not invent execution):

- `test_library_series_store.py`
- `Relevant existing DC reserve/smoothing suites located during audit; document exact paths before scope approval`
- `test_write_surface_lock_policy.py`

## Stop conditions and handoff

This ticket first delivers research and an adapter decision; implementation is conditional. No new dynamics engine, paid media/model generation, or unsupported modelled trace labelled measured.

Follow [spec.md](../spec.md) and [HANDOFF.md](../HANDOFF.md). Keep source, numerical values, semantics, applicability and approval evidence distinct. Record unresolved fields and safe fallbacks; software test success does not establish independent domain calibration. All five acceptance IDs remain planned until executed evidence is attached.

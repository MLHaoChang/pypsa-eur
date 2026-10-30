<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LG-08: Add one complete representable European tariff pilot

Status: needs-info
Type: research-and-implementation
Priority: P1
Group: R3
Blocked by: LG-01, LG-03
Baseline: master `aba252d2384f55116bbd15516a5b025951ba265e`; recheck before claiming.

## Observed gap

Two TenneT items are partial because weighted monthly peaks are unsupported. No complete new EU schedule may inherit a misleading total.

## Research assignment

Which official schedule for one operator/customer class can the current tariff engine represent completely, and what remains excluded?

Candidate starting points, not import approval:

- Official regulator/DSO/TSO schedule selected through existing EU-C source register and terms; exact edition/eligibility required.
- Existing ACM/TenneT EU-C09 and Danish DataHub candidates; successful official resource access/rights must be recorded, not inferred.

Apply [the source protocol](../research-protocol.md). Deliver a field/source map, exact resource/rights evidence, bounded permitted fixture or extraction recipe, independent numerical/time/identity oracle, and a GO/NEEDS INFO/NO GO record for a specified consumer. Do not mark a candidate verified from a landing page. Desk research can prepare before dependencies; implementation completion needs all listed dependencies and any ticket-specific gates.

## Native anchors and bounded scope

- `pypsa-gui/backend/services/library/common_items/tariff`
- `pypsa-gui/backend/services/library/common.py`
- `pypsa-gui/backend/services/commercial/tariff_engine.py`
- `pypsa-gui/backend/models/commercial.py`
- `pypsa-gui/backend/tests/test_library_common_tariff.py`

These are inspected anchors, not permission to rewrite whole directories. Before code, record an exact file allowlist including relevant tests, permitted new reference/fixture files and one release fragment. If a new route, item kind, engine, migration or out-of-scope path is needed, return a scope amendment for review. Check the outsourced register and open PR overlaps first.

## Implementation steps after admission and required plan gates

1. Select one schedule only after enumerating voltage/customer class, taxes, effective dates, energy/demand/contracted charges, holidays/TOU, reactive charges, export rules and TSO charges already included in DSO rates.
2. Build a pure offline extraction/normalization adapter into the existing tariff item schema and a per-field source manifest. If unsupported weighted-peak/ratchet/fractional-band clauses apply, choose another fully representable pilot or preserve partial status; no engine expansion hidden in data work.
3. Publish the permitted record to the allowed scope, show eligibility/missing clauses in the existing commercial editor, and run independent month and annual bill calculations using fixed dispatch.

## Planned acceptance tests — write first

| ID | Trigger | Required observation |
|---|---|---|
| LG-08-01 | Bill a fixed trace under the selected complete schedule. | Independent line-item and monthly/annual arithmetic match including eligible taxes. |
| LG-08-02 | A DSO price includes a TSO component already. | It is counted once; no duplicate upstream network fee. |
| LG-08-03 | Use an ineligible voltage class or expired schedule. | Selection/use refuses or requires an explicit eligible choice. |
| LG-08-04 | Inspect an existing TenneT partial item. | Its unsupported weighted-peak flags remain and total stays None. |
| LG-08-05 | A selected schedule has unsupported holidays/00:30 band/ratchet. | It cannot be labelled a complete bill; reason and excluded components persist in export. |

Regression anchors (locate renamed/equivalent tests during the current-master audit; do not invent execution):

- `test_library_common_tariff.py`
- `test_tariff_engine_core.py`
- `test_tariff_engine_demand.py`
- `test_settlement_inputs.py`
- `test_lp_tariff_capacity.py`

## Stop conditions and handoff

Exact licence and native full-representability evidence are prerequisites. Conditional minute boundaries belong to ticket 10; weighted monthly peak support needs a separate reviewed engine brief.

Follow [spec.md](../spec.md) and [HANDOFF.md](../HANDOFF.md). Keep source, numerical values, semantics, applicability and approval evidence distinct. Record unresolved fields and safe fallbacks; software test success does not establish independent domain calibration. All five acceptance IDs remain planned until executed evidence is attached.

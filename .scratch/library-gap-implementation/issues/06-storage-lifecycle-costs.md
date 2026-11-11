<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LG-06: Admit bounded storage cost and lifecycle references

Status: needs-info
Type: research-and-implementation
Priority: P1
Group: R2
Blocked by: LG-01, LG-02
Baseline: master `aba252d2384f55116bbd15516a5b025951ba265e`; recheck before claiming.

## Observed gap

Storage planning needs chemistry/duration and power/energy boundaries; degradation/augmentation evidence is not a universal calibrated site law.

## Research assignment

Which DEA/PNNL resource cells directly support native battery cost, lifetime, efficiency and replacement fields for a defined chemistry/duration?

Candidate starting points, not import approval:

- https://ens.dk/technologydata
- https://www.pnnl.gov/projects/esgc-cost-performance/download-reports
- https://ora.ox.ac.uk/objects/uuid%3A03ba4b01-cfed-46d3-9b1a-7d4a7bdf6fac
- https://www.batteryarchive.org/

Apply [the source protocol](../research-protocol.md). Deliver a field/source map, exact resource/rights evidence, bounded permitted fixture or extraction recipe, independent numerical/time/identity oracle, and a GO/NEEDS INFO/NO GO record for a specified consumer. Do not mark a candidate verified from a landing page. Desk research can prepare before dependencies; implementation completion needs all listed dependencies and any ticket-specific gates.

## Native anchors and bounded scope

- `pypsa-gui/backend/services/library/europe_reference.py`
- `pypsa-gui/backend/services/library/reference_data`
- `pypsa-gui/backend/services/library/defaults_pack`
- `pypsa-gui/backend/services/finance/replacements.py`
- `pypsa-gui/backend/services/finance/lcos.py`

These are inspected anchors, not permission to rewrite whole directories. Before code, record an exact file allowlist including relevant tests, permitted new reference/fixture files and one release fragment. If a new route, item kind, engine, migration or out-of-scope path is needed, return a scope amendment for review. Check the outsourced register and open PR overlaps first.

## Implementation steps after admission and required plan gates

1. Select one chemistry/duration/reference configuration with explicit currency/money year, power/energy/EPC/connection scope, efficiency boundary, calendar/cycle exposure, replacement/augmentation and O&M assumptions. Respect PNNL current user-scoped terms.
2. Extend existing versioned reference data and native fields only where the source supports them. Do not split aggregate CapEx into MW/MWh components without evidence. New lifecycle laws remain reference-only until an existing or separately approved consumer is validated.
3. Check finance replacements against a hand cashflow and reserve/usable-energy boundary. Oxford ODbL and Battery Archive access restrictions are distinct; do not download or share restricted curves as if CC BY, and do not fit a site failure law from cells.

## Planned acceptance tests — write first

| ID | Trigger | Required observation |
|---|---|---|
| LG-06-01 | A 2 MW/8 MWh reference gives distinct power/energy costs. | Independent MW/kW and MWh/kWh calculation matches and EPC is counted once. |
| LG-06-02 | Only an aggregate installed cost is published. | No invented power/energy split or unjustified duration extrapolation. |
| LG-06-03 | A replacement occurs within a known planning horizon. | Hand-discounted replacement/salvage oracle agrees and avoids duplicate augmentation. |
| LG-06-04 | A cycle-life value has stated DoD/temperature/C-rate conditions. | Incompatible site use is flagged/reference-only, not a universal failure or degradation default. |
| LG-06-05 | Preview and explicit use under the admitted scope. | Original edition/pins and year persist; no old-project reprice or common promotion of user-scoped data. |

Regression anchors (locate renamed/equivalent tests during the current-master audit; do not invent execution):

- `test_storage_cost_assurance.py`
- `test_finance_replacements.py`
- `test_finance_basis_and_lcos.py`
- `test_library_europe_reference.py`
- `test_oj06_ambiguous_storage_cost_journey.py`

## Stop conditions and handoff

Finance plan review precedes engine edits. Detailed ageing calibration, BLAST-Lite integration and cell data ingestion are separate approved scopes, not implied by this ticket.

Follow [spec.md](../spec.md) and [HANDOFF.md](../HANDOFF.md). Keep source, numerical values, semantics, applicability and approval evidence distinct. Record unresolved fields and safe fallbacks; software test success does not establish independent domain calibration. All five acceptance IDs remain planned until executed evidence is attached.

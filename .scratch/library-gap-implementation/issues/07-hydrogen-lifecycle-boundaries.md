<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LG-07: Expand hydrogen technical, O&M and replacement references

Status: needs-info
Type: research-and-implementation
Priority: P1
Group: R2
Blocked by: LG-01, LG-02
Baseline: master `aba252d2384f55116bbd15516a5b025951ba265e`; recheck before claiming.

## Observed gap

AEC/PEM references do not cover every plant/stack/BOP/pressure/life boundary, and cost projections can mix input/output and LHV/HHV bases.

## Research assignment

Which exact DEA/H2A cases support one AEC or PEM configuration and its native electrical, production, cost and replacement fields?

Candidate starting points, not import approval:

- https://ens.dk/technologydata
- https://www.hydrogen.energy.gov/program-areas/systems-analysis/h2a-analysis
- https://www.pnnl.gov/projects/esgc-cost-performance

Apply [the source protocol](../research-protocol.md). Deliver a field/source map, exact resource/rights evidence, bounded permitted fixture or extraction recipe, independent numerical/time/identity oracle, and a GO/NEEDS INFO/NO GO record for a specified consumer. Do not mark a candidate verified from a landing page. Desk research can prepare before dependencies; implementation completion needs all listed dependencies and any ticket-specific gates.

## Native anchors and bounded scope

- `pypsa-gui/backend/services/library/europe_reference.py`
- `pypsa-gui/backend/services/library/reference_catalogue.py`
- `pypsa-gui/backend/project_templates/eh_templates.py`
- `pypsa-gui/backend/project_templates/eh_staged_hubs.py`
- `pypsa-gui/backend/services/finance/replacements.py`

These are inspected anchors, not permission to rewrite whole directories. Before code, record an exact file allowlist including relevant tests, permitted new reference/fixture files and one release fragment. If a new route, item kind, engine, migration or out-of-scope path is needed, return a scope amendment for review. Check the outsourced register and open PR overlaps first.

## Implementation steps after admission and required plan gates

1. Extract one configuration with input-MWel versus output-MWH2, LHV/HHV, stack/plant boundary, pressure, water/cooling/compression auxiliaries, fixed/variable O&M, technical/economic life and stack replacement. Record projection/vintage and original money year.
2. Map each admitted field to existing reference/asset/finance consumers. Keep unsupported auxiliaries and process constraints visible rather than automatically wiring new loads. H2A is a model case, not a current quote or replacement finance engine.
3. Add versioned read-only comparison and only source-compatible reviewed native apply. Independently calculate production, parasitic energy and replacement-cost bounds for the chosen example; do not double count compressor or BOP costs.

## Planned acceptance tests — write first

| ID | Trigger | Required observation |
|---|---|---|
| LG-07-01 | Convert stated electrolyser electricity input and H2 output. | Hand efficiency/production calculation matches on the declared LHV/HHV basis. |
| LG-07-02 | A source quotes output kW but target expects input kW. | Explicit documented conversion or refusal; no same-number copy. |
| LG-07-03 | Stack life differs from plant life and replacement cost excludes BOP. | Replacement schedule/cashflow resets only the supported component, without a second full-plant cost. |
| LG-07-04 | Compression or cooling is outside the source boundary. | Exclusion is visible; no fabricated parasitic demand. |
| LG-07-05 | A projected cost and a current measured value are compared. | Distinct scenario/edition labels and currency-year basis; no automatic template reprice. |

Regression anchors (locate renamed/equivalent tests during the current-master audit; do not invent execution):

- `test_library_europe_reference.py`
- `test_library_reference_apply.py`
- `test_finance_replacements.py`
- `test_energy_hub_templates.py`
- `test_energy_hub_template_staged.py`

## Stop conditions and handoff

No assumed industrial recipe, ammonia conversion or control model from electrolyser reference cells. Any new process or finance engine behavior needs its own plan decision.

Follow [spec.md](../spec.md) and [HANDOFF.md](../HANDOFF.md). Keep source, numerical values, semantics, applicability and approval evidence distinct. Record unresolved fields and safe fallbacks; software test success does not establish independent domain calibration. All five acceptance IDs remain planned until executed evidence is attached.

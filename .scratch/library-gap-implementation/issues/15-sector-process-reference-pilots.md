<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LG-15: Map mining, ammonia, hydrogen and DC process data to existing example consumers

Status: needs-info
Type: research-and-implementation
Priority: P2
Group: R5
Blocked by: LG-01, LG-07
Baseline: master `aba252d2384f55116bbd15516a5b025951ba265e`; recheck before claiming.

## Observed gap

Generic energy models need explicit process/product boundaries; public typical values cannot supply every site recipe or operating/control constraint.

## Research assignment

For the actual shipped H2/NH3/DC examples and future mining scope, which process inputs/outputs, specific consumption and operating constraints are native and evidenced?

Candidate starting points, not import approval:

- DEA renewable fuels/industrial-heat catalogues and exact source cells already mapped in the EU package.
- Official plant/OEM/peer-reviewed primary process evidence for selected mining and data-centre conditions; customer inputs remain org scoped.

Apply [the source protocol](../research-protocol.md). Deliver a field/source map, exact resource/rights evidence, bounded permitted fixture or extraction recipe, independent numerical/time/identity oracle, and a GO/NEEDS INFO/NO GO record for a specified consumer. Do not mark a candidate verified from a landing page. Desk research can prepare before dependencies; implementation completion needs all listed dependencies and any ticket-specific gates.

## Native anchors and bounded scope

- `pypsa-gui/backend/project_templates/eh_ammonia.py`
- `pypsa-gui/backend/project_templates/eh_templates.py`
- `pypsa-gui/backend/project_templates/eh_staged_hubs.py`
- `pypsa-gui/backend/services/library/reference_catalogue.py`

These are inspected anchors, not permission to rewrite whole directories. Before code, record an exact file allowlist including relevant tests, permitted new reference/fixture files and one release fragment. If a new route, item kind, engine, migration or out-of-scope path is needed, return a scope amendment for review. Check the outsourced register and open PR overlaps first.

## Implementation steps after admission and required plan gates

1. Produce a per-sector field-gap map covering product units/purity, input carriers, auxiliary power/heat/water, conversion yield, min-load/ramp/start-up boundaries, planned maintenance and costs. Distinguish actual implemented fields from planned process detail or dynamics.
2. Choose one source-compatible H2/NH3 reference pilot, using the current template/native reference path; keep mining/DC workload data as labelled research until a demonstrated consumer exists. Do not duplicate existing template work or invent a generic process_recipe kind.
3. Validate a small mass/energy and capacity-year balance. Binding a process to a future 3D building depends on #711 runtime and explicit existing process identity; publishing a recipe cannot create that binding or electrical topology.

## Planned acceptance tests — write first

| ID | Trigger | Required observation |
|---|---|---|
| LG-15-01 | One process makes a stated tonnes/day output from multiple carriers. | Independent unit and mass/energy balance is explicit and agrees within justified tolerances. |
| LG-15-02 | A source provides specific energy but not minimum-load or restart constraints. | Only supported energy fields are available; control limits remain unknown. |
| LG-15-03 | A staged template changes production between periods. | Period duration and capacity-year/product units remain consistent; no duplicated total. |
| LG-15-04 | Mining commodity/ore grade differs from a published case. | No automatic generic recipe or transferable performance claim. |
| LG-15-05 | Request process-to-building linkage before #711 runtime exists. | Unavailable capability is explained; no fabricated scene or physical/electrical link. |

Regression anchors (locate renamed/equivalent tests during the current-master audit; do not invent execution):

- `test_library_defaults_pack_ammonia.py`
- `test_energy_hub_templates.py`
- `test_energy_hub_template_staged.py`
- `test_eh_site_template.py`

## Stop conditions and handoff

Capacity/process engine changes need a separate reviewed plan. Public reference data does not substitute for a site flowsheet, production guarantee or control recipe.

Follow [spec.md](../spec.md) and [HANDOFF.md](../HANDOFF.md). Keep source, numerical values, semantics, applicability and approval evidence distinct. Record unresolved fields and safe fallbacks; software test success does not establish independent domain calibration. All five acceptance IDs remain planned until executed evidence is attached.

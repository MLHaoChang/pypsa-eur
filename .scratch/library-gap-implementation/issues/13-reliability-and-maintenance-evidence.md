<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LG-13: Separate supply-quality references from component outage/maintenance data

Status: needs-info
Type: research-and-implementation
Priority: P2
Group: R5
Blocked by: LG-01
Baseline: master `aba252d2384f55116bbd15516a5b025951ba265e`; recheck before claiming.

## Observed gap

Class-average outage defaults and public service indexes do not provide campus-specific failure/repair distributions or maintenance event data.

## Research assignment

What service-quality cohorts and component exposure/event/repair observations are actually available with usable terms and native semantics?

Candidate starting points, not import approval:

- https://www.eia.gov/tools/FAQs/faq.php?id=1194&t=6
- Existing EIA-861/OEB/Enedis terms records and official cohort documentation.
- Exact NERC class-default citations already in occurrence.py; customer/OEM maintenance evidence only under explicit org permission.

Apply [the source protocol](../research-protocol.md). Deliver a field/source map, exact resource/rights evidence, bounded permitted fixture or extraction recipe, independent numerical/time/identity oracle, and a GO/NEEDS INFO/NO GO record for a specified consumer. Do not mark a candidate verified from a landing page. Desk research can prepare before dependencies; implementation completion needs all listed dependencies and any ticket-specific gates.

## Native anchors and bounded scope

- `pypsa-gui/backend/services/adequacy/occurrence.py`
- `pypsa-gui/backend/services/adequacy`
- `pypsa-gui/backend/tests/test_adequacy_occurrence.py`
- `pypsa-gui/backend/tests/test_adequacy_profiled_units.py`

These are inspected anchors, not permission to rewrite whole directories. Before code, record an exact file allowlist including relevant tests, permitted new reference/fixture files and one release fragment. If a new route, item kind, engine, migration or out-of-scope path is needed, return a scope amendment for review. Check the outsourced register and open PR overlaps first.

## Implementation steps after admission and required plan gates

1. Build two explicitly separated field maps: service indexes for supply screening and asset failures/repairs for native outage models. Preserve cohort/exposure, planned/forced event definitions, censoring, age/weather, repair clock and common-cause limits.
2. First admit a bounded service-quality reference under current scope permissions. Add component overrides only when actual FOR/EFORd/MTTR semantics and admissible observations support them; otherwise retain screening defaults labelled approximate.
3. Validate a small availability/COPT oracle and VRE profile guards. Research maintenance cost/event schedules separately from forced outages and battery ageing; no rate derived merely from SAIDI/SAIFI.

## Planned acceptance tests — write first

| ID | Trigger | Required observation |
|---|---|---|
| LG-13-01 | Supply data contains SAIDI/SAIFI only. | It is display/screening reference, not silently converted into UPS/genset FOR/MTTR. |
| LG-13-02 | An outage source includes planned downtime or censored events. | Exposure and event definition are retained; incompatible target parameter use refuses. |
| LG-13-03 | A two-unit system has explicit independent failure probabilities. | Exhaustive small-state probability oracle matches native availability output. |
| LG-13-04 | A VRE trace already includes outages. | Native no-double-count guard remains effective. |
| LG-13-05 | A maintenance record is org-private or lacks cohort evidence. | It stays private/reference-only and is not promoted into a universal common failure law. |

Regression anchors (locate renamed/equivalent tests during the current-master audit; do not invent execution):

- `test_adequacy_occurrence.py`
- `test_adequacy_profiled_units.py`
- `test_adequacy_copt.py`
- `test_adequacy_time_basis.py`
- `test_adequacy_benchmarks.py`

## Stop conditions and handoff

Adequacy plan review precedes engine edits. Sparse public BESS/site failure evidence is an allowed NO GO; no synthetic component observations or implicit independent-failure claim.

Follow [spec.md](../spec.md) and [HANDOFF.md](../HANDOFF.md). Keep source, numerical values, semantics, applicability and approval evidence distinct. Record unresolved fields and safe fallbacks; software test success does not establish independent domain calibration. All five acceptance IDs remain planned until executed evidence is attached.

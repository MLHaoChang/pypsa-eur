<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LG-14: Add one bounded calibrated-model C&I demand profile set

Status: needs-info
Type: research-and-implementation
Priority: P2
Group: R5
Blocked by: LG-01
Baseline: master `aba252d2384f55116bbd15516a5b025951ba265e`; recheck before claiming.

## Observed gap

Two generic synthetic load shapes cannot represent diverse climate/end-use operating patterns. They must not be advertised as measurements.

## Research assignment

Which small ComStock building/climate/end-use subset improves a real C&I example and can be scaled without fabricating measured provenance?

Candidate starting points, not import approval:

- https://www.nlr.gov/buildings/end-use-load-profiles
- Exact ComStock release, building/cohort metadata, calibration scope and resource terms selected after native requirements mapping.

Apply [the source protocol](../research-protocol.md). Deliver a field/source map, exact resource/rights evidence, bounded permitted fixture or extraction recipe, independent numerical/time/identity oracle, and a GO/NEEDS INFO/NO GO record for a specified consumer. Do not mark a candidate verified from a landing page. Desk research can prepare before dependencies; implementation completion needs all listed dependencies and any ticket-specific gates.

## Native anchors and bounded scope

- `pypsa-gui/backend/services/profile_gen/load_archetypes.py`
- `pypsa-gui/backend/services/profile_gen/load_upload.py`
- `pypsa-gui/backend/study_library/load_profiles`
- `pypsa-gui/backend/routers/profiles.py`

These are inspected anchors, not permission to rewrite whole directories. Before code, record an exact file allowlist including relevant tests, permitted new reference/fixture files and one release fragment. If a new route, item kind, engine, migration or out-of-scope path is needed, return a scope amendment for review. Check the outsourced register and open PR overlaps first.

## Implementation steps after admission and required plan gates

1. Choose one commercial building/climate cohort and a small 15-minute end-use subset with release, calendar/weather/timezone, occupancy and energy units. Label it modelled/calibration-informed, not customer meter data; avoid national bulk ingestion.
2. Reuse existing load-profile upload/generation paths and numerical pin mechanisms after checking their present consumer contracts. Owner glossary says profiles are not generic Library items; do not add a profile item kind or confuse a generated profile with an equipment reference.
3. Normalize to user-entered annual MWh only through documented integration/scaling, preserving end-use/carrier splits and simultaneous peak behavior. Keep the original synthetic profiles and saved generated data unchanged.

## Planned acceptance tests — write first

| ID | Trigger | Required observation |
|---|---|---|
| LG-14-01 | Scale a 15-minute kW profile to a stated annual MWh. | Independent duration-weighted integral matches target without treating sum(kW) as kWh. |
| LG-14-02 | Use a mismatched weather/calendar/leap-year profile. | Explicit mapping decision or refusal; no silent dropped February/day/DST interval. |
| LG-14-03 | Select end uses in electricity and heat. | Each native carrier keeps the correct units and no aggregate double count. |
| LG-14-04 | Source includes modelled rather than measured values. | UI/export/chat retain the modelled label and cohort limits. |
| LG-14-05 | Load another climate or zero/invalid annual demand. | Compatibility and finite/nonnegative scaling checks hold; old synthetic seeds are unchanged. |

Regression anchors (locate renamed/equivalent tests during the current-master audit; do not invent execution):

- `test_profile_gen_load_family.py`
- `test_profile_gen_load_upload.py`
- `test_profiles_load_routes.py`
- `test_profile_gen_snapshot_window.py`

## Stop conditions and handoff

No customer-meter accuracy claim, new universal profile kind or wholesale dataset download. Data-driven sizing is limited to the demonstrated profile consumer.

Follow [spec.md](../spec.md) and [HANDOFF.md](../HANDOFF.md). Keep source, numerical values, semantics, applicability and approval evidence distinct. Record unresolved fields and safe fallbacks; software test success does not establish independent domain calibration. All five acceptance IDs remain planned until executed evidence is attached.

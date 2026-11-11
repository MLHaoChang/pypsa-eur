<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LG-05: Replace supported grid-equipment assumptions and resolve the H2 voltage-pair gap

Status: needs-info
Type: research-and-implementation
Priority: P1
Group: R1
Blocked by: LG-01
Baseline: master `aba252d2384f55116bbd15516a5b025951ba265e`; recheck before claiming.

## Observed gap

All 29 transformer vk values and 12 cable resistance entries are assumed; all nine STATCOM CapEx/O&M/lifetime entries are assumed. H2 MILP lacks a 220/110 kV option.

## Research assignment

Can exact technical records support the consumed voltage classes and bases, and is 220/110 kV in scope for this campus tool?

Candidate starting points, not import approval:

- Exact OEM/test/utility records for model, voltage, frequency, MVA/Mvar, impedance, losses and cable installation basis. DOE CCMS distribution efficiency data is not a blanket HV catalogue.
- Existing .scratch/campus-library-gaps/issues/01-h2-hub-220-110kv-transformer.md is canonical for the voltage gap.

Apply [the source protocol](../research-protocol.md). Deliver a field/source map, exact resource/rights evidence, bounded permitted fixture or extraction recipe, independent numerical/time/identity oracle, and a GO/NEEDS INFO/NO GO record for a specified consumer. Do not mark a candidate verified from a landing page. Desk research can prepare before dependencies; implementation completion needs all listed dependencies and any ticket-specific gates.

## Native anchors and bounded scope

- `.scratch/campus-library-gaps/issues/01-h2-hub-220-110kv-transformer.md`
- `gridspine/templates/data/campus_assets.yaml`
- `gridspine/templates/campus_assets.py`
- `gridspine/static/campus_invest.py`
- `gridspine/static/campus_milp.py`
- `tests/gridspine/test_campus_assets_reference.py`
- `tests/gridspine/test_campus_milp.py`

These are inspected anchors, not permission to rewrite whole directories. Before code, record an exact file allowlist including relevant tests, permitted new reference/fixture files and one release fragment. If a new route, item kind, engine, migration or out-of-scope path is needed, return a scope amendment for review. Check the outsourced register and open PR overlaps first.

## Implementation steps after admission and required plan gates

1. Select the most-used compatible transformer/cable subset. Build per-field source/assumption records: nameplate MVA/voltage/frequency/base, positive/zero sequence only when known, losses/load/temp, conductor and installation/ampacity conditions.
2. Obtain the engine scope/plan decision for H2 220/110 kV. Add cited compatible entries if supportable; otherwise make the MILP unresolved outcome as explicit as least-cost rather than guessing a unit. Keep this fix separate from broader data additions if its engine files overlap.
3. Validate existing schema and per-unit conversion path; replace only supported fields, preserving assumption tags on unknown technical/cost fields. Leave STATCOM economics unavailable/assumed if exact public evidence is insufficient.

## Planned acceptance tests — write first

| ID | Trigger | Required observation |
|---|---|---|
| LG-05-01 | Convert an impedance from a stated rated MVA/kV base. | Independent base-conversion oracle matches; an unknown base is refused. |
| LG-05-02 | Use 50 Hz versus 60 Hz or a cable at an incompatible installation temperature. | No silent reuse of incompatible technical ratings. |
| LG-05-03 | Run the H2 example investment selection with no compatible voltage pair. | A reviewed explicit unresolved outcome, never a substituted voltage or fabricated transformer. |
| LG-05-04 | A cited cost has no test evidence for losses/impedance. | Only cost provenance improves; technical assumptions stay flagged. |
| LG-05-05 | A compatible subset is applied and a small campus case is evaluated. | Native loading/loss behavior agrees with an independent small electrical calculation; no grid-code certification claim. |

Regression anchors (locate renamed/equivalent tests during the current-master audit; do not invent execution):

- `tests/gridspine/test_campus_assets.py`
- `tests/gridspine/test_campus_assets_reference.py`
- `tests/gridspine/test_campus_invest.py`
- `tests/gridspine/test_campus_milp.py`

## Stop conditions and handoff

Engine changes require plan review. No vendor product import with guessed electrical fields; owner scope determines H2 fix. Existing assumed records are not silently declared calibrated.

Follow [spec.md](../spec.md) and [HANDOFF.md](../HANDOFF.md). Keep source, numerical values, semantics, applicability and approval evidence distinct. Record unresolved fields and safe fallbacks; software test success does not establish independent domain calibration. All five acceptance IDs remain planned until executed evidence is attached.

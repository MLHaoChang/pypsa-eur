<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LG-02: Complete the coordinated money-year and template/study/regional repin

Status: needs-info
Type: research-and-implementation
Priority: P0
Group: R0
Blocked by: LG-01
Baseline: master `aba252d2384f55116bbd15516a5b025951ba265e`; recheck before claiming.

## Observed gap

Pack 2026-10-25 fixes labels, while templates/study/regional bases still use older pins. A standalone bump can trigger currency_year_mixed.

## Research assignment

For each adjacent asset, tariff, CO2, finance and study assumption, what source money year is correct, and does a value need conversion or only relabelling?

Candidate starting points, not import approval:

- Existing .scratch/money-year/issues/01-templates-case-year-and-repin.md is the canonical issue; this ticket is its execution expansion, not competing ownership.

Apply [the source protocol](../research-protocol.md). Deliver a field/source map, exact resource/rights evidence, bounded permitted fixture or extraction recipe, independent numerical/time/identity oracle, and a GO/NEEDS INFO/NO GO record for a specified consumer. Do not mark a candidate verified from a landing page. Desk research can prepare before dependencies; implementation completion needs all listed dependencies and any ticket-specific gates.

## Native anchors and bounded scope

- `.scratch/money-year/issues/01-templates-case-year-and-repin.md`
- `pypsa-gui/backend/project_templates/eh_templates.py`
- `pypsa-gui/backend/project_templates/eh_ammonia.py`
- `pypsa-gui/backend/project_templates/eh_site.py`
- `pypsa-gui/backend/project_templates/eh_staged_hubs.py`
- `pypsa-gui/backend/services/study/library.py`
- `pypsa-gui/backend/services/study/compile.py`
- `pypsa-gui/backend/services/adequacy/redundancy.py`
- `pypsa-gui/backend/tests/test_study_ledger_from_pack.py`

These are inspected anchors, not permission to rewrite whole directories. Before code, record an exact file allowlist including relevant tests, permitted new reference/fixture files and one release fragment. If a new route, item kind, engine, migration or out-of-scope path is needed, return a scope amendment for review. Check the outsourced register and open PR overlaps first.

## Implementation steps after admission and required plan gates

1. Produce a row-level old/new year and numeric-change table, including tariffs, finance, CO2 and redundancy pack selection. Obtain the existing issue owner/engine plan decision before edits.
2. Create new immutable regional versions on the corrected EU base, then change new-template/study case years and pins together. Remove only the now-obsolete acknowledged-bump entry. Convert adjacent values only with an explicit source/index/basis decision.
3. Retain every historical pack and saved-project pin; verify normal new template/study construction and deliberate mixed-year refusal. Document relabelling versus actual converted rows separately.

## Planned acceptance tests — write first

| ID | Trigger | Required observation |
|---|---|---|
| LG-02-01 | Load a saved project pinned to 2026-10-22. | Its values, hash and case basis remain identical. |
| LG-02-02 | Create each affected single/staged/ammonia template and guided study. | Asset, tariff, CO2 and case bases are consistent or a precise unresolved field blocks monetary use. |
| LG-02-03 | Only the study pack pin changes while tariff year is old. | Existing mixed-year refusal remains effective; no gate is disabled. |
| LG-02-04 | Compare relabel-only rows before and after migration. | Numbers remain identical; any converted row has a separate independent conversion oracle. |
| LG-02-05 | Construct a new regional example. | Correct EU base/provenance is selected; absent local monetary data never falls back to EUR. |

Regression anchors (locate renamed/equivalent tests during the current-master audit; do not invent execution):

- `test_library_defaults_pack_money_year.py`
- `test_library_defaults_pack_regional.py`
- `test_study_ledger_from_pack.py`
- `test_energy_hub_templates_pack.py`
- `test_flow_money_years.py`

## Stop conditions and handoff

No blanket relabel of tariffs/CO2/finance without evidence; no amendment to historical version files. Engine plan approval is required.

Follow [spec.md](../spec.md) and [HANDOFF.md](../HANDOFF.md). Keep source, numerical values, semantics, applicability and approval evidence distinct. Record unresolved fields and safe fallbacks; software test success does not establish independent domain calibration. All five acceptance IDs remain planned until executed evidence is attached.

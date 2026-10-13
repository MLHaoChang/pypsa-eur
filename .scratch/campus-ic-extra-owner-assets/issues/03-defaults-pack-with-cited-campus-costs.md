# Publish the cited campus reference costs: a new defaults-pack version, then the shipped library

Status: ready-for-agent
Type: task
Blocked by: none for part A (IC); part B (campus) lands with or right after part A. Coordinate with 01: the
new library is in **2025 money**, so the IC route's money handling (01, step 5) starts to matter in practice.

Read [the spec](../spec.md) for the context. This issue has **two owners**, in order: part A is IC-owned
(`services/library/*`, the pack-hash fixture, the parity test); part B is campus-owned. Neither side edits the
other's files.

## Background

Branch `feat/gridspine-campus-asset-costs` (pushed to origin, not merged) researched cited public costs for the
81 entries of the campus asset library (Swedish regulator Ei's *Normvärdeslista elnät 2024–2027*, converted from
2022 SEK to 2025 EUR at the ECB rate and the euro-area HICP; O&M shares and lifetimes from the Danish Energy
Agency's *Technology Data for Energy Transport*). Where no source fits (shunt reactors, STATCOMs, extrapolated
sizes, higher switchgear ratings), the value stays `assumed`. It also added a cost tag `literature` to the campus
loader. It could not ship the values: changing `gridspine/templates/data/campus_assets.yaml` fails the IC parity
test `pypsa-gui/backend/tests/test_library_defaults_pack_campus.py::test_the_campus_cost_fields_match_the_current_yaml`,
which compares the live YAML with pack version **2026-10-07 by name** (`NEW = "2026-10-07"`), and fixing that is
IC-owned.

Read these on that branch with `git show`, do not copy them around by hand:

```sh
B=origin/feat/gridspine-campus-asset-costs
git show $B:docs/superpowers/research/2026-10-12-campus-asset-reference-costs.md    # the note; section "Adopting these values (for the pack owner)" is the recipe
git show $B:docs/superpowers/research/2026-10-12-campus-asset-reference-costs.yaml  # the proposal: a complete, loadable library
git show $B --stat                                                                   # what else that branch changes
```

The branch also edits `gridspine/templates/campus_assets.py` (the `literature` tag), `harness/catalogue.py`,
`services/campus_electrical_service.py` and `tests/test_campus_electrical_service.py`. The last three are also
touched by `feat/gridspine-campus-ic-source` (the investment case's source, `meta.study_method`); expect a merge
of the two on the campus side, additive on both.

## Part A (IC session): pack version 2026-10-12 and a parity test that follows the YAML

1. **New pack version** `pypsa-gui/backend/services/library/defaults_pack/versions/2026-10-12/` (or the date it
   lands), copied from 2026-10-07. **2026-10-07 is never edited.**
   - `values.csv`: the 2026-10-05 rows unchanged. For each of the 81 campus entries, the three rows (`overnight`,
     `lifetime`, `fom_share`) take the proposal's values, with:
     - `currency_year = 2025` (the proposal's `price_year`);
     - `source` = the note's citation key expanded with the row it came from, e.g.
       `"Ei Normvärdeslista elnät 2024-2027 (R-NR-T-5-6), converted"`, or
       `"assumed (gridspine campus_assets.yaml)"` for an `assumed` value;
     - `source_url` = the citation key's URL (the proposal's header lists them: `[EI-NVL]`, `[DEA-TRANS]`,
       `[DEA-DIST]`, ...);
     - `illustrative = false` for a `literature` value, `true` for an `assumed` one (the pack README's own
       definition: "not taken from a cited source");
     - `note` = the proposal's note for that value.
   - `manifest.json`: `seeded_from.campus_equipment.sha256` = sha256 of the proposal file as it will be shipped
     (with its header rewritten from "proposal" to "shipped", part B), and `price_year = 2025`.
   - `pypsa-gui/backend/tests/fixtures/defaults_pack/pack_hashes.json`: the new version's hash.
   - `smoke/check_bundle.py` and `tests/test_energy_hub_class_c_authoring.py` (its fake root): the new manifest.
2. **The parity test follows the YAML, not a version name.** In `tests/test_library_defaults_pack_campus.py`,
   `test_the_campus_cost_fields_match_the_current_yaml` compares the live YAML with the **latest** pack version, or
   with the version whose manifest pins the YAML's sha256; never with `"2026-10-07"` hard-coded. Its money asserts
   (`currency_year == 2026`, `price_year == 2026`, the placeholder source string) move to the new version's values;
   the 2026-10-07 assertions stay, for 2026-10-07.
3. **Pins elsewhere.** Findings Known limit 6: U2 pins `load_defaults_pack("2026-10-05")`; check that nothing in
   production loads "latest" unpinned and that the U2 tripwire for newer packs still holds.

Acceptance (IC): both 2026-10-07 and the new version load and are pinned; every 2026-10-05 row is in the new
version unchanged; 243 campus rows, each with the proposal's value, `currency_year 2025`, a non-empty `source`, a
`source_url` for every `literature` row, `illustrative` exactly `tag == "assumed"`; the parity test passes against
the new shipped YAML and fails on a one-cent change of any campus cost; the 2026-10-07 tests still pass.

## Part B (campus session): ship the proposal, in one commit with (or right after) part A

- Copy the proposal over `gridspine/templates/data/campus_assets.yaml`, header rewritten from "proposal" to
  "shipped" (its sha256 is the one part A pins; agree the bytes first).
- Bring in the `literature` tag (`gridspine/templates/campus_assets.py`) from the branch if it has not merged.
- `tests/gridspine/test_campus_assets.py::test_no_cost_claims_to_be_anything_but_assumed`: change to the
  proposal's tag rules (a test of the shipped data).
- Texts that say every shipped cost is an assumed placeholder: `pypsa-gui/frontend/src/pages/CampusLibrarySection.tsx`,
  `pypsa-gui/frontend/src/pages/CampusInvestmentSection.tsx`, `INVESTMENT_NOTES` and `MILP_NOTES` in
  `services/campus_electrical_service.py`, and the copilot texts in `harness/catalogue.py`, e.g. "costs are
  reference values from cited public sources (tag literature) or assumed where none was found; neither is a quote".
- The pinned shipped numbers in `pypsa-gui/backend/tests/test_campus_electrical_service.py` (`upfront_per_unit ==
  2_500_000` for `TR_132_33_63`, `price_year == 2026`) and any in `tests/test_campus_electrical_ic_source.py`
  (it reads the default library through `library_with`/`scaled_capex`, and asserts `2_500_000.0`): data, not
  logic; update them to the new values.
- Every existing campus run turns stale (the library changed): expected, and the IC refuses a stale study (issue
  01), so cases are rebuilt on the new costs.
- `meta.illustrative` becomes false for `literature` costs and `meta.price_year` 2025: the IC route (01) must
  then refuse or convert for a case stated in another money year.

## Comments

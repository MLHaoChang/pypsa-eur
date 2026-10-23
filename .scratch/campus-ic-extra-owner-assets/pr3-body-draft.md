Closes campus-IC issue 03, part A (`.scratch/campus-ic-extra-owner-assets/issues/03-defaults-pack-with-cited-campus-costs.md`). Part B, which ships the library, is campus-owned and follows.

## Changes proposed in this Pull Request

### Summary

```text
services/library/defaults_pack/versions/
  2026-10-22/            unchanged (never edited)
+ 2026-10-23/            copy of 2026-10-22; only the 243 campus rows differ
    values.csv           81 entries × (overnight, lifetime, fom_share) ← docs/superpowers/research/2026-10-12-campus-asset-reference-costs.yaml
                         value, original_value, money_year 2025 (money rows), source, source_url, source_year, illustrative, note
    manifest.json        version; campus_equipment.sha256 = the shipped library (97ce51fb…), price_year 2025,
                         reference; campus_reference_costs {from 2026-10-22, why}
```

Per row:
- `source`: `Ei Normvärdeslista elnät 2024-2027 (<row codes>), converted`, or `Danish Energy Agency, Technology Data for Energy Transport v0008 (Table 13)` or `… v0006, ch. 111 (note C | notes L and Q)`, or `assumed (gridspine campus_assets.yaml)`.
- `source_url`: the citation key's URL, set on every `literature` row.
- `illustrative`: true exactly when the proposal tags the value `assumed`.
- `source_year`: Ei 2023, DEA-TRANS 2025, and DEA-DIST 2025. Version 0006 carries no year; version 0008 (Dec 2025) confirms its distribution data unchanged. An assumed value keeps 2026.

**The parity test follows the YAML** (`test_library_defaults_pack_campus.py`). It compares the live `campus_assets.yaml` with the latest version whose manifest pins the YAML's sha256 (`_version_pinning`), and fails with a "no version pins it" message when none does. It never names 2026-10-07. The money asserts read the pinned version's `currency` and `price_year`. Today the placeholders pick 2026-10-22. Once part B ships the library, 2026-10-23 is picked.

**The shipped bytes.** The manifest pins `.scratch/campus-ic-extra-owner-assets/campus_assets.shipped.yaml` (sha256 `97ce51fb…`). This is the proposal with its header rewritten from "proposal" to "shipped": lines 1 and 8–16 change, and every other byte is the proposal's. Part B copies that file over `gridspine/templates/data/campus_assets.yaml` byte for byte. Any other bytes would need this pin changed before merge, or a new version after it. `.scratch/…/make_pack_2026-10-23.py` regenerates the version byte for byte.

A new latest version moves these pins:
- `typed_units`: `LATEST = 2026-10-23` for the latest and the legacy successor. `TYPED` stays the first typed version.
- `regional`: the regional packs keep base 2026-10-22, since campus rows are never regional. The test asserts that BASE is vendored, not that it is the latest.
- `smoke/check_bundle.py` and the fake root in `test_energy_hub_class_c_authoring.py` gain the new manifest.
- The pack README.

Templates, redundancy and the IPP and ammonia packs pin 2026-10-22, so they are unchanged.

### Evidence

- New `tests/test_library_defaults_pack_campus_reference.py`, 7 tests:
  - pinned; latest after 2026-10-22; typed;
  - every non-campus row unchanged, and the campus rows keep their key, typing and place;
  - each campus row has the proposal's value and provenance;
  - by hand: TR_132_33_40 at 1 453 600 EUR from R-NR-T-5-5, and CB_33_AL95 assumed;
  - the manifest pins the shipped sha and 2025 money;
  - the new version matches the proposal, and a one-cent change fails;
  - the assumption rows carry the new stamp.
- The 25 test modules that read the defaults pack pass, apart from `test_packaging_requirements::test_openpyxl_parses_uploads_with_defusedxml_in_this_environment`. That test fails because this venv has no `defusedxml`; it is not this change.
- The campus, harness-prompt and IC campus suites: 306 passed.
- U2's bump tripwire (`test_study_ledger_from_pack`) passes.
- Ruff is clean on the changed files. The only finding is D301 in `smoke/check_bundle.py`, which was already there before this change.

mp-code-review (spec 03 part A; both axes run inline, without sub-agents): one Standards finding was fixed, the duplicated campus kind map, which the new test now imports from the campus test. Spec: every acceptance point is covered. The spec's "every 2026-10-05 row unchanged" is read as "every 2026-10-22 row other than the campus rows unchanged", because 2026-10-22 is the parent, and it carries the typed columns that 2026-10-05 lacks. Known limit 6 has been checked: U2 still pins 2026-10-05 and its bump tripwire passes.

### Merge Danger

**Door:** two-way

**Blast Radius:** defaults pack (latest version)

`load_defaults_pack()` with no version now returns 2026-10-23. Its only production reader is `reference_catalogue.pack_for_asset`, which falls back to the latest pack for an asset without a pack stamp; the EU technology rows it reads are unchanged. Nothing prices campus equipment from the pack yet.

## Checklist

**Required:**
- [x] Changes are tested locally and behave as expected.
- [x] Code and workflow changes are documented.
- [x] A release note is added as a new file under `doc/release-notes.d/` (see its `README.md`); `doc/release_notes.md` is not edited directly.
- [x] The description is human-written and any AI-generated content is marked. (AI-generated: this PR was written by Claude Code.)

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01CpAMEX2FeZ9nroh4C6b1fX

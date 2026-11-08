# 03. A `generic` pack tariff carries no currency, so the currency gate cannot check it

Status: done (#1279)
Type: task
Raised by: GS-01 session, issue 01 (GK B condition C3), 2026-11-06.
Gated: yes (money semantics; hash).

## What is missing

Issue 01 derives a tariff's currency from its jurisdiction. The defaults pack's `tou_reference_illustrative` is `jurisdiction: "generic"` (EUR in its pack meta), so its copies read with no currency and `currency_mismatch` never fires for it. A GBP project with that copied tariff is not refused. Stamping the meta's currency onto the copies would move every stored copy's hash, the stored commercial config and `_network_input_fingerprint`, so issue 01 left it out. The guided study refuses a non-EUR tariff itself (`compile.currency_year_of`, `currency_mixed`), so this is a gap only for a copy used outside it.

## What a fix would need

- A decision: stamp `currency` on new copies and accept that each new copy differs from an older one, or read an unstated currency from the copy's `pack_hash` stamp (the pack names its meta).
- Tests: a GBP project refuses the copied generic tariff; existing copies keep their hashes.

## Pointers

- `pypsa-gui/backend/services/library/defaults_pack/loader.py` (`_parse_tariffs`, `pack_tariff`, `tariff_is_unchanged`).
- `pypsa-gui/backend/models/commercial.py` (`Tariff.currency`, `jurisdiction_currency`).

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `services/library/defaults_pack/loader.py`, `models/commercial.py`.
- Blocked by: none.
- Model tier: opus.
- Gated: GK B (money semantics, hash).
- Size: M.
- Plan to GK B first: stamp-on-copy versus reading the currency from `pack_hash`; existing copies keep their hashes.

### 2026-11-08: GK A plan GO (GLE-03), with conditions

Decision: read the currency, don't stamp it. `stamped_tariff_currency` (loader.py) reads the stamped pack's meta currency, and `tariff_currency_mismatch` (binding.py) falls back to it when the tariff states none. Nothing is written, so every copy, hash and stored config keeps its bytes (the pins test is unchanged).

- GLE3-1: catch only `DefaultsPackError` (parse) and `DefaultsPackLookupError` (load and lookup). A pack that fails to load raises, so the gate is never silently off. Done: `test_a_vendored_pack_that_fails_to_load_is_not_read_as_unknown`.
- GLE3-2: a regional stamp (the regional packs ship no tariffs) and an unknown region read as None. A regional stamp asks for that regional pack. Done: `test_a_stamp_that_names_no_shipped_tariff_reads_no_currency`, `test_a_regional_stamp_reads_that_regional_pack`.
- GLE3-3: the stamped currency is stripped and upper-cased before the compare. Done: `test_a_stated_currency_wins_over_the_stamp_and_the_stamp_is_normalised`.
- GLE3-4: both callers are tested, preflight and attach (`bind_commercial` on a Library ref). Done: `test_a_gbp_project_refuses_the_copied_generic_tariff_in_preflight`, `test_a_gbp_project_refuses_a_library_ref_to_a_stamped_generic_copy`.
- GLE3-5: a stamp-read refusal says "(from its defaults-pack stamp; state the tariff's currency if you rewrote its rates)". Nothing else matches the old text (git grep).
- GLE3-6: the pack is read through `load_defaults_pack` (a deep copy). No new cache.
- GLE3-7: mutants on binding.py and loader.py are all killed. Two equivalent guards were dropped from the code.

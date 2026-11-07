# 03. A `generic` pack tariff carries no currency, so the currency gate cannot check it

Status: ready-for-agent
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

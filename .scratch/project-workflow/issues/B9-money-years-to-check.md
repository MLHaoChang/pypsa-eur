# B9. Section 5: a to-check item when asset costs mix money years

Status: done (#583, 1b1aa5cc)
Type: task
Blocked by: GS-EU P2b merged (the helper below)
Owner: whoever owns `services/flow_service.py` (W0 / W4a). GS-EU stays out of that file.
Gated: no engine change and no new data. The money-year semantics are the gatekeeper's (2026-10-21, option 1).
Size: S

## Why

A Library cost reference is applied **as-is in its own money year**, with the user's explicit confirmation. No price index is declared, so nothing is converted. A project can therefore hold asset costs in different money years. For example, 2020 EUR comes from the defaults pack and 2025 EUR from a DEA PV reference. The UX reviewer asked for that to stay visible after apply as a standing, acknowledgeable "to check" item in section 5 (Assets & sizing). Until this ticket lands, P2c shows the same sentence inline in the section-5 cost table.

## What to build

One kind-(c) item, display-only. It never blocks Run.

- **Code:** `asset_cost_money_years_mixed`.
- **Severity:** `warning`, acknowledgeable.
- **Section:** `assets` (section 5).
- **Target:** `component` and `entity` = the first asset in the newest money year; `field` = `capital_cost`.
- **When:** `services.library.reference_apply.money_years_in_use(n)` returns more than one year. It returns `{money year: [(class, asset, field), ...]}` from the asset provenance and leaves out fields with no recorded money year.
- **Message** (plain words, no internal ids): "Asset costs use different money years (2020, 2025), not adjusted for inflation."
- **Acknowledgement:** bound to the set of years. Pass `value = sorted(years)` to `_item`, so `flow_acks.json`'s value hash changes, and the warning re-raises, when a year is added or removed. It does not re-raise when another asset joins a year already listed.
- **Clears:** when only one money year remains.

## Tests

- One year (or none recorded): no item.
- Two years: one warning in `assets`, with the message listing both years and the target field `capital_cost`.
- Acknowledge, then apply another asset in an existing year: still acknowledged. Then add a third year: re-raised.
- `run_ready` is unaffected.

## Pointers

- Helper: `pypsa-gui/backend/services/library/reference_apply.py` (`money_years_in_use`, `money_disclosure`), from the GS-EU P2b PR.
- UX wording (UX reviewer, 2026-10-21): use one sentence everywhere, "2020 EUR price data, read as 2026 money: not adjusted for inflation." The section-level item uses the summary message above.

## Comments

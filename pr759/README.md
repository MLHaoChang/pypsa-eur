# PR #759 (LC-16 slice C) UX evidence, condition 7

Built app against the authenticated native backend, loopback model wire (no paid inference), oj-grid with two investment periods (2030, 2040). PR head 02c9cac.

Each case, in Expert and Guided: `before/lc16c-<case>-<mode>-before.png` (full page, card waiting for approval) and `-before-close.png` (the card); `after/lc16c-<case>-<mode>-after.png` and `-after-close.png` (the receipt, read back from the project).

- `solver`: solve strategy full → rolling, horizon 48 snapshots, with the rolling note. The horizon's old value shows "—" because it did not apply under full; the Expert receipt now says the same (fixed in 02c9cac).
- `economics`: discount rate 7 → 5 % (stored as 0.05), default lifetime 25 → 30 yr.
- `name`: network name oj-grid → Harbour grid.
- `weightings`: 2030 Years 1 → 10 yr, 2040 Years 1 → 10 yr, 2040 Objective 1 → 0.5. Re-shot at 1196c02 after UX asked for "yr" on the Years rows.
- `bulk`: "Set L1 and L2 to 500 MVA" as bulk_update_components is refused with the preview form, and lands on one multi-asset preview card (L1, L2 300 → 500 MVA), then one receipt.

"No unit" on unitless rows is the agreed UX follow-up, not this PR. The Horizon unit stays "snapshots".

`report.json` holds each card's and receipt's text. Script: `scripts/smoke-lc16c-settings.mjs` (also in the PR, with the `lc16c` journey in `composer-fixture.py`).

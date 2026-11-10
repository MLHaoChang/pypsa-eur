<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Guided forms and prompts

Status: ready-for-agent
Blocked by: 01
Type: feature
Model: Sonnet. Reviewer: UX.

Owner decision UI-MODERN-2 (10 Nov 2026): file this issue from the screenshot audit and build it. Source: findings 3, 4, 7 and 11 in `.scratch/ui-modern/audit.md`. DENSITY-1 holds: the 9–11 px text and Inter stay.

## What to build

Guided reads as one product: a section shows plain words, one way in per row, and a heading that agrees with its own text.

### Slice 1 (built): sections 3, 5 and 7, and the wording rules

- **Section 7, Commercial & parties (finding 3).**
  - Every field has a human label from one word map (`FIELD_WORDS`, `ITEM_WORDS`, `OPTION_WORDS` in `tariffModel.ts`): "Tariff ID", "Charge type", "Unit", "Measured on", "per kWh", "tax or levy". No bare `id`, `name`, `kind`, `unit` or `measured on`.
  - The six "Add … item" links are one control: a charge-type select and one "Add item" button.
  - The tables and small actions use the Guided cards' border, padding and muted heads.
  - The section is headed "complete" and its text agrees: an import tariff is optional, so the sentence says so instead of "build one". The backend state is unchanged.
- **Sections 3 and 5, assumed rows (finding 4).**
  - The Assumptions card (LC-46) is the quieter mark: no warning icon, one "Edit ›" per row, one "Keep all defaults".
  - No engineering keys in the row text: `flow_service._LEDGER_PLAIN` says the ledger rows in plain words (`p_max_pu 0`, `tau_f`, `i_max`, `k_gfm_min`, `RoCoF`, `island_constraints`, `inv_power_lifetime`, "sidecar" are gone from the message). The ledger's own `value` is unchanged, so the acknowledgement and the report's ledger appendix keep the record.
  - A section that is "not needed" lists no defaults. **Backend change:** for an off-grid template, the `assumed_*` rows filed to Connection & grid are no longer raised (the microgrid's "to check" count goes from 18 to 14). Errors and blockers are unchanged.
- **Wording rules in Guided (finding 11).**
  - The section heading separates the state from its summary with "·": "complete · 9 assets".
  - Real plurals: "1 load", "9 assets", "1 unit has no outage data".
  - A colon, not an em dash, joins clauses: the assumed hint, the verdict headlines, the run and risk-check notices, the delegate titles.

Shots: `.scratch/ui-modern/shots-05/`, `before-` and `after-`, dark and light, `dc-` and `mg-`, sections 3, 5 and 7. Script: `pypsa-gui/frontend/scripts/ui-modern-05-shots.mjs`.

### Slice 2 (open)

- **Sections that are a sentence and a link (finding 7).** Carriers, Loads, Assets and Time series each show "N assets. Anything this section still needs is listed above…" and an Expert link. Give each a card with something to do in Guided, or a list of what it holds.
- **`SiteCard.tsx` em dashes.** Five strings ("None — the site runs as an island", "… — normally open; …", "… — everything on this bus keeps power", "data missing — …", "N units — M without outage data") and the Grid connection option labels. Left out of slice 1 because PR #1335 was editing the file.
- **The microgrid's section 7.** Contracts and Finance inputs show 21 selects on one screen with unstyled underlined actions ("Add contract", "Reload the contracts", "State the finance inputs"). Group them, and give the actions the card style.
- **Assumed rows that are still engineer's prose.** "Q(U)", "the staged microgrid's blocks on the plain unit names", unit names such as `diesel_1` in the "Existing island" row.
- **Em dashes the backend builds** and Guided shows (refusals such as "A study is still running — wait for it…", the site check's notes).

## Done when

- Each slice-1 point has before and after shots in dark and light on the data centre and microgrid templates.
- Tests pin the label map, the single add control, the plurals, the plain ledger words and the not-needed rule.
- The PR merges with the UX reviewer's OK.

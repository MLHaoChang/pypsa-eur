<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Guided forms and prompts

Status: done (slice 1 #1340, slice 2 #1353)
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
  - No engineering keys in the row text: `flow_service._LEDGER_PLAIN` says the ledger rows in plain words (`p_max_pu 0`, `tau_f`, `i_max`, `k_gfm_min`, `RoCoF`, `island_constraints`, `inv_power_lifetime`, "sidecar" are gone from the message; RoCoF takes the island panel's word, "Rate of change"). The ledger's own `value` is unchanged, so the acknowledgement and the report's ledger appendix keep the record.
  - An off-grid site's unused connection asks for no defaults. **Backend change:** when section 3 is "not needed" because the site is off-grid, the open point of connection's own `assumed_*` rows (its description, and the rows on the connection link) stay in the flow but are out of scope: Guided shows one muted line, Expert lists them under "Not in scope", and they count nowhere ("to check": microgrid 18 to 14, staged microgrid 15 to 11). Any other row there, such as the staged microgrid's hospital feeder losses, is still asked for. A grid connection brings them back. Errors, blockers and run verdicts are unchanged.
- **Wording rules in Guided (finding 11).**
  - The section heading separates the state from its summary with "·": "complete · 9 assets".
  - Real plurals: "1 load", "9 assets", "1 unit has no outage data".
  - A colon, not an em dash, joins clauses: the assumed hint, the verdict headlines, the run and risk-check notices, the delegate titles.

Shots: `.scratch/ui-modern/shots-05/`, `before-` and `after-`, dark and light, `dc-` and `mg-`, sections 3, 5 and 7. Script: `pypsa-gui/frontend/scripts/ui-modern-05-shots.mjs`.

### Slice 2 (built)

- **Sections that were a sentence and a link (finding 7).** Carriers, Loads, Assets and Time series list what they hold: one row per carrier, load, asset or time series, with its kind and one figure ("Capacity: 10 MW", "Peak load: 40 MW", "Availability time series"; an extendable asset reads "sized by the study"). Each row has one "Edit ›": an asset or load opens its own editor in the drawer over the card, a time series opens the Time Series drawer on that row, a carrier (no Guided editor) opens Expert with "Back to Carriers & processes". A group longer than 8 folds to "(N more) Show all". The list is the flow's `holds`, built server-side from the same set as the heading's count; it decides no state, and the assistant's copy of the flow leaves it out.
- **The microgrid's section 7.** The Contracts and Finance inputs actions ("Add contract", "Reload the contracts", "State the finance inputs", "Remove", …) use the card's bordered small-action style, one constant shared with the tariff builder (`actionStyle.ts`). The finance inputs were already grouped in titled field sets, so no select moved.
- **Assumed rows.** "Q(U)" reads "voltage-dependent reactive power", "blocks on the plain unit names" reads "settings for each unit", and `process_uc`, `discount_rate` and `eu_de` no longer show. Unit names such as `diesel_1` stay: they are the names the section lists and the editors show.
- **Refusals.** "A study is still running: wait for it…" and "A risk check is running: wait for it…" use a colon.
- **Slice 1's surviving mutants.** Tests now cover the tag's two-part condition and the "has / have" verb.

Shots: `.scratch/ui-modern/shots-05b/`. Script: `pypsa-gui/frontend/scripts/ui-modern-05b-shots.mjs`.

### Left for a follow-up

- **`SiteCard.tsx` em dashes** (five strings and the Grid connection option labels): PR #1335 is still editing the file.
- **"synthetic illustrative data — representative week…"** in section 1: template data (`project_templates/`), which the report prints too.
- **Em dashes in backend refusals** outside the flow (`routers/results.py`, `routers/reports.py`, `services/study/runner.py`, `services/adequacy/eh_review.py`): other tests and the frontend match some of these by text.

- **Helper generators in section 5** (`__lost_*`, `__buy_*`): they are counted and listed as assets with "Edit ›" (counted before slice 2 too; the 3D scene hides them). Raised by GK B (44) in its first review of #1353.
- **Custom series outside section 6's list** (`eh_energization_mw`, `ic_export_price`, `ic_import_price` on Links, `ic_ref_price` on Buses): they are inputs of the energisation and commercial editors, and the Time Series drawer has no slot for them (it cannot open a Bus series at all), so section 6's list and count leave them out (GK B (44) F8, declined with its agreement). Section 6 in Guided does not say so yet.
- **The edit drawer's units on tonne buses**: `.scratch/ui-modern/issues/05c-edit-drawer-units-on-tonne-buses.md` (GK B (44) R6).
- **UX (17)'s two notes on 101d4826**: a parity test of the group titles against the Expert tabs' own labels (`BottomPanel.tsx` `TABS`), and "unit not known" as visible text beside a figure with no unit.

## Done when

- Each slice-1 point has before and after shots in dark and light on the data centre and microgrid templates.
- Tests pin the label map, the single add control, the plurals, the plain ledger words and the not-needed rule.
- The PR merges with the UX reviewer's OK.

## Comments

### Slice 2 plan verdicts (10 Nov 2026)

UX (16): GO with S1–S7.
- S1. A row's "Edit ›" opens its Guided home where one exists (the field drawer over the card; time-series rows through `openTimeSeriesFor`). Only where none exists does it go to Expert, with the Back pill set. The PR says which kind goes where, with a test per kind.
- S2. One link per row, "›" as U+203A.
- S3. Kind and figure words come from the owning panel's word maps, with a parity test; figures carry units; an extendable asset reads "sized by the study"; a missing figure says why.
- S4. The list has a loading state, a failed-query sentence and the empty state "Nothing here yet"; the heading count equals the rows listed.
- S5. Section 7 on the microgrid: sub-head words from a map shared with Expert Economics; no select changes meaning or order within its group; the three actions keep their names; shots in both modes.
- S6. Em dashes become colons or two sentences, option labels too. No raw key or `diesel_1`-style name reaches an assumed message.
- S7. Shots: a folded group, the Back pill, the time-series drawer from a row, the empty state, Expert Economics, the staged microgrid's section 3.
- A group longer than 8 folds to "(N more) Show all". If the PR passes the size limit, section 7 becomes its own PR.

GK B (43): GO with H1–H6.
- H1. `holds` rides the flow payload. The list and the count call one shared function (`_site_assets`); `len(holds) == asset_count` is pinned on every shipped template and on a hand-built network.
- H2. `holds` feeds no state, count, blocker, verdict, note, acknowledgement or fingerprint (test on all templates).
- H3. Raw numbers only; a missing or non-finite value is null, never 0; an extendable asset shows the size as entered, flagged `extendable`.
- H4. Profiles list attribute names only, and handle a multi-period axis and an empty frame.
- H5. The assistant's copy of the flow leaves `holds` out.
- H6. Plain words only from `_LEDGER_PLAIN`; `git grep` the old text before rewording a refusal; hand mutants of the shared set and the null rule.
- H7. The tests that kill slice 1's four surviving mutants (`flow_service.py:1224`, the tag's two-part condition, three shapes; `:565`, the has/have verb) land in slice 2's first commit, each shown red under its mutant in the PR body. GK B's OK on #1340 stands without them. GK B (44) took over from (43) on 10 Nov.

### Slice 2 review, PR #1353 (10 Nov 2026)

UX (16): OK on cd68f795, S5 accepted as built. Two follow-ups, done in the next push: group titles as the Expert tables name them ("Generators (14)"), and the loading bars stop pulsing under reduced motion.

GK B (44): NOT OK on cd68f795, F1–F8, answered in one push.
- F1. The assistant-tool test compares against the endpoint's payload minus the lists.
- F2. A size's unit comes from its bus (`process_products.bus_unit`): MW / MWh, t/h / t on a tonne bus, null where it is not known.
- F3. The inert-read test reads the session's own context and adds the investment-case digest; it is red under a list that writes.
- F4. A list stops at 200 rows (`HOLDS_CAP`) and the section carries `holds_total`; the card then says "Showing the first 200 of N. The rest are in Expert."
- F5. The template list is every `TEMPLATE_META` id plus `eh_site`.
- F6. A row says whether its value is a series' peak (`series`); a peak is the value furthest from zero, sign kept.
- F7. A text flag ("True", "1", "yes") counts as extendable; a carrier named "nan" is null, tested.
- F8. The four custom series stay out (see "Left for a follow-up").

UX (17): OK on 101d4826, with the two notes listed under "Left for a follow-up".

GK B (44): NOT OK on 101d4826, R1–R7, answered in one push.
- R1. The extendable flag goes through the engine's own rule, `gridspine.drivers.process_chain.truthy`: 1.0, "1.0" and a float column count.
- R2. The cap is pinned for loads, for a frame wider than the cap and on the route (`holds_total` beside a cut list, the heading counting the total).
- R3, R4, R7. A load's peak is tested through `_holds`; a +5 / -5 tie goes to the positive value; an unreadable bus gives no unit.
- R5. A cut list shows no per-group count ("Generators", not "Generators (2)"); the sentence under the rows carries the numbers.
- R6. A ticket, not this PR (see above).

<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Walkthrough: data-centre grid readiness, 2026-11-17

Owner decision WALK-E2E-1. Scope: the data-centre grid-readiness workflow, from the site and grid inputs to the readiness verdict and the report that carries it, plus data-to-model import onto the same project. Judged against `.scratch/datacenter-grid-readiness/spec.md` (D1–D5), `docs/agents/review-ux.md`, and the owner decisions HOLISTIC-1, EXPERT-GUIDED-1, RAW-NAMES-1, DECISION-GAPS-1, KEY-LATER-1, DENSITY-1, THEME-LIGHT-1 and SAGC-1.

How: master 9491878d, the API and Vite run locally, signed in as the dev admin, Playwright at 1440×900 in Guided and Expert, light and dark. Projects were created through the wizard and through `POST /api/projects/from_template/…` with every backup mode (Gensets, Batteries, Hybrid), IT load 24/30/36 MW and all four architectures. Every number below was checked by hand. All figures are the shipped template's generic example values. Bugs: 8, in `issues/`.

## Journey map

| User goal | Guided | Expert |
|---|---|---|
| Create a DC project | Projects › New project › From template › Data Center Energy Hub (or staged growth) › setup form: IT load, PUE at design, Backup mode, Power architecture › Create project | same wizard |
| See the readiness verdict | 3 Connection & grid › Site › "Grid readiness: N things to look at, N not yet known · N pass after battery/UPS" (expand) | 9 Readiness and run › Issues › Grid readiness |
| Fix an input a row flags | Assumptions list above the Site card: Connection ramp limit, Power oscillation limit, Import efficiency, Fault level at the PoC, each "Edit ›" to a field drawer. Rows offer only "Ask the assistant" (off without a key, KEY-LATER-1) and "Set PoC X/R ›" | row "Go to field ›"; 3 Grid connection › Connection limits |
| Pick a grid code | none (the scorecard uses the last campus study's profile, else EU RfG/DCC) | Grid connection › Grid codes (upload, review, publish); profile in Grid study settings |
| Backup mode, outage proof | creation form only | Grid study › Backup (switch and Assess) |
| Compare architectures | creation form only | Grid study › Power architecture › Compare (energy price needed when the tariff has none) |
| Control presets / design intents | none | Grid study › Intent comparison |
| P–V / Q–V, dynamic rows | 10 Results & reports › Grid plan › Run grid checks | Grid study › Run |
| The pack it writes | 10 Results & reports › Download report (.docx) | Results & reports › Reports › Generate report…; chapter "Grid readiness (screening)" |
| Import data onto the model | none for assets; section 7 "Fill values from a PDF…" for tariffs and PPAs | bottom panel asset list › "Import values from a file…" (CSV, XLSX, PDF) |

## What meets the requirements

- **Numbers.** Every scorecard figure I re-derived matches (fault level 250 and 60 MVA, IT 24–36 MW):
  - SCR: 250 / 30 = 8.333 and 60 / 30 = 2.0.
  - Energization: 40 × 0.995 + (4 × 10 − 10) = 69.8 MW, and 71.8 MW after an import raised two gensets to 12 MW.
  - Reactive range: 0.48 × 40 = 19.2 Mvar needed; offered 4 × tan(acos 0.95) = 1.315 Mvar; 0.95 pf → 13.16 Mvar under the generic profile.
  - RVC on the voltage form at X/R 4: (cos ψ·23.4 + sin ψ·2.34) / 250 = 3.178 %, and 13.24 % at 60 MVA.
  - Flicker scales with 1/S_k: 1.359 → 5.66.
  - Oscillation: 7.2 MW / 40 MW = 18 %.
  - Architecture NPV, MV UPS vs AC: −3.6 capex − 0.66 O&M − 5.68 energy = −9.93 M€. Energy leg: 375,640 MWh × €80 × the 15-year 7 % annuity factor 9.108 = 273.7 M€.
- **Guided and Expert agree** on every verdict, value and summary count. The report's chapter carries the same counts (5 red flags, 14 unknown, 17 assumed on the 60 MVA project).
- **Assumed labels**: every template limit carries "assumed" and a source sentence. Unknown never passes. "Advisory: nothing here blocks Run" is stated in both modes and in the report.
- **Grid codes**: switching between EU RfG/DCC, Generic assumed and US IEEE 2800/NERC changes limits and sources as expected (D3: no TSO-specific profile).
- **Guided connection limits** work by hand: Edit › opens the field drawer, Save writes, the Assumptions count drops from 4 to 3, and Reset names what it resets to.
- **Data-to-model import (Expert)** works end to end:
  - Each column maps to a labelled field, with a unit and a currency plus money year for costs, and a visible reason while Preview is off.
  - The preview names the cell for each value ("gens.csv › B2 (12 MW)") and leaves out an unknown asset with a reason.
  - Apply writes 5 values with Undo, and the readiness check picks them up after a save.
- **Dark and light** render the scorecard, the drawer and the compare table legibly. Chips keep their colours (THEME-LIGHT-1).

## What does not

| # | Bug | Impact |
|---|---|---|
| 01 | Backup mode "Batteries" is a dead end at the template's own defaults (refused at IT 36; never possible on staged; the Backup section won't assess it) | high |
| 02 | The staged energization refusal prints `(2027, Timestamp('2030-07-07 13:00:00'))` | medium |
| 03 | "AC (LV UPS)" offers no grid services, yet frequency support and voltage control pass (scorecard and compare) | high |
| 04 | The scorecard keeps the old verdict after an edit until the project is saved (SCR 8.3 Pass shown for a 60 MVA grid) | high |
| 05 | The SCR's inverter capacity is a hidden template constant (30 MVA); 45 MW of added battery inverters never lower it | high |
| 06 | Scorecard rows show `'ups_battery'`, `bess_new` and an em dash | low |
| 07 | The asset import's receipt and left-out rows use field keys and `'genset_9'` | low |
| 08 | A fault level saved as "Measured" is still shown as "(assumed)" | medium |

Already filed elsewhere, cited and not re-filed:

- Backup mode, architecture, IT load and PUE can be set only at creation, and Guided never reaches Backup, Power architecture or Intent comparison: #1613 issue 07 and #1654 issue 20. One extension: the wizard's Batteries hint sends Guided users to "the Backup section", which Guided doesn't have. That hint is fixed under 01.
- A DC wording in non-DC reports: #1660 issue 28.
- Raw names in general: #1618 issue 06, #1613 issue 05, #1654 issue 19 (06 and 07 here name the DC strings).

In flight, not walked: GC-07 pack read-back (#1659), GC-09 compliance workflow (#1577), GC-03 SCR/ESCR/WSCR as campus steps (#1589), GC-02 voltage control (#1584), the PSS/E import (#1652).

Not walked: the chat dock (KEY-LATER-1). P–V/Q–V margins and the dynamic rows were not walked either: "Run grid checks" on an unsolved project gets "the project is not solved; solve it and save, then draft the campus". The reliability run that would supply a solve is walked in #1660. The PDF branch of the import (DocumentValuesPicker) and section 7 "Fill values from a PDF…" were not walked: there was no datasheet fixture to upload.

## Improvements

1. **Give each Guided scorecard row its own "Edit ›"** to the input it reads (fault level, ramp limit, oscillation limit, PoC X/R). Reason: in Guided the only row action is "Ask the assistant", which is off without a key. The fix links sit in a separate Assumptions list above the card, so a user reading a Fail has to find them. Expert has "Go to field ›" on every row (EXPERT-GUIDED-1: both modes act on the same row).
2. **Show both RVC values, and say which one decides.** Reason: with X/R assumed, the shown value switches to the X/R-free bound when only the voltage form passes (owner decision AS11B-RVC-1). So a 30 MW battery site shows 7.84 % while a 36 MW gensets site shows 3.18 %, and the smaller load reads worse.
3. **Grade Warn and Fail by margin.** Reason: RVC at 13.2 % against a 3 % limit, and flicker Pst 5.7 against 0.6, read only "Warn", like a value just over the line. A Fail at, say, twice the limit tells the developer which flag to take to the operator first (the spec's "red flag").
4. **Pre-map import columns** whose header equals a field key or label (`p_nom`, `marginal_cost`), pre-select a unit when only one is offered, and pre-fill currency and money year from the project's finance inputs. Reason: a table that already uses the model's names needed 6 manual picks across ~45 options before Preview would open.
5. **Explain the compare's two headers.** "Peak within import limit" includes the firm gensets (limit 69.8 MW, not 40). "Effective PUE" for this project reads 1.32 against a stated 1.3, because it counts the import and transformer losses. Reason: both read as contradictions of what the user typed.
6. **Let Guided pick or see the grid code.** Reason: Guided shows "Screening against EU RfG…" with no way to change it, and HOLISTIC-1 asks for the same workflow in both modes. A read-only line with "change it in Expert" is the minimum.
7. **Report wording.** The readiness chapter joins clauses with "Grid readiness — …" and writes "red flag(s)". Use a colon, and the correct singular or plural (review-ux.md wording rules, which apply to text the backend builds).
8. **Keep the LOLE row's result across restarts, or say why it's gone.** "No reliability study for this project since the app was opened" reads as if a study never ran. The verdict needs a reliability run the user may already have paid for.

## Skipped and risk

- Not filed as bugs: the RVC basis switch (by decision AS11B-RVC-1) and the battery strings left out of the normal-hours energization (by design in `dc_backup.py`). Both are improvements 2 and 01's wording.
- The scorecard was walked on unsolved projects only. Rows that read solved sizes (reactive range once `bess_new` is sized, LOLE, P–V/Q–V) were checked only in their "not known yet" state. Risk: wrong numbers after a solve would not be caught here.
- Screenshots stayed in the session scratchpad; none are committed.

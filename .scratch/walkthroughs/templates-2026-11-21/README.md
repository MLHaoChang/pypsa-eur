<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Walkthrough: three templates, 2026-11-21

Owner decision WALK-CADENCE-1 (daily template rotation), day 2. Templates: `eh_ipp_hybrid` (Renewables IPP with battery), `eh_concentrator_staged` (Mining Concentrator, staged growth), `eh_ammonia_staged` (Green Ammonia Hub, staged growth). Follows `templates-2026-11-20/`; it adds what day 1 left unwalked: Expert field edits, dark theme, the assistant and the Grid study panel.

How: master `6b421b06`. `pixi` is not installed on this machine, so I started the API (`uvicorn`) and Vite from the existing Python venv and `node_modules`; no API key is set. Dev admin, Playwright at 1440×900, one long-lived browser. Each template was created through Projects › New project › From template, walked in Guided (Run, Results), then in Expert (Run LOPF, Results, field edits, Reports, investment case, Grid study). The theme was light for the IPP until the dark-theme step; the concentrator and ammonia ran in dark (the choice persisted). Shots are downscaled to 800 px in `shots/`.

**Timing caveat.** I polled every 1.5–2 s and waited a fixed 3 s after clicking Run, so each time below is an upper bound within that granularity. The concentrator's first run did not finish; its time is a lower bound.

**Machine.** 4 cores, 15 GB, HiGHS. Times on the owner's machine may differ.

## Per template

| Step | IPP | Concentrator | Ammonia |
|---|---|---|---|
| Create from template | no form; opens at once | no form; a few seconds | no form; still on Projects 13 s after the click, open by the next check (≤ 25 s) |
| Guided Run | ≤ 10.5 s ("Run the optimisation") | **not finished after 30 min**; Abort did nothing (restarted the API) | ≤ 3.3 min (about 195 s); verdict: no goal set, not certified |
| Guided figure | none on Results ("Not run yet"); cost only in the status bar | none | "Cost over the plan, 2027–2035 (discounted)" EUR 116 M |
| Expert Run LOPF | ≤ 7.5 s; EUR -4.55 M (**agrees with the status bar**) | 4,380 snapshots (I rebuilt the horizon, see below): ≤ 284 s; EUR 314 M | ≤ 32 s; EUR 188 M (**Guided 116 M**, status bar 120 M) |
| Evidence report | cannot be built: no LLM key (`template-e2e` 16) | not tried | not tried (Guided offers "Download report (.docx)"; not opened) |
| Investment case (Expert) | ≤ 3 s; IRR, NPV, payback and LCOE established (DSCR, LLCR, PLCR not), raw numbers | not run | ≤ 24 s; only pre-tax IRR 22.18 %, the rest "not established" |
| Dark theme | Results and Grid study readable | banners and Results readable | Results readable |
| Assistant | off | off | off |
| Grid study panel | data-center intents (issue 04) | same panel | same intent list |

## Expert field edits (the day-1 gap)

| Panel | IPP | Concentrator | Ammonia |
|---|---|---|---|
| Loads | no loads (generation-only): "No data" | `site_services` 3 → 30 MW in the table: saved; after a re-run Unserved energy 0 → 38.1 GWh, cost at VoLL EUR 190.6 M, total EUR 314 M → 2.84 B (also see the next row) | both loads are time series (`0 SERIES`): no static value to edit |
| Assets / Capacity Bounds | battery max 50 → 20 MW: Properties says "Customised 20 MW"; cost fell with it, but the sizing tables kept the old 32 MW (issue 01) | `pv_new_2030` max 40 → 10, run together with the load edit, so the cost jump cannot be split between them | `electrolyser_new_2030` max 40 → 12 MW: built exactly 12.0 MW; total 188 → 193 M; Run LOPF showed "1 out of date" after the edit |
| Economics | discount rate 7 → 10 %: saved, **no effect** on the battery cost or Results (issue 02) | not edited | Asset costs table read, not edited: shows 0 annualised capex for priced candidates (issue 06) |
| Grid connection | Strong → Weak or limited: Guided step 3 shows the same choice | not edited | not edited |
| Evidence report follows the edit | not checkable: no LLM key | – | – |

The 6-hourly rebuild on the concentrator: the Guided study would not finish, so in Expert › Model Horizon › Snapshot window I chose "Same year per period" at 6-hourly. That replaces the template's three periods with one repeated 2030 window, so the concentrator Expert figures are not the template's own and should not be compared with a full-resolution run. The Results cards, the 30-MW load effect and the lost-load log line all came from that reduced model.

## What worked

- Create, open, Guided run, Expert run, investment case all work on the IPP and the ammonia hub in under 3.5 min on a 4-core machine, and the IPP in seconds.
- Expert edits to Capacity Bounds and Loads reach the solve: ammonia built exactly the 12.0 MW cap, the concentrator's heavier load produced unserved energy.
- The grid-connection choice round-trips from Expert to Guided; the Expert header says "Run LOPF (1 out of date)" after an edit.
- The Guided ammonia Results page shows a "Sized design" table with built size and allowed range, and the sizes agree with Expert's Investments by asset (electrolysers 18.4 and 10.5 MW, H2 store 22.6 MWh, NH3 tank 446.9 MWh).
- Dark theme: no unreadable text or lost controls in the shots I read (IPP Results and Grid study; concentrator and ammonia Results, including the two warning banners).
- On the concentrator the "Interrupted save detected" banner appeared after I killed the API mid-write and said what it found (an orphaned `.tmp`). That was my doing, not a finding.

## Findings, by severity

New (files in `issues/`):

| # | Finding | Impact |
|---|---|---|
| 01 | After a re-run, Results tables and cards show the previous run's capacities beside updated cost cards (IPP battery 32 MW vs the 20 MW cap; ammonia Key capacities empty then stale) | high |
| 02 | Economics › Discount rate 7 → 10 % saves and does nothing to the model or Results; the investment case then flags the mismatch | high |
| 03 | Guided Connection step on the IPP gives four connection sizes (70, 50, 20, 500 MW) and flags "must stay on" on a no-load site | medium |
| 04 | Grid study offers data-center intents (UPS, diesel) on a wind/solar plant and a mine | low |
| 05 | The user's own project goes read-only ("Another user is editing") after a solve; two reproductions | medium |
| 06 | Economics › Asset costs shows "0 €/kW/yr" annualised capex for priced candidates (ammonia) | medium |
| 07 | IPP "Total system cost -€4.52 M" has no stated basis beside the developer Net (+4.14 M) | medium |
| 08 | "Checked figures are off on this installation" replaces LCOE and Curtailment cards with no way forward | low |

(Numbered in file order, not by severity.)

Seen again, added to the existing issue (no duplicate; these were not filed again):

- `template-e2e` 12: the concentrator's Guided run was still going at 30 min, Abort did nothing, the Solve Queue did not list it; also the 6-hourly Expert horizon that does solve.
- `template-e2e` 25: Expert header "Idle" during the Guided study (concentrator); read-only banner cross-reference.
- `template-e2e` 27: IPP Guided Results "Not run yet … Go to Goal and run the study" right after "The optimisation finished" (the run button itself is now fixed).
- `template-e2e` 16: Reports › Generate report needs an LLM key ("No usable LLM profile"); TE2E-16-PLAIN-1 is not on this master.
- `template-e2e` 18: ammonia Guided EUR 116 M vs Expert Summary EUR 188 M vs status bar 120 M.
- `template-e2e` 01, 06, 05: location note on all three; ammonia no goal; raw names in the IPP Connection step (`eh_sk_mva`, `eh_poc`), the ammonia Sized design and investment-case reasons.
- `templates-2026-11-20` 06, 07, 01 (merged folder): ammonia investment case mostly "not established"; raw unitless NPV on the IPP; LCOE card wording; "Next: Goal" on the IPP.

Open PRs #1676, #1700 and #1729 own other walkthrough folders; I appended to none of their issue files.

## Observed, not filed

- A sidebar "Saved 1mo ago" appeared once on the IPP a minute after a save; I did not see it again.
- Closing the browser page mid-run marks the queue entry "Aborted by user" (my first IPP run, closed by my script). That is probably intended.

## Not walked, and what to trust

- **The assistant is off.** The chat panel reads "Add an Anthropic API key to talk to me" and every Guided step shows "Set up the assistant". I did not enter a key or work around it, so no chat, "Fix with the assistant" or "Let the assistant do this" was walked, and no assistant-written report prose.
- **Evidence report vs field edits.** Not checkable without a key (see `template-e2e` 16). I did not open the Guided "Download report (.docx)" on ammonia.
- **Concentrator Guided figures.** The Guided run never finished, so Guided vs Expert agreement is untested there, and the Expert figures come from the reduced 6-hourly, one-year-per-period model.
- **Edits not done.** Economics (concentrator, ammonia: read only), Grid connection (concentrator, ammonia), Assets-palette additions, per-period bounds, and tariff or contract edits under Commercial & parties. The concentrator's load and PV-cap edits were made together.
- **Timings** are upper bounds from polling (above). The first concentrator run is a lower bound of 30 minutes.
- Nothing here was repeated on a second run, so "seen once" findings (01, 05 on the concentrator) may depend on timing.

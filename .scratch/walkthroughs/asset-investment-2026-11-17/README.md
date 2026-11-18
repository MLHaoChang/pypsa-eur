<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Asset investment walkthrough (2026-11-17)

This walkthrough covers sizing, adequacy, HOMER parity, and campus assets and library. It was commissioned under WALK-E2E-1.

The walk ran on master `9491878d` with a local API (127.0.0.1:8000) and Vite (127.0.0.1:5173). It signed in as the dev admin and drove the app with Playwright in Guided and Expert, in light and dark theme. It used the shipped templates **Island Microgrid** and **Data Center Energy Hub** (defaults). The chat dock was not walked: no `ANTHROPIC_API_KEY` (KEY-LATER-1).

The decisions it judges against:

- **WALK-E2E-1**: walk each workflow end to end.
- **EXPERT-GUIDED-1**: Expert never shows less than Guided.
- **RAW-NAMES-1**: plain words, never engine names.
- **HOLISTIC-1**: one source per number across the tool.
- **DECISION-GAPS-1**.
- The asset-sizing rows: AS-PROTO-1, AS-BUILD-1, AS-AUTONOMY-1, AS-NOUPS-1.
- **HOMER-GAPS-1**.
- The `docs/agents/review-ux.md` bar.

The coordinator's docs PR had not landed the WALK-E2E-1, HOLISTIC-1, EXPERT-GUIDED-1, RAW-NAMES-1 and DECISION-GAPS-1 rows during the walk. They are cited from the commission and from `.scratch/expert-catalogue/spec.md`.

**Result: 8 bugs filed** (`issues/01`–`08`): 4 high, 2 medium, 2 low.

## Journey map

| # | User goal | Guided | Expert | API |
|---|---|---|---|---|
| J1 | Size the design for an intent and see the sized assets | 5 Assets & sizing lists candidates as "sized by the study"; 9 Run study; 10 Results | 8 Grid study › Intent comparison (A–E, Backup autonomy, PQ inputs) › Compare intents; Results › Capacity expansion | `POST /results/eh_study`; `POST /projects/{name}/intent-comparison` |
| J2 | Set a reliability goal (LOLE), get a verdict and capacity credit | 8 Scenarios & optimisation › Goal card; 9 Run study; 10 Verdict and Reliability tile; Show the engineering detail | Results › Reliability › Adequacy › Sequential Monte Carlo (+ ELCC picker), planning loops, Energy Hub reference design | `/flow/reliability_goal`, `/results/mc`, `/results/mc/elcc_candidates` |
| J3 | Off-grid minimum renewable fraction and operating reserve | No entry before a run. Reached only from the Issues card after an infeasible run | Solver settings › Off-grid constraints; Results › Capacity expansion › Operating reserve | `/results/operating_reserve` |
| J4 | Supply architectures, system-type map, grid-extension break-even | None | None (03d has a route and a chat tool only) | `…/supply-architectures`, `…/grid-extension-break-even` |
| J5 | Annual load growth | None | Model Horizon › per-period load multipliers typed by hand | n/a |
| J6 | Campus equipment into the investment case | 10 Investment tile "Not run yet" | Results › Commercial & finance › Investment case; Grid study › Campus investment | `/results/investment_case`, `/campus-electrical/{name}/owner-assets` |
| J7 | Campus library, including the 220/110 kV units | None | Grid connection › Equipment library (raw YAML) | `/campus-electrical/{name}/library` |

## What meets the requirements

- **J2, Guided run.** Island Microgrid runs from 9 in 20 s and lands on a plain verdict with a yearly cost and a biggest risk. Dark theme renders the Results page cleanly.
- **FMEA arithmetic checks out by hand.**
  - Diesel occurrences: 0.05 × 8,760 / 48 h = 9.125/yr (shown 9.13).
  - Diesel criticality: 60.42 MWh × €5,000/MWh = €302,100 (shown €302,114).
  - Feeder criticality: 0.01 unavailability × 13,140 MWh × €5,000/MWh = €657,000.
- **J2, Expert Monte Carlo.** It runs, reports LOLE and EUE with 95 % CIs, and compares the engines side by side. ELCC runs on the picked assets and states that credits are marginal and don't sum.
- **J1, intent A.** The form states the AS-AUTONOMY-1 rule in words: "backup energy = critical load × hours … The project default is 4 h". Intent D is offered (AS-NOUPS-1).
- **J7.** The shipped library holds the 220/110 kV units with the OPTCL-based losses and their sources (CLG03-SOURCE-2, #1500).
- **Assumptions.** Each Guided step lists its template assumptions with "(assumed)" and an Edit link.

## What does not

| Issue | Severity | Gist |
|---|---|---|
| [01](issues/01-goal-card-says-no-goal-after-certified-run.md) | high | The Goal card says "No goal yet" while the verdict is "under the 3 h/yr goal". Expert MC gives no verdict for 4.59 h/yr |
| [02](issues/02-certified-lole-ignores-critical-feeder-outages.md) | high | Certified 0.0 h/yr leaves out the hospital's only feeder (hand check ≈ 88 h/yr), which the same page ranks as the biggest risk |
| [03](issues/03-sized-design-never-shown.md) | high | The study sizes `diesel_new` and `bess_new`, but no screen in either mode shows the MW |
| [04](issues/04-improve-card-overstates-feeder-share.md) | medium | Improve says the feeder is "100% of the outage risk" (hand check 42 %) and suggests a remedy that can't fix a feeder |
| [05](issues/05-intent-comparison-refuses-on-dc-template.md) | high | The intent comparison refuses on the data-centre template ("no AS-05 PQ tags"), and no screen can set those tags |
| [06](issues/06-intent-comparison-ignores-project-values.md) | medium | The intent comparison asks again for the ramp, envelope, Pst and PoC the project holds; critical load shows 10 decimals |
| [07](issues/07-elcc-nameplate-column-shows-peak-output.md) | low | The ELCC "Nameplate" column shows wind's 4.85 MW peak, not its 6 MW, so the 25 % credit has an unstated base |
| [08](issues/08-mc-draws-line-says-500-run-uses-2000.md) | low | The MC panel says "MC draws 500"; the run used n=2000 |

**Already filed elsewhere (cited, not re-filed).** The keyed walkthrough audit (#1618, `.scratch/ux-walkthrough-2026-11/issues/`) already covers these:

- **01**: Guided says Certified while Expert Results reads "Not run yet". Seen again here on Island: Expert › Reliability header "Not run yet", status bar "Obj: —".
- **02**: generation-only template.
- **05**: no verdict without a goal on staged growth.
- **06**: raw names on screens.

The walk found more raw names in this group's screens, for #1618's issue 06:

- Reliability › Adequacy: `reserve_margin in solver settings (0.15 = 15% of peak)`, `p_max_pu = 0`, `import_model=islanded, import_firmness=planning_limit_only`, `eh_role`, `P19 template`, `P9 thin slice`, `ReferenceDesignReport`, and the pipeline keys `target: ok`, `dtc: skipped`.
- MC table: `vre`, `storage_unit`, `deterministic_scenario`, `analytic_convolution`, `hours_per_year`.
- Grid study: `ups_bridged`, `gfl_pq`, `gfm_vsm`, `E4`, and "AS-05 PQ tags".

**In flight, not walked:**

- Homer 01d, reserve-holding units (#1576).
- Homer 03b, system-type map (#1617).
- Homer 04a, load growth as % per year (#1604).
- eh_site investment case at the report stage (#1624).
- Guided results headline (#1611).

**Not fully walked:**

- **J6.** The investment case stopped at "The investment case needs the site connection. Set it in Connection & grid ›". The template suggests `grid_import` but does not save it. The campus MILP (minutes) was not run.
- **J3.** Not run to an infeasible case.

## Improvements

1. **Offer the cheaper battery duration the study already found.** The Island study's lever table:

   | Battery duration | Cost | Goal |
   |---|---|---|
   | 4 h (the design) | €3.5 m | meets |
   | 24 h | €3.1 m | meets |
   | 72 h | €3.1 m | meets |

   Neither the verdict nor Improve mentions this. Reason: a 4 h design costs €0.4 m/yr more than a 24 h one that also meets the goal. That is the decision this group exists for (DECISION-GAPS-1).
2. **Give Guided an entry for the off-grid renewable fraction and the operating reserve.** Put it on the Goal card's "More settings" for an island site. Reason: HOMER-GAPS-1 built these for off-grid sites. In Guided today they are only reachable after an infeasible run, from the Issues card, and the assistant route needs a key (KEY-LATER-1).
3. **Rename "Island break-even" and surface the grid-extension break-even (03d).** On Island Microgrid the assumption row "Island break-even: not applicable" is about pricing an island capability. A HOMER user reads it as the grid-extension break-even, which has a route but no screen. Reason: one name per concept. The break-even distance (km*) is the island user's first question.
4. **Say which fleet each reliability number is for.** Guided certifies the sized design at 0.0 h/yr. Expert Monte Carlo on the same project samples the unsized as-built fleet and returns 4.59 h/yr, with no note. Reason: two numbers for "my reliability" that differ by design need a fleet line ("as built" / "with the study's new units").
5. **Show the equipment library as a table, not a 70 KB YAML textarea.** Reason: the library is "what the investment step may buy, and what it costs". Comparing the 220/110 kV units' costs and sources needs rows, not YAML.
6. **Make the FMEA ΔEUE column one quantity.** For the feeder it is the conditional annual loss (13,140 MWh). For the diesels it is the expected loss (60.42 MWh). Reason: the column ranks by both, and the reader can't tell.
7. **Save the template's suggested point of connection on create.** Reason: the investment case refuses until the user presses Save site connection on a value the template already suggests.
8. **Match template card copy to the form.** The Data Center card says "50 MW hyperscale campus". The form defaults to 36 MW IT at PUE 1.3, about 47 MW. Reason: the first number a user reads should match the model.

## Skipped and risk

- The chat dock (KEY-LATER-1) and the in-flight PRs above were not walked.
- The campus MILP, the planning loops and the margin loop were not run, because each takes minutes.
- J5 (load growth) waits on #1604.
- Risk: issue 02's root cause is read from code (`hub_fleet_scope`). A builder should confirm that no other sampler covers internal Links before changing the certification.

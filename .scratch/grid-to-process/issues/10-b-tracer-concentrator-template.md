# B tracer: `eh_concentrator_staged`

Status: ready-for-agent (triage 2026-10-28: still blocked by 09, and by 09's measurement being within budget)
Type: task
Blocked by: 08, 09
Gated: no (template data)
Model tier: opus
Size: M

Read [the design](../design.md) §3.B first.

## What to build

The template on the hourly `year` time base (D4), with every number from design §3.B and its label in
the ledger:

- the flowsheet, with the mine as a fixed ore Generator;
- the crusher, the coarse-ore stockpile, 2 (then 3) mill lines, flotation, the concentrate shed,
  the 21-day shipments and `__lost_concentrate`;
- the connection: a 2027 `non_firm_dynamic` envelope, then firm in 2030 and 2035;
- PV and BESS candidates per period;
- a PoC fault level of 150 MVA.

The commercial and finance blocks follow the generic site's industrial preset.

The concentrate has no buy-in, so only `__lost_concentrate` applies.

## Acceptance

Design §3.B, "Acceptance checks (B)", except:
- the investment-case check, which belongs to ticket 11;
- the motor-start check, which belongs to ticket 12.

## Triage (2026-10-28)

**Verified on master (41ade3be):** not built; no `eh_concentrator_staged` anywhere. 08 is done; 09 is not.

**Model tier raised to opus.** The template has to solve on a 26,280-snapshot year with linearised UC and pass the 1e-6 mass balances. Debugging an infeasible or slow year LP is engine work, not data entry.

### Agent brief

**Category:** enhancement
**Summary:** the `eh_concentrator_staged` template on the `year` time base, with design §3.B's numbers ledgered.

**Desired behaviour:**
- A new staged template `eh_concentrator_staged`, built like `eh_ammonia_staged`, with the same template registration, ledger and sidecars. It contains the flowsheet and numbers of design §3.B (Flowsheet, Numbers), each number labelled in the ledger as `demo` or `sourced`.
- It uses `stage_network(time_base="year")` from 09.
- The commercial and finance blocks follow the generic site's industrial preset.
- The assistant's `create_project_from_template` lists `eh_concentrator_staged`, and the assistant inventory is regenerated (owner G2P-14-NOW, 2026-10-28: moved here from issue 14).

**Acceptance:**
- [ ] `create_project_from_template` with `eh_concentrator_staged` creates the project, and the assistant inventory check passes.
- [ ] Design §3.B "Acceptance checks (B)": the year solve within 09's budget, the mass balance, the shed carrying across shipments with the week build refused, non-firm curtailment, and restart. Two exceptions: the investment-case check (11) and the motor-start check (12).

**Out of scope:** the IC blocker (11); motor fields (12); any change to `stage_network` beyond calling it.

**Likely files:** new `pypsa-gui/backend/project_templates/eh_concentrator.py` and `pypsa-gui/backend/project_templates/eh_concentrator_staged/`, `pypsa-gui/backend/project_templates/eh_templates.py`, `pypsa-gui/backend/routers/projects.py`, `pypsa-gui/backend/services/flow_scope.py`, `pypsa-gui/backend/harness/catalogue.py`, new tests under `pypsa-gui/backend/tests/`.
**Blocked by:** 09, and 09's measurement within the ≤ 30 min budget. If 09 reports over budget, this goes back to the owner.
**Model:** Opus.

## Notes from 09 (2026-10-28)

- Set `delivery_interval_h = 504` on the shed; the validator then refuses the week build of the same network
  (`inventory_cadence_needs_year`, naming the shed).
- The binary UC check (`services/solver/process_uc.py`, `CHECK_WINDOW = 168`) re-solves the first 168 snapshots of
  each period. On the year base that is the first week of January (from 2030-01-01), so it holds no shipment of a
  cycle loaded out at hours 480–503; say so in the template's ledger, or choose the year's start.
- The campus island report (`services/campus_electrical_service`, `campus_island_report.py:259-264`) lists every
  failing hourly E1 row uncapped; on 26,280 hours that table can run to thousands of rows.

## Comments

- 2026-10-28, vessel size (GK A plan verdict, condition 1). The design's 25 kt per vessel does not close the mass balance: at 1,000 t/h of ore and a mass pull of 0.0189, the plant makes 18.9 t/h of concentrate, or 9.5 kt in a 21-day cycle, so 25 kt is about 2.6 times one cycle's production. The template makes each vessel the year's nameplate concentrate over its vessels: 8,760 h // 504 h = 17 vessels a year, so 7.8 / 9.7 / 11.7 kt in 2027 / 2030 / 2035 (15.1 / 18.9 / 22.6 t/h × 8,760 h ÷ 17). Ledgered as `demo`, with the arithmetic.
- 2026-10-28, finding (GK A plan verdict, condition 5). Lost concentrate in 2027, the year whose connection is non-firm (two 150-h winter events at 15 MW of 25 MW):
  - every 2027 candidate off (PV, BESS, the pile extension): 221 t lost;
  - the same with a 20 MW × 4 h BESS installed: 69 t lost, so a BESS lowers it;
  - every candidate on at the pack's price: the plan builds PV to its 40 MW cap, no BESS and no pile extension, and loses about 0 t.

  At pack price, a 4-h BESS does not pay against 150-h events, and PV carries the curtailment. The tests pin all three. Prices were not tuned to force a BESS build.
- 2026-10-29, the mine as a ceiling (GK A plan verdict). The mine is an ore Generator whose fixed schedule (800 / 1,000 / 1,200 t/h) is an upper bound, not a must-run feed. Ore the plant cannot take in an hour is not mined; the process view reports it per period as `unused_supply` on `ore_rom`, and the lost concentrate shows at the next vessel. `test_2027_with_every_candidate_off_loses_concentrate_and_a_bess_lowers_it` pins the unmined ore to the schedule less the mine's dispatch. The frontend does not show `unused_supply` yet.
- 2026-10-29, the shed is fixed (GK A plan verdict). The concentrate shed is 40 kt and not extendable, although the design marks it "(extendable)". The largest vessel, in 2035, is about 11.7 kt (1,200 t/h × 0.0189 × 8,760 h / 17), so the shed holds more than three vessels and an extension would never be built. The ledger's "concentrate shed" row says so.
- 2026-10-29, GK A (4) `GK NOT OK @4b2b7c16` (PR #869 comment 6485998916). Items 1, 2, 4 and 5 met, and the end-pin and load-out parts of 3. To land:
  - (a) the undo's `links_t.p_min_pu` restore is tested: `test_the_undo_puts_back_a_minimum_load_series` (it fails when the undo drops the series);
  - (b) the mine-as-ceiling and fixed-shed records above;
  - (c) `test_an_abort_during_the_parallel_check_leaves_no_live_worker` (it fails without the terminate), and the budget run prints peak memory with the check's workers in it (`RUSAGE_SELF` and `RUSAGE_CHILDREN`);
  - item 7, the year-base budget, waits on the owner (options A/B/C, through the coordinator).
- 2026-10-29, owner decisions (rows in PR #893). G2P-10-BUDGET-1 = A: the year-base budget is 45 min (`BUDGET_S = 45 * 60`); the ledger's solver row records 2,346 s in all, the check 54 s on 3 workers, peak 4.62 GB on 4 cores. G2P-10-SUGGEST-1 = D: `eh_critical` on the `site` bus, `process_load: true` in the metadata, and the suggester tests skip flagged templates; the suggester gap is issue 19.
- 2026-10-29, follow-up (plan speed, owner decision G2P-10-BUDGET-1). The year-base plan took 2,346 s (about 39 min) of its 45 on HiGHS IPM. To bring it back under 30: try a HiPO build of HiGHS, or a commercial solver where the user has one, and re-measure with `PYPSA_GUI_YEAR_BUDGET_LIVE=1`. Not built here.

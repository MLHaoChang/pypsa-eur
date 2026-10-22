# Retune `eh_ammonia_staged` so the 2027 Haber-Bosch loop cycles again at honest electrolyser prices

Status: ready-for-agent
Type: task
Blocked by: none (PR #549 merged 2026-10-22)
Found in: GS-2b (typed cost units, PR #549). Owner decision AMM-RETUNE (`docs/agents/owner-decisions.md`): the
electrolyser price stays honest; the old price survives only as a labelled test fixture.

Gated: engine gatekeeper plan review first if the retune touches investment or UC engine code. A template-data change
(demand, sizes, a lever) needs no engine change.

## Problem

Electrolysers in the staged H2 hub and the ammonia hub were priced per MW of electrical output (x efficiency 0.68);
the row is per MW of electrical input. #549 fixed it: `electrolyser_new_{2027,2030,2035}` overnight cost
1,282,481 -> 1,886,002 EUR/MW (fom 51,299 -> 75,440 EUR/MW/y). The H2-hub demo NPV moved +14.6 M -> -2.65 M EUR
(test, tol 1 %).

In `eh_ammonia_staged` the honest price changes the 2027 plan: no `electrolyser_new_2027` is built (0 MW) and the
Haber-Bosch loop is off all 168 h of the 2027 binary window. Its binary-check cost is 41.2 M EUR against 6.9 M
linearised (disclosed in the template's ledger). The template used to show the loop cycling in 2027 (2027 delivers
50 t/d, below the 64 t/d minimum load, so the loop has to stop and restart); that story, and the restart-time rule it
exercised, now run only on a variant that pins the old price.

## What to build

Retune the template's data so that, at the honest electrolyser price, the 2027 loop is built and cycles: choose the
lever (delivery target, sizing, a 2027 price or subsidy row with a stated source, storage or import limits) with the
owner's design intent from `.scratch/grid-to-process/` (design §3.A) in mind, and state which lever moved and why in
the ledger notes (code and `eh_template.json`). Do not move the electrolyser price back, and do not loosen a
tolerance.

## Tests first (red before green)

1. `tests/test_eh_ammonia_staged.py::test_at_the_honest_electrolysis_price_the_2027_loop_stays_off` pins today's
   behaviour (no 2027 electrolyser, loop off 168 h, 41.2 M vs 6.9 M); it fails once the retune lands. Replace it with
   a test that the 2027 binary window has stops and runs, with the restart-time rule met.
2. `test_in_the_binary_window_every_stop_lasts_the_restart_time` passes on the template as shipped, without
   `_electrolysis_at_the_pre_gs2b_price`; then delete that fixture and `_PRE_GS2B_ELECTROLYSER` (or keep a labelled
   fixture only if another test still needs the old price).
3. Re-measure the template's ledger numbers (binary-check cost, linearised cost, the gap per period) and update the
   notes in code and `eh_template.json`; `test_the_check_reports_a_finite_gap_in_every_period` stays as strict as it
   is.
4. The H2 hub (`eh_h2_hub_staged`) and every other `eh_*` template keep their prices
   (`test_every_template_and_redundancy_asset_keeps_its_price_but_the_electrolysers`).

## Pointers

`pypsa-gui/backend/project_templates/eh_ammonia_staged/` (`eh_template.json`, the builder), `eh_staged_hubs.py`;
`tests/test_eh_ammonia_staged.py`; `tests/test_library_defaults_pack_ammonia.py`;
`services/library/defaults_pack/README.md`. Facts: the #549 body.

## Not in scope

Changing the electrolyser price, the pack, or the pricing rules; the H2 hub's NPV.

## Comments

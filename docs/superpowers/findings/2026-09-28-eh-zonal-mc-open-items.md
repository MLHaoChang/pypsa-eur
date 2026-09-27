# EH zonal MC — the four open items closed: grid storage, several grid areas, two-area COPT screening, common-mode import outages

**Date:** 2026-09-28  
**Branch:** `claude/eh-zonal-mc-import-outages` (continues from `d4a30d1`)  
**Plan:** [`2026-09-28-eh-zonal-mc-open-items.md`](../plans/2026-09-28-eh-zonal-mc-open-items.md)  
**Closes:** "Still open" in [`2026-09-27-eh-zonal-mc-import-outages.md`](2026-09-27-eh-zonal-mc-import-outages.md)

Each work package was built TDD (red → green), then reviewed by an independent reviewer agent against its diff. The review findings and what was done about them are recorded under each package. After WP5 an end-to-end QA and integration run closes the note.

## WP1 — grid-side storage dispatch

**What changed.**
- `mc_zonal.ZonalInputs` now holds `areas: (GridArea, …)`. `simulate_zonal_blocks` dispatches grid stores under a pinned, non-anticipative policy, per hour and per draw:
  1. Grid stores discharge against the grid's own deficit first.
  2. The remaining surplus is offered through the Link.
  3. The hub's stores dispatch against what is left.
  4. Grid stores then give remote support, bounded by the Link headroom.
  5. Grid stores charge only from surplus that was not offered to the hub.
- `_discharge_only` / `_charge_only` are `mc._dispatch`'s two passes with a per-draw remaining-rating bound `p_rem` (S, draws), so the two discharges in one hour share one rating. `mc._dispatch` indexes the rating per store, not per draw, so it could not be reused.
- `grid_area.storage` / `storage_dispatched` disclose the stores.

**TDD.** Red: `AttributeError: single_area`, `TypeError: … 'grid_storage_enabled'`. Green: `test_energy_hub_zonal_storage.py`. `tests/zonal_oracle.py` is a frozen copy of the 2026-09-27 kernel and serves as the bit-identity oracle: a grid without storage, or with `grid_storage_enabled=False`, returns exactly the oracle's arrays.

**Review findings (independent reviewer, commit `546f9c8`) and resolution.**

| # | Finding | Severity | Resolution |
|---|---|---|---|
| R1.1 | Bit-identity, the five-step policy, units / delivery ratio, period re-initialisation, runtime guards (ratio 0, efficiency floor, `grid=None`) | no bug | — |
| R1.2 | `test_grid_storage_never_charges_…` passed vacuously. The Link had q > 0, so the battery charged in Link-outage hours (3 100 MWh logged); a mutant charging from all surplus still passed | test gap | Rewritten: a firm Link with headroom and a hub that is short. It asserts `charge_mwh == 0`, and that the full battery does support in the same scenario. A diagnostics `trace` was added to `simulate_zonal_blocks` (per-area own / support / charge MWh; it never changes the result). The mutant is now killed. |
| R1.3 | Step 1 (grid-first) was not pinned; the "bridges the grid's own shortfall" acceptance item was not met | test gap | `test_grid_storage_serves_the_grid_own_deficit_first`: `own_mwh > 0`, and hub EUE ≤ the no-storage EUE draw by draw. The skip-step-1 mutant is now killed. |
| R1.4 | The shared per-hour rating (`p_rem`) was not pinned | test gap | A unit test of `_discharge_only` twice in one hour, plus `test_a_store_at_its_rating_for_the_grid_gives_no_remote_support` (the battery spends its 60 MW on the grid, so support must be 0). The reset-rating mutant is now killed. |
| R1.5 | A grid area on a different horizon than the hub would be silently wrong | risk | `_AreaState` raises `ValueError` when the residual length or periods differ. Test added. |
| R1.6 | Offered-but-unused power is never stored (a conservative consequence of step 5) and should be stated | design note | Stated in the `grid_area.note`. |
| R1.7 | `storage_dispatched` was true for stores rated 0 MW in every period | nit | Now filtered on `p_nom_mw > 0` and a non-zero series. Test added. |

All three reviewer mutants were re-run against the new tests and each fails at least one test.

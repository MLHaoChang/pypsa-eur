<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# I2b: the islanding-step rows credit a genset's full headroom, the event rows its load step

Status: done
Model: Opus
Type: inconsistency (documented, not fixed)
Found by: session_01TP6mmWWALLE8q4y73ypwoq, building the seamless event rows (issue 02), 2026-10-16

## What

`services/solver/island.py` `_seamless_rows`: (b↑), which the islanding step
(E1) rows use, credits an online genset at its full headroom,
u·p_max_pu·η·p_nom − η·p. The event rows (E2, E3, E5) credit the same genset
at min(that, load_step_max_pct·P_n). That is what gridspine's I6 gives inside
its window (frequency.py:632, `step_mw`), with its ramp after the step not
credited. So at the islanding step a spinning genset counts for more than I6
gives it.

The toy `test_a_spinning_genset_adds_headroom_and_burns_its_minimum_load`
shows the gap: a 6 MW spinning genset against a 10 MW step needs 4 MW of BESS
under E1's rows and 7 MW under E2's.

## Why it is not fixed now

No shipped template has a spinning genset at a seamless island. The staged
microgrid's diesels are not `spinning`, and its tie is open, so E1's step is
about 0 (worst E1 RoCoF 0.008 Hz/s at k=3). Changing E1's credit would move
the I2b toy pins and any site with spinning gensets, so it needs its own plan
review.

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role ready-for-agent

Was: `needs-triage`. Credit an online genset in the E1 rows at min(headroom, load_step_max_pct x P_n), as the event rows and gridspine I6 already do. Files: `services/solver/island.py` (`_seamless_rows`), the I2b toy pins in the solver tests. Blockers: none; the ticket asks for its own plan review first, so the plan goes to GK A before code. Reviewer: GK A. No shipped template is affected.

### 2026-11-06: plan GO, GK A (14)

GO with the cut coefficient included. Conditions: the I7 headroom_up coefficient of a spinning genset is η·min(load_step_max_pct/100, max p_max_pu − p_min_pu), from one helper shared with the infeasibility hint's `_genset_credit_max`; TDD on the toy pin; a `test_island_cuts` case where the step cap binds (coefficient and `added_mw`); every moved pin listed old → new in the PR body; evidence that no shipped template moves; the per-unit reported `headroom_up_mw` and the event rows' behaviour stay unchanged, and x_g enters only rows it loosens; the "Known gap" text dropped; a release-note fragment.

### 2026-11-06: fixed (GS-04), Status done

`_seamless_rows` builds the per-genset credit x_g ≤ min((b↑) share, load_step_max_pct·u·P_n) on every solve, and Island-headroom-up (E1) uses it too. The toy now needs 5.2 MW of BESS under E1 alone: the genset runs at its 1.8 MW minimum, so the import step is 8.2 MW and the credit is 3 MW. The event rows still need 7 MW. `_cut_coefficients` credits a spinning extendable genset at `_genset_credit_per_mw`, the helper `_genset_credit_max` now uses too. No shipped template's `island_config.json` has `spinning` or `linearised_uc`, so none moves.

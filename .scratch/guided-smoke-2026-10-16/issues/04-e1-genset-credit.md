<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# I2b: the islanding-step rows credit a genset's full headroom, the event rows its load step

Status: needs-triage
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

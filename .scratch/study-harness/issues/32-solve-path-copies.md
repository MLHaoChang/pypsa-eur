<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 32 — Solve-path copies and in-place fills (SP-1)

Status: done (#1144)
Type: bug
Gated: yes (GK B)
Model tier: opus
Owner decisions: SOLVE-PKG-1, FILL-11-7, USE-2
Parent: [30-solve-transient-identity.md](30-solve-transient-identity.md), [31-statistics-fill-private-copy.md](31-statistics-fill-private-copy.md)

Four follow-ups from BUG-1 (#1133) and B6 (#1139) in the solve path, measured first:

1. `hold_solve_inputs`'s network copy: needed, and cheap. Kept.
2. `run_simulation`'s lasting normalisations ran before the snapshot, so an unlocked reader could
   hash a mix the network never had. Now held first, then refreshed after the normalisations.
3. The solve's in-place cost fill (`services/solver/assumptions.py`): the LP is built from the
   network it is given, so it stays. Kept.
4. `periodized_cost_view` with a solver model attached: the model is shared by reference. Kept.

Found while answering S6: the AC-PF stage (after a LOPF in `run_simulation`, and its own route)
fixes dispatch into `p_set` and sets controls in place after the LOPF's hold was released. Now
held for the stage.

## Acceptance criteria

- [x] A reader between two lasting normalisations hashes the pre-solve inputs (red on master).
- [x] `refresh()` publishes one finished copy with one swap; a racing reader sees the old or the new snapshot.
- [x] `refresh()` is a no-op after release, after replacement, and on a nested hold.
- [x] A normalisation that raises leaves no snapshot.
- [x] A reader mid AC-PF stage hashes the post-solve inputs.
- [x] Solve results equal master's (fixture recorded on 4e637711f).
- [x] `test_statistics_fill_private_copy`, `test_assumptions_revert_on_raise` and the issue 30 tests stay green and unedited.

## Comments

GK B (24) plan ruling, GO WITH CHANGES (2026-11-05). Items 1, 3 and 4 are argued closed, with
the measurements in the PR body. The existing pins stay green and unedited. Item 2: early hold
plus refresh, not the waiting-reader alternative.
- S1. Red test first: a reader inside a wrapped `_normalise_dynamic_indexes`, between the
  cleared transformer type and the stale dispatch fix, equals the pre-solve identity.
- S2. Atomic publish: the copy is finished, then one reference swap under the hold's lock. The
  pin is at the swap itself; a reader racing `refresh()` gets the old or the new fingerprint.
- S3. Identity check under the slot's lock: no-op after release or after a newer hold.
- S4. Release on raise: a raise in `_sanitise_transformer_types` leaves the registry empty.
- S5. Nested holds: a nested refresh never replaces the outer snapshot; the outer refresh
  still works after a nested hold came and went.
- S6. Name every runner that bypasses `run_simulation`, list the `hold_solve_inputs` call sites.
- S7. Before/after fixture: objective, optimal capacities, dispatch sums recorded on 4e637711f.

Provenance graph 04b (`.scratch/provenance-graph/issues/04-runs-and-results-freshness.md`). It builds on 04a (#1464) and follows GK B (53)'s plan GO of 2026-11-13: R1 (a), W1–W5, and the client condition. UX (20) chose the wording, option (a). The Guided follow-up is issue 12.

## Changes proposed in this Pull Request

Backend (GK B):
- `results:reserves` -computed_in-> `run:reserves` is pinned on the reserve products and floors digest the solve recorded. It is also `derived_from` `input:reserve_weights` (the hours and period weights). The verdict is `runs.reserves_verdict`.
- `reserve_products.solved()` reads the record, the frame, the current digest and the weights key in one read (`runs.locked_read`). That read uses the edit-counter and network bracket, and takes the mutation lock only when it is free, never waiting for it (04a's F2 rule). The verdict maps back to the same flags as today.
- The two callers (`results/reserves.py`, `results/billing.py`) pass the digest as a callable. A digest that cannot be read is therefore stale, where today it raised.
- R1 (a): reserves is not a run kind of the flow (`KINDS`, `freshness_by_kind`), and the `runs.py` docstring says so. The reserves card keeps its own "Out of date" state.

Client (UX):
- `useUncheckedRunKinds` reads the `uncompared` kinds the Run requests. An unchecked kind is never counted as current.
- The Run button reads "(2 out of date · 1 unchecked)": one parenthesis, each part shown only when it applies, the unchecked part muted (`opacity-75`), and no ›.
- The tooltip adds "<Kinds> results came with the project and were not checked against its inputs. Run to check them."

### W1: the flags, before / now

| Row | Today | Now |
|---|---|---|
| untouched (string digest or callable) | frame, `[]` | frame, `[]` |
| inputs-only edit, weights unchanged (W2) | `[not_solved, inputs_changed]` | same |
| reweighting only, digest unchanged (W2) | `[not_solved, inputs_changed]` | same |
| no digest passed | weights compared only | same |
| no record / another axis | `[not_solved]` | same |
| record without `weights` (older shape, W4) | `[not_solved, inputs_changed]` (None ≠ key) | same |
| recorded None against a current None (V2) | n/a: a current digest was always a hex string, or not passed (and so not compared) | `[not_solved, inputs_changed]` |
| current digest or weights key unreadable (W4) | *raise* | `[not_solved, inputs_changed]` |
| an edit during the read twice, or a network swap mid-read (W3) | n/a | `[not_solved, inputs_changed]` |

`test_reserve_products` :764/:812/:827 are unchanged and pass. `test_both_callers_flag_a_stale_solve_as_today` drives `billing.reserve_lines` and `reserve_view` on a stale case.

W3 changed with 04a's F2. The reserves read no longer has a lock-wait fallback, because no read waits for the lock. A held lock reads the commitments as they stand (`test_a_held_lock_never_stalls_or_stales_the_read`). An edit landing during the read is read again once (`test_an_edit_during_the_read_is_read_again_then_unreadable`). An edit right after the read is stale on the next read.

W5: the reserves verdict is not cached.

### Checks

- `tests/test_provenance_graph_reserves.py`: 17 tests. The neighbouring reserve, billing, value-flow and DC-backup suites pass (205). `AppHeader.runGate` vitest has 7 tests, including unchecked-only and both, and tsc is clean. `scripts/coord/prepush.sh` passes.
- Mutants against 04a: `reserve_products.py`: 9 killed, 0 survived; `runs.py`: 14 killed, 0 survived.
- Shots (Expert, with a mocked flow) are in `.scratch/provenance-graph/shots-04b/`: out of date only, unchecked only, and both. UX (20) checked them at 48c2e1a9. Guided hides the header Run, so the Guided shots show no count; issue 12 tracks this.

## Checklist

**Required:**
- [x] Changes are tested locally and behave as expected.
- [x] Code and workflow changes are documented.
- [x] A release note is added as a new file under `doc/release-notes.d/` (see its `README.md`); `doc/release_notes.md` is not edited directly.
- [x] The description is human-written and any AI-generated content is marked. (AI-generated: written by Claude Code.)

Skipped: the Guided Results card's unchecked notice (issue 12, UX follow-up). Risk: Guided users see no "unchecked" mark until issue 12 lands; today they see none either.

Session: session_01G7qCumghRZMeJ12hfk8vfU

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01G7qCumghRZMeJ12hfk8vfU

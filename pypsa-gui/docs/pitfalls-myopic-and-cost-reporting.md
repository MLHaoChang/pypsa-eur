# Pitfalls: Myopic Solves and Cost Reporting

These three findings came out of the 2026-08-05 myopic-foresight examination.
They previously lived only as rows in `CLAUDE.md`, which is gitignored (see
`.gitignore:89`) and exists only in the local working copy — a parallel
session that resets this worktree would discard them along with the rest of
that file. They are moved here, verbatim, so they survive.

The measured numbers below (−42.9%, +22.2%, 977 MW, 47 / 1 756 / 5 183 MWh,
977 / +195 / +234 MW, 47 / 56 / 68 MWh, etc.) are all from the same 3-period
test system used during that examination, not from production data.

For the full investigation behind these findings, see:
- `docs/superpowers/findings/2026-08-03-compare-tab-correctness.md`
- `docs/superpowers/findings/2026-08-05-myopic-foresight-e2e.md`

---

## Summing the per-period LP objectives to get a myopic horizon cost

**Symptom:** Adding up each myopic iteration's per-period LP objective to
report a single "horizon cost" produces a number that is wrong in both
directions depending on config — there is no correction factor that fixes it,
because the sign of the error flips with `lf_aggregate_future`.

**Cause:** `n.objective_constant` is **identically zero under
`multi_investment_periods=True`** — PyPSA's `define_objective` builds the
multi-invest constant but the `terms.append(...)` that consumes it sits
inside the single-period `else` branch (verify: flat network → 10 000, same
shape multi-invest → 0). Capacity frozen by an earlier myopic period is
`p_nom_extendable=False`, PyPSA charges CAPEX for extendables only, and the
constant that should carry the rest is that zero. So `sum(variable +
constant)` charges each asset's CAPEX ONCE, in its build period, never for
the rest of its service life: measured −42.9% on a 3-period system, and
**+22.2% the other way** with `lf_aggregate_future=True` (the lookahead
window's future-period OPEX is counted once in the lookahead and again when
that period is solved). No correction factor works — the sign depends on
config. Three surfaces reported three different numbers for one solve before
this (solve log −75.5%, status bar −42.9%, Economics correct);
`test_cost_totals_contract.py` now pins them together.

**Rule:** Report `services/cost_totals.py::horizon_system_cost(n, cfg)`
instead, the same statistics basis Economics and Compare use; it takes
`n`/`cfg` explicitly because the solve queue prices a background project
outside `solving_context(ctx)`, where `get_cost_breakdown()` would price the
foreground one.

---

## Myopic freezing capacity at the FIRST period, silently

**Symptom:** A myopic run reports `optimal` even though the fleet never
grows to meet rising demand after the first period — growing demand is
instead absorbed by unserved energy, with no warning.

**Cause:** `_freeze_period_capacities` freezes every extendable asset ACTIVE
in the period, and an asset left at the default `build_year = 0` is active
in every period — so iteration 1 freezes it and no later period can ever add
to it. Measured with +44% demand growth: gas froze at 977 MW and unserved
ran 47 → 1 756 → 5 183 MWh, versus 977/+195/+234 MW and 47 → 56 → 68 MWh with
per-period vintage bounds. This is not a flaw in the freeze itself — PyPSA
has one capacity variable per asset, so vintages are the ONLY way to expand
in more than one period, and that path works correctly. The defect was the
silence.

**Rule:** `validate_for_run` now emits `myopic_capacity_locked_after_first_period`
(warning, not error, because "decide the fleet once then operate it" is a
legitimate intent) — if you see fleet size static across periods on a myopic
run, check for this warning before assuming the solve is broken.

---

## `_myopic_period_objectives` left on the network after a non-myopic re-solve

**Symptom:** Re-solving the SAME network full-horizon (non-myopic) after a
prior myopic run still gets reported through the myopic path — the Summary
line, `/results/objective_decomposition`, and `_compute_run_objective` all
behave as though the run were myopic when it was not.

**Cause:** `_myopic_period_objectives` is the marker every downstream
consumer keys off to answer "did this run go myopic?", and only the myopic
driver wrote it. Nothing cleared it on a subsequent non-myopic run, so the
previous run's marker stayed in place and the full run was reported through
the myopic path.

**Rule:** `run_simulation` now clears the marker on the non-myopic branch.
Any future "which strategy ran?" marker needs the same treatment — set (or
clear) it on every branch, not just the one it describes.

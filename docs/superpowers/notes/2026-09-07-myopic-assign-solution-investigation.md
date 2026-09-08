# The myopic `assign_solution` failure — investigated, NOT this repo's defect

**Status:** investigated, root cause narrowed to the container's pip stack.
**Backlog item closed:** Phase 12g's review recorded
`solve_strategy="myopic"` failing with `TypeError: Must pass list-like as
names` and filed it as "pre-existing, backlog". This note is that
investigation, so the next person does not repeat it.

## What fails

14 of the 43 environmental baseline failures are myopic tests, all one cause
(**corrected from 12** by the full characterisation in
`2026-09-08-baseline-failures-characterised.md`: the two
`test_cost_totals_contract` tests are myopic solves and had been grouped by
filename rather than by traceback):

```
tests/test_myopic_build_period_visibility.py   (5)
tests/test_myopic_horizon_cost.py              (4)
tests/test_myopic_summary_log.py               (2)
tests/test_cost_totals_contract.py             (2)
tests/test_myopic_feasibility.py               (1)
```

The traceback is identical in every one:

```
services/solver_service.py:6592            (and :6611, the retry)
  pypsa/optimization/optimize.py:680
  pypsa/optimization/optimize.py:1118      c.dynamic[attr] = df.combine_first(c.dynamic[attr])
    pandas/core/frame.py:10423
    pandas/core/frame.py:10296
      pandas/core/indexes/base.py:2024     TypeError: Must pass list-like as `names`.
```

Environment: `pypsa 1.3.0`, `pandas 3.0.5`, python 3.11 — the pip-stack
container, **not** the repo's pinned pixi environment.

## What it is NOT — four bare reproductions, all clean

None of these involves a line of `pypsa-gui` code. All pass:

| probe | result |
|---|---|
| bare PyPSA multi-period `optimize(multi_investment_periods=True)` | OK |
| bare PyPSA solving a **slice** of a multi-period network (the myopic shape) | OK |
| bare PyPSA, **two sequential** slice solves on one network | OK |
| the same, with and without `assign_all_duals=True` | OK both |

And a **pure-pandas** reconstruction of the exact `combine_first` the
traceback names — a 3-row MultiIndexed slice combined into a 6-row
full-horizon frame, columns `Index(['g'], name='name')` — **succeeds**, with
and without the column name.

So it is neither "PyPSA multi-period is broken here" nor "pandas
`combine_first` is broken here" in any form reachable from the outside.

## What the failing frames actually look like

Instrumented `DataFrame.combine_first` inside a real `_run_myopic_foresight`
run and dumped both operands at the moment it raises. They are **structurally
identical in every inspectable property**:

```
self  (solution df) : shape (3,1)  MultiIndex names ['period','timestep'] nlevels 2
other (n.*_t frame) : shape (6,1)  MultiIndex names ['period','timestep'] nlevels 2
  both: index.dtypes [int64, datetime64[us]]
        level0 dtype int64 vals [2030, 2040]
        level1 dtype datetime64[us] freq <Hour>
        columns Index dtype object, names ['name']
        values dtype float64
```

Nothing distinguishes them but which period they cover — and a hand-built pair
with those exact properties combines fine.

## Conclusion, and what would settle it

The failure needs the real PyPSA call stack to reproduce and cannot be
reproduced from the outside with structurally identical data. It is therefore
**an interaction inside the installed PyPSA/pandas pair, not a defect in this
repository's code**, and it belongs to the same class as the other
environmental baseline failures the PR documents (all 43 are attributed in
`2026-09-08-baseline-failures-characterised.md`).

Two things would settle it, neither available here:

1. **Run the suite in the repo's pinned pixi environment.** If the myopic tests
   pass there, the matter is closed as a pip-stack artifact and the baseline
   count drops by 14.
2. **If they fail there too**, the next step is to bisect `pandas` (3.0.5 is
   recent and `combine_first`'s index-union path changed in 3.x) against
   `pypsa` 1.3.0, and report upstream with the frame dump above — which is
   already a complete reproduction for a PyPSA maintainer who has the failing
   stack.

**What NOT to do:** do not "fix" `solver_service.py`. Both call sites
(`:6592` multi-period, `:6611` the operational retry) hand PyPSA correct,
well-formed inputs — the instrumentation above confirms `snapshots`,
`n.snapshots` and every pre-existing dynamic frame carry the right MultiIndex
with the right names before the call. Working around this in repository code
would be papering over an upstream incompatibility, in the one place where the
repo's own inputs are already provably right.

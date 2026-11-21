# The solver settings route accepts any float discount rate

Status: needs-triage
Type: bug
Found in: TCU 11 (#1628), GK B (64) review.
Owner area: app backend (GK B reviews).
Parent: `11-bundle-import-and-experiment-copy-cost-guard.md`.

## What

TCU 11 added `discount_rate_problem(value)` (`services/solver/periodized_costs.py`), the rule for a discount rate
the annuity can take: no bool or non-number, finite, above -1, and `_annuity(r, RATE_RULE_LIFETIME)` finite. The
bundle import applies it. The solver settings PUT (`/api/simulation/solver_config`) checks only `float`, so a rate
of -2 set there reaches the same `ZeroDivisionError` in the re-derive and the solve.

## Acceptance

- The PUT applies `discount_rate_problem` and refuses a bad rate with a typed 422 naming the rate, before anything
  is stored; a valid rate is stored as today.
- Tested for -2, -1, NaN, inf, a bool and a string, plus a valid edge (0.0).

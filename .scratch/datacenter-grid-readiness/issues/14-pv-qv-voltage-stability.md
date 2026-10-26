<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# P–V and Q–V voltage-stability margins at the PoC (screening)

Status: done (PR #714)
Type: task
Blocked by: none (09 is the owner's priority, not a dependency)
Gated: **yes** (gridspine)
Model tier: opus
Size: M
Owner decision: SAGC-1 (G7a), 2026-10-23

**Confidentiality.** Cite only the public Saudi Arabian Grid Code (SAGC, May 2026) and generic engineering. Never name a client, its network, its MW figures or any scope-of-work text, here, in code, tests, fixtures or PR bodies.

## Why

A large new load needs a static voltage-stability margin. The app has none.

## What to build

1. **P–V:** step the PoC load (at a set power factor) in pandapower until the power flow no longer
   converges; report the nose point and the margin from the planned load.
2. **Q–V:** step reactive injection at the PoC; report the Q margin (V at Q = 0) and the slope.
3. Run intact and over the worst contingencies from the contingency set.

Labelled **screening**: the binding answer is the RMS study (SAGC-1 keeps dynamics in PSS/E).

## Done when

- Results list nose-point MW, margin in MW and %, and the Q–V margin per case.
- A test on a two-bus fixture matches the analytic nose point within tolerance.

## Triage (gatekeeper, 2026-10-23)

**Status `ready-for-agent`, gated, size M.** There is no P–V or Q–V code in gridspine today, so
this is a new module (`static/voltage_stability.py`). `Blocked by: 09` is dropped: pandapower
already takes the line charging from `net.f_hz`.

Rulings:

- **Nose point:** step the PoC load at the set power factor and treat the last converged step as
  the bracket. Then bisect between the last converged and the first non-converged load to a
  tolerance, so the result does not depend on the step size. Report the nose MW, and the margin in
  MW and as a % of the planned load.
  - Non-convergence can come from the solver, not from collapse. Retry each failing point once
    with a flat start and more iterations before calling it the nose. The row records the retry.
- **Q–V:** sweep a fictitious PV source at the PoC over its voltage set-point and read Q. Report
  the minimum-Q point (the Q margin, the distance from Q = 0) and dQ/dV at the operating point.
- **Contingencies:** run intact plus the worst N-1 cases from the existing `contingency_set`, read
  only. "Worst" means the lowest PoC voltage in the existing N-1 results, capped at a
  user-settable number of cases (default 5).
- **The test** uses a two-bus fixture: a stiff source V behind reactance X feeding a load at power
  factor angle φ (lagging). Its analytic nose is P_max = V² · cos φ / (2X · (1 + sin φ)), and the
  computed nose must match it within 1 %. Rows are labelled "screening; binding answer is the RMS study".

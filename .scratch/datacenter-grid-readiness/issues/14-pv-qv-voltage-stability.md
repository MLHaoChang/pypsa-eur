<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# P–V and Q–V voltage-stability margins at the PoC (screening)

Status: needs-triage
Type: task
Blocked by: 09
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

<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Planning-criteria contingencies: emergency ratings, N-M-1 / N-1-1, bus-section loss, angle limit

Status: needs-triage
Type: task
Blocked by: 09
Gated: **yes** (gridspine)
Model tier: opus
Size: M
Owner decision: SAGC-1 (G6), 2026-10-23

**Confidentiality.** Cite only the public Saudi Arabian Grid Code (SAGC, May 2026) and generic engineering. Never name a client, its network, its MW figures or any scope-of-work text, here, in code, tests, fixtures or PR bodies.

## Why

SAGC-type planning criteria go past plain N-1 on normal ratings. The contingency set
(`gridspine/static/contingency_set.py`) has N-1 branches and units and N-2 pairs only.

## What to build

1. **Per-element emergency ratings** and a two-stage check: within the emergency rating right after
   the outage; within the normal rating after taps, shunts and redispatch act.
2. **N-M-1** (maintenance outage, then a fault) and **N-1-1** (fault, system re-adjusted, second
   fault) enumeration in contingency_set.py, with the same keys as today's rows.
3. **Bus-section loss** as a contingency.
4. **Angle-limit check** across a branch (or between named buses) after each contingency.

## Done when

- The new contingency kinds are enumerated with stable ids and counted in the ledger.
- A fixture shows a branch that passes stage 1 on its emergency rating and fails stage 2 if no
  remedial action exists.
- Run time on case39 stays reported; N-M-1 / N-1-1 sets can be capped by the user.

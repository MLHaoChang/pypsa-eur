<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Planning-criteria contingencies: emergency ratings, N-M-1 / N-1-1, bus-section loss, angle limit

Status: done (#702, #708; follow-ups 16–18 in #709)
Type: task
Blocked by: none (09 is the owner's priority, not a dependency)
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

## Triage (gatekeeper, 2026-10-23)

**Status `ready-for-agent`, gated, size M. Ship it as two PRs:** (a) emergency ratings with the
two-stage check, plus bus-section loss; (b) N-M-1 / N-1-1 enumeration plus the angle-limit check.
Nothing here touches frequency, so `Blocked by: 09` is dropped.

Rulings:

- **Emergency rating is optional per element.** When it is absent, stage 1 uses the normal rating,
  which is conservative, and the row says "no emergency rating given".
- **Stage 2 ("after remedial action") means taps and switched shunts on automatic control, then
  re-solve.** Reuse the tap logic in `static/campus_taps.py` where it applies.
  - The transmission contingency path has no redispatch model today. Redispatch stays out of this
    ticket, and stage-2 rows say "no redispatch modelled".
  - This still meets the acceptance: the fixture fails stage 2 because no remedial action clears
    it.
  - A redispatch step is a follow-up if the owner wants it.
- **New kinds keep the existing ledger columns** (`contingency_id`, `kind`, `element_ids`,
  `order`). Their ids are stable and lexically ordered, as N-2 pairs are today.
  - Kinds: `n_m_1` with id `"{maint}|{fault}"`, `n_1_1` with id `"{first}>{second}"`, and
    `bus_section`.
  - N-M-1 and N-1-1 sets are capped by a user setting. The run reports the count it skipped,
    never silently.
- **The angle-limit check is per branch or named bus pair.** Its limit comes from the planning
  criteria or profile, and no limit set gives verdict "unknown".
- **Keep case39's run time reported**, as the acceptance asks. 11 and 14 read this module, so the
  existing `n1` / `n2` functions keep their signatures.

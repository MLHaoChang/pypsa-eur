<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Per-unit reactive capability curve and voltage-control deadband (G9)

Status: needs-triage
Type: task
Blocked by: 10 (10a, the profile schema)
Gated: **yes** (gridspine; UX reviewer if the profile UI changes)
Model tier: opus
Size: S–M (to be confirmed at triage)
Owner decisions: SAGC-1 (G9), D3-KEEP, 2026-10-23

**Confidentiality.** Cite only the public Saudi Arabian Grid Code (SAGC, May 2026) and generic engineering. Never name a client, its network, its MW figures or any scope-of-work text, here, in code, tests, fixtures or PR bodies.

## Why

Issue 10's triage moved G9 out of 10a: "if small" was not the implementer's call, and 10a was
already the larger half. 10a adds a P-dependent Q band at the PoC (`q_capability_pq`), asymmetric
power-factor limits and dynamic rows. Two SAGC-type requirement shapes are still missing:

- a reactive capability **per generating or storage unit**, as a curve in Q/P_rated against
  P/P_rated (and possibly against voltage), not one fraction at rated power. Today
  `q_range_ibr` is one value at rated active power (IEEE 2800).
- a **voltage-control deadband**, e.g. ±0.5 % of nominal voltage, inside which a unit in voltage
  control does not change its reactive output.

## What to build (to be confirmed at triage)

1. **Schema:** an optional per-unit capability curve (points `(p_pu, q_min_pu, q_max_pu)` on the
   unit's rating, reusing 10a's point validation), and an optional `voltage_deadband_pct` limit,
   each with clause and source. No default value; unset means "no limit set" (D3-KEEP).
2. **Check:** each unit's stated capability (template or library data) against the curve, at the
   operating points the study solves. Where the data lacks a curve, the verdict is "unknown",
   never pass.
3. **Deadband:** recorded and reported. Whether the campus study's reactive dispatch should honour
   it (static) or whether it only goes to the RMS study list is a triage question.
4. **Profile UI** shows and confirms the new limits like the others.

## Open questions for triage

- Is the curve also a function of voltage (a P-Q-V surface), or P-Q only for the first cut?
- Which unit data carries the units' own capability curves today, if any (the templates' `pf_rated`
  only gives one point)?
- Is the deadband a check, or a reported requirement like the dynamic rows?

## Done when

- A blank profile can hold the curve and the deadband, and the profile UI shows them.
- A unit with no capability data reports "unknown", never pass.

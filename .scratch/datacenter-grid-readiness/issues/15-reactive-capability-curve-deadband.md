<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Per-unit reactive capability curve and voltage-control deadband (G9)

Status: done
Type: task
Blocked by: none (10a merged in #691, 60934d14)
Gated: **yes** (gridspine; UX reviewer for the profile UI)
Model tier: opus
Size: M
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

## Triage (gatekeeper, 2026-10-26)

**Status `ready-for-agent`, gated, size M, opus.** 10a has merged (#691), so `q_capability_pq`,
its point validation (`_check_q_capability`) and `q_band_at` already exist in
`gridspine/templates/grid_codes.py`. No unit data carries a tabulated capability curve today. The
campus units have `p_mw` and `s_mva`, and `campus_reactive.ibr_q_capability` already screens
sqrt(S² − P²) against `q_range_ibr` at rated P.

Answers to the open questions:

- **P–Q only** for the first cut. A P–Q–V surface needs a public SAGC clause that states one. If
  the implementer finds one, it becomes a new issue, not scope added here.
- **Unit capability today** is the inverter circle sqrt(S² − P²) from `s_mva` and `p_mw`. The
  islanding `pf_rated` adds no curve. A unit without `s_mva` or `p_mw` reports `unknown`, never
  `pass`.
- **The deadband is a reported requirement**, like `dynamic_requirements` and `v_max_30min`. The
  static campus reactive dispatch has no voltage-control loop, so nothing can pass or fail it.

Rulings:

- **Schema:** a new single limit `q_capability_unit` with the same `points` shape as
  `q_capability_pq` (`p_pu, q_min_pu, q_max_pu` on the unit's rating), validated by the same
  `_check_q_capability`, plus `voltage_deadband_pct` (`{value, clause, source}`, value > 0 and
  finite). Both are optional with no default (D3-KEEP), and both go into `_SINGLE_LIMITS` and
  `PROFILE_KEYS`, so `confirm_limit` and extraction cover them with no new path.
- **Check:** extend `campus_reactive` next to `ibr_q_capability`, without rewriting it. For each
  IBR unit, at the active-power points the study solves (at least rated P and the curve's own
  breakpoints within 0–1 pu), compare the circle's available Q against `q_band_at(curve, p_pu)` on
  both sides. Rows get the same `reported`/`pass`/`fail` vocabulary and a `basis` label. When
  `q_range_ibr` and the curve are both set, each is checked and reported separately, never merged.
- **Deadband:** `reported_requirements` emits one `reported` row (applies_to `unit`). No study
  reads the value.
- **UI:** `CampusGridCodeSection` shows and confirms both like the other single limits. The UX
  reviewer reviews it.
- **Tests:** a curve that a unit's circle fails at part load but passes at rated P (proves the
  curve is not one point); no `s_mva` gives `unknown`; validation refuses non-monotone `p_pu` or
  `q_min > q_max`; a blank profile holds both keys and round-trips through load and confirm.
- SAGC-1: the clause numbers cited come from the public SAGC only, and fixture numbers are generic.

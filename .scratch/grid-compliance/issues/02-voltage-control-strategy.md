<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-02 Voltage-control strategy in the load flow and PoC P–Q across voltage

Status: ready-for-agent
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: none
Gated: **yes** (GK A, gridspine)
Model tier: opus
Size: M
Owner decision: GC-SCOPE-1, GC-TEXT-1 (2026-11-17)
TRGC: 12 (and the steady-state inputs of 06/07)
Likely files: `gridspine/static/campus_reactive.py`, `gridspine/static/campus_flow.py`, new `gridspine/static/voltage_control.py`, `gridspine/templates/grid_codes.py` (shared with GC-07: GC-02 adds only voltage-control keys), `gridspine/drivers/campus_study.py` (shared; own step function), `pypsa-gui/backend/services/campus_electrical_service.py` (shared; own section), new `tests/gridspine/test_voltage_control.py`

**Confidentiality (GC-TEXT-1).** Cite only the public grid code (SAGC / TRGC by SE / NGSA) and generic engineering. TRGC numbers and titles may be named. Never name a project, customer, consultant, EPC or OEM, here, in code, tests, fixtures, UI text or PR bodies.

## Why

TRGC 12 asks for the plant's voltage-control strategy. GridSpine checks a Q band, PF rows and the
unit Q capability, and stores `voltage_deadband_pct` without using it. The load flow has no
Q(V) droop, no V-control or PF mode, and no plant power controller (PPC) setpoint at the PoC.

## What to build

1. **Control modes** per plant (`voltage_control.py`): `q_fixed`, `pf`, `v_control`, `q_of_v`
   (droop slope %, deadband from the profile's `voltage_deadband_pct`, Q limits from
   `ibr_q_capability`). Applied in `campus_flow.solve_cases` by an outer loop on PoC voltage.
2. **PPC setpoint** at the PoC: the plant meets the PoC target (V, Q or PF) by sharing Q across
   units pro rata to available Q; flags when a unit hits a limit.
3. **PoC P–Q chart across voltage**: for V_PoC ∈ {0.90, 0.95, 1.00, 1.05, 1.10} pu and P from 0
   to P_max, the achievable Q at the PoC after collector and transformer losses. CSV + scorecard
   row against the profile's Q band.
4. Profile keys in `grid_codes.py`: `q_v_slope_pct`, `voltage_control_modes`; absent → "unknown",
   never passed.

## Done when

- Single-unit fixture: in `q_of_v` with slope s and deadband d, Q at V = 1 + d + Δ equals
  −Δ/s · Q_max (hand value) to 1e-3 pu; inside the deadband Q = 0. Tests cover both deadband edges
  (mutation "≤ vs <" is killed).
- `v_control` holds V_PoC within 1e-4 pu of setpoint when Q is available, and reports the limit
  when not.
- P–Q chart at 1.0 pu matches `ibr_q_curve` minus a hand-computed transformer Q loss.
- Existing `campus_reactive` and `campus_compliance` tests still pass unchanged.

Release note: one new file under `doc/release-notes.d/` (see its README).

## Comments

**GK A2 plan verdict (2026-11-17): GO, one PR, with conditions.**

- (a) "≤ vs <" at ±d is an equivalent mutant (the offset droop is continuous there). Killing tests cover the
  offset (dropping −d), the sign (both sides of v_ref), the slope unit (s % taken as pu) and the ±Q_max clamp.
- (b) Fail closed: judge all five voltages, V named in the detail. Optional key `q_band_v_range_pu`
  ({min, max, clause, source}, finite, min < max) narrows the judged range; rows outside read "reported".
  No shipped value; a test covers both cases.
- V1 Grid equivalent Z = c·Un²/S_k'' with c stated; v_control and q_of_v run at the S_k min and max
  equivalents; impedance hand-value test.
- V2 Non-convergence at any root-finder evaluation makes the case non-converged (never at_limit, never
  pass); at_limit records the bound and the shortfall. Both tested, incl. a swallowed-LF-error mutant.
- V3 g evaluated at both bounds first; equal signs → at_limit at the nearer bound; g(−1) > g(+1) → named
  error. Tested.
- V4 Q_max of the droop stated (plant level) and the same in the hand test; the pf mode's sign tested at
  P > 0 and Q > 0.
- V5 PPC sharing tested with two unequal units; a unit at P = S (no headroom) is distinct from a unit
  with no rating.
- V6 The P–Q chart states whether STATCOM counts (it does); switched banks in their own column, not
  judged. The 1.0 pu hand test takes the transformer Q loss from literal numbers.
- V7 New keys finite and > 0, modes a subset of the four, unknown refused, a reason test each.
- V8 control=None leaves solve_cases unchanged; one HourFlow pinned equal to master's.
- V9 Shared files: own step and own section only.

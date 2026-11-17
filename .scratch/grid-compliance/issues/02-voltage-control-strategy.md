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

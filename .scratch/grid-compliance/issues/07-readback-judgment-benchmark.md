<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-07 External-result read-back, automatic compliance judgment, RMS–EMT and field benchmark

Status: ready-for-agent
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: none (parts 1–4 on a neutral channel frame); part 5 waits on 04 and 06 for their output files
Gated: **yes** (GK A, gridspine)
Model tier: opus
Size: L (two or three PRs)
Owner decision: GC-SCOPE-1, GC-TEXT-1 (2026-11-17)
TRGC: 06, 07, 08, 24, 25
Likely files: new `gridspine/readback/channels.py`, new `gridspine/readback/judge.py`, new `gridspine/readback/benchmark.py`, `gridspine/readback/__init__.py` (shared with GC-05: exports only), `gridspine/drivers/readback.py`, `gridspine/templates/grid_codes.py` (shared with GC-02: GC-07 adds only dynamic envelope keys), new `tests/gridspine/test_judge.py`, `tests/gridspine/test_benchmark.py`, `tests/gridspine/test_channels.py`

**Confidentiality (GC-TEXT-1).** Cite only the public grid code (SAGC / TRGC by SE / NGSA) and generic engineering. TRGC numbers and titles may be named. Never name a project, customer, consultant, EPC or OEM, here, in code, tests, fixtures, UI text or PR bodies.

## Why

Dynamic requirements are listed today as "needs an RMS study" and never judged. TRGC 06/07 need
each test judged against the code's envelopes; 08 needs the RMS and EMT models benchmarked;
24/25 need the model validated against field measurements. Read-back today is load-flow only.

## What to build

1. **Channel frame** (`channels.py`): one in-memory form (time index, named channels in SI/pu with
   units). Readers for CSV, COMTRADE (IEEE C37.111, ASCII and binary) and PSCAD `.inf`/`.out`.
2. **Dynamic envelope keys** in `grid_codes.py`: LVRT/HVRT voltage–time curves, reactive-current
   gain and rise time, active-power recovery time and rate, settling time and band for setpoint
   steps, frequency-response droop. Absent key → "unknown", never passed.
3. **Judge** (`judge.py`): per case, pass/fail/unknown with the measured value, limit, margin and
   the time it occurred: stays-connected within the ride-through curve; reactive current reaches
   its target within the rise time; P recovers to x % within t; step response rise/settling/
   overshoot.
4. **Benchmark** (`benchmark.py`): IEC 61400-27-2 style windowed errors (pre-fault, fault,
   post-fault windows; mean error, mean absolute error, max error) between two frames after
   resampling; tolerances from the profile or user input. Used for RMS vs EMT (08) and model vs
   field (24/25).
5. **Pack read-back**: map GC-04 psspy and GC-06 PSCAD outputs to battery case ids; results land
   in the run directory and the campus service.

## Done when

- Synthetic step response with known rise time 50 ms and 5 % overshoot: judge reports both to
  within one sample. Settling-band edge tests kill "≤ vs <".
- A voltage trace exactly on the LVRT curve passes; 1 ms below it fails at the right time.
- Benchmark of a frame against itself gives zero errors; against a frame with a constant 0.02 pu
  offset gives mean error 0.02 in every window; windows split at the event times given.
- COMTRADE reader passes a known small sample (analog scaling a·x+b checked by hand).
- A case with no matching channel is "unknown", never "pass".

Release note: one new file under `doc/release-notes.d/` (see its README).

## Comments

**2026-11-17, GK A2 plan GO** (three PRs; PR3 after GC-04's battery merges). Conditions:

PR1 (channels + benchmark)
- B1 Every row (or the result header) states "whole-window metrics; no IEC pre-filter or sub-window split". The deferred pre-filter and transient/quasi-steady sub-windows are a needs-triage follow-up. Cite IEC 61400-27-2 for the method, 27-1 only for the model.
- B2 Any aggregate verdict is pass only when every row passes; any unknown, or zero rows, is unknown. No aggregate → say so in the docstring.
- B3 Reference/test unit mismatch refuses, tested with real rescaled units (kV vs V).
- B4 Tolerance edge: exactly at the limit passes, one ulp above fails (np.nextafter). Separate refusals for NaN, inf and negative tolerances, and for an event on or outside the span edge.
- B5 MXE keeps its sign and the time of the first occurrence on ties; tested with a negative-only error.
- B6 COMTRADE PS conversion both ways (S-file with primary True/False, P-file with primary=False divides by the ratio); multi-rate times follow each segment; a refused missing value names channel and row.
- B7 PSCAD per-file time compare is exact; a 2nd .out differing in one sample refuses.
- B8 Every guard has a targeted mutant its own test kills; list in the PR body.

PR2 (envelopes + judge)
- J1 Voltage-within-curve is a test-validity row; the obligation is the stays-connected row (connected at every sample while V is on/above the LVRT curve, on/below for HVRT; a trip after V first leaves the curve is allowed). No connected channel → unknown, also when V left the curve early (stated in the row). Never pass on the voltage row alone.
- J2 Curve: linear between distinct t, vertical step at repeated t (right-continuous), last value held, t from t_event. Tests mid-ramp, both sides of a step, HVRT mirrored.
- J3 Name the voltage quantity (positive-sequence or min phase-to-phase at PoC) in row and docstring; the channel role says which.
- J4 Iq: ΔV from a stated pre-fault mean window; stated deadband convention; sign capacitive for undervoltage, inductive for overvoltage; right magnitude wrong sign fails; Iq in pu of rated current, unit mismatch refuses.
- J5 P recovery: pre-fault mean over a stated window; stays to end of record or a stated hold; a dip back fails; ramp_min on its own row.
- J6 Step: rise is 0 → rise_level_pct of the change; band centred on the commanded target, else an end-of-record mean disclosed in the row; overshoot direction-aware, downward step tested.
- J7 Droop: droop_pct > 0 finite; ΔP≈0 or Δf inside deadband → unknown or finite guard, never inf/NaN.
- J8 Case status: fail if any row fails, else unknown if any unknown, else pass; zero rows unknown; absent profile key unknown "not in profile".
- J9 Profile values finite and in range (pct in (0,100], times ≥ 0, points non-empty, t non-decreasing), each refusal tested; grid_codes.py change additive, existing PROFILE_KEYS and shipped profiles validate unchanged; line order coordinated with GC-02.

**2026-11-17, GK A2 plan GO for PR3** (part 5, pack read-back; build after PR2 has its OK). Conditions:

- K1 Pack binding: `judge_pack` takes the pack's manifest (or its directory), not a loose battery.json; refuse when battery.json's sha256 is not the manifest's entry. Each judgment row and the judgment.json header record the manifest data_hash and contract version. An unknown battery or contract version is refused; each known version has an acceptance test.
- K2 Ratings: rated MW/MVA come from the campus run the pack was built from, with their source recorded; an explicit argument only with a stated source. Non-finite or ≤ 0 refused. Hand test of MW → pu and Mvar → pu with literals.
- K3 connected: no inference. FRT cases read unknown from pack output alone and judgment.json states why. Contract gap filed as `12-contract-connected-channel.md` (GC-04, GC-06), blocking any FRT pass.
- K4 1-phase faults: the LVRT row on positive-sequence V says "positive-sequence voltage; per-phase ride-through not judged". Step target is the battery's commanded step (pre-step mean + step_pu), stated in the row (J6).
- K5 Files: both PSCAD namings present → refuse as ambiguous; missing → unknown "no result file"; extra files listed; a file lacking a contract channel the case needs → unknown naming it. Each tested.
- K6 Output: judgment.csv/json written atomically; a re-run replaces both. Case summary follows J8. End-to-end status counts with literals.
- K7 Shared files: `drivers/readback.py` additive only; `readback/__init__.py` gets GC-07's exports only.
- Mutants on pack.py and the driver function, survivors argued in the PR body.

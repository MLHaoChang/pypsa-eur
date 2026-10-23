<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Harmonic and flicker screening for large drives and thyristor heaters

Status: needs-triage
Type: task
Blocked by: none
Gated: **yes** (gridspine, and the choice of standard)
Model tier: opus
Size: L

Read [the design](../design.md) §4.3 and the
[research note](../research/se-electrifying-refining-desw024.md) §6a (gap 3, harmonics and flicker
part) and §7.3. **Motor start stays in [issue 12](12-minutes-scale-motor-start-restart.md).** This
ticket does not do motor-start ΔU or restart; it only adds the steady-state emission check.

## Why

Electrified refining adds large variable-speed drives (turbine-to-motor swaps, note §6) and
phase-angle-controlled heaters. Both inject harmonics, and heater control causes flicker. The app
has nothing for this: the only hits are statements that studies carry "no dynamics, no harmonics"
(`gridspine/static/campus_invest.py:182`, `campus_milp.py:194`). The paper never mentions it
(note §7.1). A client asking "can this connect at the PoC?" gets an answer on fault level and SCR
but not on power quality (note §6a.3, gap 3, "no issue covers harmonics, flicker").

## What to build

1. **A static check** `gridspine/static/harmonics.py` beside `strength.py`. It reads the PoC fault
   level from `shortcircuit.py` (the same input `strength.scr` uses) and returns one row per
   unit and per PoC: harmonic voltage distortion (individual order and THD), and the flicker
   severity Pst / Plt estimate, each with a limit, a margin and a pass/fail band.
2. **Per-unit inputs:**
   - rated MVA;
   - drive topology (`6-pulse | 12-pulse | 18-pulse | active-front-end`) or heater control
     (`phase-angle | burst | thyristor-stack`);
   - a harmonic current spectrum (order, % of rated current), or a standard default per topology;
   - for flicker, the largest power step and its repetition rate (reuse the process-unit profile).
3. **Aggregation** at a PoC: sum harmonic currents by order (arithmetic sum as a conservative
   default; the gatekeeper may choose root-sum-power), then divide by short-circuit MVA.
4. **Results:** a campus-study output file and a row in the grid-readiness scorecard
   (`services/adequacy/grid_readiness.py`, where `_rvc_row` is at `:442`).
5. **Demo limits are ledgered**; the client's grid code governs, as in issue 12.

## Standards (candidates for the gatekeeper to confirm)

- **IEC 61000-3-6** (harmonic emission limits for MV/HV/EHV installations).
- **IEC 61000-3-7** (flicker emission limits for fluctuating loads).
- **IEEE 519** (harmonic limits at the point of common coupling).
- The operator's grid code overrides all of these where it differs.

## Acceptance (stated first)

- A 12-pulse drive passes where a 6-pulse drive of the same MVA fails at the same PoC, with the
  default spectra (demo values).
- Doubling the fault level halves the voltage distortion contribution (to 1e-9).
- A unit with no spectrum uses the topology default and the row says so.
- A thyristor heater whose step and repetition exceed the Pst limit fails; a burst-fired one of the
  same MW passes.
- No unit with `rated_mva` missing is silently skipped; the row says "no input".

## File pointers (checked on origin/master)

- `gridspine/static/strength.py:1` (stage 2, SCR pre-check; `scr` at `:59`), `:52` (`band`).
- `gridspine/static/shortcircuit.py:1` (IEC 60909 fault levels).
- `gridspine/static/campus_invest.py:182`, `campus_milp.py:194` (the "no harmonics" notes to update).
- `services/adequacy/grid_readiness.py:236` (scorecard), `:442` (`_rvc_row`).
- `.scratch/grid-to-process/issues/12-minutes-scale-motor-start-restart.md`.

## Open questions

- Gatekeeper: which standard is the default, and are the three above the right set?
- Gatekeeper: harmonic summation law (arithmetic, or the IEC 61000-3-6 exponent)?
- Gatekeeper: do filters (passive or active) belong in this ticket or a follow-up?
- Owner: is a vendor spectrum expected from the client, or do we ship defaults only?
- Owner: should flicker wait for issue 12's PoC step logic to avoid two RVC paths?

## Dependencies

- None blocking. Shares the PoC fault-level input with issue 12; do not duplicate it.
- Related: issue 12 (motor start, kept separate), issue 15 (heaters appear as electric Links).

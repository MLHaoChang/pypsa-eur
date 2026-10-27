<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Harmonic and flicker screening for large drives and thyristor heaters

Status: done (#707 harmonics, 7e96e15f; #727 flicker, 70a002d5). Owner 2026-10-23, Q17: default spectrum per topology + optional vendor spectrum per unit.
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
- A burst-fired heater whose step and repetition rate exceed the Pst limit fails, and the same heater
  with a smaller step or a lower rate passes. Phase-angle control is the harmonic case (it carries a
  spectrum) and the smooth one for flicker. Flicker is decided by step size and rate, never by the
  control type's name.
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

## Triage (gatekeeper, 2026-10-23)

**Status `needs-info`:** one owner question is open (below). After that it is `ready-for-agent`, still
gated.

Answers to the gatekeeper questions:

- **The default is IEC 61000-3-6 (harmonics) and IEC 61000-3-7 (flicker).**
  - `shortcircuit.py` is IEC 60909, the RVC row already cites IEC 61000-3-7, and the projects are
    European.
  - **IEEE 519 is not in this ticket.** It judges current distortion (TDD) at the PCC against
    I_sc/I_L, which is a different quantity and a second code path. It gets a follow-up issue if a
    client needs it.
  - The operator's grid code still overrides all of these.
- **Limits come from the grid-code profile, as the RVC row does.** `_rvc_row` reads
  `profile["rvc_limit_pct"]` with its clause and source (`services/adequacy/grid_readiness.py:442`).
  Add `thd_limit_pct`, per-order limits, `pst_limit` and `plt_limit` there, with the same
  "no limit set → verdict unknown" behaviour, rather than a separate ledger. Demo values are marked
  as not from the code, as `rvc_limit_pct` is.
- **The summation law is IEC 61000-3-6's general summation law,** I_h = (Σ I_h,i^α)^(1/α), with
  α = 1 for h < 5, 1.4 for 5 ≤ h ≤ 10 and 2 for h > 10.
  - Arithmetic summation is the same formula with α = 1 everywhere, so expose it as one switch, not
    a second path.
  - Voltage distortion is U_h = I_h · h · Z_sc. Z_sc comes from `_poc_sk_mva`, which keeps the
    "doubling the fault level halves it" acceptance exact.
- **Filters go to a follow-up.** A unit's spectrum may already be the post-filter spectrum, and the
  row says which.
- **Flicker does not wait for issue 12, so I took that question off the owner's list.**
  - The relative step d = ΔS / S_k is the same one-liner `_rvc_row` already uses, read from the same
    `_poc_sk_mva`.
  - Pst follows IEC 61000-3-7's analytic method: t_f = 2.3 · (F · d_max)^3.2 per step,
    Pst = (Σ t_f / T_p)^(1/3.2).
  - Whichever of 12 and 17 lands second reuses the other's PoC step helper. Neither adds a second
    fault-level read.
- **Ship it in two PRs:** harmonics first (M), then flicker (S–M). Size L in one PR is too much to
  review.

Owner question (send it as these options):

- **Where do the harmonic spectra come from?**
  - *(Recommended)* Ship default spectra per topology, labelled `demo`, and accept an optional
    vendor spectrum per unit. The row says which one it used, as the acceptance asks. This works on
    day one and gets exact when the client sends data.
  - Vendor spectra only. A unit without one gets "no input". Nothing is assumed, but most early
    projects would show no harmonic answer.
  - Defaults only, no vendor input. Simplest, but a client with a measured spectrum cannot use it.

<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Power-quality screening additions: IEEE 519 TDD, unbalance, harmonic impedance scan

Status: ready-for-agent
Type: task
Blocked by: part 1 (TDD): grid-to-process issue 17's harmonics PR; parts 2 and 3: none
Gated: **yes** (gridspine)
Model tier: opus
Size: S + S + M (three PRs are fine)
Owner decision: SAGC-1 (G5), 2026-10-23

**Confidentiality.** Cite only the public Saudi Arabian Grid Code (SAGC, May 2026) and generic engineering. Never name a client, its network, its MW figures or any scope-of-work text, here, in code, tests, fixtures or PR bodies.

## Why

SAGC asks for current distortion against IEEE 519, voltage unbalance, and network harmonic
impedance. Grid-to-process issue 17 builds voltage THD and flicker but explicitly leaves IEEE 519
out. **This ticket lifts that exclusion.**

## What to build

1. **IEEE 519 current TDD** at the PCC, limit by Isc/IL ratio band, beside issue 17's voltage check.
2. **Unbalance screen:** negative-sequence voltage from the single-phase load share and the
   negative-sequence short-circuit power at the PoC (`static/island_sc_seq.py` style inputs).
3. **Harmonic impedance scan:** R(h) and X(h) of the pandapower net seen from the PoC, with skin
   effect and line capacitance, written as CSV, plus the R–X envelope over the max, min and normal
   short-circuit cases.

Screening only: filter design stays in PowerFactory / PSCAD.

## Done when

- Each check returns rows with limit, margin and band, and is labelled screening.
- Tests: TDD limit band chosen correctly at the Isc/IL boundaries; unbalance matches a hand
  calculation; the impedance scan shows the resonance of a fixture with a known shunt capacitor.

## Triage (gatekeeper, 2026-10-23)

**Status `ready-for-agent`, gated, three PRs, in this order:** 2 (unbalance, S), then 3 (impedance
scan, M), then 1 (TDD, S).

- **Part 1 waits on grid-to-process issue 17.** Issue 17 is still `needs-info` on its one owner
  question (where the harmonic spectra come from). TDD needs the same per-unit current spectra and
  the same "limits from the profile, no limit → unknown" rule. So TDD is a second check on 17's
  spectra and summation, not a second spectrum path.
  - The IEEE 519 exclusion in 17's triage was a scope cut for that ticket. SAGC-1 now puts TDD
    here, which is consistent with it: 17 stays IEC 61000-3-6 voltage THD.
  - **Band choice:** take the I_sc/I_L boundaries from IEEE 519's current-distortion table as the
    profile's default bands. A row stores the band it chose. The test hits each boundary from both
    sides.
- **Part 2 (unbalance):** negative-sequence voltage u₂ ≈ S_unbalanced / S_sc2, where S_sc2 is the
  negative-sequence short-circuit power at the PoC from `static/island_sc_seq.py`. The test is a
  hand calculation on a fixture.
- **Part 3 (impedance scan):** sweep h · f0 with `f0 = net.f_hz` (after 09 this is the only source
  of the fundamental, so no constant). Use pandapower's line R, X and C scaled per harmonic, with a
  √h skin-effect term on R.
  - The test fixture has a shunt capacitor whose parallel resonance h_r = √(S_sc / Q_c) is known
    analytically. The scan's peak must land within one harmonic step of it.
  - Write the R(h) and X(h) CSV per short-circuit case (max, min, normal), plus the R–X envelope.
- **Every row is labelled "screening".** Filter design stays out of scope.

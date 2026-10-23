<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Power-quality screening additions: IEEE 519 TDD, unbalance, harmonic impedance scan

Status: needs-triage
Type: task
Blocked by: 09; ships after grid-to-process issue 17 (harmonics, then flicker)
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

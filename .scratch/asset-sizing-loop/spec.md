<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Asset-sizing loop (AS)

Status: ready-for-agent

## Goal

In the owner's words: customer design preferences flow into capacity-expansion sizing, and the sized result
loops back through electrical design, grid compliance and control settings.

## Sources

- Research note: [`ai-load-smoothing-sizing-loop.md`](../datacenter-grid-readiness/research/ai-load-smoothing-sizing-loop.md), sections 4 and 5.
- Prototype: [`RESULTS.md`](../datacenter-grid-readiness/research/prototype-as-loop/RESULTS.md). The loop converges with four changes to the cuts; they are applied in AS-03 to AS-06.

## Design intents

| Intent | Technologies offered | Services required |
|---|---|---|
| A. Replace diesel | Energy BESS (GFM), optional fast tier, no genset | Backup autonomy P_crit x 4 h (editable), ride-through without transfer, smoothing, GFM MVA |
| B. Keep diesel + MV smoothing BESS | Genset, high-C BESS at MV, LV UPS kept | Smoothing and ramp; genset covers autonomy; UPS covers the bridge |
| C. Keep diesel, no new BESS | Genset, UPS, GPU power floor | Ramp by the GPU floor alone; flicker by STATCOM; residual shown |
| D. No UPS (BESS-EPS in parallel) | GFM BESS for bridge + smoothing; optional genset | Bridge reserve from the BESS; seamless dP headroom; grid-code warning |
| E. MV UPS (series) | MV UPS with storage; genset optional | Rectifier ramp = grid ramp; ride-through by the UPS; Q from the MV UPS |

## Owner decisions (docs/agents/owner-decisions.md)

- AS-PROTO-1: prototype the loop first. Done; converges with changes. AS-BUILD-1: file all ten, start AS-01, AS-02, AS-04, AS-08.
- AS-AUTONOMY-1: intent A sizes backup energy as critical load x 4 h by default; editable per project.
- AS-NOUPS-1: offer intent D; its grid-code check warns where the profile does not accept transfer to BESS as ride-through.

## Confidentiality

SAGC-1 applies. Cite only public sources and generic engineering. All limits are marked "assumed". No client document enters the repo.

## Issues

01 intents, 02 Store+Link storage, 03 LP floors, 04 flicker inputs, 05 PCS/STATCOM, 06 loop runner, 07 settings sheet, 08 grid-code keys, 09 spectrum, 10 comparison UI.

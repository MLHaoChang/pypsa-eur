<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-02 — Separate power and energy storage

Status: ready-for-agent
Type: task
Blocked by: none
Model: Opus
Reviewer: GK B

Spec: [spec.md](../spec.md). Source: research note section 5, [RESULTS.md](../../datacenter-grid-readiness/research/prototype-as-loop/RESULTS.md).

## Scope

Model each storage technology as a PyPSA `Store` (energy E_k) with charger and discharger `Link`s (power P_k), as PyPSA-Eur does for battery charger/discharger. Bound E_k/P_k between technology hours (supercap 5–60 s, high-C BESS 0.25–1 h, energy BESS 2–8 h) and add the C-rate cap P_k <= c_max,k x E_k. Replaces the fixed `max_hours`. Add supercap and high-C BESS to the defaults pack (power and energy priced separately). Migration keeps `StorageUnit` projects working.

## Acceptance

- [ ] Tests first: an LP with one energy BESS solves with E/P inside 2–8 h; a hand case (100 MW, 4 h) gives 400 MWh.
- [ ] C-rate cap binds in a test where arbitrage would otherwise pick a shorter duration.
- [ ] Supercap and high-C BESS exist in the defaults pack with sourced, "assumed" costs.
- [ ] A legacy `StorageUnit` project gives the same objective (within solver tolerance) after migration.
- [ ] Two technologies can compete on cost in one run.

## PRs

## Comments

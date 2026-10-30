<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-02 — Separate power and energy storage

Status: done
Type: task
Blocked by: none
Model: Opus
Reviewer: GK B

Spec: [spec.md](../spec.md). Source: research note section 5, [RESULTS.md](../../datacenter-grid-readiness/research/prototype-as-loop/RESULTS.md).

## Scope

Model each storage technology as a PyPSA `Store` (energy E_k) with charger and discharger `Link`s (power P_k), as PyPSA-Eur does for battery charger/discharger. Bound E_k/P_k between technology hours (supercap 5–60 s, high-C BESS 0.25–1 h, energy BESS 2–8 h) and add the C-rate cap P_k <= c_max,k x E_k. Replaces the fixed `max_hours`. Add supercap and high-C BESS to the defaults pack (power and energy priced separately). Migration keeps `StorageUnit` projects working.

## Acceptance

- [x] Tests first: an LP with one energy BESS solves with E/P inside 2–8 h; a hand case (100 MW, 4 h) gives 400 MWh.
- [x] C-rate cap binds in a test where arbitrage would otherwise pick a shorter duration.
- [x] Supercap and high-C BESS exist in the defaults pack with sourced, "assumed" costs.
- [x] A legacy `StorageUnit` project gives the same objective (within solver tolerance) after migration.
- [x] Two technologies can compete on cost in one run.

## PRs

## Comments

- GK B plan verdict: GO with nine conditions. How each is met:
  1. The input fingerprint (exact and non-exact) appends the tagged Stores' columns only when a Store carries
     them; an untagged network keeps master's fingerprint (pinned in a test). The investment case keys on the
     dispatch digest, which follows the re-solve.
  2. P is the AC-side MW, the charger's p_nom; the tie is PyPSA-Eur's (charger = discharger efficiency x
     discharger p_nom). The hand case uses efficiency 0.96 and pins the discharger at 100 / 0.96.
  3. Each row (tie, min_hours, max_hours, C-rate) has a test that fails when the row is dropped (mutation run).
     The shipped C-rates equal 1 / min_hours, so the C-rate row binds only with a tighter project limit; its
     test sets 0.25 /h.
  4. Validation (`split_storage_sizes`) refuses fixed sizes or bounds that cannot meet the hours, before the
     solve.
  5. The tags survive the NetCDF save/load and the project copy; the reloaded store keeps its rows.
  6. Migration is exact or refused by name (inflow, spill_cost, marginal_cost_storage, any time-varying input,
     the per-period SOC options, build_year/lifetime in a multi-period network). No route or chat tool calls
     it yet.
  7. Pack 2026-10-30 is 2026-10-26 byte-equal plus 13 rows; regional packs unchanged. Unpinned callers are
     listed in the PR body.
  8. The wrapper is chained before the objective scale; a two-period myopic run applies it in each period.
  9. The StorageUnit-only readers are filed as [AS-11](11-split-storage-readers.md).


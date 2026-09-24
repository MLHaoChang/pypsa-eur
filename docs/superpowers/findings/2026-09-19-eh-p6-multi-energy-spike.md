# Phase 6 — Multi-energy ENS spike (dedicated-bus honesty)

**Date:** 2026-09-19  
**Plan:** `docs/superpowers/plans/2026-09-14-eh-reference-design-gaps.md` Phase 6  
**Rule:** Do **not** surface bus-carrier `by_carrier` lost-load as multi-energy ENS on shared-bus models.

## Blocker (confirmed)

One involuntary VOLL Generator per load-bearing bus (`assumptions` 5b). Capture is snapshot × **bus**. Electrical ENS-cap / FMEA ΔEUE / shed-hours filter via `electrical_columns`. Re-labelling compare `by_carrier` as multi-energy ENS would be dishonest when loads of different carriers share a bus.

P4b already refused per-load attribution on this geometry (islanded + retained critical). P6 owns any future multi-slack redesign.

## Options

| ID | Approach | Honesty |
|---|---|---|
| A | Relabel `by_carrier` as ENS | **NO** — shared-bus lie |
| B | Dedicated-carrier buses + scoped metrics | **YES** for sector hubs with carrier-dedicated buses (P4b pattern) |
| C | Per-Load / per-(bus,load) slacks | Full redesign; unlocks shared-bus attribution |
| D | Per-(bus, carrier) slack | Middle ground; still fails mixed VoLL on same carrier |

## Ship P6(a) = option B

1. Fixture: electrical + H₂ loads on **separate** buses (`buses.carrier` matches load class).
2. Preflight: fail-closed when a bus hosts Loads whose canonical carrier ≠ bus carrier class (or mixed Loads).
3. Report section `multi_energy`: `ens_by_carrier_mwh` from bus-period capture grouped by bus carrier; honesty notes `dedicated_bus_by_carrier`, `no_per_load_attribution`, `shared_bus_not_supported`.
4. Electrical ENS / FMEA default path **unchanged**.
5. Ranking: carrier-scoped ΔEUE helper available for H₂ columns (opt-in); default FMEA stays electrical.

**Defer P6(b):** multi-slack / per-Load redesign for shared-bus industrial+residential attribution.


## Fixture note (solve)

`generator_p_nom_invalid` rejects fixed `p_nom=0`. Starved H₂ fixtures omit the
H₂ generator (VOLL sheds on the dedicated bus) — same pattern as
`test_adequacy_ens_cap` side-bus. Do not use a zero-capacity placeholder gen.

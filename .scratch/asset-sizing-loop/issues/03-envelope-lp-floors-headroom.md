<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-03 — Envelope → LP floors and headroom

Status: done
Type: task
Blocked by: 01, 02
Model: Opus
Reviewer: GK A

Spec: [spec.md](../spec.md). Source: research note section 5, [RESULTS.md](../../datacenter-grid-readiness/research/prototype-as-loop/RESULTS.md).

## Scope

In `extra_functionality`, written like `_reserve_row`, add from each `DcPhase` envelope (`dynamic_envelope`: S, A, T, checkpoint D and d): power floor sum of fast-tier P_k^smooth >= S + A (revised: with a ramp limiter the grid takes no instant share, and step and oscillation coincide); energy floor E_k^smooth >= sum (not max) of the `step_mwh`, `checkpoint_mwh` and `oscillation_mwh` events / usable SOC window. Per snapshot headroom: p_dis(t), p_ch(t) <= P_k - P_k^smooth and E^smooth/2 <= e(t) <= E_k - E^smooth/2. Make the opt-in island backup reserve available outside island mode (intent A/D: e(t) >= P_crit x T_aut), and add the genset run-hour cap sum p_gen(t) w_t <= p_nom x H_max. Reuse `smoothing.py` functions.

## Acceptance

- [x] Tests first, hand-computed: S = 30 MW, A = 10 MW gives a 40 MW power floor (not 30).
- [x] Energy floor is the sum of event energies: the prototype case gives 1.82 MWh, not the max 1.5 MWh.
- [x] Headroom: with a floor in place, arbitrage dispatch never exceeds P_k - P_k^smooth in any snapshot.
- [x] Backup reserve applies with island mode off; intent A uses 4 h (80 MW gives 320 MWh).
- [x] Genset run-hour cap of 100 h/yr binds in a test with a long outage price spike.

## PRs

## Comments

**2026-10-30, GK A plan GO (conditions).** Rows only in `services/solver/envelope_floors.py`; shared files get one wrap line + import in `solver_service.py`, one `SolverConfig.envelope_floors` field (end of class; floats finite and >= 0, None a no-op) and the fingerprint append. Changing the field changes both `_network_input_fingerprint` and the investment-case digest (test); it does not mark readers stale (LC-40). Energy floor pinned at 1.835 MWh = 2·(0.75 + 0.16667) + 0.00177, hand-computed as literals; RESULTS.md's "1.82" does not reproduce from the smoothing formulas (discrepancy noted). Backup row is sum η_dis·(e − E_sm/2·[tier]) >= backup MWh (AC critical energy). SOC window from the Store's `e_min_pu`/`e_max_pu` in the band row, no new column; energy floor sums E_sm unweighted. A flagged Store without the AS-02 charger/discharger (or a backup Store without a discharger) is refused before the solve, naming it. One mutation test per row; custom columns survive the `.nc` round-trip; genset cap weighted by `snapshot_weightings.generators` per investment period, active generators only, pinned at 100 h (uncapped 150 h). Floors are the max over phases because P_sm and E_sm are single size variables per store.

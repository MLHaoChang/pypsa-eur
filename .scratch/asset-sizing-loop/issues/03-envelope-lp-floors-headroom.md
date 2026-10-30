<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-03 — Envelope → LP floors and headroom

Status: ready-for-agent
Type: task
Blocked by: 01, 02
Model: Opus
Reviewer: GK A

Spec: [spec.md](../spec.md). Source: research note section 5, [RESULTS.md](../../datacenter-grid-readiness/research/prototype-as-loop/RESULTS.md).

## Scope

In `extra_functionality`, written like `_reserve_row`, add from each `DcPhase` envelope (`dynamic_envelope`: S, A, T, checkpoint D and d): power floor sum of fast-tier P_k^smooth >= S + A (revised: with a ramp limiter the grid takes no instant share, and step and oscillation coincide); energy floor E_k^smooth >= sum (not max) of the `step_mwh`, `checkpoint_mwh` and `oscillation_mwh` events / usable SOC window. Per snapshot headroom: p_dis(t), p_ch(t) <= P_k - P_k^smooth and E^smooth/2 <= e(t) <= E_k - E^smooth/2. Make the opt-in island backup reserve available outside island mode (intent A/D: e(t) >= P_crit x T_aut), and add the genset run-hour cap sum p_gen(t) w_t <= p_nom x H_max. Reuse `smoothing.py` functions.

## Acceptance

- [ ] Tests first, hand-computed: S = 30 MW, A = 10 MW gives a 40 MW power floor (not 30).
- [ ] Energy floor is the sum of event energies: the prototype case gives 1.82 MWh, not the max 1.5 MWh.
- [ ] Headroom: with a floor in place, arbitrage dispatch never exceeds P_k - P_k^smooth in any snapshot.
- [ ] Backup reserve applies with island mode off; intent A uses 4 h (80 MW gives 320 MWh).
- [ ] Genset run-hour cap of 100 h/yr binds in a test with a long outage price spike.

## PRs

## Comments

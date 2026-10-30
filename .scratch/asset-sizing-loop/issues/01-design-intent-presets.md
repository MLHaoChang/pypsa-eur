<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-01 — Design-intent presets A–E

Status: ready-for-agent
Type: task
Blocked by: none
Model: Sonnet
Reviewer: GK A

Spec: [spec.md](../spec.md). Source: research note section 5, [RESULTS.md](../../datacenter-grid-readiness/research/prototype-as-loop/RESULTS.md).

## Scope

Add one design intent per scenario as scenario metadata, extending DC 03 `backup_mode` and DC 05 architecture: a closed set of presets A–E (see spec table). Each preset lists the technologies offered, which are extendable, and the services that become constraints. Data model and validation only; no LP changes. Hard constraints (for example no diesel) become technology bounds; soft ones are a ranking in the comparison, not the objective.

## Acceptance

- [ ] Tests first: each intent A–E loads, round-trips and rejects an unknown intent.
- [ ] Intent A default backup energy = critical load x 4 h (AS-AUTONOMY-1): 80 MW critical load gives 320 MWh. The hours value is editable per project and validated > 0.
- [ ] Intent D is offered. Its grid-code check warns when the profile does not accept transfer to BESS as ride-through (AS-NOUPS-1); the warning does not block the scenario.
- [ ] Intent B/C genset p_nom >= P_crit and run-hour cap fields exist; intent D bridge reserve default 0.25 h (80 MW gives 20 MWh).
- [ ] Existing `backup_mode` projects load unchanged.

## PRs

## Comments

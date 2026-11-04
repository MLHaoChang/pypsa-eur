<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 28 — L1 ledger source is always None

Status: ready-for-agent
Type: bug
Blocked by: 04
Gated: yes (GK B)
Model tier: opus
Design: [design.md](../design.md) §6.1 (input gate loop L1, data-gap fills)

Found by SH-05 (c). It touches the same L1 data-gap path as issue 27, so in practice it waits for 27 to merge.

## Defect (master, `pypsa-gui/backend/harness/run_loops.py`, `run_round`)

`run_round` reads the ledger sources and `_gap_sources` from `services/run_envelope.read()`, the card view, which carries no audit and no `at_grant`. So the L1 ledger row's `source` is always None, and the "data gaps you may fill" list is probably empty.

## Acceptance

- A red test first, shown failing against the current code.
- L1 ledger rows carry their source.
- The gap list is built from the audit/`at_grant` view, not the card view.
- GK B sees the plan before any code.

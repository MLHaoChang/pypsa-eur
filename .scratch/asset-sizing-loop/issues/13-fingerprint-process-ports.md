<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-13 — Input fingerprint independent of the process's Link ports

Status: ready-for-agent
Type: bug
Reviewer: GK A

Raised by GK A in the AS-05 review (#974).

## Scope

`workflow_tools._network_input_fingerprint` hashes every Input column in PyPSA's component defaults. PyPSA registers extra Link ports (`bus2`, `efficiency2`, ...) on process-wide defaults once any network in the process uses them. Every network built after that carries them at their defaults. So an unchanged project can get a new fingerprint in the long-running API, which shows a spurious "out of date" or forces a recompute. Hash only the extra-port attributes a network populates.

## Acceptance

- [x] A Link network hashed before and after a `bus2` network loads in the same process gets the same hash (normal and exact).
- [x] The order dependence reproduced by running `tests/test_extras_passthrough.py` first is gone.
- [x] A Link-network pin is restored in `tests/test_pcs_statcom.py`.
- [x] A populated extra port still changes the hash.

## PRs

- Branch `claude/fingerprint-process-ports`.

## Comments

- A network that saved unused extra-port columns (all at their defaults) gets a new fingerprint once, so its existing studies show out of date once.

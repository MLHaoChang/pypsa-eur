<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 15 — Study result and active project can carry over from another project

Status: needs-triage
Impact: high
Templates: concentrator, IPP, ammonia (3 repeats)
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

"Run study" showed the Data Center result before any run ("Archetype weak_flexible ... Cost@target €382.7m"); LOPF ran against the previous project ("Current project: Data Center Energy Hub (staged growth)" in the ammonia walk). Caveat: the walk deleted the previous project through the API out of band, which may have caused it. Not yet reproduced with UI-only deletion.

## Why it matters

If real, a user decides on another project's numbers.

## Suggested fix

Reproduce with UI-only create/delete; key study result and active project by project id.

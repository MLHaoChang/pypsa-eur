<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07: Status bar reads "Obj: —" after every solve, and would show € M costs as "0.003 B€"

Status: done

Done in #1670 (17cf71cf).
Impact: low
Templates: all
Found by: control-strategy walkthrough, 2026-11-17 (master 9491878d)

## Repro

1. Island Microgrid, Expert › Run LOPF. Wait for "Completed". `/api/simulation/status` gives objective 3473806.32.
2. Read the status bar at the bottom right.

## Actual

- It reads "Obj: —" right after the solve and after a reload. The same happens on Green Ammonia Hub (objective 115,678,228).
- `useSimulationStore.setResult` is called only on the reconnect path (`App.tsx:483`). The normal done handler in `layout/AppHeader.tsx:~733` formats a toast but never sets the store.
- `components/StatusBar.tsx:120` formats the objective as `(objective / 1e9).toFixed(3) B€`, so the €3.47 M microgrid would read "0.003 B€".

## Expected

After a solve, the status bar shows the objective in the same form as the "Solved · …" toast (`formatObjective`), for example "Obj: €3.47 M", or the field is removed.

## Breaks

WALK-E2E-1 (right numbers): the chrome contradicts the Results header ("solved · 2.8 s").

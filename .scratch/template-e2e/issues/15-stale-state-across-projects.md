<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 15 — Study result and active project can carry over from another project

Status: wontfix
Impact: high
Templates: concentrator, IPP, ammonia (3 repeats)
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

"Run study" showed the Data Center result before any run ("Archetype weak_flexible ... Cost@target €382.7m"); LOPF ran against the previous project ("Current project: Data Center Energy Hub (staged growth)" in the ammonia walk). Caveat: the walk deleted the previous project through the API out of band, which may have caused it. Not yet reproduced with UI-only deletion.

## Why it matters

If real, a user decides on another project's numbers.

## Suggested fix

Reproduce with UI-only create/delete; key study result and active project by project id.

## Triage (2026-11-20, master 8a039460)

Checked by code reading only; the app was not re-run. The walk deleted the earlier project through the API, so the cause may be that. First task: reproduce with UI-only create and delete. If it reproduces, key the study result and the active project by project id (GK B). If not, set this wontfix and record the steps tried. Blocked by: none.

## Reproduction attempt (2026-11-20, master ae6ea750a) — not reproduced

Fresh dev app (SQLite, admin account), headless Chromium driving the UI only; no API calls for create, run or delete:

1. New project > From template > Data Center Energy Hub > Create project (project A).
2. Expert mode > Run LOPF, wait for the solve.
3. Results > Reliability > Adequacy > Energy Hub reference design > Run study. Waited until the button read "Run study" again; the report showed "Cost@target" (study result present in A).
4. Scenarios > trash icon on A > Delete (confirm). The app returned to "Untitled", 0 buses.
5. Projects > New project > From template > Data Center Energy Hub (project B, same session, no reload).
6. Expert mode > Run LOPF, wait; Results > Reliability > Adequacy > Energy Hub reference design.

Result: B shows no study result ("Cost@target" absent, button reads "Run study", not "Run again"); LOPF solved B's own network. Nothing carried over from A.

Code check agrees: the frontend keys the study query by project id (`nk(currentProject, 'results', 'eh_study')` in `EhReferenceDesignPanel.tsx`, and the delete handler clears `currentProject` and invalidates that project's queries), and the backend holds study records in the per-project context (`STUDY_KEYS` in `services/project_context.py`).

Not tried: deleting from the Projects home page rather than the Scenarios panel; reloading the page between delete and create; a second, different template as B (the retry with Industrial Hydrogen Hub failed on a test-harness locator, so B reused the Data Center template, which makes "Current project" text alone unable to tell A from B — the absent study result is what shows isolation). The walk's carry-over most likely came from deleting the project through the API: that bypasses the frontend's `currentProject` clearing and context reset. Reopen with UI-only steps if it recurs.

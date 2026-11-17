<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Notes for step 2 (browser journeys)

What the step-1 probe learned about driving the app. The probe ran against the dev servers. CI journeys use the built app and `scripts/composer-fixture.py` like `smoke-copilot-oj01.mjs`.

## Selectors that worked (role/label first)

- Sign in: `#email`, `#password`, button "Sign in"; wait for `/projects`. CSRF cookie `pypsa_gui_csrf` → header `X-CSRF-Token` (for API clean-up only; every decision goes through the UI).
- New project: button "New project" → `getByRole('dialog')` → button "From template" (exact) → the card button whose name starts with the template name (escape the parentheses in "(staged growth)").
- DC templates open an inline form (`form[aria-label="<name> setup"]`): IT load, PUE at design, Backup mode, Power architecture; submit `form button[type=submit]` ("dc-form-create"). The staged DC form has no IT load.
- "Your own site" (eh_site) lands on the Guided site form, with no project yet. Pick the radio `input[type=radio][value=<site type>]`, open "More", then `site-create` ("Create the site").
- Creating a project can take more than 60 s on the first build. Wait for the toast "Created '<name>' from template", then for `/app`. A second create with the same name refuses with "already exists": delete via `DELETE /api/projects/<name>` first.
- Mode: buttons "Guided" / "Expert" (exact) in the header group "Interface mode".
- Guided rail: `getByRole('navigation', { name: 'Project flow sections' })`, buttons named after the sections ("1 Project & site" …); the last one, "↳ Improve", stays hidden until a result exists. Card host `hub-design-panel`.
- **Escape closes the whole Hub design panel** (and with it the rail). Reopen it with `sidebar-hub-design`; never press Escape to close a drawer.
- eh_site radios are `sr-only`: click `getByTestId('site-type-<id>')`.
- Drawers: **Edit ›** / **Open here ›** open `field-drawer` (close with `field-drawer-close`). Section 2's Edit and "Open … in Expert" switch to Expert with a "Back to <section>" button; recover with that button, or with "Guided".
- Expert: section blocks `[data-testid^=flow-section-]`; an entry's panel overlays the canvas, so close it ("Close") before opening the next. Components: type in "Search components…", click the `option` (Enter does not select); the property panel is the last `aside`.
- Run (Guided): section 9 "Run study" (`hub-run-start`); verdict `hub-results-verdict`. Goal: "Allowed shortfall (hours per year)", "Save goal" (`hub-goal-save`).

## Pitfalls

- Parallel runs against one dev backend time out; run journeys one after another.
- Use `page.setDefaultTimeout(8000)` with explicit long waits only for create and run.
- A full study can take minutes. The run accepts a small budget (`budget_solves`, stages subset, `run_profiles.py:448` quick profile) and fewer simulation draws ("Simulation runs per check" under Goal → More settings). For PR CI, choose the smallest settings the UI offers, and check whether the UI exposes the budget at all; if not, that is a gap for the coordinator.

## Expected-fail map (gap → journey step)

01 location gate (once built, Run must wait for it), 02 connection limit in Guided, 03 recipe edit in a Guided drawer (adding a carrier or process unit is asserted to be an Expert link that returns, GAP03-ADD-1), 04 load editor value and unit, 05 raw-name scan, 06 goal value shown, 07 flexibility and critical share, 08 assumed run inputs in Results.

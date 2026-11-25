<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 23 The campus study writes a PSS/E hand-off bundle, but the app offers no download

Status: done
Blocked by: none (sibling: 09 item 6 owns the DGS download, and 04c owns the bundle's contents)
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), walk W3.
Model: sonnet. Review: GK A (bundle seam) + UX.

## Problem

A Grid study run on an Energy Hub project (data-centre template: Grid connection › Draft from project › Grid study › Run study) writes the PSS/E hand-off (`gridspine/handoff/campus_bundle.py`, one bundle per island hour). A planning → dynamics Study project offers its per-hour bundle download in `GridspinePanel`. The campus study offers none: the files exist under the project's `gridspine/` folder, and no route or button serves them.

## User-visible impact

The grid-compliance journey ends with "hand the case to the TSO's PSS/E": the app builds that case and the user cannot take it.

## Acceptance criteria

- [ ] A read route lists the latest campus run's bundles (hour, files, size, the run they came from) and serves one bundle as a zip. The viewer row is read. A path outside the run folder is refused (tested).
- [ ] The Grid study results (Grid code & connection) show "Download PSS/E case" per bundle, with the run's freshness: a bundle from a run older than the last edit says so (the same stale rule as the results).
- [ ] With no campus run, the button is absent and the section names what to run.
- [ ] End to end in the real app: data-centre template › Run LOPF › Grid study › Run study › Download PSS/E case. The zip holds the `.raw` and `.dyr` that `campus_bundle` wrote. `psse_raw_parse` reads the `.raw` back in the test (a round trip).

## Likely files

`pypsa-gui/backend/services/campus_electrical_service.py`, `pypsa-gui/backend/routers/campus_electrical.py`, `pypsa-gui/frontend/src/pages/CampusElectricalPanel.tsx`, `gridspine/handoff/campus_bundle.py` (read only), a `golden_route_surfaces.d` / `route_chat_disposition.d` fragment.

## Comments

Premise changed (2026-11-24, builder; coordinator decision, option A): no code called `cs.psse_campus` in the app's study, so no bundles were written. The scope became "write and serve": a `psse` step inside the cuts stage of the campus Run (`campus_electrical_service._psse_bundles`, outcome in `psse_status.json`; `ContractError`/`OSError` recorded, any other error fails the study as before), `GET /api/campus-electrical/{name}/psse` and `GET .../psse/{bundle}.zip` (a stale run's zip carries `X-Run-Stale: true`), and the "PSS/E case" section.

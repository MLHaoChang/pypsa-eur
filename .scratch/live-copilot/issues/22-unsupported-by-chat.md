<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-22 — features chat must refuse, by name and with a reason

Status: ready-for-agent (outsourced task E, docs/agents/outsourced.md)
Type: task
Baseline: master `ae95a94a866730c0fe67fad37e238dc281786bd6`
Dependencies: LC-01 capability discovery
Catalogue: [feature catalogue](../feature-catalogue.md); journey OJ-46

## Problem

The inventory found human features with no chat tool. Some should stay out
of chat; some are only unbuilt. Today the copilot cannot tell them apart and
may improvise (for example, guess an endpoint or say "I can't" without a
reason). LC-C1 requires explicit refusal reasons.

| Feature | Route(s) | Proposed disposition |
| --- | --- | --- |
| Admin: organizations, users, resend password, claim legacy projects, email test | `routers/admin.py` | Out of chat (security): refuse, point to the Admin page |
| Sign-in, password, account | `routers/auth.py` | Out of chat |
| Local settings: API key, reveal log | `routers/local_settings.py` | Out of chat (secrets) |
| Queue pause/resume, clear finished (instance-wide, super-admin) | `routers/solve_queue.py:1087-1164` | Out of chat |
| Project sharing (members) | `PUT /api/projects/{name}/members` | Unbuilt: candidate destructive-tier tool; owner to decide later |
| Map layout, OSM suggestions, geocode, site 3D context and thumbnails | `routers/projects.py` map/site routes, `routers/geocode.py` | Read-only navigation only; layout edits stay manual |
| Library series and meter-data upload, tariff edit | `routers/library.py` | Partly covered (`list_library_items`, `import_urdb_tariff`, `attach_tariff`); uploads stay manual |
| Grid-code PDF upload and publish | `routers/campus_electrical.py` | `campus_extract_grid_code` covers extraction; publish stays manual |
| Dictation | `routers/dictation.py` | Input path, LC-07 |

## Scope

1. A declared list of "not via chat" capabilities with a reason and the page
   that does it, exposed through `find_capabilities` so the copilot can say
   "that's on the Admin page; I can't do it from chat because …" and open
   the page.
2. A test that every router path is either reachable by a tool, listed here,
   or a UI-internal route, so a new route cannot be silently missed.

## Done when

OJ-46 passes in Tier A; the route-coverage test fails when a new route has no
disposition.

<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-02 — readiness feed for wind/solar generators

Status: ready-for-agent
Type: task
Baseline: master `5d5bf26b83aa18c9a2a0a3418d3beeecccc8d086`
Blocked by: RP-01
Requirements: R1 in [the spec](../spec.md)

`GET /api/projects/{name}/profiles/readiness`, read-only. It returns one row per wind/solar generator (carrier match as in the engine's carrier guard):
`{generator, technology, bus, lon, lat, coord_source: bus|missing, profile: none|uploaded|generated|network_native, provenance?, stale_reason?}`.
`stale_reason` is `bus_moved` when the stored provenance's point differs from the current coordinates, or `horizon_changed` when the snapshots no longer match the applied year.

Acceptance:
- Fixture with 3 renewables: one on a bus with coordinates, one on a 0/0 bus, and one with an uploaded profile. The rows match exactly.
- Moving the bus after a generated profile sets `stale_reason: bus_moved`.
- The chat tool `get_profile_readiness` (catalogue entry, read safety) returns the same rows, and the route inventory fixture is updated.

# Coordinator

Current coordinator session: `session_01G7qCujBYvGjYQYsUmuicsY`

Engine gatekeeper A (gridspine, DC, grid-to-process and IC/adequacy engine reviews; owner decision GK-SPLIT-1): `session_01G7qCujBeddE4Ah1uMrSs3i`

Engine gatekeeper B (harness, app backend, investment/finance and outsourced reviews; GK-SPLIT-1): `session_01G7qCunbvxN6JVyrzVTsisA`

Engine gatekeeper B2 (finance and business case, ui-modern, other app fixes, GC-09 and app-backend packages opened from 2026-11-17 04:00Z; owner decision GKB2-1): `session_01G7qCuktSamT1a4awVcJbFA`

Engine gatekeeper A2 (grid-compliance packages GC-01..GC-08 only; owner decision GC-PACE-1): `session_01G7qCukjUpjfyYfWvahgpvY`

UX reviewer (UI coherence gate, owner decision 2026-10-20): `session_01G7qCukhWGAmHjzkdxrmkEX`

Send every coordinator message to this id. A rotating coordinator (`.claude/skills/coordinator/SKILL.md`, "Rotation") replaces its line in a tiny PR (and does the same when a gatekeeper or UX reviewer rotates), and tells each live session the new id.

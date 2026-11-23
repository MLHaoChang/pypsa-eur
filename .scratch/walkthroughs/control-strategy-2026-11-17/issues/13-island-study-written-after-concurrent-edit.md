<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 13: An island study can be saved for a sidecar that changed while it ran

Status: needs-triage
Impact: low
Templates: Island Microgrid
Found by: GK B3 (6) review of #1913 (non-blocking), 2026-11-23

## Repro

1. Start the island study (Expert › Grid study › Island › Run), which takes minutes.
2. While it runs, change an island requirement (the Guided Island requirements card, chat, or the Expert editor).

## Actual

`run_island_study` (`pypsa-gui/backend/services/campus_electrical_service.py`) reads the sidecar at the start, solves outside the sidecar lock, then writes `island_study.json`. The edit's write deletes the old study, but the running study writes its result afterwards. The new sidecar then shows a study made from the old one.

## Expected

A study whose sidecar changed while it ran is not shown as current. For example, record the sidecar `revision` the study read and drop the result (or mark it stale) when it no longer matches at write time.

## Note

Same `revision` as the PATCH/PUT `base` check (`island_revision`, GK B3 K1).

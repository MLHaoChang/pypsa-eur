<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02d — The Energy Hub readiness preview still assumes the 50 MW pack cap

Status: needs-triage

Raised by GK A 32 on #1772 (02c). Follow-up to 02c.

## Problem

After 02c, a weak-grid study started from chat or `POST /api/results/eh_study` runs on the
network's connection limit. `GET /eh_readiness` without an import cap still previews the
pack's 50 MW constant, so the preview can disagree with the study it precedes.

## Wanted

- The readiness preview takes the network's connection limit as the study does
  (`flow_service.import_capacity`), or says plainly that the network fixes none.
- A test that a 45 MW network previews 45 MW, by route and by chat if chat reaches it.

## Notes

Out of scope for 02c by its plan. Engine-adjacent (`eh_study_runner` area): plan to GK A.

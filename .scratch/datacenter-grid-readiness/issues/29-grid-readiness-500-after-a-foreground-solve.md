<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GET /api/campus-electrical/<p>/grid-readiness returns 500 after a foreground solve in the same process

Status: needs-triage
Size: S
Blocked by: none
Gated: GK B
Likely files: pypsa-gui/backend/services/adequacy/grid_readiness.py (`_live_network`); pypsa-gui/backend/tests/test_grid_readiness_live.py
Type: task (bug)
Found by: reported to the coordinator by a session that hit it.

## Problem

After a foreground solve in the same backend process, `GET /api/campus-electrical/<p>/grid-readiness` returns 500: "Copying a solved network with an attached solver model is not supported". `grid_readiness._live_network` takes the project's mutation lock and calls `ctx.network.copy()`; a solved network keeps its solver model attached, and PyPSA refuses to copy it.

Re-activating the project does not clear it. A backend restart does.

## Expected

- The scorecard reads a solved loaded network without a 500. `_network_copy` in `workflow_tools.py` already copies networks for the fingerprint, so check how it avoids the refusal before writing a second way.
- If the live copy cannot be made, fall back as for a held lock (`(None, "out_of_date")`, the saved file), not an error.
- A test: solve the loaded network in the same process, then call the route (or `_live_network`) and get 200 / a copy.

## Notes

Not reproduced here. Check that the fix does not copy the solver model's state into the scorecard's numbers (the rows that read `p_nom_opt` are in #28's scope).

<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Results read loading % on an assumed rating as real

Status: needs-triage
Owner decisions: IMPORT-ZERO-RATING-1
Review: GK B (app backend).
Blocked by: `04-unrated-branches-runnable.md` (the readers' marker).

## Problem

Since issue 04, the PSS/E and DGS readers give an unrated line, or an unbounded external grid, a finite rating that never binds. Each one is listed under `report.assumed` and marked on the element: `n.lines.s_nom_source` / `n.generators.p_nom_source` = "assumed" (others read "file"; only "assumed" means assumed, a row added later has "" or NaN). The columns are strings because a bool column breaks the netCDF save after a solve. The app's results still read that rating as real, so an assumed 2757 MVA line shows a "low loading" that the file never stated.

## Expected behaviour

Results and overload checks treat a branch or source whose rating is assumed as having no stated rating:

- `pypsa-gui/backend/services/loadflow_summary.py` (`_rating`, ~255-269; over/at-limit, ~332-341): the row keeps its loading %, carries `rating_assumed: true` and a "rating assumed" label, and is left out of the over-limit and at-limit counts.
- `pypsa-gui/backend/services/run_grid_pf.py` (~163, 186-200): the same for AC power flow utilisation and its `util > 100` overload rule.
- `pypsa-gui/backend/services/asset_results/compute.py` (`br_loading` ~1276-1279, congestion `_LOADED` ~1301): the same for per-asset loading and the congested count.
- `pypsa-gui/backend/services/checked_charts.py` (~80-91): the branch-loading chart marks the assumed-rating branches.
- Setting `s_nom` (or `p_nom`) on a marked element sets its `s_nom_source` (`p_nom_source`) to "file", so a rating the user entered reads as real.

## Acceptance criteria

- [ ] Each of the four views labels assumed-rating rows and leaves them out of the overload, at-limit and congested counts (tests on an imported case14).
- [ ] Editing the rating sets the source to "file", and the next run reads it as real.
- [ ] A network without the marker gives the same results as before.

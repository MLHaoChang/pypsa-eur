# The IPP template's PV `p_nom` is a DC (MWp) figure

Status: needs-triage
Type: gap
Found in: TC-02 (issue 02, PR #1190), 2026-11-06.

Issue 02 defines a Generator's `p_nom` as its AC rating. `pypsa-gui/backend/project_templates/eh_ipp.py` adds
`pv_plant` with `p_nom=PV_MWP` (50 MWp) and prices it from the AC `solar-utility` row. Latent today (AC rows scale 1),
but the template's capacity, its profile and its cost basis disagree. Decide the plant's AC rating and DC/AC ratio
(e.g. p_nom = MWp / ratio, `dc_ac_ratio` set), with the investment case's expected numbers re-pinned.

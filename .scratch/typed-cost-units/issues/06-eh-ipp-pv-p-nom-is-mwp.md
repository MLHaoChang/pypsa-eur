# The IPP template's PV `p_nom` is a DC (MWp) figure

Status: done
Type: gap
Found in: TC-02 (issue 02, PR #1190), 2026-11-06.

Issue 02 defines a Generator's `p_nom` as its AC rating. `pypsa-gui/backend/project_templates/eh_ipp.py` adds
`pv_plant` with `p_nom=PV_MWP` (50 MWp) and prices it from the AC `solar-utility` row. Latent today (AC rows scale 1),
but the template's capacity, its profile and its cost basis disagree. Decide the plant's AC rating and DC/AC ratio
(e.g. p_nom = MWp / ratio, `dc_ac_ratio` set), with the investment case's expected numbers re-pinned.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Not buildable until answered.
- Question (GK A): Owner: what AC rating and DC/AC ratio should the `eh_ipp` `pv_plant` have (today `p_nom` = 50 MWp)? E.g. ratio 1.2 gives p_nom about 41.7 MW AC with `dc_ac_ratio` set and the investment-case numbers re-pinned (M, sonnet, GK A); or keep 50 as the AC rating.
- Blocked by: the answer.

### 2026-11-07: needs-info answered (coordinator)

Owner (card, TCU06-AC-1): "Ratio 1.2 → ~41.7 MW AC". Set the eh_ipp pv_plant dc_ac_ratio = 1.2 and p_nom ≈ 41.7 MW AC (50 MWp / 1.2), and re-pin the investment-case numbers. M, Sonnet, GK A.

# The `dc_ac` confirmation's text goes stale once `p_nom` is the AC rating

Status: needs-triage
Type: gap
Found in: TC-02 (issue 02) design review by GK A, 2026-11-06.

Issue 02 defines a Generator's `p_nom` as its AC rating and adds `dc_ac_ratio`. The `dc_ac` confirmation that
`reference_apply.plan` asks for on an `output_electric_ac` PV reference still says "This generator's capacity isn't
marked as AC or DC; if it is the DC module rating, the cost per MW is overstated by the DC/AC ratio". Issue 02 kept it
unchanged (AC rows unchanged). Reword it to the new definition, or retire the confirmation, together with the
`confirm` enum at `pypsa-gui/backend/harness/catalogue.py:4286` and `:5024` and the frontend card that offers it.

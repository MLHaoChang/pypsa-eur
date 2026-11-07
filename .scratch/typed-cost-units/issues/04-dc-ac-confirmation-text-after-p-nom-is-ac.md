# The `dc_ac` confirmation's text goes stale once `p_nom` is the AC rating

Status: ready-for-agent
Type: gap
Found in: TC-02 (issue 02) design review by GK A, 2026-11-06.

Issue 02 defines a Generator's `p_nom` as its AC rating and adds `dc_ac_ratio`. The `dc_ac` confirmation that
`reference_apply.plan` asks for on an `output_electric_ac` PV reference still says "This generator's capacity isn't
marked as AC or DC; if it is the DC module rating, the cost per MW is overstated by the DC/AC ratio". Issue 02 kept it
unchanged (AC rows unchanged). Reword it to the new definition, or retire the confirmation, together with the
`confirm` enum at `pypsa-gui/backend/harness/catalogue.py:4286` and `:5024` and the frontend card that offers it.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `services/library/reference_apply.py:216`, `harness/catalogue.py` (~4286, ~5024), the frontend card offering `dc_ac`.
- Blocked by: none.
- Model tier: sonnet.
- Gated: GK A; GK B for the `catalogue.py` enum.
- Size: S.
- Reword to the AC `p_nom` definition or retire the confirmation; the plan says which. Error-kind/catalogue JSON fragments as steward requires.

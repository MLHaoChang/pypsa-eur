<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-26 — weak_flexible replaces the import Link's p_nom without saying so

Status: done

Found by GK A in the LC-06 S5 review (#881). The `weak_flexible` pack sets each
selected import Link's `p_nom` (and a finite `p_nom_max`) to
`import_overlay.import_p_nom_mw / len(links)` (50 MW by default;
`services/adequacy/archetypes.py`, the `weak_flexible` branch of the pack
apply). That is the pack's intent (spec §6, `ImportOverlaySpec`: "total or
per-study power cap, equal-split across selected Links"). It is also how
`pack_overrides.import_p_nom_mw` reaches the run.

What the user is not told: when the Link's own rating differs from the pack
cap, nothing says the run used another value.
- The apply step returns no warning for it, so the `apply_pack` stage note is empty.
- The `run_eh_study` catalogue text does not say the pack sets the Link rating.
- The certification shows only the resulting `fleet_scope.import_cap_mw_max`
  (cap × efficiency), not the Link's rating it replaced.

So a project whose Link is rated 80 MW is studied at 50 MW, and the report
reads as if that were the connection.

## Acceptance

- When the pack cap differs from a selected Link's own `p_nom`, the apply step
  adds a warning naming the Link, its rating and the cap used; it reaches the
  `apply_pack` note and the report.
- The `run_eh_study` description says that weak_flexible sets the import Link
  rating from `import_p_nom_mw` (default 50 MW).
- A test through the chat tools with a Link rated other than the cap shows the warning.

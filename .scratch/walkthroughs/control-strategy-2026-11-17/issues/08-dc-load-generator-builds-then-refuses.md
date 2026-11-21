<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 08: The data-centre load generator builds a profile, then refuses it in engine words with no link

Status: done
Impact: medium
Templates: Data Center Energy Hub (any template whose load comes from a site spec)
Found by: control-strategy walkthrough, 2026-11-17 (master 9491878d). Shot: `../shots/08-generator-refuses.png`

## Repro

1. Projects › New project › From template › Data Center Energy Hub › Create project (defaults: IT 36 MW, PUE 1.3).
2. Guided › 4 Loads & demand › it_load › "Edit ›".
3. Profile › Archetype › Shape "data centre", Peak MW 36 › Preview.

## Actual

- The preview builds: Peak 36 MW, Year 289,617 MWh, In model 293,217 MWh.
- Then: "Cannot be applied to this model". "Apply to it_load" is disabled with "This network cannot take the series yet: see the warning above."
- The warning's reason is hidden behind a collapsed disclosure: "this project's data-centre load comes from its dc_load spec (the template's it_load/cooling split); change it there, not with a data-centre profile".
- "dc_load spec" is an engine word. "There" is not a link, and template-e2e issue 20 found that the DC form cannot be edited after creation. The user has no way to change the IT load.

## Expected

- For a spec-driven load, the editor says so before any work: "This load follows the data-centre set-up (IT 36 MW, PUE 1.3)", with "Change the set-up ›" to the editor that holds it.
- The archetype picker is not offered, or it is read-only.
- The refusal reason is visible text, not a collapsed disclosure, since the disabled button depends on it.

## Breaks

- RAW-NAMES-1.
- `review-ux.md`: "Disabled reasons are visible text"; "A refusal message names only actions its real caller can take".
- WALK-E2E-1 (no dead ends).

Builds on `.scratch/template-e2e/issues/20-expert-setup-not-editable-after-create.md` (on `claude/template-e2e-expert`).

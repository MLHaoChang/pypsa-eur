<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02: The staged energization refusal prints Python code text

Status: done

Done in #1693 (20950317).
Type: task
Impact: medium (misleads; the user sees program internals)
Templates: eh_datacenter_staged (New project wizard), and any API or chat caller of the same check
Found by: DC grid-readiness walkthrough, 2026-11-17 (master 9491878d)
Breaks: review-ux.md "No Python-repr quotes … in user-visible text, refusals … included"; RAW-NAMES-1; review-ux.md "Dates display ISO"

## Repro

1. Projects › New project › From template › Data Center Energy Hub (staged growth).
2. Set Backup mode to Batteries. Press Create project.

## Actual

Under IT load: "IT load: the 2027 peak is 16.7 MW (at (2027, Timestamp('2030-07-07 13:00:00'))), above the 15 MW energized import plus 0 MW of firm gensets (the whole genset fleet (no genset redundancy required)) = 15 MW".

The multi-period snapshot is printed as a Python tuple with `Timestamp('…')`. The 2027 phase is dated 2030-07-07 because the staged template reuses one modelled week, which the text doesn't explain. Parentheses nest three deep, and with no gensets in this mode, "the whole genset fleet" describes nothing.

## Expected

"IT load: the 2027 peak is 16.7 MW (2027 phase, hour 07-07 13:00 of the modelled week), above the 15 MW energized import = 15 MW; battery strings carry outages only". No code text, an ISO date or the phase year, and no genset clause when the mode has no gensets. The single-year template's text ("at 2030-07-07 14:00:00") should follow the same wording.

## Pointers

`backend/project_templates/eh_templates.py` (the `dc_load.phases` refusal text); `backend/services/adequacy/site_peak.py:80`. Fix together with 01 if one builder takes both.

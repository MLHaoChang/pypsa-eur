<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01: Backup mode "Batteries" is a dead end at the template's own defaults

Status: done

Done in #1693 (20950317); #1696 (7004431d).
Type: task
Impact: high (blocks a decision the spec offers: battery backup in place of diesel, DC 03)
Templates: eh_datacenter, eh_datacenter_staged (New project wizard, Guided and Expert)
Found by: DC grid-readiness walkthrough, 2026-11-17 (master 9491878d)
Breaks: spec feature 3 (battery-based backup) and D4; WALK-E2E-1 "no dead ends"; the form's own promise "Every value starts at the example's own default, so you can create it as it is"

## Repro

1. Projects › New project › From template › Data Center Energy Hub.
2. In the setup form, leave every value at its default (IT load 36, PUE 1.3) and set Backup mode to Batteries.
3. Press Create project.
4. Repeat with Data Center Energy Hub (staged growth), Backup mode Batteries.
5. Create the single-year template with Batteries and IT load 30 (it is accepted), open it in Expert › Scenarios & optimisation › Grid study › Backup.

## Actual

- Step 3: "1 field needs attention" and, under IT load: "IT load: the 2030 peak is 47.2 MW (at 2030-07-07 14:00:00), above the 40 MW energized import (× 0.995 efficiency) plus 0 MW of firm gensets (the whole genset fleet (no genset redundancy required)) = 39.8 MW". Nothing says what to change.
- Step 4: the staged form refuses the same way ("the 2027 peak is 16.7 MW … = 15 MW"). Its IT load is read-only ("Phases come from the build plan: change them there"), and the build plan only exists after the project is created. Batteries can't be created on the staged template at all (only by lowering the PUE, which misstates the site).
- The hint under Backup mode reads "Battery strings replace the gensets. They are sized later, in the Backup section." In fact the strings are sized at creation: the IT-30 project already has 9 strings of 5 MW. Its Backup section refuses to assess them: "This project was created with Backup mode set to Batteries, so its saved network already has the battery strings. To assess a backup, create the project with Gensets and switch the Backup mode here."

The arithmetic is right. Backup strings hold their charge outside an outage by design (`services/adequacy/dc_backup.py` docstring), so the normal-hours peak must fit the import alone: 36 MW × 1.311 (the AI-training peak per MW of IT at PUE 1.3) = 47.2 MW > 40 × 0.995 = 39.8 MW. The largest IT load that passes is 39.8 / 1.311 ≈ 30.4 MW (checked: IT 30 is accepted at 39.59 MW).

## Expected

- The refusal says why in plain words (batteries carry outages only, so the normal peak must fit the import), and names each way out with its number: IT load at most 30 MW, or Hybrid, or a larger connection after creation.
- On the staged template, either the phases can be changed in the form, or Batteries is shown as unavailable with that reason before Create.
- The Backup-mode hint is true: either the strings are sized later in Backup, or the hint says they are sized at creation. A Batteries project's Backup section can assess its own strings (the outage proof), instead of telling the user to recreate the project.

## Pointers

`pypsa-gui/frontend/src/pages/hubDesign/dcLoadForm.ts` (`BACKUP_HINT`, `DC_DEFAULTS`); `backend/project_templates/eh_templates.py` (the `dc_load` energization check); `backend/services/adequacy/site_peak.py:80`; `frontend/src/pages/DcBackupSection.tsx:139`.

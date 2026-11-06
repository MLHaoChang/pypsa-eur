<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# DC Backup card: "battery backup" wording

Status: needs-triage
Type: wording
Found by: Logged reviewing the DC cards (DC-05/DC-06); owner list DOCS-1.

## Problem

The card says "battery backup" where the model's term is the backup fleet. Align the wording with the owning panel's word map. Details: ask the coordinator.

## Where to look

`pypsa-gui/frontend/src/pages/DcBackupSection.tsx` (and `DcBackupSection.test.tsx`); `.scratch/project-workflow/design.md` also uses the phrase.

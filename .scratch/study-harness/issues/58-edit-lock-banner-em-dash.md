<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 58 — The edit-lock banner shows an em dash

Status: needs-triage
Gated: UX
Type: wording

## What is wrong

UX 25 saw an em dash in the edit-lock banner while reviewing the ieee14 Guided run shots on #1616 (@eaa78fdc). The house style keeps em dashes out of user-visible text. The banner component is `pypsa-gui/frontend/src/components/LockBanner.tsx`, but the dash may come from a string it receives; the exact text was not pinned down when this ticket was filed.

Found by UX 25 in #1616's review; not blocking there.

## Acceptance

- The edit-lock banner's visible text has no em dash, in every state it renders (someone else holds the lock, solving).
- The banner's tests assert the new wording.

## Journey fit

Every step where a project can be read-only (Guided and Expert). No hand-off change.

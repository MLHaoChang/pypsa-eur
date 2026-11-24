<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 24 Switchgear short circuit reads PASS while its Limit column is blank

Status: done
Blocked by: none
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), walk W3 (text dump `t-res.txt`).
Model: sonnet. Review: GK A + UX.

## Problem

After a campus Grid study on the data-centre template, Results › AC power flow & grid › Grid code & connection reads "Switchgear short circuit: NOT RATED (as is) / PASS (with assets)" at 25.0 kA, while the Limit column shows "— kA". `CampusElectricalPanel.tsx` (~718–725) prints `num(c.limit)` with a null limit. Either the "with assets" verdict takes a rating the row does not carry, or the row should not read PASS.

## User-visible impact

A compliance PASS is shown with no stated rating, so the user cannot check it or cite it in a TSO submission.

## Acceptance criteria

- [ ] The switchgear row carries the rating the "with assets" verdict used: the bought switchgear's kA and its source. The Limit column shows it, and the as-is column keeps NOT RATED.
- [ ] A check row with a null limit can never read PASS. The backend test and a vitest both pin this (a null limit reads "not checked" with what is missing, matching line ~645's rule).
- [ ] End to end in the real app: data-centre template › Grid study › Run study. The switchgear row shows a kA limit beside PASS, and the report's compliance table shows the same.

## Likely files

`gridspine/drivers/campus_study.py` (switchgear check), `pypsa-gui/backend/services/campus_electrical_service.py`, `pypsa-gui/frontend/src/pages/CampusElectricalPanel.tsx`.

## Comments

<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06 — Output names such as `p_nom_opt` are not in `modelNames.generated.json` and still show raw

Status: needs-triage
Type: task
Spec: [spec.md](../spec.md) §3.6
Found by: GK B3 on #1812 (non-blocking item 2)

## What

`modelNames.generated.json` holds input parameter names only. Solver output names such as
`p_nom_opt` are not in it, so the raw-name guard (02c) cannot translate them. They still
show as written in `CompareView` and `SiteOverlay`.

## To decide

- Which output names appear in user-facing text (grep `_opt` and the other result columns in
  `CompareView.tsx` and `SiteOverlay.tsx`).
- Whether to extend the generator to output names, or give them a hand-kept map beside the
  plain-name helpers.
- Whether the copy guard (`uiCopyGuard.test.ts`) should then cover these two surfaces.

## Acceptance

- No raw output name reaches a Guided or Expert surface without a plain name, or it has an
  allow row whose `fix` is this file.

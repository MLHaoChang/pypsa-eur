<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Duplicate issue numbers in four feature folders (backlog hygiene)

Status: needs-triage
Source: docs sweep 2026-11-21. Do not renumber: file names and PR bodies already cite these numbers. Triage decides which
file keeps the number or whether a suffix (`13a`, `13b`) is added.

Two files share one number in each case below (`.scratch/<folder>/issues/<file>`).

- **grid-compliance 13**
  - `13-genset-in-strength-fault-level.md`
  - `13-pack-manifest-ratings.md`
- **template-e2e 20**
  - `20-expert-setup-not-editable-after-create.md`
  - `20-study-size-not-shown.md`
- **template-e2e 21** (also duplicated)
  - `21-open-project-slow-and-session-noise.md`
  - `21-verdict-text-at-resolution-floor.md`
- **study-harness 50**
  - `50-keep-lp-frames-after-ac-pf.md`
  - `50-run-scope-cap-admits-non-finite-budget.md`
- **study-harness 44** (also duplicated)
  - `44-gates-table-rule-words.md`
  - `44-offer-eh-site-v3-to-start-task.md`
- **assistant-assurance 12**
  - `12-acpf-raw-convergence.md`
  - `12-summary-cap-binding-null.md`

## Acceptance

- Each duplicate pair is resolved by the owner's chosen rule, and every in-repo reference (`Blocked by:`, PR bodies, the
  README of the feature) is checked before any rename.

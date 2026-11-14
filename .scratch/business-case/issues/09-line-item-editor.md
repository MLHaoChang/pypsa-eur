<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 09 — Line-item table editor

Status: ready-for-agent (blocked by 07, 08; Opus; UX plan first, GK B for figures)
Type: task
Blocked by: 07, 08
Gated: yes (UX; GK B for the figures)
Model tier: opus
Size: M

Read [the spec](../spec.md) §4, §7, `docs/agents/review-ux.md` and
`.scratch/project-workflow/design.md` first. Send the UI plan to UX before code.

## What to build

In the "Business case" view on the Investment tab, the line table: one row per
line, the S columns as selects (with only the units the source allows), the T
columns as typed cells, the per-year grid as an expanding row with paste from a
spreadsheet (BCA-SCOPE-1 Q2), "add line" with the offered rows, "apply preset", the locked
"from the energy model" rows (06) beside the table, provenance badges (stated /
assumed with source on hover), revenue cells empty and marked not established, the
hint text per BCA-SCOPE-1 Q1, and every refusal shown on its row and column (G9).

## Tests

Vitest for the table (select options per source, refusal placement, paste);
Playwright for apply-preset → edit → save; UX shots.

## Comments

### 2026-11-11: triage

Checked on master: `services/finance/business_case.py` and `project_templates/business_case_presets/` do not exist; 01 and 02 are done (#1322, #1320); business-case 04 is in progress in another session and was left alone. Verdict: ready-for-agent, held by its Blocked by line. Likely files, tests and acceptance are in the body.

### 2026-11-14: plan GO (GK B 56, UX 20)

UX ruling: the editor lives in the Business case view on the Investment tab (DCBC Q3); the
lines are not solve inputs, so design.md's "no price edited in Results" does not apply.
One home only: the Guided question (14) reaches the same table. UX conditions 1–10 (the
"Finance inputs ›" link through the mode-aware opener, units in words, a visible chip key,
refusals opening with the column label on their cell, visible disabled reasons, a sticky
Name column with a visible scroll, confirm with the count before a preset replaces lines).

GK B conditions:
- O1 one rule: the options route's `units_by_source` and the validator's `unit_not_allowed`
  read the same function (`models.business_case.allowed_units`); a route test in both
  directions (offered units save clean, the others are refused on `lines[i].unit`).
- O2 fail closed: no network, no current solve or an unreadable catalogue gives
  `outputs: null` with a reason, never `[]`; a lock timeout is 409 `network_busy` after a
  bounded wait.
- O3 at the trial merge: test_route_chat_disposition, test_project_route_acl_policy,
  test_write_surface_lock_policy, test_golden_coverage, assistant_route_parity and
  assistant_control_matrix `--check`.
- O4 units pinned against `OutputSpec.units` of the real catalogue; eh_site, eh_datacenter
  and the no-template case.
- O5 (10) figures as the report gives them; not_established / n/a / None show the reason,
  never 0; a test pins that no client-side sum appears.
- O6 the 09 PR is GK B's; the 10 PR goes to UX for screens and GK B for the data wiring.

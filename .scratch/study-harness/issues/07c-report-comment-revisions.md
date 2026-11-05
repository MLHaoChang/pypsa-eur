<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07c — Report comments become revisions (HH-08A)

Status: ready-for-agent
Type: task
Blocked by: 07
Gated: yes (GK B)
Model tier: opus
Owner decisions: HARNESS-970-1
Folded from: #970 HH-08A, through issue 07's "Folded from #970" item 2 (coordinator decision under HARNESS-970-1: filed as its own issue)

## What to build

A user reads the hand-off report and comments ("shorten the grid section",
"show the BESS duration in hours"). Keep each comment as a durable item until
it is addressed or withdrawn.

- Edits are a closed set: report paragraph or bullet text, workbook text and
  number formats.
- An edit preserves the evidence references and disclosures, and never
  changes a number, formula, chart range or source assumption.
- An unsupported comment stays open with its reason.
- A revision is a new copy against the saved report's digest; a stale base is
  refused.
- A revision that needs a model call goes through issue 24's reservation.

Build on issue 07's harness chapter and revert preview and on the reports
service (`pypsa-gui/backend/services/reports/`). Recheck the design against
master first: #970 is pinned at #944. Numeric repairs (#970's 08B and 08C)
stay out.

## Acceptance criteria

- [ ] A comment is stored durably and stays open until addressed or withdrawn.
- [ ] An addressed comment yields a new report copy whose numbers, formulas, chart ranges, evidence references and disclosures equal the base's.
- [ ] A comment outside the closed edit set stays open with its reason.
- [ ] A revision against a stale digest is refused.
- [ ] A model call for a revision is reserved through issue 24's `send_reservation`.

## Likely files

`pypsa-gui/backend/services/reports/` (a revisions module), `pypsa-gui/backend/routers/reports.py`, tests beside them

## Comments

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

### GK B (37): plan verdict, GO WITH CHANGES (R1–R10, verbatim in substance)

Plan by SH-07c (session_01G7qCukZnvJHLm2tCfWuFy3), 2026-11-08. Master has no saved report workbook, so the closed set's "workbook text and number formats" maps to the report's own `Table` cells, columns and caption. There are no stored formulas; chart ranges are `figures`/`FigureRef`.

- Q1. Model drafts are NO-GO in this PR. Standing rule (SH-07): a report job is tied to a harness run only by the harness's own explicit launch; a route never infers a run from state, and a human request is never charged to a run's budget.
  - R1: this PR ships explicit edits only, with no `draft` flag and no model call.
  - Drafting comes later as its own slice: (C) a runner stage the harness launches, or a draft charged to the user's own session budget, never a run's.
  - Whichever slice drafts, a draft is a proposal the user confirms, bound to its digest, and never applied on its own.
- Q2. Value-preserving re-spelling only; no unit conversion and no rounding.
  - R2: one test per allowed re-spelling (thousands separator, trailing zeros, unit spacing or suffix).
  - One test per refusal: rounding, 240 min → 4 h, 0.2 → 20%, a sign change, and a number moved to another block with the totals equal. Use real units (MW, GW, h).
- Q3. Routes only, no assistant tool.
  - R3: each new route is declared in the chat-disposition manifest as having no assistant tool.
  - At the trial merge, run test_route_chat_disposition, test_project_route_acl_policy and test_write_surface_lock_policy, plus route_parity and inventory `--check`.
  - GET needs read access; POST, withdraw and address need write access (the report-save role).
- R4. Re-audit, never copy. The revision's section audit is recomputed on the new text by the job's number audit and must equal the base's. Mutant: copying the base audit must die.
- R5. One named per-report lock covers the base_digest check, the anchor_sha check, save_version and the comment update, and is shared with every other writer of `reports/<id>/`. A two-thread test gives exactly one v<N+1>; the other address gets 409 `revision_stale_base`.
- R6. Each invariant part has its own killing case, and each refusal reason is asserted by name. The parts: number multiset, block types, callouts, audit, source_paths, the figures dict, evidence_hash and table shape.
- R7. Provenance. v<N+1> records `revised_from {version, digest}` and the comment id, and the sealed export's manifest names it. An unrevised version file stays byte-equal, shown by a pin test.
- R8. `comments.json` is read strictly:
  - version and shape are checked;
  - a malformed or wrong-version file is a typed refusal, never a 500 or an empty list;
  - an anchor must name an existing block of the type the edit needs;
  - reload durability is tested, and the bundle export and import carry the file.
- R9. Withdrawn and addressed are terminal (409). A refused address stores its reason and stays open.
- R10. mutants.py on revisions.py, plus hand mutants, each with its killing test named in the PR body:
  - drop the digest check;
  - drop the anchor check;
  - each invariant part;
  - accept a rounded re-spelling;
  - allow an edit outside its own block;
  - let a terminal comment transition.

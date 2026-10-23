<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

Status: ready-for-agent — gatekeeper reviews the plan first (a new report seam; E1a's condition 3); written 2026-10-23

# Outsourced task L: Reports list the project's template ledger in an assumptions appendix (E1)

Branch: `outsource/report-template-ledger` · one PR against `master` · PR body starts with `Outsourced task L`

**Gated.** Before any code, open a draft PR whose body is your plan, and wait for `GK OK` on the plan. See "Plan to send first".

## Read first

1. `AGENTS.md` (repo root), and `docs/agents/review-gk.md`.
2. `.scratch/project-workflow/issues/E1-report-template-ledger.md`, the issue. Read all of it, including Acceptance.
3. `.scratch/project-workflow/issues/E1/e1a-pr-body.md`, what E1a (PR #540, merged) already does for placeholders in the flow, the peak check, the scorecard and the investment case.
4. `pypsa-gui/backend/services/reports/chapters.py`, whole file (l.1-330 or so). It holds the three chapters the report already has (`INVESTMENT`, `ISLAND`, `GRID_READINESS`, l.22-30) and, for each, a `*_payload`, a table builder and a `*_disclosures` function. The grid-readiness chapter (l.249-330) is your model: it is the smallest, and it was added the same way.
5. `pypsa-gui/backend/services/reports/evidence.py`:
   - `SectionKind` (l.69) and `SECTION_TITLES` (l.72-100);
   - `_chapter_sections` (l.701-731), `_chapter_tables` (l.734-747), `_chapter_disclosures` (l.750-762);
   - `collect_evidence` (l.765-824): the `grid_readiness=` keyword and how the evidence hash stays unchanged when a source is not passed.
6. `pypsa-gui/backend/services/reports/assemble.py`:
   - `CATEGORY_ORDER` and `_CATEGORY_OF_SECTION` (l.88-97). The appendix belongs to no category, like the pipeline;
   - `_chapter_section` (l.355-378) and the order in `evidence_only_document` (l.401-430);
   - `_grouped` (l.113-125): a section with no category sorts last.
7. `pypsa-gui/backend/routers/reports.py`, `_current_chapter_sources` (l.334-391). It builds the keyword sources for `collect_evidence`. `routers/report_jobs.py:130-165` (`_evidence_for`) calls it too. A new source goes in this one function, and both routes get it.
8. `pypsa-gui/backend/services/flow_service.py`, `read_template` (l.247-256). It reads `eh_template.json` as a dict, or `None`. Use it. Do not add a second reader and do not import from `services/adequacy/grid_readiness.py` (another session owns that file).
9. The ledger rows themselves: `project_templates/eh_templates.py`, `_placeholder_ledger` (l.921-955) writes `item`, `label`, `value`, `unit`, `source`, `note`. Other rows (`project_templates/eh_ammonia.py` l.219-250) carry only `item`, `value`, `source`, `note`. `growth_inputs` rows have `kind`, `year`, `set`, `example_value`, `value` (`eh_templates.py` l.898-960).
10. `pypsa-gui/backend/tests/test_report_grid_readiness.py` and `tests/test_report_chapters.py`. Your tests follow their style. `tests/test_report_roundtrip.py` and `services/reports/roundtrip.py` for the docx round trip.
11. The decision-study report, for the study seam: `services/study/report.py` `SECTIONS` (l.78-89, section `assumptions`) and `render_docx.py` l.167-172 (the assumptions table). This is a separate pipeline with its own `AssumptionsLedger`.

## Goal in plain words

E1a made placeholders visible where a user acts on them. No report reads the template ledger yet, so a report can still hide the fact that a staged example took placeholder growth values. This task adds an assumptions appendix that lists the project's template ledger rows, and the unset growth placeholders by name.

## The report seams (named)

The code has two report pipelines, not three. The issue's "adequacy, grid-readiness and study reports" map like this:

- **Pipeline 1, `services/reports/`** (adequacy, Energy Hub reference design, investment case, island and grid-readiness chapters, one `ReportDocument`). One new section covers both the adequacy and the grid-readiness reports. It is a new chapter, `template_ledger`, built exactly the way `grid_readiness` is:
  - `chapters.py`: `TEMPLATE_LEDGER` id, a `TITLES` entry ("Template assumptions"), `template_ledger_payload(meta)`, `template_ledger_table(payload, source_path)`, `template_ledger_disclosures(payload)`, and a `NO_TEMPLATE_LEDGER` message ("no template ledger for this project");
  - `evidence.py`: add `"template_ledger"` to `SectionKind`, a `SECTION_TITLES` entry, a `template_ledger=` keyword on `collect_evidence`, and one branch each in `_chapter_sections`, `_chapter_tables`, `_chapter_disclosures`;
  - `assemble.py`: build the section with `_chapter_section`, add it before the pipeline section, and give it no category (`section_category` returns `None` for it, like the pipeline). Add it to the `notes = {...}[section_id]` dict at l.374;
  - `docx_writer.py`: the `SECTION_TITLES` copy at l.80-91 gets the same entry;
  - `routers/reports.py`: `_current_chapter_sources` reads `read_template(project.directory)` and passes `template_ledger=` when `eh_template.json` exists; nothing for a project without one, so its evidence hash does not move.
- **Pipeline 2, `services/study/`** (the decision-study report). Its `assumptions` section shows the study's own `AssumptionsLedger`. State in the plan whether a decision-study project can carry `eh_template.json` at all. If it cannot, this pipeline gets one line ("no template ledger") and no code. If it can, append the rows as a second table under the existing "Assumptions table" in `render_docx.py` (l.167-172) and the HTML renderer. Do not change `AssumptionsLedger` or `run_hashes`.

## What the appendix lists

- Every row of the ledger: label (else the `item`), value, unit, source (`assumed`, `synthetic`, `sourced`, `entered`), note. A row without a unit shows a blank, never "0". Use `services/reports/formatting` (`F.fmt_text`, `F.NOT_ESTABLISHED`) as the grid-readiness table does.
- The unset growth placeholders by name: every `growth_inputs` row with `set: false`, shown by the label of its `placeholder <kind> <year>` ledger row (for example "IT load 2038 (placeholder)"). They come first.
- A project with no ledger shows "no template ledger".
- A project created without `planning` shows today's report plus the ledger rows and no placeholder rows.

## Acceptance tests (write first; each fails before the code)

Under `pypsa-gui/backend/tests/`:

1. `test_report_template_ledger.py` (new), in the style of `test_report_grid_readiness.py`:
   - a staged DC created with entered years and no `dc_load` (E1a's builder; see `tests/test_e1_planning_staged_dc.py` and `tests/test_e1b_planning_staged_hubs.py` for how they make one) lists one row per placeholder, named "IT load 2038 (placeholder)" or its siblings, before the other rows;
   - a project without `planning` lists the ledger rows and no placeholder row;
   - a project with no `eh_template.json` gets a "no template ledger" section;
   - `evidence_hash` is unchanged for a project whose `template_ledger` source is not passed (compare with and without the keyword, as `test_report_grid_readiness.py:49` does);
   - the section sorts after the categories and before nothing else but the pipeline.
2. A docx round trip: write the evidence-only report to docx and read it back with `services/reports/roundtrip.py`. The appendix survives, and a comment on it lands in the right section (`tests/test_report_roundtrip.py` shows the call).
3. A route test through `routers/reports.py` (`create_report`) and `routers/report_jobs.py` (`_evidence_for`) with an `eh_template.json` project: both include the section.
4. If pipeline 2 gets code: one test on the docx render and one on the HTML render.

Existing report tests (`test_report_assemble.py`, `test_report_chapters.py`, `test_report_grid_readiness.py`, `test_report_docx_writer.py`, `test_report_docx_reader.py`, `test_report_roundtrip.py`, `test_report_roundtrip_routes.py`, `test_chat_report_tools.py`) must pass unchanged. A snapshot or section-count test that this section legitimately moves may be updated; list each in the PR body.

## Plan to send first (the gatekeeper's questions)

In the draft PR body:
- the pipeline 1 changes, file by file, as above, and your answer on pipeline 2;
- the row shape you accept (which keys are required, what a row without a label or unit shows);
- how the placeholder rows are found: by `growth_inputs` `set: false`, or by the `placeholder <kind> <year>` item name, and what happens if the two disagree;
- the evidence-hash effect for existing projects (none expected);
- the test list.

## Files it may touch

- `pypsa-gui/backend/services/reports/chapters.py`, `evidence.py`, `assemble.py`, `docx_writer.py`.
- `pypsa-gui/backend/routers/reports.py`: `_current_chapter_sources` only.
- `pypsa-gui/backend/services/study/report.py`, `render_docx.py`, `render_html.py`: only if the plan says pipeline 2 gets code, and only the assumptions section.
- New test files above, `doc/release-notes.d/outsource-report-template-ledger.md`, and the `Status:` line of the E1 issue.

## Files it must NOT touch

- `pypsa-gui/backend/services/adequacy/grid_readiness.py`, `grid_limits.py`, `gridspine/**`, `data/grid_codes.yaml`.
- `project_templates/*`: the ledger is read, not changed. E1a owns how rows are written.
- Profiles, carrier KPIs, curtailment, navigation files and `harness/**`.
- `services/reports/roundtrip.py`, `docx_reader.py` (read them; change only if the round-trip test proves a gap, and say so in the plan).
- Any frontend file. `doc/release_notes.md`.

## Review

The engine gatekeeper reviews the plan, then the PR (`GK OK @<sha>`). No UX review: the appendix is report content, not an app screen.

## Repo rules

- Branch `outsource/report-template-ledger` from current `master`. One PR. Never force-push. Do not merge it; the coordinator merges.
- PR body starts with `Outsourced task L`, then follows `.github/pull_request_template.md`. Mark it AI-generated.
- Release-note fragment `doc/release-notes.d/outsource-report-template-ledger.md`: `* Feat(pypsa-gui): Reports carry a template-assumptions appendix listing the project's template ledger, with unset growth placeholders named.`
- Do not skip, loosen or delete any existing test.

Commands (repo root; `pixi install --frozen -e default -e test` once):

```bash
pixi run gui-tests tests/test_report_template_ledger.py
pixi run gui-tests tests/test_report_assemble.py tests/test_report_chapters.py tests/test_report_grid_readiness.py tests/test_report_docx_writer.py tests/test_report_docx_reader.py tests/test_report_roundtrip.py tests/test_report_roundtrip_routes.py tests/test_chat_report_tools.py
pixi run gui-tests   # full backend suite before opening the PR
```

## Done when

- [ ] `GK OK` on the plan is in the draft PR before the first code commit.
- [ ] The tests above pass, and each new one fails on `master`.
- [ ] The existing report tests pass, apart from any listed legitimate update.
- [ ] A project without `eh_template.json` has the same `evidence_hash` as on `master`.
- [ ] The diff touches only the files under "Files it may touch".
- [ ] The full backend suite is green, or the PR lists each failure that also fails on `master`.

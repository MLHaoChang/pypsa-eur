<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Whole-application capability, parameter and navigation coverage

Status: ready-for-agent
Type: task

Read [the plan](../../../docs/superpowers/plans/2026-10-10-live-voice-conversation.md)
and [coverage contract](../application-coverage.md). Generate a matrix from tools,
routes, service schemas and GUI bindings. The [inventory](../capability-inventory.json)
records 220 tools, 14 result tabs and 17 panels; behavioral/control mapping is pending.
Map every eligible tool, panel/subview/filter/editor, parameter type, job adapter,
evidence section, import/export/report operation and fixture. Resolve AR against
actual UI/module vocabulary. Fix adequacy/fmea navigation parity and audit other
aliases. Add missing shared typed tools over services. Respect ACL/mode/project
eligibility; no browser-click or provider-only workaround.

Test discovery and subsequent schema offering/execution for every eligible tool
under OpenAI's current 128-tool offer bound and verify Claude parity. Test all
navigation acknowledgments and parameter types/units/previews/freshness.
Keep matrix regeneration/check in CI. Done when every required interaction has a
mapping and passing contract, every family has a real journey assigned to issue
05, and remaining limitations block the full-coverage claim explicitly.

## Comments

2026-10-10: First TDD slice implements missing tab/panel navigation, rejects invalid
inputs, proves provider-neutral discovery/offer/continuation and adds regeneratable
source inventory plus CI drift checks. See [handoff](../session-handoff.md) for
red/green commands/counts. Editable-control mapping, eligibility-aware UI targeting
and all-family behavioral fixtures remain open; this issue is not complete.

2026-10-11: Control/parameter matrix slice landed. `smoke/assistant_control_matrix.py`
reads CreationForm `FIELD_MAP` + `attributeCatalog` editability/closed sets without
site packages; writes/checks `.scratch/live-voice/capability-matrix.json` (165 creation
controls, 3 editability overrides, 3 closed sets). AR maps to Asset Reliability /
adequacy+FMEA inventory IDs (not a invented module). All 14 coverage families have
`pending:issue-05-*` journey assignments. CI:
`.github/workflows/assistant-control-matrix.yaml`. Tests:
`tests/test_assistant_control_matrix.py` (9 passed). Remaining for issue 08 close-out:
behavioral fixtures on issue 05, any still-missing typed tools over services, and an
explicit full-coverage limitation block — do not mark the whole-application claim done.

2026-10-11: Full-coverage limitation block landed. `capability-matrix.json` now carries
`full_coverage` (`claimed: false`, `status: not_established`, 14 of 14 family journeys
pending, one blocker per `pending:issue-05-*` journey plus standing blockers for pending
behavioral fixtures, unaudited UI-control/tool parity and hosted gates), and
`application-coverage.md` has a matching "Full-coverage limitation block" section.
`assistant_control_matrix.py --check` fails if the matrix claims coverage while any journey is
pending or any blocker remains, or if `journeys_pending` drifts; passing journeys alone do
not flip the claim because standing blockers need an explicit edit. Tests:
`tests/test_assistant_control_matrix.py` (17 passed, 8 new). Issue 08 stays `claimed`: it is
done only when issue 05 journeys pass and the blockers are cleared with evidence.


2026-10-27: Slice A (GUI-route to tool parity) landed. `smoke/assistant_route_parity.py` scans the GUI
with the TypeScript checker (`frontend/scripts/api-bindings.mjs`), matches every binding to a
flattened app route and writes/checks `.scratch/live-voice/route-parity.json`: 466 GUI routes,
280 tool, 102 needs-tool, 84 limitations (closed reasons), 0 unclassified. `capability-matrix.json`
carries a `route_parity` summary and one `route-parity:needs-tool:<family>` blocker per family.
CI: `.github/workflows/assistant-route-parity.yaml`. Tests: `tests/test_assistant_route_parity.py`.
Slice B adds typed tools for the needs-tool rows; slice C does the rest and the issue-05 journeys.

2026-10-27: Slice B-campus landed. The last 31 needs-tool rows (gridspine-campus) are covered:
24 new tools calling their route handlers in-process (campus file, MILP status/start/cancel,
grid-readiness limits, island report/cuts/GFM card, library reset, gridspine external dispatch and
readback from uploads, and the 10 grid-code document/draft/confirm/publish/delete tools), and 7
routes matched to existing tools once the parity scan follows `run_in_threadpool(service, ...)`.
Owner decision GRIDCODE-CHAT-1 supersedes plans C10 and C12: confirm, publish, the deletes and the
MILP start show a content card first (`harness/campus_cards.py`, `edit_card.CARDED_ACTIONS`); the
campus file and grid-code drafts are whole-file writes that refuse a stale read
(`file_changed_since_read`). route-parity.json: 0 needs-tool of 466 GUI routes. Tests:
`tests/test_lv08_bcampus_tools.py`, `tests/test_assistant_route_parity.py`. Slice C (behavioral
journeys on issue 05) remains; the full-coverage claim stays `not_established`.
GK B plan-delta conditions a–f: every campus card's receipt reads the effect back (stored sha256,
the limit's `code` source, the profile or document gone, the new MILP job and its basis settings),
so a missing effect is `uncertain`; delete, publish, confirm and the MILP start say what they do in
words; a file moved after the card is `edit_preview_stale` (also when a whole-file base check trips
at confirm); both saves run the service's own YAML checks at preview (`check_campus_text`,
`check_draft_text`); the extract card names the extraction provider call.

2026-10-29: R1/R2 plan, GK B GO with conditions (session_01G7qCuie2cD7SpMavjY6YZ4). Two PRs, R1 first.
Coordinator (owner option 1): the five Site 3D scene routes are held for SC-07 under a new closed
reason `held-by-spec`; R2 builds `get_process_view` and the site-anchor GET/PUT tools (PUT behind a
content card, GRIDCODE-CHAT-1).
- R1: trial-merge with #891 and #892 heads and master; run `test_assistant_control_matrix` and
  `assistant_control_matrix --check`. `_full_coverage_problem` never reads `FAMILY_JOURNEYS` (a
  mutation reading it fails a test). An unavailable variant is not a blocker, but its pointer resolves.
- `held-by-spec`: the `issue` pointer names an existing file containing the route's method and path;
  the guard fails when that issue's `Status:` is done or wontfix; each held row adds
  `route-parity:held:<issue>` to the full_coverage blockers and counts as a limitation, never a tool.
- R2 removes only the needs_tool rows it closes (a fragment file only when left empty). #853's
  `GET /api/profiles/schema` row: whichever lands second carries the fix; master never goes red.
- `get_process_view`: the route's project/ACL resolution, read tier; hand oracle, available:false and
  a not-the-caller's-project refusal.
- `set_site_anchor`: carded, through `anchor.write_anchor` only; the digest binds project,
  expected_revision and the new lat/lng/source/confirmed; `validate_anchor` at preview and inside the
  confirmed write; `_check_project_lock` and `_may_write` at confirm; a moved anchor is
  `edit_preview_stale` and writes nothing; the receipt reads back the stored row. Mutations: no
  revision binding fails the stale test; no confirm-time validation fails a tampered-args test.
- New error kinds only in `tool-error-kinds.d/`. R2 trial merge runs the listed route/ACL/lock,
  catalogue, prompt, selection and kind-manifest tests plus the inventory, route-parity, control-matrix
  and json_fragments checks; any exact-count pin changed is named in the PR body.

2026-10-29: R2 built (session_01G7qCukn8KpPCqXtzidyFhy, stacked on R1 #906). `get_process_view` (read;
the route's foreground project, a foreground project the caller may not read is 404 `not_authorized`),
`get_site_anchor` (read) and `set_site_anchor` (write, carded in `harness/edit_card_site.py`: through
`anchor.write_anchor` only; the digest binds project, stored revision, expected_revision and the
validated anchor; a stale expected_revision is `file_changed_since_read` before any card and
`edit_preview_stale` at confirm; the receipt reads the stored sidecar). The five scene routes are
`held-by-spec` limitations naming SC-07 (`.scratch/site-scene-generation/issues/08-ai-guided-project-flow.md`,
which now lists them); `assistant_route_parity.py --check` fails on any needs-tool row and on a held
route whose issue is missing, does not name it, or is done/wontfix. route-parity: 0 needs-tool.
Tests: `tests/test_lv08_r2_tools.py`, `tests/test_assistant_route_parity.py`.

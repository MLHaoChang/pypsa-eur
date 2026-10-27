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

2026-10-27: Slice B-campus (in progress). 21 of the 31 gridspine-campus needs-tool routes covered:
14 new tools calling their route handlers in-process (campus file, MILP status/start/cancel,
grid-readiness limits, island report/cuts/GFM card, library reset, gridspine external dispatch and
readback from uploads), and 7 routes matched to existing tools once the parity scan follows
`run_in_threadpool(service, ...)`. Remaining: the 10 grid-code draft/confirm/publish routes, pending
the owner's decision on plan C10 (`not_via_chat` `grid_code_publish`). Tests:
`tests/test_lv08_bcampus_tools.py`, `tests/test_assistant_route_parity.py`.

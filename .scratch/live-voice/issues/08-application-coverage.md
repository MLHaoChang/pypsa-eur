<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Whole-application capability, parameter and navigation coverage

Status: ready-for-agent
Type: task

Read [the plan](../../../docs/superpowers/plans/2026-10-10-live-voice-conversation.md)
and [coverage contract](../application-coverage.md). Owner decision LV08-SHRINK-1
(29 Oct) shrinks this issue to the remainder below. LC-06 now builds the behavioural
family journeys with hand oracles (`smoke/family_manifest.py`, `tests/test_lc06_*.py`);
the requirement-by-requirement comparison is in
[08-lc06-comparison.md](08-lc06-comparison.md). Do not build slice C's solver,
reliability, gridspine or experiment journeys again.

The matrix, route parity, navigation parity, typed tools and the 128-tool/Claude offer
test are done (see Comments). Open scope is R1–R4 only:

- **R1. One ledger.** Replace the 14 `pending:issue-05-*` journey ids in
  `assistant_control_matrix.FAMILY_JOURNEYS` by references to the LC-06 manifest variants,
  and make the `full_coverage` guard read variant status from `family_manifest`.
  The claim stays `not_established` until the blockers clear.
- **R2. Route-parity drift.** 8 GUI routes read `needs-tool` again: `GET /api/results/process_view`,
  `GET`/`PUT /api/projects/{name}/site-anchor` and the four `sites/{site_id}/scene` routes
  (adopt, proposals, apply, cancel). Close each by a typed tool or a declaration
  (`assistant_route_parity_declarations.d/`), coordinating with the packages that added the routes.
- **R3.** Families LC-06 leaves out (D01/02/20–24) moved to
  [LC-32](../../live-copilot/issues/32-uncovered-families-d01-d02-d20-d24.md) (needs-triage).
- **R4. Voice-only gates stay with issue 05:** hosted profile/device/latency, the prolonged
  mixed-domain conversation, spoken navigation acknowledgements and client-only UI-control parity
  (`ui-local-control-parity-unaudited`).

Done when R1 and R2 are merged with their checks in CI, R3 has an owner, and R4's blockers
remain explicit in `full_coverage`.

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

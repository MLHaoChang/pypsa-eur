# LC-03 — generic native evidence and navigation adapters

Status: ready-for-agent
Type: task
Baseline: master `c75ca5584b873418f10f1bca42f868255d445b7b`
Dependencies: LC-01/02 and current native evidence stores

Read the [specification](../spec.md), [assessment](../gap-assessment.md),
[acceptance matrix](../test-plan.md) and
[implementation plan](../../../docs/superpowers/plans/2026-10-18-whole-application-live-copilot.md).
Applicable contracts/cases: LC-C3/C4/C7; LC-T06/07/08/09/10/19.

Domain cases: applicable [LC-D04–18](../domain-coverage.md), including every
distinct engine's units, fidelity, uncertainty and unsupported-claim boundaries.
Shared UI/chat/export facts must also pass a separate independent oracle.

Create typed common adapter interfaces and add one family per PR. Persist required immutable historical sections on completion; use matching UI/API/chat/export facts and source ACKs. Reuse existing selection actions. Legacy missing evidence refuses; never restore historical inputs or use current results for another run.

Expected ownership: run_evidence, native completion publishers, navigation, results DTOs/pages; domain-specific adapters reviewed separately.

Rebaseline current master and incoming interfaces before implementation. Write
one meaningful public-seam RED case, then implement that slice and regress its
neighbors. Record actual source/commands/results and remaining gates here.
No new capability is enabled by this issue. Paid calls, reviewer contact,
deployment and full-coverage/domain-approval claims remain outside this package.


## Build plan (2026-10-19, session commissioned by the coordinator)

Rebaselined on master `14a49dd` (PR391 merged). PR402 (LC-10), PR404 (LC-15)
and the OJ-45 branch are open and owned by another session; nothing here
edits their files' changed regions. Three PRs, in this order:

**PR-A, VS-1 (OJ-01, OJ-09 load-flow half).** Branch `claude/copilot-results-registry`.
1. `services/result_views.py`: the typed registry. One frozen `ResultView` per
   results tab in `RESULTS_TAB_ENUM`, plus gated non-tab views (the
   profile-generator reads of PR377: an entry goes live only while its tools
   are in the catalogue). Each declares: view id, panel and tab, Expert and
   Guided labels (None when the mode lacks it), family, its read(s) as exact
   tool calls, display verification (`displayed_data` only for load flow
   today, else `mounted_view`), historical section (None until captured),
   units/claim limits, entity or multi-period requirements. A test fails when
   a tab is added without a registry entry and read.
2. Latest run = current results. `get_loadflow_summary(run_id=…)` returns the
   current summary bound to that run when it is the newest completed run, its
   input fingerprint equals the current inputs and the results are fresh;
   otherwise it stays unavailable and says whether the inputs changed since.
   Never current values for another run.
3. Ordinary-turn ACKs: the loop issues the existing v1/v2 ACK in ordinary
   turns too; a client that declares it can ACK gets a bounded server wait
   (no model polling) and the tool result says `display.verification` and
   `say: opened|showing`. The frontend ACKs in every turn, not only strict.
   No new ACK vocabulary.
4. New tools-on prompt fragment (pinned fragments unchanged): read before you
   show; name the run; one short paragraph of key points from the read; say
   "showing" only with a displayed-data ACK; offer the next section.
5. Tier A: `oj-grid` (3 buses, L1/L2 300 MVA, T1 100 MVA, Gas 100 MW, Solar
   80 MW, Battery, H2 tank, 24 h) solved twice by real HiGHS through
   `run_simulation`; hand-calculated oracle beside it; OJ-01 over four wires;
   OJ-09's "show me its load flow" half.

**PR-B, VS-2 (OJ-02, OJ-03, OJ-15, OJ-16, OJ-18 interim).**
`list_result_views` (read tier): the registry projected on the active
project — per view the data status from its read (no values), whether this
conversation already showed it, the read to call and its verification
level. Per-session shown tracking from issued results navigation. Prompt:
walk control (next/skip/stop, manual navigation ends it), "something else",
OJ-16 deltas only from `compare_project_runs`, and Q5's interim answer.

**PR-C, retained load flow.** At foreground-run completion publish a bounded
`loadflow` section (the same oracle) into the immutable manifest, so
`get_loadflow_summary(run_id)` answers for older runs; legacy runs without it
refuse with the exact run ID. Other families follow one adapter per PR.

### PR-A self-review (mp-code-review, 2026-10-19) and dispositions

Fixed:
- "showing" could describe data other than the read: the v2 ACK binds the
  tab's default view (LOPF, full window), while a read can use another
  source or window. The ordinary navigation binding now records the turn's
  load-flow read; `display.say` is "showing" only when the shown source and
  window equal it, and otherwise returns `differs_from_read`. The prompt no
  longer asks for source/window arguments `ui_open_panel` doesn't take.
- `run_is_current` and `inputs_changed_since_run` are now top-level on every
  run-bound answer; an unknown fingerprint is `null`, not `false`; a
  run-bound refusal drops the "read the current results" hint; no-network guard.
- An in-turn server-issued project switch rebinds the navigation binding and
  forgets earlier reads (OJ-09 now asserts "showing" on the copy).
- Units declared per view; the Tier A narration check reads the streamed
  answer; the browser runner solves twice, checks the answer names the second
  run, and checks MW/MVA in the rendered table.

Kept, with reasons:
- `generate_renewable_profile` / `apply_generated_profile` are writes, not
  result views; LC-04 cards them. The other four PR377 tools are declared.
- The registry has no production caller until PR-B's `list_result_views`.
- Prompt-cache: `results_walk` is a new static tools-on part placed after the
  stance, as LC-10 placed its part; the cache refills once per deploy.

### PR-B self-review (mp-code-review, 2026-10-20) and dispositions

Fixed:
- A tab the user opened themselves (the turn's `ui_context` results tab,
  validated against the registry) now counts as shown and is returned as
  `on_screen`, so the walk does not re-offer it (manual navigation ends it).
- Tests added: go back; each walk step quotes only its own read (streamed
  text); user-opened tab; declared statuses; reset forgets shown sections.
- `shown_results` is cleared by `reset_history` and by a rewind that cuts a
  turn; the panel/tab check moved into `note_shown`.

Kept, with reasons:
- "Never a tab with no data" is a listing fact plus the prompt rule, not a
  refusal: a user may ask to open an empty tab, and ui_open_panel must still
  open it. The probe counts any non-empty, non-refused read as data.
- Per-tab reads differ from issue 11 item 1 where the issue's read needs an
  argument the registry cannot fix: summary reads `get_results(statistics)`
  and `cost_breakdown` (not `get_project_results_summary(name)`), investment
  reads `get_investment_case` (not `explain_investment(class, name)`).
- `shown_results` is not hydrated after a restart; a walk may then re-offer
  a section, which errs on showing, never on hiding.
- Guided lists Guided tabs first with their labels; other tabs are listed as
  "(advanced)" and open without a mode switch (as the app already does).
- Each listing runs one read per view (statuses only); acceptable for the
  network sizes Tier A covers; revisit if a large network is slow.

### PR-C self-review (2026-10-20) and dispositions

Fixed:
- A past run is not recomputed: a request for another source or window
  refuses with `historical_scope_mismatch` and the captured source/window;
  `limit` trims the retained ranking.
- Retained answers carry an allow-list of the summary's own facts; current-
  only pointers (ui_reference, evidence_token, freshness) never pass.
- The solved-input fingerprint is taken before the capture; if the inputs
  moved during it, no section is stored. A failed or skipped capture is
  logged and recorded as the manifest warning `loadflow_not_captured`.
- The refusal no longer points at issue 06 or invites reading the current
  results; tests cover missing, corrupt and unfinished sections (LC-T08).

Kept, with reasons:
- Only chat-started foreground solves capture load flow. Queue, study and
  report runs refuse with their run ID until each gets its own capture.
- "Latest" stays the newest completed run of any kind (LC-10's rule): a study
  finishing after a solve makes that solve's run historical, which is honest.
- The capture reuses the read path (and its network guard) on the monitor
  thread; a busy guard skips the section with the warning above.

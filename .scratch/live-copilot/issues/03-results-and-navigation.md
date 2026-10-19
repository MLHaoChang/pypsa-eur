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

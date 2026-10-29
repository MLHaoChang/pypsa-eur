<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# P1: authorized evidence identity and validity

Status: in progress — list_components page evidence (A1.1, current-input part of A1.3, A1.5 page capture) in #806; A1.2 revocation cases executable (#813), REVOKE-TOCTOU closed (#818); get_timeseries steps (#826); A1.4 integrity cases executable (#840); A1.4-RETAINED closed by a record MAC (#850); A1.3 run as-of cases executable (#856); A1.5 complete-source load aggregate (#864); A1.6/A1.7 aggregate handler validity (#868) and emissions/lost-load null-not-zero (#879); one aggregate gap helper and pilot POLICY_VERSION (2026-10-29 slice); rest of P1 open (see "Remaining P1 slices")
Progress: Authorized retained evidence, ACL rechecks shipped; status says whole phase open, displayed-data revision proof pending
Type: task
Blocked by: 01; live-voice issue 06 for retained-run guarantees

Read the [spec](../spec.md), [test matrix](../test-plan.md),
[plan](../../../docs/superpowers/plans/2026-10-10-assistant-answer-assurance.md)
and harness README. Design status/review is in the plan; readiness is subject to
the listed dependencies and reviewed contract, not implementation certification.

## Scope

Capture full typed tool data before truncation, scope it to actor/project/run/entity/metric/window and persist through the existing run/artifact design. Implement bounded authorized evidence resolution, ACL revocation/revision rechecks, historical as-of labels, complete-source aggregates and mode/coverage/solver validity. Do not infer integrity or convergence from a hash alone.

## Test-first acceptance

Required case groups: **A1.1–A1.6** in the shared test matrix. Start with a failing
public-seam tracer, implement minimally, run green and relevant regressions.
Use independent numerical fixtures and isolated authorized projects; preserve
provider-neutral handlers, ordinary safety gates and cumulative budgets.
Record every required case as executable/passed/red/pending with a reason.
Opt-in skip or schema reachability is not behavior coverage. No paid APIs are
needed to start these deterministic slices.

## Completion and handoff

Done only when the required behavioral cases and dependencies pass, every
affected public channel uses the same approved contract, and regression/style/
schema/UI checks pass. Append commands, exact red/green counts, policy version,
remaining blockers and next runnable tracer to [handoff](../session-handoff.md).

## Comments

### First implementation continuation

Current-input capture, owned persistence, exact component/metric/unit matching and
fresh source/ACL checks are implemented for a static-input pilot. This ticket is
not complete: retained runs, coverage masks, all-domain adapters and recovery/race
fixtures remain required. See [implementation status](../implementation-status.md).

### Proposed reliability revision

P1 also requires A1.7: preserve raw validity/missing masks before sanitization and apply numerical validity/coverage rules. Coordinate ticket 10; matching a sanitized service response cannot certify calculation correctness.

See the [reliability contract](../reliability-contract.md). Original approval
does not cover these additions; review/freeze them before implementation.

### 2026-10-11 — shared harness implementation

Scoped raw evidence is now immutable through the store API, owner/project authorized and recalculated on every public lookup. Explicit retained IDs preserve a bounded historical run after foreground edits; current-source records still invalidate. Per-owner bounded transactions enforce quotas/atomic files across independent POSIX processes. The project/network runtime is still single-worker, Windows locking and complete browser/backend restart journeys are not certified, and retention is 24 hours. Unresolved effect reservations are not pruned or reused.

See [implementation status](../implementation-status.md) and [contracts](../harness-contracts.md). Whole ticket remains open.

### 2026-10-28 — list_components page evidence

`list_components` is the second checked read (`static_input_page_evidence`). A page's evidence holds the exact static inputs of the rows it showed, by entity name (RFC 6901 paths), bound to owner/org/project and a digest of the class's rows and input metrics taken before the handler; a changed digest at capture or lookup, or another project, makes it unavailable. Out-of-range pages are refused before the handler. Answers name the rows ("rows a..b of N, not the whole class"). Retained runs, coverage masks, aggregates, results families and other read families remain open.

### 2026-10-28 — A1.2 revocation before publish and read

A1.2 is now executable in `tests/assurance_acceptance/test_p1_revocation.py`, covering two revocations: org membership removed (project ACL) and user disabled. If access is revoked after a `get_component` or `list_components` capture and before publish, nothing is published: no approval, no token, no answer record, and the stream ends with `assurance_publication_failed`. A tool called after revocation returns `tool_error` with no values. No log record carries the value or the draft. Each route is read successfully before revocation. After it, the answer, export and evidence routes give exactly the status and body of an unknown id (404 `Record not found` for membership, 401 for disabled), and history projects `unavailable` without the text. One product fix: `records.load` used to answer a revoked project with 404 `Project not found`, which told a revoked record apart from a missing one. Still pending: CSRF on a stateful ACK (no assurance ACK route before P3) and a reviewer cache hit (the reviewer is withheld, so no cache exists).

#### Follow-ups

- **REVOKE-TOCTOU** (closed 2026-10-28: commit re-checks access after the save and discards the record if it was revoked; `test_access_revoked_during_commit_publishes_nothing`). The project ACL is not held under `network_guard`. A revocation that lands between commit's last `records.authorize` (`harness/assurance.py`, inside the `network_guard` block) and `records.save` can still commit the answer; the next read refuses it. Closing the window needs the ACL check and the save inside one DB transaction, or a re-check after save that deletes the record.
- **REVOKE-KIND.** Revocation surfaces as the generic `assurance_publication_failed` ("could not be saved"), and `harness/loop.py` logs it as an ERROR traceback. A typed kind (for example `assurance_access_revoked`) and a warning-level log need a ChatPanel change and UX review.

### 2026-10-28 — get_timeseries series-step evidence

`get_timeseries` is the third checked read (`series_input_evidence`). It covers `loads/p_set` (MW) and `generators/p_max_pu` (p.u.).

- **Exact args only:** `component`, `name` and `attribute`. A `period` argument, or a comma or padding in the name, is refused.
- **Source digest:** taken by the guard before the handler runs, over the raw series that `routers.network_time_axis.resolve_series` reads. That is a user upload if there is one, else the network column; the handler reads through the same function. The digest covers the project, the args, the source, the index levels (int64 ns plus tz) and the values (float64 bytes).
- **What certifies:** a step certifies only where the shown value equals the raw value after a JSON round-trip. Steps are mapped by timestep (and period), never by position. NaN, inf and None never certify.
- **Path:** `/<name>/<timestep>`, or `/<name>/<period>/<timestep>`. Answers say "one step, not the whole series".
- **Invalidation:** any edit to the series, another project, or revoked access makes the evidence unavailable.

#### Follow-ups

- **SERIES-WINDOW.** The guard refuses a series with `series_too_long_to_check` before the handler runs when either:
  - it has more than `MAX_SERIES_POINTS` (744) steps, or
  - its facts would exceed `MAX_SERIES_FACT_BYTES` (a long or wide-character name).

  So a full-year series can't be checked yet. The likely end state is a compact series record (a values array plus an index digest, with facts resolved on lookup), or a windowed checked read with exact window args.

### 2026-10-28 — A1.4 corrupt, lost, expired, revised records and restart

`tests/assurance_acceptance/test_p1_integrity.py` makes A1.4 executable on the answer, export and evidence reads and on history. Already enforced: byte corruption, truncation, non-JSON, wrong shape, a missing or oversize checksum envelope, lost evidence (the answer reads 409, the evidence 404 like an unknown id), expiry, duplicate completion (a differing re-save is refused, an identical one is a no-op), saved inputs across a reload plus a new chat session, and unsaved inputs lost by that reload.

Fixed here:
- **A checksum alone was integrity.** A record rewritten with a recomputed checksum kept its facts: the current-input kinds re-checked only `source_sha256`. `evidence()` now re-derives every stored fact from the live source (`static`, `static_page`, `series`); a fact the source does not give, or an empty list, is unavailable. The calculated kind and current-run scoped results already recomputed their facts from the live source.
- **Versions were never read back.** `answer()` now refuses a receipt whose `policy_version` is not `POLICY_VERSION`, and an answer record whose `version` is not 1; `evidence()` refuses a `version` other than 1 or an `adapter_version` other than the one this server writes for the record's kind (for scoped results, a profile `calculations.PROFILES` still registers).
- **A malformed receipt gave a 500.** A rewritten answer whose receipt is not an object, or whose `evidence_ids` is not a list, now reads 409 `answer_evidence_unavailable`.

**Still certified by a hash alone: retained scoped results (A1.4-RETAINED).** A `request_scope.retain` record carries its own `raw`; `evidence()` checks `digest(raw) == source_sha256` and that `scoped_calculate(raw)` gives the stored facts and slots, and the file envelope is an unkeyed `digest(record)`. A record whose `raw`, `source_sha256`, facts, slots and envelope are all rewritten together still reads as checked, without any write to the live project. A server-keyed MAC over the envelope (a key outside the record store) would close it; until then A1.4 is not closed for retained runs.

Not covered: a real process restart (the in-process reload stands in), retention reclaim during a lookup, duplicate completion replayed through the stream (only `records.save` is driven), run and artifact APIs (A1.3, A4), and an attacker who rewrites both the record and the live project (out of scope: that is write access to the project).

### 2026-10-28 — A1.3 a selected completed run, checked as of its finish

`get_run_evidence` is the fourth checked read (`run_metric_evidence`, adapter `run-metrics/1`). `tests/assurance_acceptance/test_p1_run_as_of.py`: 38 cases; the 11 green there are refusals that must stay refusals (inexact args, claims off the recorded metric, non-number claims).

- **Exact args only:** `run_id`. A `section`, any other arg, or a non-string id is refused before the handler; sections stay unchecked data.
- **Only a completed run:** the guard reads the run through `run_evidence.checked_run_manifest`. Failed, running, interrupted or missing runs, malformed manifests (non-object `metrics` or `sections`, no `finished_at`), and runs whose recorded sections no longer verify (including sections pruned by retention) are refused with `run_not_checkable` (named in the stream) before the handler runs; the message names the run's status (failed, running, interrupted, not found, corrupt, unverified). The guard digests the manifest; capture re-reads it and certifies only metrics whose shown value and unit equal the recorded ones.
- **Facts:** one per finite recorded metric, path `/<metric>`, `entity_id` = the run id. The scope names the run's kind, mode, `finished_at` and `unsaved_inputs`. Answers read "Run r (completed t): m = v unit (as of that run, not the current inputs)", adding "solved with unsaved inputs" when the run used them.
- **As-of validity:** lookup re-reads the run's manifest, so editing the current inputs (which invalidates a `get_component` answer from the same moment) leaves it checked. A newer failed or running run neither replaces it nor can be checked. A changed manifest (metric, `finished_at`, `mode`, `status`), a corrupt section, another active project, a rewritten evidence fact or scope, or revoked access (404/401, like an unknown id) makes it unavailable. Metric names and units are escaped in the answer.

Not covered: the run's `sections` and `compare_project_runs` deltas (unchecked reads still), the run manifest's own integrity beyond its digest at capture (it has no MAC, so an attacker who can write the project directory can publish a fake run; that is write access to the project), and runs from `retained_results`/scenario stores that are not in `run_evidence`.

### 2026-10-28 — A1.5 a complete-source load aggregate

`get_aggregate_load` is the fifth checked read (`aggregate_load_evidence`, adapter `aggregate-load/1`). `tests/assurance_acceptance/test_p1_aggregate.py`: 40 cases. GK B (7) plan GO with five conditions, all met.

- **Exact args only:** none (every load), `section` (normalised as the handler does: lower case, stripped; `entity_id` is the normalised name) or `names` (a comma list with no padding, empty or repeated name). Both, any other arg, a null, empty or blank value, or a value over 1,024 characters (`MAX_AGGREGATE_ARG_CHARS`) are refused before the handler (`capability_unsupported`, like the earlier checked reads).
- **Complete source or nothing:** the guard resolves the target loads itself and reads each member's raw series through `resolve_series`, the source the handler sums. It refuses with `aggregate_not_checkable` before the handler runs when the sum would not be complete, and the message names the gap: no matching loads, an unknown name, k of N loads with no profile, missing or non-finite steps, a non-numeric (for example numeric-string) profile, or a member whose index is not the network's snapshots (an upload on an older snapshot set would otherwise sum "completely" over the wrong steps). The handler fills a missing step with 0, drops unknown names and counts a repeated one twice, so none of those cases reaches it on a checked turn.
- **Facts:** `/peak` and `/mean` (MW), computed over every step and every load from the raw source, so they are the global statistics, not a truncated result's, a page's or one member's. `entity_id` is the section, the names as given, or "all loads". The per-step sum uses the handler's own expression (`pd.concat(...).sum(axis=1)`), so the facts equal the shown values bit for bit (tested on 50 loads of awkward floats). At capture, peak, mean, `total_loads` and `loads_with_profile` must all equal the guard's, or nothing is stored: an edit between the guard and the handler leaves the answer unchecked. The model sees only the certified statistics, the load count and the step count; the per-step sum stays unchecked. Answers read "Aggregate load (section s, N loads, M steps): peak = v MW (sum over every load and step)."
- **Invalidation:** the source digest covers the project, the args, the ordered target names and each member's series digest. Editing a member, an upload over a member, a load joining or leaving the selection (a new load, a bus move), another project or revoked access makes the evidence unavailable. Lookup re-derives the facts from the live source (A1.4).

Not covered: per-step sums (would need SERIES-WINDOW's compact record), energy (needs snapshot weightings), static `p_set` loads (the handler leaves them out; refused here as "no profile" rather than certified), and fixing the handler's unchecked behaviour (null-not-zero, unknown and repeated names), which is A1.6/A1.7 work.

### 2026-10-28 — A1.4-RETAINED closed: a server-keyed MAC on every record

`tests/assurance_acceptance/test_p1_retained_mac.py` (8 cases, 7 red on the base). Every record envelope now carries `mac`, an HMAC-SHA256 of the canonical record and its id (so a sealed record copied over another id is refused). Its key is derived from `SECRET_KEY` and a 32-byte per-install key in `<app data>/assistant_assurance.key` (mode 0600, created once with a hard link, or an exclusive create where links are unsupported, so concurrent processes agree; it sits beside the record store, not in it). `load()` and the checked-chart verify read a record only through `unseal()`, so a retained run whose `raw`, `source_sha256`, facts, slots and checksum are rewritten together reads 409 on the answer, export and evidence reads, and so does a record sealed under another key or missing its MAC or checksum.

Consequences: records saved before this change, and every record after `SECRET_KEY` or the key file changes, read as unavailable (retention is 24 hours). With the dev default `SECRET_KEY` the MAC rests on the key file alone, so an attacker who can read app data can forge; a deployment that sets `SECRET_KEY` outside app data is protected against one who can only write the record store. Tests that rewrite a record to reach the deeper checks now re-seal it with `records.seal`.

Not covered: replay of an older, validly sealed version of a mutable record (`operation`, `ui_action`, `conversation_context`) within its 24 hours; a monotonic counter would close it. An unresolved `operation` reservation saved before this change no longer reads, and the reclaim loop still keeps unresolved reservations, so such a file holds a quota slot until removed by hand.


### 2026-10-28 — A1.6/A1.7 slices a and b (#868, #879)

- **a (#868).** `aggregate_load_profile` (route and `get_aggregate_load`) no longer fills a missing step with 0, counts a repeated name twice or drops an unknown one. It reports `complete`, `gaps`, `unknown_names` and `loads_without_profile`, and returns null peak and mean unless the sum is complete. Tests: `test_p1_validity_aggregate.py`.
- **b (#879).** Emissions and lost-load results return null, not 0, for a value the solve didn't record: the CO₂ shadow price, lost-load totals, and a VOLL that was not derivable. The results pages show "—" with "not recorded by this solve". Tests: `test_p1_validity_results.py`, `test_retained_emissions.py`, results vitests.
- Follow-ups from #879, outside P1: SummaryTab shows a cap whose binding is null as "not binding"; Dispatch's hidden lost-load tiles are unlabelled; the LostLoad tables clip rows at about 1000 px.

### 2026-10-29 — one aggregate gap helper; pilot POLICY_VERSION

- `assurance_records.aggregate_gaps(snapshots, unknown, members)` now names the gaps of an aggregate load sum for both paths. The order is unknown names, no loads, no profile, other snapshots, not a number, missing or non-finite steps, no steps. `aggregate_source` refuses on the first gap, and the handler reports them all. Each path still reads its own source: `resolve_series` on the checked side, `_user_ts` then the network on the handler side.
- No change of behaviour: on 18 scenarios, the handler's response JSON (every gap, the null-step totals and the sum) is byte-equal to master's (`tests/assurance_acceptance/aggregate_json_dump.py`, run on both trees and compared with `cmp`). The helper also gives "the loads do not share the network's snapshots" when members cannot be aligned and no gap was found before. No public path reaches that case, because such a member already fails the snapshots check.
- `harness/pilot_evaluation.py` checks the receipt against `records.POLICY_VERSION`, not a literal "1".
- Tests: `test_p1_validity_aggregate.py` pins the full ordered gap list on both paths for 14 cases: unknown, only unknown, no loads match, no profile, no profile at all, missing step, infinite step, other snapshots, both off snapshots, bool, numeric string, repeated timestamp, timezone-aware, and many gaps (five gaps at once). The checked path refuses on the first gap and never raises. "No steps" is tested on the helper alone, since pypsa refuses empty snapshots. `test_pilot_evaluation_scoring.py`: a receipt with policy version "2", 1 or None still gives `unsupported_policy`.
- Mutation: `tools/aggregate_gap_mutations.py` makes each pair of gap kinds trade places in the helper's result. Every such swap fails a test. The exceptions are pairs that never appear together ("no loads match" with anything, "no steps" with a missing step); swapping those is an equivalent mutant.

#### GK B (9) plan GO conditions (2026-10-29)

1. No behaviour change, proved: master's `test_p1_aggregate`, `test_p1_validity_aggregate` and pilot_evaluation tests run unchanged and green on the PR tree, and the handler's response JSON is byte-equal to master's. Met: 107 passed for master's copies of the validity, aggregate and scoring files; `test_pilot_evaluation` is unchanged (212 passed, 21 skipped); JSON byte-equal on 18 scenarios.
2. Parity parametrised over the six gap kinds (unknown, no profile, snapshots, not a number, missing/non-finite, no steps). The router keeps the full ordered list, and one case has several gaps. Met: the many-gaps case has five.
3. Members that cannot be aligned (tz-aware next to naive, a repeated timestamp) give a gap on both paths, never a 500. Met.
4. Swapping two gap checks fails a test. Met, except the equivalent mutants named above.
5. A receipt with policy_version "2" still gives `unsupported_policy`. Met.
6. These conditions are recorded here.

### Remaining P1 slices (2026-10-29)

Smallest first; each goes to GK B as a plan before it is built.

- **REPLAY.** A monotonic per-record sequence for mutable records (`operation`, `ui_action`, `conversation_context`), so an older, validly sealed copy reads unavailable. Only `assurance_records` changes.
- **Shared fact builder.** One builder for the fact dicts of the static, static_page, series, run and aggregate kinds. Refactor only, behind the A1.x suites.
- **SERIES-WINDOW.** A compact series record: a values array plus an index digest, with facts resolved on lookup. It lifts the 744-step cap without new args. Windowed args would need `harness/catalogue.py`; blocked while #851/#869 hold it.
- **REVOKE-KIND** and **frontend assurance_state.** Blocked: both need `ChatPanel.tsx` (held by #883) and UX review.
- **Run sections, compare_project_runs, run manifest MAC.** Blocked: all go through `services/run_evidence.py` (LC-03 (6), #883).

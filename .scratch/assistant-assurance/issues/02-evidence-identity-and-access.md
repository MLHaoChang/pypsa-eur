<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# P1: authorized evidence identity and validity

Status: in progress — list_components page evidence (A1.1, current-input part of A1.3, A1.5 page capture) in #806; A1.2 revocation cases executable (#813), REVOKE-TOCTOU closed (#818); get_timeseries steps (#826); A1.4 integrity cases executable (#840); A1.4-RETAINED closed by a record MAC (#850); A1.3 run as-of cases executable (#856); A1.5 complete-source load aggregate (#864); A1.6/A1.7 aggregate handler validity (#868) and emissions/lost-load null-not-zero (#879); one aggregate gap helper and pilot POLICY_VERSION (2026-10-29 slice); run manifest MAC (run-evidence PR 1, #930); checked run sections and compare deltas (run-evidence PR 2); rest of P1 open (see "Remaining P1 slices")
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
- No change of behaviour: on 19 scenarios, the handler's response JSON (every gap, the null-step totals and the sum) is byte-equal to master's (`tests/assurance_acceptance/aggregate_json_dump.py`, run on both trees and compared with `cmp`). The helper also gives "the loads do not share the network's snapshots" when members cannot be aligned and no gap was found before. No public path reaches that case, because such a member already fails the snapshots check.
- `harness/pilot_evaluation.py` checks the receipt against `records.POLICY_VERSION`, not a literal "1".
- Tests: `test_p1_validity_aggregate.py` pins the full ordered gap list on both paths for 15 cases: unknown, only unknown, no loads match, no profile, no profile at all, missing step, infinite step, other snapshots, both off snapshots, bool, numeric string, repeated timestamp, timezone-aware, many gaps (five gaps at once) and empty upload (five gaps ending in "no steps"). The checked path refuses on the first gap and never raises. "No steps" alone is tested on the helper, since pypsa refuses empty snapshots. `test_pilot_evaluation_scoring.py`: a receipt with policy version "2", 1 or None still gives `unsupported_policy`.
- Mutation: `tests/assurance_acceptance/aggregate_gap_mutations.py` swaps each pair of gap kinds in the helper's result and leaves every other gap in place. Each of the 14 pairs that can appear together fails a test. The other 7 pairs never appear together ("no loads match" with anything, "no steps" with a missing step), so swapping them is an equivalent mutant.

#### GK B (9) plan GO conditions (2026-10-29)

1. No behaviour change, proved: master's `test_p1_aggregate`, `test_p1_validity_aggregate` and pilot_evaluation tests run unchanged and green on the PR tree, and the handler's response JSON is byte-equal to master's. Met: 107 passed for master's copies of the validity, aggregate and scoring files; `test_pilot_evaluation` is unchanged (212 passed, 21 skipped); JSON byte-equal on 19 scenarios.
2. Parity parametrised over the six gap kinds (unknown, no profile, snapshots, not a number, missing/non-finite, no steps). The router keeps the full ordered list, and one case has several gaps. Met: two cases have five gaps each, and "no steps" is reached through both paths (empty upload).
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
- **Run sections, compare_project_runs, run manifest MAC.** In progress (Assurance 02 run-evidence session): PR 1 the manifest MAC, PR 2 the checked section and compare reads; see below.

### 2026-10-29 — Run evidence: manifest MAC, checked sections and compare (plan)

Seam: `run_evidence.checked_run_manifest` stays the one guard every checked run read goes through. PR 1 seals every terminal manifest with a server-keyed MAC; PR 2 adds checked `get_run_evidence(run_id, section)` (only `{value, unit}` pairs inside the section are facts) and checked `compare_project_runs` (deltas recomputed from both checked manifests; `allow_input_changes: true` stays unchecked).

#### GK B (12) plan GO conditions (2026-10-29)

Split: PR 1 is the MAC alone; PR 2 opens after PR 1 merges.

PR 1 (MAC):
1. Existing readers see no change: `mac` is stripped in `_load_run` and `get_run_evidence`; only `checked_run_manifest` reads it raw. `_load_run` and `get_run_evidence` output for a run published under the PR is byte-equal to master's. Run at the trial merge: `test_experiment_comparison`, `test_results_workbook_experiments`, the experiment execution and reconciliation suites, `test_p1_run_as_of`, `test_retained_results*`.
2. Record MACs are unchanged by extracting `server_key(domain)`: a fixed record sealed on master and on the PR tree gives the same MAC. Separate domain labels; a record MAC never verifies as a run MAC and the reverse.
3. One MAC per run id, ever: a republish with different content is refused, an identical one returns what is stored. (Already so on master: `publish_run` and `start_run` refuse a second manifest with `run_already_finalized`; run ids come from job handles, uuids or nonces.)
4. MAC input: canonical {domain, project_id, run_id, manifest-without-mac}. Mutants (drop project_id, drop run_id, skip the verify) each fail a named test.
5. Old manifests read as unchecked data and are refused when checked (`run_not_checkable`, "unverified"); no re-seal, no migration. Both tested.

PR 2 (sections + compare):
6. A section fact's `value` is a finite int or float, never a bool; `unit` a str of 1..16 characters. Paths use the existing pointer escape (a key holding '~' and '/' tested). A metric key at two pointers in one section is certified at neither, or claims match by path; never first-match-by-metric. Tested.
7. A section's facts are capped so the record stays under save()'s 128 KiB; beyond the cap, `run_not_checkable` with a reason (tested with a small monkeypatched cap).
8. `delta_pct`'s unit matches what `_comparison` computes, asserted by hand (100 -> 110); a baseline of 0 gives no pct fact. The `_comparison` extraction is byte-identical: master's compare tests run in the PR tree.
9. Mutants: the plan's list, plus accept a bool value and certify a duplicated metric key.
10. `harness/catalogue.py` untouched (#851). Trial-merge with #927 and REVOKE-KIND if open; files both change listed in the PR body.

### 2026-10-29 — Run evidence PR 1: a server-keyed MAC on every run manifest

`tests/assurance_acceptance/test_p1_run_manifest_mac.py` (13 cases, 8 red on master). `publish_run` writes `mac`, an HMAC-SHA256 of canonical {domain, project_id, run_id, manifest-without-mac} under `assurance_records.server_key(b"pypsa-gui run-manifest/1")`; record MACs use the same helper under their own label, byte-for-byte as before (a record sealed on master pins it). `checked_run_manifest` verifies it last, so a malformed or unfinished run still names its status; a missing or wrong MAC reads `unverified`, refused as `run_not_checkable` before the handler and 409 on lookup. Every other reader (`_load_run`, `get_run_evidence`, `list_project_runs`, `compare_project_runs`, retained results, experiments) gets the manifest without `mac`: `run_manifest_dump.py` prints the same bytes on master and on the PR tree.

Met conditions: 1 (dump byte-equal; named suites green), 2 (pinned record MAC green on master and PR; cross-domain test), 3 (refusal and identical-republish tests), 4 (`run_manifest_mutations.py`: skip-verify, drop project_id, drop run_id each killed; dropping either from the binding alone is equivalent, since the body carries both), 5 (unsealed run reads unchecked, refused when checked).

Consequences: runs recorded before this change, and every run after `SECRET_KEY` or the key file changes, stay readable history but are never checkable; there is no re-seal. A run whose key file cannot be read is published without a MAC (logged) rather than failing the run; that run is then never checkable (GK B (12) kept this fallback at review: it fails closed for checking and keeps history). With the dev default `SECRET_KEY` the MAC rests on the key file alone. A project copied to another install keeps its runs as unchecked history.

Not covered: a run id reused after retention deleted its directory would get a second manifest (ids come from job handles, uuids or nonces, so none is reused today). PR 2 (checked sections and compare) follows.

### 2026-10-29 — Run evidence PR 2: checked run sections and compare deltas

`tests/assurance_acceptance/test_p1_run_sections.py` (35 cases) and `test_p1_run_compare.py` (39 cases); 43 of the first 69 red on master, the 26 green there being refusals that must stay refusals (inexact args, claims off a recorded value).

- **Sections.** `get_run_evidence` with exactly `run_id` and a `section` is checked through `run_evidence.checked_run_section` (the sealed-manifest guard, then the section's own hash). Facts are the section's `{"value", "unit"}` objects: a finite int or float (never a bool) and a unit of 1..16 characters, at `/data/<pointer>/value`, metric = the pair's own key. Claims match facts by path, so a key repeated at two pointers is two facts and a value never certifies at the other's path. Over `MAX_SECTION_FACTS` (200), facts encoding over `MAX_SECTION_FACT_BYTES` (120 KiB, since a long key repeats in every path under it), or no such pair: `run_not_checkable` before the handler. Scope `run_section` (adapter `run-section/1`) carries the section's sha256; lookup re-reads both and re-derives the facts. The rest of the section stays unchecked; today that means the retained results tables, whose units sit outside the data.
- **Compare.** `compare_project_runs` with two different run ids, optional `metrics`, and `allow_input_changes` absent or false is checked through `run_evidence.checked_run_comparison`: both runs through the sealed-manifest guard, then `_comparison` (extracted from `compare_project_runs`; `run_manifest_dump.py` still byte-equal to master's, `test_run_evidence_compare` unchanged and green). Facts: each delta in its metric's unit at `/deltas/<m>/delta/value`, and `delta_pct` with unit `%` at `/deltas/<m>/delta_pct` when the baseline is not zero (100 -> 110 gives 10 EUR and 10.0 %, asserted by hand). Incomparable runs (mode or fingerprint), a failed or unverified side (named by role) or no delta in a shared unit: `run_not_checkable`. Scope `run_comparison` (adapter `run-compare/1`); lookup recomputes from both manifests.
- **Harness.** `compare_project_runs` joins `CHECKED_TOOLS` (`run_comparison_evidence` in `assurance_coverage`); `harness/catalogue.py` untouched. A source digest of the manifests binds each record, so another validly sealed manifest under the same run id (the run-id-reuse gap of PR 1) is refused too.
- **Mutants** (`tests/assurance_acceptance/run_read_mutations.py`): 9 of 9 killed: section facts without the byte cap, section lookup without the fact re-derive or the source digest, section guard taking extra args, a bool value as a fact, a claim matched by metric instead of path, compare guard taking `allow_input_changes: true`, compare lookup without the recompute or the source digest.

Not covered: per-family unit maps for the retained results sections; runs from `retained_results`/scenario stores outside `run_evidence`.

### 2026-10-29 — REPLAY closed: a DB high-water mark on every mutable record

`tests/assurance_acceptance/test_p1_replay.py`: 32 cases.

**Threat model.**
- In scope: an attacker who can write the record store, up to restoring the whole store directory. They cannot read `SECRET_KEY` or the key file, and cannot write the DB.
- Out of scope: rolling back the store and the DB together (a backup restore); anyone who can read the key, since they can forge anything; and write access to the project or the DB, since that is project access.

**What changed.**
- **The mark.** Each save of a mutable record (`operation`, `ui_action`, `conversation_context`) takes the next value of a mark in a new DB table, `assurance_record_seq` (migration `0010_assurance_record_seq`). The table holds `owner_id`, `record_id`, `seq`, `epoch` and `expires_at`. The save seals `{seq, epoch}` into the record under its MAC.
- **Load.** `load()` accepts a mutable record only when its sealed mark equals the DB row exactly. Each of these reads 409 `evidence_unavailable`:
  - a lower seq (a replay);
  - a higher seq (the mark was rolled back);
  - another epoch;
  - no mark at all.

  The mark is stripped before the record is returned, so callers see the record as before.
- **Write order.** The mark is bumped (`UPDATE … SET seq = seq + 1 RETURNING`) and committed inside the store's file lock. Only then is the file sealed and written. A crash between the two leaves the file one behind its mark, which reads as unavailable and never as the older version. The next save writes a current version again. `load()` takes no lock, because an effect holds the store lock while its handler runs. A reader that finds the file one step behind its mark re-reads for up to 2 s (`_MARK_SETTLE_SECONDS`), so a save in flight lands and reads as the new version (tested). A reader that already holds the store lock skips the wait, because no save can land then. Only a file exactly one seq behind in the same epoch waits; every other mismatch is refused at once (GK B (10) NOT OK fix, tested with the settle time patched to 30 s). A crash or replay stays mismatched and reads 409. Two first saves of a new id that race on the insert take seqs 1 and 2: the loser retries the update (tested outside the lock).
- **Pruning.** A pruned row starts a new epoch when its id is written again, so no copy sealed under the old chain matches. The save sweep deletes the marks of reclaimed records, and of expired records whose file is gone. An unresolved operation reservation keeps its file, so it keeps its mark.
- **Immutable kinds** (answer, evidence) take no mark; an identical re-save is still a no-op.

**Upgrade effect** (fail-closed, as #850 was).
- A mutable record saved before the migration has no mark, so it reads as unavailable until rewritten. There is no backfill, because a backfill would trust a store that may have been rolled back.
- What the user sees, for at most 24 hours (retention):
  - a pending confirmed operation from before the upgrade can't be read back, so it is refused rather than re-run;
  - a UI-action cursor or acknowledgement reads 409;
  - a conversation checkpoint isn't recovered, so the conversation continues without checked recovery.

**Not covered.** The store and the DB rolled back together. Marks for records whose files are removed by hand outside the sweep stay until a later mutable save by the same owner prunes them after expiry.

#### GK B (9) plan GO conditions (2026-10-29), option A

1. **Exact match:** load() accepts only seq == mark, and both directions are refused. Met: tests for replay, rolled-back mark and epoch.
2. **Write order:** the DB commit comes first, then seal and write. Pinned by a test that fails the file write after the commit; the process is not killed, so the test leaves file n with mark n+1 and checks that it reads 409. Two concurrent saves take distinct seqs (a two-thread test through save(), and a two-thread test of the first mark outside the lock). Met.
3. **Migration 0010** after 0009, with a downgrade. Met: `test_alembic_sqlite` (head is now 0010) and a new upgrade → downgrade → upgrade round-trip test.
4. **Upgrade effect** is fail-closed with no backfill, stated above. Met.
5. **Rows don't grow forever:** they are pruned in the save sweep, and a re-created id gets a new epoch. Met: tests for the sweep and the pruned chain.
6. **Mutable kinds only.** The immutable no-op test is kept. There are no new error kinds (409 `evidence_unavailable`, as for any unavailable record). Mutation: `tests/assurance_acceptance/replay_mutation.py` makes load() accept any mutable record it can read and unseal; each of the 21 red cases then fails. Met.
7. These conditions are recorded here.

### 2026-10-29 — S3: one shared fact builder

- `assurance_records._fact(entity_id, metric, value, unit, *tokens)` builds every fact dict; its path is `pointer(*tokens)`. Six sites use it: static (`_raw_facts`), static_page capture (`capture_input_page`) and recheck (`_page_facts`), series (`_series_steps`), aggregate (`_aggregate_facts`) and run (`_run_facts`). Each site keeps its own value normalisation. The results kinds (`scoped_calculate`, `assurance_metrics.calculate`) are out of scope.
- One spelling changed: static's `"/" + metric` is now `pointer(metric)`. It is equal for every INPUT_METRICS name, since none holds '~' or '/'; `test_p1_fact_builder` pins that on the live table.
- No change of behaviour: see the conditions below.

#### GK B (10) plan GO conditions (2026-10-29)

1. `_fact` builds the dict in today's key order: path, entity_id, metric, value, unit. Normalisation stays at each call site. Met: `test_p1_fact_builder` pins the key order.
2. A dump (`tests/assurance_acceptance/fact_record_dump.py`) covers all six sites, with the static_page capture and recheck dumped separately. It includes a static record for every INPUT_METRICS class, and a name holding both '~' and '/' for the page and series kinds. It is byte-equal between master and the PR tree, and the PR body gives both outputs' sha256. Met: 1534 lines on each tree, the same sha256 (`7e0a8f7f…df32`), `cmp` equal. Escaped names reach the static_page capture and recheck and the series site.
3. Mutation (`tests/assurance_acceptance/fact_builder_mutations.py`): breaking the builder at any one of the six sites fails a test. Met: 15 of 15 killed (entity/metric swap and dropped unit at all six sites; unescaped path at the page capture, page recheck and series sites). An unescaped path is an equivalent mutant at the static, aggregate and run sites, whose only token is a metric name.
4. The pin test reads the live INPUT_METRICS. Met.
5. The PR is opened from master after #895 and #898 merge, so its diff is S3 alone. The A1.x suites and `test_p1_replay` run unchanged at the PR head.

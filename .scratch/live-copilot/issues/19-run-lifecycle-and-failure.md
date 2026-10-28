<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-19 — run, re-run, queue and failure handling in chat

Status: ready-for-agent inside LC-05, beside LC-12 (built on `claude/lc19-run-lifecycle`)
Type: task
Baseline: master `ae95a94a866730c0fe67fad37e238dc281786bd6`
Dependencies: LC-12
Catalogue: [feature catalogue](../feature-catalogue.md); journeys OJ-20, OJ-31, OJ-32, OJ-33, OJ-34

## Problem

Running is well covered (`run_simulation`, `run_ac_pf_stage`,
`solve_queue_enqueue`, `abort_simulation`, `solve_queue_abort`,
`project_readiness`, `validate_network`, `diagnose_network`, the
`solver_error_decoder` prompt). Gaps:

- **Re-run.** The queue has `requeue`, `pause`, `resume`,
  `cancel_queued` and `dismiss` routes (`routers/solve_queue.py:823` and
  neighbours) with no chat tool; "re-run the failed one" becomes a fresh
  enqueue with possibly different inputs.
- **"Re-run" with unchanged inputs** has no rule: the solve fingerprint says
  nothing changed, but the model may solve again anyway.
- **Failure.** LC-12 item 5 explains a failed job from its log. There is no
  rule to chain `diagnose_network` and `validate_network` for an
  infeasible solve, to name the binding cause, or to offer a fix as a card.
- **Which engine.** "Run the study" can mean optimisation, AC PF, an
  adequacy run or a GridSpine pipeline (domain annex, fidelity table). The
  rule exists in the spec, not in a journey.

## Scope

1. Add chat tools over the existing per-user routes: `requeue`
   (execution tier, carded) and `cancel_queued` (destructive tier, carded;
   it cancels only the caller's queued jobs, `routers/solve_queue.py:1120`).
   `pause` and `resume` are instance-wide and super-admin only
   (`_require_instance_scope`, `:1054`); they stay out of chat with LC-22's
   refusal reason.
2. Re-run with an unchanged fingerprint says the results are current and asks
   before solving again.
3. Failure triage: on a failed or infeasible job, read the log, run
   `diagnose_network` and `validate_network`, state the cause in one
   sentence with its evidence, and offer the fix as an ordinary edit card.
   Never re-run automatically.
4. Engine resolution: an ambiguous "run" asks once with the engines the
   project supports (Choice), using the native capability metadata.

## Done when

OJ-20 and OJ-31–34 pass in Tier A, including an infeasible fixture and a
requeued failed job with the same inputs; no job starts without its card.

## Rules folded in from #567 HV-S (study copilot)

Source: draft PR #567 `.scratch/high-value-ai-study/` (commit e8d53410), research only; owner decision AI-567 (2026-10-21). Adopt the listed cases into this ticket's own numbering; each must name its token/packet limit.
- Never mark a step done from a caller-supplied result alone (`resolve_task_step`); read the result back natively before the next step.
- No replay on resume; an interrupted step resumes or is reported, never re-run silently.
- A failed step yields a correction draft, never an automatic retry.
- Superseded by AI-567b: the cross-card study brief is built by the study copilot (ticket 23, #569) on these plans; no separate progress line, the plan card is its one entry point and the card workspace Next row stays the per-card next step.
- Cases: #567 S-series (see 05-IMPLEMENTATION-AND-TESTS.md).

## Built (claude/lc19-run-lifecycle)

- Item 1 was already on master (live-voice 08 B3): `solve_queue_requeue`
  (execution) and `solve_queue_cancel_queued` (destructive), both carded.
  This branch gives them Guided card sentences.
- Item 2: `harness/run_lifecycle.py`. `run_simulation`, `solve_queue_enqueue`
  and `solve_queue_requeue` aimed at the open project whose results match its
  inputs (`calculations.solve_matches_inputs`) are refused before any frame
  with `results_current`, naming the latest run. The user's yes comes back as
  `rerun_current: true`, which shows the ordinary run card. `run_ac_pf_stage`
  is refused the same way while the project flow's
  `freshness_by_kind.loadflow` (W4b, #780) reads fresh.
- Item 3: a failed solve's `wait_for_job` result carries `triage` (the job's
  log, `diagnose_network`, `validate_network`, then what to say), and a failed
  plan job's continuation message carries the same rule in one line. Nothing
  re-runs: a run still needs its own request and card.
- Item 4: `project_readiness` returns `engines` (named from the separability
  matrix) and `ask_engine`, the ready kinds when there is more than one. The
  load flow is ready while the dispatch is fresh; its `results_current` is
  `freshness_by_kind.loadflow == "fresh"`.

## Cases adopted from #567 (HV-S), with their limits

| Case | Source | Proved by | Limit |
| --- | --- | --- | --- |
| LC19-S1 | S14 | `test_oj33_an_infeasible_solve_is_triaged_and_never_rerun` | 3 triage reads; log ≤ 200 lines by default (`max_lines` ≤ 2000); each result ≤ 4,000 chars, 40,000 per turn (`harness/results.py`) |
| LC19-S2 | S14 | `test_oj33_wait_for_job_names_the_triage_of_a_failed_solve` | `wait_for_job` ≤ 25 s per call; `triage` is 3 entries and one sentence |
| LC19-S3 | S06 | `test_oj32_a_denied_rerun_card_starts_nothing`, `test_oj34_a_denied_retry_queues_nothing` | one card per run call; zero jobs on deny |
| LC19-S4 | S12, S13 | existing `test_oj07_*` (restart, cancel, no replay) | one continuation per job; retries ≤ `TASK_STEP_RETRIES_MAX` (2) |
| LC19-S5 | S09 | existing `test_a_process_restart_loses_the_job_and_continues_once_uncertain` | uncertain step blocks dependents; no reset |

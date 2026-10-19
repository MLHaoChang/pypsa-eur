<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-19 — run, re-run, queue and failure handling in chat

Status: ready-for-agent inside LC-05, beside LC-12
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

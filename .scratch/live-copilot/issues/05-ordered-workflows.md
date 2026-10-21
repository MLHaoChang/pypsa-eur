# LC-05 — bounded change, verify, run and explain workflows

Status: ready-for-agent
Type: task
Baseline: master `c75ca5584b873418f10f1bca42f868255d445b7b`
Dependencies: LC-03/04, existing durable parent, native jobs and experiments

Read the [specification](../spec.md), [assessment](../gap-assessment.md),
[acceptance matrix](../test-plan.md) and
[implementation plan](../../../docs/superpowers/plans/2026-10-18-whole-application-live-copilot.md).
Applicable contracts/cases: LC-C5/C6/C9; LC-T04/05/10/12.

Domain cases: [LC-D06/09/17/19](../domain-coverage.md) plus each effected domain.
Resolve optimization/static/dynamic intent before execution. Control tuning
apply and any required re-solve/re-study need distinct verified native steps;
a recommendation or dispatch success cannot certify a dynamic/AC property.

Persist a finite native-operation graph and dependencies. Verify each required write before one separately admitted solve. Revalidate input/schema/policy revisions, locks, ownership and caps per step. Unknown/partial effects block dependent steps; recovery reconciles and never replays automatically.

2026-10-19 (owner decision Q1): LC-05 closes when OJ-07/08/10 pass (VS-4). Reuse `start_task`
and `wait_for_job`; the 25 s wait limit and job completion into chat are the explicit
ticket [LC-12](12-job-completion-continuation.md) (LC-C12) inside this scope.

Expected ownership: durable operation orchestration over ordinary dispatch, existing receipts/jobs/adapters and owned progress.

Rebaseline current master and incoming interfaces before implementation. Write
one meaningful public-seam RED case, then implement that slice and regress its
neighbors. Record actual source/commands/results and remaining gates here.
No new capability is enabled by this issue. Paid calls, reviewer contact,
deployment and full-coverage/domain-approval claims remain outside this package.


## Rules folded in from #567 HV-S (study copilot)

Source: draft PR #567 `.scratch/high-value-ai-study/` (commit e8d53410), research only; owner decision AI-567 (2026-10-21). Adopt the listed cases into this ticket's own numbering; each must name its token/packet limit.
- Never mark a step done from a caller-supplied result alone (`resolve_task_step`); read the result back natively before the next step.
- No replay on resume; an interrupted step resumes or is reported, never re-run silently.
- A failed step yields a correction draft, never an automatic retry.
- No cross-card study brief or separate progress line: the card workspace Next row and H1 are the entry points.
- Cases: #567 S-series (see 05-IMPLEMENTATION-AND-TESTS.md).

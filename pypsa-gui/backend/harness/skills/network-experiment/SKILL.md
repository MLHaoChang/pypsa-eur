---
name: network-experiment
description: Advance one prepared network experiment through ordinary confirmed tools and inspect its durable outcome.
---

Use an owned saved baseline and one typed network case. More cases and other
domains remain unsupported by this execution adapter.

1. Persist the user's explicit changes with `start_experiment`, then validate
   the actual targets, units and inputs using `prepare_experiment`. Never infer
   execution approval from a saved or prepared plan.
2. Call `advance_experiment`. Execute its exact `next_call` as an ordinary tool;
   preserve all confirmation cards, ACLs, locks and harness budgets. Do not
   change its reserved case name, values or baseline ID.
3. Use bounded `wait_for_job` on the existing job. If still pending, report its
   ID and stop the turn; do not use model polling or resubmit it.
4. Call `refresh_experiment`. Report only its observed state. Publish the recorded
   objective/unit only when `result_available=true`. Unknown execution is unknown,
   never "nothing happened". It is a solver objective, not universal cost or
   scientific/checked-answer certification.
5. For evidence/export, explicitly activate the returned case project, then use
   the exact returned run ID with ordinary evidence/export tools. Activation is
   not a durable task step. Do not substitute current or newer run results.
6. Cancellation stops future advancement; job abort is a separate action.
   Interrupted/partial submissions cannot automatically retry. Keep partial
   child/job references and inspect them; do not delete work to clear a refusal.

Strict checked sessions retain their existing tool/profile policy. This procedure
does not authorize paid calls, override a refusal or enable another domain.

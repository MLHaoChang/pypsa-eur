# LC-01 — model, tool and parameter discovery

Status: ready-for-agent
Type: task
Baseline: master `c75ca5584b873418f10f1bca42f868255d445b7b`
Dependencies: Existing catalogue, native schemas, ACLs and owned context

Read the [specification](../spec.md), [assessment](../gap-assessment.md),
[acceptance matrix](../test-plan.md) and
[implementation plan](../../../docs/superpowers/plans/2026-10-18-whole-application-live-copilot.md).
Applicable contracts/cases: LC-C1/C2/C10; LC-T01/02/06/17.

Derive bounded eligible capability/parameter descriptors from actual sources; resolve canonical project/case/run/entity IDs before shortcuts. Keep native validation authority and provider offer limits. Define proposed DTOs/routes as new interfaces only where existing discovery cannot supply them; settle concrete compatibility contracts before coding.

2026-10-19: deictic and recency targets (“this line”, “the latest calculation”) are split out as
[LC-10](10-focus-and-recency-resolution.md) under contract LC-C11.

Expected ownership: catalogue/toolsets, asset/service schema readers, scope resolver, companion request entry; no domain-engine algorithms.

Rebaseline current master and incoming interfaces before implementation. Write
one meaningful public-seam RED case, then implement that slice and regress its
neighbors. Record actual source/commands/results and remaining gates here.
No new capability is enabled by this issue. Paid calls, reviewer contact,
deployment and full-coverage/domain-approval claims remain outside this package.


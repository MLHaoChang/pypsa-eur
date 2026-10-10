<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Assistant reliability plan assessment and proposed additions

Status: source audit and proposed documentation revision, completed before application
changes. The subsequent [first implementation](../../../.scratch/assistant-assurance/implementation-status.md)
uses the reviewed v1 subset; the proposed reliability contracts remain pending review.
Baseline: `d43809aed8728c844cc4d5119785b68801c91a8e`.
Original handoff/design review is historical evidence. This assessment is by the
editing agent; it is not a separate independent review or release certification.

## Assessment

The [assurance plan](../plans/2026-10-10-assistant-answer-assurance.md) provides
the right core design: buffer model text, capture scoped evidence, validate typed
claims, render facts in software and commit before public output. It covers
artifacts, history, speech, ACL, reviewer limitations and recovery. Implement this
in the shared harness; prompts or a second-model agreement cannot substitute for
mechanical checks. The [capabilities plan](../plans/2026-10-10-assistant-capabilities.md)
and [harness contract](../../../pypsa-gui/backend/harness/README.md) already provide
provider parity, confirmations, bounded tools and durable task recovery to reuse.

The unresolved distinction is between matching recorded output and establishing
that the requested calculation/effect is correct. Strengthen the plan before
implementation with [reliability requirements](../../../.scratch/assistant-assurance/reliability-contract.md).
Zero hallucinations across all possible questions cannot be certified. The
implementable objective is to prevent unsupported factual publication/effects,
compute supported quantities in software, disclose unavailable evidence and
measure useful correct answers on independently specified cases.

## Source findings and their implications

| Finding | Source evidence at baseline | Required change |
| --- | --- | --- |
| Private model content is published immediately | [`loop.py`](../../../pypsa-gui/backend/harness/loop.py) lines 502/505 yield token/thinking deltas | Existing P2 buffering/publication design remains the first implementation priority |
| Courtesy checks are advisory; tool-return frames do not establish effects | `loop.py` lines 1975–1990 catch validator failures; line 2330 emits successful return metadata | Keep courtesy checks distinct; add mandatory effect gates and actual read-back receipts |
| Matching a numeric value can miss semantic attribution | [`number_audit.py`](../../../pypsa-gui/backend/services/reports/number_audit.py) line 169 selects the first matching fact; lines 141–145 accept unspecified units | Registry-owned metric/entity/run/scope and code rendering; audit stays diagnostic |
| Legacy presentation can erase invalid/missing distinctions | [`losses.py`](../../../pypsa-gui/backend/services/results/losses.py) lines 46/127 fill NaN with zero; line 136 returns zero for absent demand denominator | Capture raw masks and numerical validity before cleanup; never certify those zeros as full valid results |
| Objective and cost are deliberately different quantities | [`results.py`](../../../pypsa-gui/backend/routers/results.py) lines 235–300 define cost breakdown and objective decomposition | Register semantics/weights/currency basis and validate the bridge; no objective-to-cost substitution |
| Existing evidence helpers are a useful partial source | [`workflow_tools.py`](../../../pypsa-gui/backend/services/workflow_tools.py) lines 244–329 support four sections and bounded pages/counts | Required-slot domain summary profiles; cover every released metric/operation, beyond family sample journeys |
| Manual task resolution accepts supplied result data | [`assistant_tasks.py`](../../../pypsa-gui/backend/services/assistant_tasks.py) lines 223–241 accept a result after lifecycle checks | Require independent effect evidence before checked completion; retain normal confirmation and worker guards |

These findings describe existing seams, not newly introduced regressions. The
legacy result helpers may serve ordinary presentation needs; the assurance layer
must retain the information those projections omit. No new runtime vulnerabilities
or scientific errors are inferred solely from an advisory validator exception.

## Added requirements and execution priorities

1. Check requested task/scope/completeness, with domain key-results profiles and
   explicit missing/ambiguous slots. A true answer to the wrong quantity fails.
2. Freeze metric/formula definitions, computed dependencies and numerical validity
   masks. Independently test weighted arithmetic, cost bridges and predicates.
3. Add required pre-effect guards and verify real postconditions; quarantine
   uncertain effects and prevent replay or model-only completion reconciliation.
4. Gate every public surface and require per-operation/metric mappings, useful
   positive fixtures, adversarial false-accept checks and versioned quality reports.

Tickets [09](../../../.scratch/assistant-assurance/issues/09-request-scope-and-key-results.md),
[10](../../../.scratch/assistant-assurance/issues/10-metric-calculation-and-validity.md)
and [11](../../../.scratch/assistant-assurance/issues/11-required-guards-and-effect-verification.md)
extend P0–P7 with concrete dependencies and acceptance groups. Add contracts first,
then build one real request-scoped numerical tracer alongside the unsafe-stream
tracer. Full release remains blocked by incomplete metric/operation coverage and
required negative, recovery, browser and domain-review gates.

## Validation boundary

Transferred documentation was restored onto `docs/assurance-reliability-review`;
transferred acceptance source remains in the archive. No patches were applied to
application code or tests. Original RED/regression counts in the transfer remain
reported historical results. New requirement groups have no executable coverage.
Current verification results are recorded in the [handoff](../../../.scratch/assistant-assurance/session-handoff.md).

Current checks: **60 existing controls passed** in stream-attempt, pre-dispatch,
number-audit and report-generator suites; inventory matched **220 tools, 14 result
tabs, 17 panels**; all **125 local documentation links** resolved and no trailing
whitespace was found. These checks preserve the baseline and document consistency;
they do not establish the proposed assurance behavior. No new application tests
were authored and no paid model requests ran.

The frozen v1 protocol is retained as the acceptance baseline. Added observable
contracts require review/compatibility decisions before freezing; do not relabel
the amended design independently approved. This work uses no paid model requests.

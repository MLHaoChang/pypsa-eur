<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Independent assistant assurance plan review

Historical review of the transferred design. Later additions in the
[reliability assessment](2026-10-10-assistant-assurance-reliability.md) remain
proposed; this review does not approve their contracts or tests.

Status: independently reviewed; GO for plan publication and opt-in acceptance
test creation. NOT implementation or release approval.
Reviewer: separate agent `answer_assurance_review`, requested by the user.
Baseline: master d43809ae. [Plan](../plans/2026-10-10-assistant-answer-assurance.md),
[spec](../../../.scratch/assistant-assurance/spec.md),
[tests](../../../.scratch/assistant-assurance/test-plan.md).

## Initial code audit incorporated in the draft

- Guard intermediate text, thinking, tool-result cards, reload and speech, not
  merely final prose. Separate private protocol trace from approved history.
- Tool-return/queued/failed/partial statuses need canonical outcome adapters;
  navigation success needs current-generation acknowledgement.
- Capture evidence before model truncation and page/global summaries must differ.
- Existing report audit cannot establish signed/context-bound semantic claims.
- Render factual content from validated blocks; a claim ledger cannot certify
  contradictory free narrative/captions.
- Bind exact chart dataset/indices/render spec/bytes atomically and verify sidecar
  integrity, not just uploaded file bytes.
- Reuse immutable run/durable conversation/job work; distinguish authentication,
  authorization, hash integrity, evidence matching and scientific correctness.
- Include malicious annotations, revocation races, historical/cached references,
  units/locale/signs and view/progress semantics in acceptance requirements.

## Formal review and disposition

Formal review identified three required changes before publication:

1. Basic approved-only UI/history/summaries/exports/current TTS belongs in P2,
   not deferred to P6. Until P3 outcome adapters exist, withhold unsupported
   action-completion claims. **Resolved** in spec, phase table and ticket 03.
2. Freeze minimum v1 fields/order/negotiation/API status codes before acceptance
   tests. **Resolved** in [protocol-v1](../../../.scratch/assistant-assurance/protocol-v1.md).
3. Commit owned approved record+receipt before public tokens/links/speech; SSE
   delivery is not atomic. Permit authenticated idempotent fetch on interrupted
   delivery without regeneration/tool replay. **Resolved** in spec and plan.

The separate agent re-reviewed all revised core documents and eight issues and
gave this final verdict: **GO for documentation publication and opt-in expected-red
acceptance tests; no design blockers remain; not implementation/release approval.**

Implementation caution incorporated: ordinary tool lifecycle frames can precede
answer approval, but strict public result projections must omit unchecked prose
and unapproved artifact URLs. Existing AssistantResultCard must not bypass the
gate. Approved artifact links wait for required publication checks.

Remaining release gates: real domain fixtures, independent interpretation
calibration, security/recovery races, browser accessibility, measured performance
and supported voice/device tests. Plan review is not a correctness guarantee.
Review was read-only; the reviewer ran no tests or paid calls. Initial review was
interrupted by an agent usage limit after delivering findings; resumed review
completed successfully and approved the revisions.

# LC-04 — supported parameter edits and verified postconditions

Status: ready-for-agent
Type: task
Baseline: master `c75ca5584b873418f10f1bca42f868255d445b7b`
Dependencies: LC-01/02 and ordinary admission/confirmation

Read the [specification](../spec.md), [assessment](../gap-assessment.md),
[acceptance matrix](../test-plan.md) and
[implementation plan](../../../docs/superpowers/plans/2026-10-18-whole-application-live-copilot.md).
Applicable contracts/cases: LC-C1/C5/C10; LC-T02/03/04/05.

Domain cases: [LC-D02/09/15/17/18](../domain-coverage.md) and each additional
schema family. Independently check storage power/energy cost conversion and
annualization, total versus per-length line parameters and each control model's
unit/base. Unsupported checked scopes remain explicit after generic discovery.

Support exact entity/field/unit/basis selection from native schemas, complete old/new previews and verified writes. First independent fixtures cover Store/StorageUnit cost fields and several line parameters; they are examples of the generic interface. Read-only/computed/unknown fields, ambiguity and unavailable checked effects must not be guessed.

2026-10-19: the first storage/line slice is VS-3 in the proposed order and must pass
[OJ-05/06](../owner-journeys.md). Whether Expert chat edits show an old→new card is an open owner decision.

Expected ownership: native parameter schema/read/write adapters, confirmation previews and effect receipts; no raw code or URL executor.

Rebaseline current master and incoming interfaces before implementation. Write
one meaningful public-seam RED case, then implement that slice and regress its
neighbors. Record actual source/commands/results and remaining gates here.
No new capability is enabled by this issue. Paid calls, reviewer contact,
deployment and full-coverage/domain-approval claims remain outside this package.


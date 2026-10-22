# LC-04 — supported parameter edits and verified postconditions

Status: done (#479, d327f645)
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

2026-10-19 owner decisions: LC-04 closes when [OJ-05/06](../owner-journeys.md) pass (VS-3).
Every chat edit shows an old→new card before it applies, in Expert and Guided alike (Q3).

Expected ownership: native parameter schema/read/write adapters, confirmation previews and effect receipts; no raw code or URL executor.

Rebaseline current master and incoming interfaces before implementation. Write
one meaningful public-seam RED case, then implement that slice and regress its
neighbors. Record actual source/commands/results and remaining gates here.
No new capability is enabled by this issue. Paid calls, reviewer contact,
deployment and full-coverage/domain-approval claims remain outside this package.


## Record (2026-10-20, session on `claude/lc04-edit-card`)

Rebaselined on master `fed26ad4`. Found: Expert ran every `write`-tier chat
tool with no card (`harness/confirm.py` `_confirm_tiers`); the card payload
carried raw args only; old→new existed only for `apply_project_changes`.

RED first: `tests/test_oj05_selected_line_edit_journey.py` failed with no card
in Expert. Then built:

- `harness/edit_card.py`: the one preview → confirm → receipt seam (README
  "Edit cards (LC-04)"). Edit classification pinned for all 76 write tools.
  Component adapter runs the native handler on an isolated copy; settings
  adapter for `update_solver_config`; an `arguments` card for edits without an
  adapter (never `verified`). Digest re-checked before the write.
- Frontend `components/EditCard.tsx`: card table and receipt line (stale,
  undo); a malformed card cannot be approved.
- Prompt fragment `prompts/edit_card.md` (pinned).

Results: `tests/test_edit_card.py` (18), OJ-05 Tier A (15, both wires, Expert
and Guided, deny, undo, refusals), OJ-06 Tier A (4), `EditCard.test.tsx` (4).
Storage check: StorageUnit energy part 150 EUR/kWh, 15 y, 4 h → capital_cost
65 876.77 EUR/MW/yr = hand annuity. A Store's capital_cost is annualised at
solve time, so its card shows no annual row.

Open: GK answers on Store annual-row note and StorageUnit `overnight_cost`;
launcher overlap (L2); `get_energy_flows` after #423; adapters for the other
settings edits (they show `arguments` cards today).

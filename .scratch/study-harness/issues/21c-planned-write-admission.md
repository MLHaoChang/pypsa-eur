<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 21c — Stage-2 planned writes: admit `apply_demand_from_excel` under the envelope by its own card digest

Status: ready-for-agent
Type: task
Blocked by: 21
Gated: yes (GK B)
Model tier: opus
Owner decisions: SH21-SPLIT-1, SH21-ACCEPT-1, CHAT-DATA-SPEC-1, LOAD-SHAPE-1, COP-1
Design: [design.md](../design.md) §3 (stage 2), §6.6, §7; issue 21 §2 "Stage 2" and §6 C1–C3

## Why

Issue 21 lets a run take setup inputs from the chat and create its project at
the grant. Its stage-2 half, intake rows that are **planned writes** on the
created network (a load time series from an upload), was split out by the
owner (SH21-SPLIT-1): until this issue lands, `apply_demand_from_excel` stays
carded inside a run, as it is today. GK B (19) Q2 (a) admits only this one
tool here; `apply_equipment_mapping`, `apply_document_values` and
`attach_tariff` as planned writes stay carded until each has its own §6.6
sweep test.

## What to build

1. **An edit card for `apply_demand_from_excel`** (`harness/edit_card*.py`):
   the rows the write makes (the Load's `p_set` series, by load uid via
   `services/component_identity`, with the upload id, sheet, columns and the
   series' sha256), its digest from the card's own `_digest`, and a readback.
   The tool resolves the project through the bound context (the §6.6 sweep
   test, `tests/test_harness_run_skeleton.py`) before it joins
   `run_profiles.BOUND_CONTEXT_TOOLS`.
2. **Planned writes from intake.** `run_intake` records a `write` row
   `{write: "apply_demand_from_excel", args, source}` for a cited load
   series; the plan-review card lists it, and the grant digest binds it.
   `EH_SITE["inputs"]["required"]` names the item when the profile needs it.
3. **Stage-2 steps.** Before the profile's fetch actions, stage 2 runs the
   granted planned writes in intake order, each through its own tool.
4. **Admission (C1–C3).** `run_envelope.admit` admits a planned write only
   when the card the tool computes on the live network has exactly the rows
   and source digest the grant listed, with targets bound by uid, not name
   (C1). Plan check and write share one network revision atomically (inside
   `admitted_write`, issue 22): a revision bump between them sends it to a
   card (C2). No tool from the sweep or study family joins (C3, the #875 rule).

## Acceptance criteria

- [ ] C1: a planned write whose card on the created network equals the granted
      rows and digest is admitted; a renamed load, a row the plan leaves out,
      or a changed upload gets a card, not an admission (tests).
- [ ] C1: targets bind by uid: a load deleted and re-made under the granted
      name gets a card (test).
- [ ] C2: a network revision bump between the plan check and the write sends
      it to a card (test, under the project lock).
- [ ] C3: `apply_demand_from_excel` joins `BOUND_CONTEXT_TOOLS` with its §6.6
      sweep test; no sweep or study tool joins (test).
- [ ] LOAD-SHAPE-1: no shaped load profile is written without a card or a
      user-confirmed intake row (test).
- [ ] Issue 03's §7 refusal matrix still passes.

## Comments

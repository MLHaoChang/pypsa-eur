<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-47 — One change-set card: a tick box per row, replacing a card per fix

Status: ready-for-agent
Type: task
Model: Opus (design touches the confirmation and read-back path)
Reviewers: GK B, UX (card UI)
Blocked by: none
Source: owner decision C1C9-FILE-1 (2026-11-10); change C3 of the workflow review (artifact RKkscAxy5yZG6EuxjWH71W); COPILOT-1 ("faults diagnosed and fixed as one change-set card").
Evidence: **confirmed, text only.** No cards appear without chat, so the per-fix cards were not walked (no API key); the Guided write rule was read from the code.

## What

One card lists every proposed write of a step (gap fixes in run-study, finding fixes in hub-design, profile applies in renewable-profile — today a card each) with:

- a tick box per row, the old and new value and the source of each new value;
- one approval for the ticked rows; the harness applies them under one confirmation and reads each back (`harness/effects.py`, `outcomes.py`).

It extends the existing edit cards (`harness/edit_card*.py`, `EditCard.tsx`) and keeps the Guided write rule: Guided still sees a card, one per step instead of one per row.

## Keep

Destructive rows (deletes, imports) stay on their own card, one at a time; `CARD_ONLY_TOOLS` are never auto-approved. An unticked row is not written. A partly failed apply reports which rows landed (checked outcomes, no silent success).

## Done when

- A step with N proposed writes raises one card; ticking a subset applies exactly that subset, each read back.
- Destructive rows never join the set.
- Tests for tick/untick, partial failure and the Guided rule; shots; release-note fragment.

## Likely files

`pypsa-gui/backend/harness/edit_card.py` and `edit_card_*.py`, `harness/confirm.py`, `harness/effects.py`, `harness/outcomes.py`, `pypsa-gui/frontend/src/components/EditCard.tsx`, `harness/workflows/run-study.md`, `hub-design.md`, `renewable-profile.md`.

## Comments

**Plan GO, GK B (2026-11-10).** Routing goes first: members come from what LC-16 routing leaves, and two cards in one step is acceptable. Slices: S1 is the backend with membership off behind a module constant; S2 is `ChangeSetCard`, switching membership on, and the workflow text. Conditions:
K1 preview the members cumulatively on one copy and put the combined `requirement_violations` on the card; a set that fails combined is not offered.
K2 two members on the same component never share a set.
K3 an item's digest covers what it writes; a stale row is refused `edit_preview_stale`.
K4 one test per membership exclusion, with hand-mutation.
K5 `/confirm` `ticked` rules: 400 on a non-set token or an unknown id, `[]` = deny, missing = all.
K6 new error kinds in a `tool-error-kinds.d` fragment.
K7 the receipt never counts `uncertain` as landed, and there is one tool_result per tool_use.
K8 never auto-approved; expiry or deny denies all; `decided(by="change_set:<token>")` only for ticked rows.
K9 `source` comes only from an adapter field, never model text.

**Plan GO, UX (2026-11-10).**
C1 the receipt is in words ("Applied", "Applied: not confirmed yet", "Failed: <reason>", "Not applied (unticked)").
C2 a count line "3 of 4 applied · 1 failed" in role=status.
C3 when 0 rows are ticked, the disabled Apply shows its reason as text.
C4 true settled lines: "Cancelled: nothing changed", and timeout, stop and reload each show a true line.
C5 the same field labels in both modes.
C6 a failed row's reason stays on its row.
C7 shots: dc Site 4 rows with one unticked, before and after, dark and light, partial failure, 0 ticked, Cancel all, Expert, renewable-profile with several generators.

**K1 ruling, GK B (2026-11-10): option A**, a union check over the members' native rows.
K1a: allow-list `scalar_bound` only; any other kind means no set.
K1b: K2 targets come from each preview's own rows, derived rows included; an incomplete preview (or a "more" note) is not a member.
K1c: the K2 split is tested, and a union that trips a bound means no set.
K1d: the card says the rows were checked together; the allow-list, overlap and union checks are hand-mutated.
GK B rotated to (42) session_01G7qCujySwKRbkMQGwxXfz2.

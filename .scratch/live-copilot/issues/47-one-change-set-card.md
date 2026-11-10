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

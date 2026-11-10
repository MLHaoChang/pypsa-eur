<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-46 — One Assumptions card: every parameter with its default, accept all or edit inline

Status: done (#1313)
Type: task
Model: Sonnet
Reviewers: GK B, UX (card UI)
Blocked by: none
Source: owner decision C1C9-FILE-1 (2026-11-10); change C4 of the workflow review (artifact RKkscAxy5yZG6EuxjWH71W); `.scratch/ui-modern/audit.md` finding 4.
Evidence: the Guided count is **measured** (walkthrough of master 64686f9d). The chat count is **text only** (no API key, chat not walked).

## What

Replace one-question-per-parameter and a wall of "assumed" rows with **one Assumptions card**: every parameter with its recommended default and source, an **Accept all defaults** button, and inline edits for the few the user knows.

- **Guided hub-design guide (measured):** the "assumed, confirm it or set your own" rows number **5** on the data centre (4 in Connection & grid, 1 in Scenarios) and **15** on the island microgrid (2 + 4 + 7 + 2 across four sections). Each row has two links ("Open here", "Open in Expert") and no accept-all; the microgrid's Connection & grid section is headed "not needed" yet lists four. Rows carry raw keys (`p_max_pu 0`, `tau_f`, `RoCoF`, `island_constraints`) and the same warning icon as a real problem.
- **Chat (text only):** `investment-decision.md` (lines 41-44) asks one question per key parameter; `renewable-profile.md` asks sites, technology, preset, weather, resolution in turn. Ask them as the same card.

The answers fill the study ledger in one call (`set_study_parameter` in a batch, or a new `set_study_parameters`).

## Keep

Inputs the user must give themselves (chat-intake citation rules, LOAD-SHAPE-1) stay required fields, never defaults. Assumed values stay visibly marked as assumed until accepted. DENSITY-1 and `docs/agents/review-ux.md` ("one bulk action, no duplicate buttons") apply; plain words, not raw keys.

## Done when

- Guided shows one card per section (or one for the guide) listing all assumed values; Accept all confirms them in one write; an inline edit changes one without leaving the card. Counts 5 and 15 are the test fixtures.
- investment-decision and renewable-profile ask once, with the card, not per parameter.
- Required-from-user inputs never get a default or an accept-all.
- Tests, shots (dark and light, data centre and microgrid) and a release-note fragment.

## Likely files

`pypsa-gui/frontend/src/pages/hubDesign/SectionView.tsx`, `pages/hubDesign/cards/SiteCard.tsx`, `pages/decision/DecisionPanel.tsx`, `components/FieldDrawer.tsx`, `harness/workflows/investment-decision.md`, `renewable-profile.md`, `services/chat_tools.py` (`set_study_parameter`), `harness/edit_card.py`.

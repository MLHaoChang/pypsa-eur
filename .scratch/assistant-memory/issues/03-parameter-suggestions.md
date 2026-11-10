<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Slice 2: reusable parameter suggestions

Status: needs-triage
Blocked by: 02
Type: feature
Model: Opus. Reviewers: GK B, and UX for the UI.

Saved parameter values come back as suggestion chips next to the same field in later projects, with their source, date and currency stamp. They are applied only when the user picks one.

## Comments

### Plan (2026-11-10, session_01G7qCumi2nYUjmaVWVPaYjA)

Two slices (PR-SIZE-1, two reviewer areas): **03a** backend (store `parameter` kind, `suggestions()`, `node()`, `GET /api/memory/suggestions`, `POST /api/memory/parameters`, `POST /api/memory/suggestions/{id}/preview`, `POST /api/network/memory_parameter/apply`; GK B) and **03b** UI (chips in the asset card's edit mode, pick card, Save for reuse, Memory dialog wording, shots; UX + GK B), 03b after 03a merges. A pick is written only through the existing `update_component` edit card, stamped `memory:mem_<id>@v<n>` (plan §7.1), and bumps `apply_count` (closes the #1327 gap).

### UX GO with changes (UX 15, 2026-11-10), binding on 03b

- Save for reuse: placement (a), a muted text button beside each field in the read-out view only, shown on row hover and `:focus-within`, DENSITY-1 size.
- P1 Chip: "Used before: 7 % in Project A", no "›"; second line "Saved 2026-11-03" (ISO, as the Memory dialog); cost fields add the price-year stamp in the Compare with references / `money()` notation.
- P2 A chip equal to the current value is hidden or marked "current"; Apply never proposes a no-op.
- P3 Popover: Esc, Apply and Cancel return focus to the chip. A refusal replaces the card, opens with the field label ("Capital cost: saved in USD, this project uses EUR"), no keys or repr quotes.
- P4 Toast `role="status"` with a timeout; project kind in user words ("Saved for reuse in capacity expansion projects · Undo").
- P5 Memory dialog param words "<Field label> (<Class in user words>)" via `componentLabel`/`FIELD_LABELS`, with a parity test.
- P6 The PR says whether the Guided field drawer gets chips; if not, a named follow-up. Expert may show more than Guided, not the reverse.
- Shots: chip row, card popover, a refusal, Save toast, a chip marked current (or hidden case), Save button on focus, the Memory dialog row.

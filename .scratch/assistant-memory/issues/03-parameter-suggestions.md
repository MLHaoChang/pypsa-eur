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

### GK B GO with changes (GK B 42, 2026-11-10), binding on 03a (and 03b where it applies)

- P1 Chat path: `parameter` items are written only by the Save route; the chat proposal tool refuses `parameter` (`memory_kind_unavailable`). A test through the real tool, and a killed mutant that drops the refusal.
- P2 Money basis: Save on a cost field with neither a provenance currency nor a project money basis refuses by name (no unstamped item). Preview/apply refuse a differing currency (`currency_mismatch`) and a differing currency_year (`memory_money_year_mismatch`); never converted or relabelled.
- P3 Carrier: chosen same-carrier only. An item with a carrier is suggested only for a component with the same carrier; preview/apply refuse a mismatch (`memory_carrier_mismatch`).
- P4 Card and digest: the digest binds item id, version, value, unit, currency, currency_year, carrier and the target's current value and source; the card shows each. At apply a retired or dismissed item, or a newer version, gives 409.
- P5 Apply: through `update_component`'s validators, under the same mutation lock and revision bump as `library_reference/apply`. Undo restores the provenance entry exactly (byte-equal `replaces`), tested. `record_apply` runs after the network write, outside the project lock (mutation → solver_state → store); if it fails the apply stands and the result says the count was not recorded, tested. The change-log entry is bound to the project's org.
- P6 Access: suggestions need project read access, Save needs write access; an unknown class or field is 422, never 500. Tests: a cross-org org item (M1), a retired item never suggested.
- P7 Mutants: hand-mutate every guard (M1 org, field-in-schema, unit, currency, money year, carrier, version, digest, active-project 409, P1 chat refusal) and name the killing test; run `scripts/coord/mutants.py` on memory_store and the new routes and list survivors.
- P8 Trial merge: route-policy trio, smoke `--check`s (inventory, route_parity, control_matrix), the error-kind manifest test, the memory and library_reference/apply suites.

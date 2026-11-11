<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Slice 2: reusable parameter suggestions

Status: done (03a #1347; 03b #1386)
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

### GK B GO with changes (GK B 47, 2026-11-11), binding on 03b

With GK B 42's P2–P5 above. The `saveable` field goes in this UI PR under A1.

- A1 `saveable` is Save's own predicate: computed by calling the function the Save route uses to accept or refuse, never rebuilt from its inputs (#1343). Backend test: for every numeric field on fixtures with a plain MW bus, a non-MW bus, a money field and a derived field, `field in saveable` equals "Save succeeds", both directions; a mutated Save rule turns it red.
- A2 Save's Undo retires by the id the save returned, and only when the response says it created the item. A re-save is a new version with no Undo. Tests: a re-save leaves both versions; Undo after a first save retires exactly that id.
- A3 "current" comes from the server flag or the preview's no-op refusal, never a client compare. Test: a value equal only after rounding is not marked current.
- A4 The pick card shows every value its digest binds (value, unit, currency, price year, carrier, memory version, source project, target field); the client posts the digest untouched. Tests: changing any one in the preview changes the card text; the posted body equals the preview's digest fields.
- A5 No silent conversion: chip and card show money-year and currency as saved; a refusal opens with the field label. Test: one of each 03a mismatch kind.
- A6 Fail closed on stale data: suggestions refetch after apply, save, and a bus or carrier edit; a 409 (`edit_preview_stale`, `memory_stale`) applies nothing and offers only "Preview again". Test: a 409 on apply leaves the network revision unchanged.
- A7 Trial merge: `assistant_route_parity`, `assistant_control_matrix`, `assistant_inventory` `--check`; `test_route_chat_disposition`, `test_project_route_acl_policy`, `test_write_surface_lock_policy`, `test_golden_coverage`; `ChatPanel.manifest.test.tsx` if kinds are added.

### UX GO on the 03b plan (UX 18, 2026-11-11)

- Re-save toast "Saved value updated (version n)" with no Undo is acceptable (the store has no drop-latest action); it says the earlier value stays in the Memory dialog as a version, with no dead Undo button.
- 1 Memory line: "From memory v2 · saved in Project A · 2026-11-03 · 2024 EUR · carrier solar". A refusal is a sentence opening with the field label, no keys, no repr quotes.
- 2 A row refusal shown in place clears when its condition ends or after a timeout (#1183), in a `role="status"` region.
- 3 Chips: "current" marker; cost chips use `money()` with the price-year stamp; one entry point (no second Apply link); the 3-chip cap shows "(N more)" only if the user can reach them.
- 4 Save on hover and `:focus-within` is a real button in the Tab order with a visible focus ring; the row does not shift when it appears.
- 5 Expert-only: the PR names the flag that hides chips and Save in Guided, with a test that the Guided FieldDrawer renders neither; ticket 06 as planned.
- 6 Shots as listed, dark and light, plus one narrow width (no clipping or overflow). Harness shots with route mocks are fine.

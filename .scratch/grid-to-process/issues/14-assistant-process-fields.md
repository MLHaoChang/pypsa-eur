# Assistant: process-unit fields and the template parameters

Status: done
<!-- 2026-10-31: done, commit 189b0bba9. -->
Type: task
Blocked by: none (was 07; the template listing moved to issue 10 by owner G2P-14-NOW)
Gated: no (harness)
Model tier: sonnet
Size: S

Read `pypsa-gui/backend/harness/README.md` first; it is the contract.

## What to build

- The chat tools that create or edit Links accept the process-unit fields from ticket 02.
- `create_project_from_template` lists `eh_ammonia_staged` and `eh_concentrator_staged`.
- Regenerate the assistant inventory, as the harness README requires.

## Acceptance

- The inventory check passes.
- A chat smoke creates an HB-like unit.

## Triage (2026-10-28)

**Verified on master (41ade3be):** mostly done.
- The assistant's create/edit-component attribute docs (`services/asset_schema/attrs_doc.py`) already list the Link process-unit fields: `eh_process`, `criticality_tier`, `throughput_unit`, `specific_energy_kwh_per_t`, and the 17a harmonic and flicker fields. The Bus fields `unit`, `mwh_per_t` and `product_value` are there too.
- `create_project_from_template` already lists `eh_ammonia_staged`.

**Left:**
1. Bus `lost_production_value` and Generator `process_role` (`buy_in` | `shortfall`) are not in the attrs doc.
2. No chat smoke creates an HB-like unit.
3. `eh_concentrator_staged` cannot be listed until 10 makes it.

The old blocker 07 (a frontend form) shares no code with this, so it is dropped. Item 3 depended on 10. Owner decision **G2P-14-NOW** (2026-10-28): items 1–2 go now; item 3 moved into issue 10's acceptance.

### Agent brief

**Category:** enhancement
**Summary:** the assistant can set every process-unit field and create from every process template.

**Desired behaviour:**
- The attribute docs the chat tools read name Bus `lost_production_value` and Generator `process_role` (with its two values), in the same compact style. Writes go through the existing schema validation.
- The assistant inventory is regenerated as `pypsa-gui/backend/harness/README.md` requires.

**Acceptance:**
- [ ] The inventory check passes.
- [ ] The attrs-doc compact cap test passes. If it cannot, ask the owner (as G2P-17A-DOCCAP); never raise the cap unasked.
- [ ] A chat smoke creates an HB-like unit: an `eh_process` Link with a negative `efficiency2`, a product bus with `mwh_per_t`, and a `shortfall` Generator. The saved network then has `process_view` rows for that product.

**Out of scope:** the frontend form (07); motor fields (12a); listing `eh_concentrator_staged` (issue 10, G2P-14-NOW); new chat tools.

**Likely files:** `pypsa-gui/backend/services/asset_schema/attrs_doc.py`, `pypsa-gui/backend/harness/catalogue.py`, the generated assistant inventory, `pypsa-gui/backend/smoke/assistant_inventory.py`, tests under `pypsa-gui/backend/tests/`. The harness is busy (open PRs #795, #802, #812), so the frontier holds this until those land or the files don't overlap.
**Blocked by:** none.
**Model:** Sonnet (harness docs and a smoke).

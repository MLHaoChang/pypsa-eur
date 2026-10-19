# Process unit in the asset schema, carriers and API

Status: done
Type: task
Blocked by: 01
Gated: no (asset schema and catalogue; no solve change)
Model tier: sonnet
Size: M

Read [the design](../design.md) §2.1–2.2 first.

## What to build

1. **Link fields** in `services/asset_schema/fields.py` (Link block, `:308-338`):
   - Add `bus2..bus4` and `efficiency2..efficiency4`. `LinkCreate` already accepts them
     (`models/schemas.py:180-211`).
   - Add the unit-commitment block. Move the Generator's UC rows (gate `_UC`, `:85`; rows
     `:165-182`) into a shared helper and reuse it for Link:
     - `committable`
     - `min_up_time`, `min_down_time`
     - `ramp_limit_up`, `ramp_limit_down`
     - `stand_by_cost`
     - `start_up_cost`, read-only with a note (see ticket 03)
2. **New Link columns:**
   - `eh_process` (bool);
   - `criticality_tier` (`safety | process_critical | process_flexible | deferrable`);
   - `throughput_unit` (display only);
   - `specific_energy_kwh_per_t` (display only, derived).
3. **Carriers** in `services/carrier_catalog.py`:
   - Add `NH3`, `N2`, `ore` and `concentrate`, each with a `unit` (`MWh` or `t`; design D2).
   - Fix the dangling heat filter (`CreationForm.tsx:210` filters on a carrier the catalogue
     does not define): add `heat`, or remove the filter.
4. **Bus unit.** A product bus carries `unit`, and for an energy product the factor to tonnes
   (NH3: 5.166 MWh/t). Results use it to show tonnes.

## Acceptance

- A multi-port committable Link round-trips: create, then PUT (Asset write, full row), then
  reload. No field is lost.
- The UC fields show only when `committable` is set, as they do for Generators.
- The existing CHP form and existing projects are unchanged (regression test).

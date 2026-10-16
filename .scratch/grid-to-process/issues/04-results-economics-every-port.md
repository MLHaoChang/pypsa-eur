# Results and compare economics read every Link port; process view

Status: needs-triage
Type: task
Blocked by: 02
Gated: **yes** (economics)
Model tier: opus
Size: M

Read [the design](../design.md) §2.2 (items 4 and 5) first.

## What to build

1. **Per-port flows.** `services/asset_results/compute.py` reports `p0..p4` for multi-port
   Links.
2. **Compare economics.** `services/compare/economics.py:571-598` counts |p0| × efficiency in
   bus1 only.
   - Read every port.
   - Price each input port at its bus's price, as bus0 is today.
   - Price each output port at the product's value when one is set, else Unavailable.
3. **Process view.** A results payload per product:
   - production (t and MWh);
   - delivery;
   - the inventory level series;
   - lost production (t, and EUR at the lost-production value);
   - running hours and starts per process unit.

   A figure that cannot be resolved is null with a flag (ADR-0001). For example, lost EUR with no
   lost-production value is null, not 0.

## Acceptance

- On the A template (ticket 05): the HB's H2 drawn equals 5.93/5.166 × the NH3 made (p2 vs p1).
- Single-port Links give the same results as before (regression).

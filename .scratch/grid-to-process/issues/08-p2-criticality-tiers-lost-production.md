# P2: criticality tiers and lost-production value

Status: needs-triage
Type: task
Blocked by: 05
Gated: **yes** (adequacy)
Model tier: opus
Size: L

Read [the design](../design.md) §1.2 and §5 first.

## What exists

- VoLL is one global solver field, default 0 (`solver_service.py:175`).
- DSR is a priced resource with carrier `demand_response` (`adequacy/slack.py:39`). It
  curtails; it does not shift load.
- Criticality today is the `eh_critical` bus.

## What to build

1. **Tiers per process unit**, as `criticality_tier`:
   - `safety`: VoLL.
   - `process_critical`: a stop costs restart lost tonnes.
   - `process_flexible`: it can stop and catch up from inventory.
   - `deferrable`.
2. **The lost-production slack per product** (`__lost_<product>`) is created from the
   product's lost-production value, beside the VoLL slack, with the same naming discipline
   (`slack.py:50-55`).
3. **Adequacy and DtC** (EH study, MC, FMEA) report lost tonnes and EUR per process unit as
   well as unserved MWh.
   - An outage of the electricity supply to a `process_critical` unit adds `restart time ×
     rate`.
   - The tiers set the island pickup order where the island sidecar has no explicit blocks.
4. **The criticality tier** is separate from the `eh_critical` bus. The design keeps both. A
   safety-tier unit sits on the critical bus.

## Acceptance

- In the A template, a 12 h grid outage at full HB load gives lost NH3 = (outage + restart) ×
  rate, minus what the NH3 tank covers. A test pins it.
- With no lost-production value, lost EUR is null, flagged.

# B tracer: `eh_concentrator_staged`

Status: needs-triage
Type: task
Blocked by: 08, 09
Gated: no (template data)
Model tier: sonnet
Size: M

Read [the design](../design.md) §3.B first.

## What to build

The template on the `year_3h` time base, with every number from design §3.B and its label in
the ledger:

- the flowsheet, with the mine as a fixed ore Generator;
- the crusher, the coarse-ore stockpile, 2 (then 3) mill lines, flotation, the concentrate shed,
  the 21-day shipments and `__lost_concentrate`;
- the connection: a 2027 `non_firm_dynamic` envelope, then firm in 2030 and 2035;
- PV and BESS candidates per period;
- a PoC fault level of 150 MVA.

The commercial and finance blocks follow the generic site's industrial preset.

## Acceptance

Design §3.B, "Acceptance checks (B)", except:
- the investment-case check, which belongs to ticket 11;
- the motor-start check, which belongs to ticket 12.

# B tracer: `eh_concentrator_staged`

Status: ready-for-agent (triage 2026-10-28: still blocked by 09, and by 09's measurement being within budget)
Type: task
Blocked by: 08, 09
Gated: no (template data)
Model tier: opus
Size: M

Read [the design](../design.md) §3.B first.

## What to build

The template on the hourly `year` time base (D4), with every number from design §3.B and its label in
the ledger:

- the flowsheet, with the mine as a fixed ore Generator;
- the crusher, the coarse-ore stockpile, 2 (then 3) mill lines, flotation, the concentrate shed,
  the 21-day shipments and `__lost_concentrate`;
- the connection: a 2027 `non_firm_dynamic` envelope, then firm in 2030 and 2035;
- PV and BESS candidates per period;
- a PoC fault level of 150 MVA.

The commercial and finance blocks follow the generic site's industrial preset.

The concentrate has no buy-in, so only `__lost_concentrate` applies.

## Acceptance

Design §3.B, "Acceptance checks (B)", except:
- the investment-case check, which belongs to ticket 11;
- the motor-start check, which belongs to ticket 12.

## Triage (2026-10-28)

**Verified on master (41ade3be):** not built; no `eh_concentrator_staged` anywhere. 08 is done; 09 is not.

**Model tier raised to opus.** The template has to solve on a 26,280-snapshot year with linearised UC and pass the 1e-6 mass balances. Debugging an infeasible or slow year LP is engine work, not data entry.

### Agent brief

**Category:** enhancement
**Summary:** the `eh_concentrator_staged` template on the `year` time base, with design §3.B's numbers ledgered.

**Desired behaviour:**
- A new staged template `eh_concentrator_staged`, built like `eh_ammonia_staged`, with the same template registration, ledger and sidecars. It contains the flowsheet and numbers of design §3.B (Flowsheet, Numbers), each number labelled in the ledger as `demo` or `sourced`.
- It uses `stage_network(time_base="year")` from 09.
- The commercial and finance blocks follow the generic site's industrial preset.
- The assistant's `create_project_from_template` lists `eh_concentrator_staged`, and the assistant inventory is regenerated (owner G2P-14-NOW, 2026-10-28: moved here from issue 14).

**Acceptance:**
- [ ] `create_project_from_template` with `eh_concentrator_staged` creates the project, and the assistant inventory check passes.
- [ ] Design §3.B "Acceptance checks (B)": the year solve within 09's budget, the mass balance, the shed carrying across shipments with the week build refused, non-firm curtailment, and restart. Two exceptions: the investment-case check (11) and the motor-start check (12).

**Out of scope:** the IC blocker (11); motor fields (12); any change to `stage_network` beyond calling it.

**Likely files:** new `pypsa-gui/backend/project_templates/eh_concentrator.py` and `pypsa-gui/backend/project_templates/eh_concentrator_staged/`, `pypsa-gui/backend/project_templates/eh_templates.py`, `pypsa-gui/backend/routers/projects.py`, `pypsa-gui/backend/services/flow_scope.py`, `pypsa-gui/backend/harness/catalogue.py`, new tests under `pypsa-gui/backend/tests/`.
**Blocked by:** 09, and 09's measurement within the ≤ 30 min budget. If 09 reports over budget, this goes back to the owner.
**Model:** Opus.

## Notes from 09 (2026-10-28)

- Set `delivery_interval_h = 504` on the shed; the validator then refuses the week build of the same network
  (`inventory_cadence_needs_year`, naming the shed).
- The binary UC check (`services/solver/process_uc.py`, `CHECK_WINDOW = 168`) re-solves the first 168 snapshots of
  each period. On the year base that is the first week of January (from 2030-01-01), so it holds no shipment of a
  cycle loaded out at hours 480–503; say so in the template's ledger, or choose the year's start.
- The campus island report (`services/campus_electrical_service`, `campus_island_report.py:259-264`) lists every
  failing hourly E1 row uncapped; on 26,280 hours that table can run to thousands of rows.

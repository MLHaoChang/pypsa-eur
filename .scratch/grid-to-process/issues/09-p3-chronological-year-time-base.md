# P3: a chronological year at 3 h as a staged-template time base

Status: needs-triage
Type: task
Blocked by: 03
Gated: **yes** (solver: it changes the weighting convention the staged templates share)
Model tier: opus
Size: L

Read [the design](../design.md) §2.4 and §4.2 (owner decision D4) first.

## What to build

1. **`eh_site.stage_network`** takes a time base: `week` (today) or `year_3h`.
   - `year_3h`: 2,920 snapshots per period, with weightings `objective` = `generators` =
     `stores` = 3.
   - Stores are `e_cyclic_per_period` over the year.
   - `min_up_time` and `min_down_time` are converted as ⌈hours / Δt⌉ (4 h → 2), and the ledger
     says it rounds up.
2. **Measure before building B.** Run a demo concentrator-sized network (about 30 components,
   3 periods) at `week`, `year_3h` and a full hourly year on HiGHS on the CI runner, and report
   the times to the coordinator. The owner's D4b gate is ≤ 10 min for `year_3h`.
3. **Audit everything that assumes 1 h or 168 h snapshots** (the gatekeeper's list, design
   §4.2). Each place gets a test that runs the same dispatch at 1 h and aggregated to 3 h and
   compares, or a stated refusal:
   - **`commercial/tariff_engine.py`:**
     - energy items;
     - demand or capacity charges: a ledgered peak factor, or a refusal (decide which);
     - `_HOURS_PER_YEAR`.
   - **`commercial/lp_bindings.py`:** kWh = MW × the interval's hours.
   - **`commercial/connection.py`:** nyears and the FCA hours target.
   - **`results/finance_case.py`:** `:2220-2226` and `:1819-1888`.
   - **`adequacy/metrics.py`:** `HOURS_PER_YEAR`, the ±2 % tolerance, and LOLE/EENS weights.
   - **`solver_service.py`:** `rolling_horizon` and `lf_period_length_h` units.
   - **168 and 8760 literals:** `routers/network_time_axis.py`, `time_aggregation_service.py`,
     `validation_service.py`, `profile_shapes.py` and `study/packs.py`.
   - **Island and campus:** the island rows (`solver/island.py`) and gridspine's campus tables
     and ranking. Test them, or refuse island and campus studies on `year_3h`.
4. **The inventory validator rule** (design §2.4, gated): a representative-week template refuses
   an inventory whose delivery is not continuous or at most weekly, and names it.

## Acceptance

- A Store with a 21-day delivery cycle is planned the same on `year_3h` and on the hourly year,
  within a tolerance the ticket states.
- The week-based templates are unchanged (regression).

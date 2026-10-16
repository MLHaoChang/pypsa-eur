# A tracer: `eh_ammonia_staged`

Status: needs-triage
Type: task
Blocked by: 03, 04
Gated: no (template and pack data; it does not change engine code)
Model tier: sonnet
Size: M

Read [the design](../design.md) §3.A first.

## What to build

1. **Settle the Haber-Bosch electricity unit first (D3).**
   - `config.default.yaml:1019` says 0.2473 MWh per t NH3.
   - Upstream's link reads technology-data's `electricity-input`, which is per MWh_NH3.
   - Pin the value against technology-data v0.14.0 `costs_2030.csv` (the version the pack
     already cites) and write the answer into the ledger.
2. **Pack rows** in `services/library/defaults_pack`: `Haber-Bosch` and
   `NH3 (l) storage tank incl. liquefaction`, from technology-data v0.14.0, with their source
   lines. Follow how the pack's versions are added; ask the coordinator if a new pack version is
   needed.
3. **The template** `eh_ammonia_staged` in a new module beside `eh_staged_hubs.py`, registered
   the same way. It starts from `build_eh_h2_hub_staged` and changes the following:
   - It drops the fuel cell and `h2_offtake`.
   - It adds:
     - an `nh3` bus (MWh, factor 5.166 MWh/t);
     - `haber_bosch`: committable, fixed 160 t/d, p_min_pu 0.4, min up/down 24 h / 8 h, ramp
       0.2;
     - `nh3_tank`: extendable;
     - `nh3_delivery` at 50 / 100 / 150 t/d via `load_scale`;
     - `__buy_nh3` at the grey buy-in price (501 EUR/t);
     - `__lost_nh3` at the shortfall penalty (800 EUR/t, demo; above the buy-in);
     - extendable `h2_storage`.
   - Every number goes in the ledger with the label from design §3.A.
4. **Validator rules.** The validator rules (design §2.3 and §2.4) are gated and live in
   tickets 03 and 09. This template only has to pass them.

## Acceptance

Design §3.A, "Acceptance checks (A)", except the investment-case check, which belongs to
ticket 06.

# 02e — No-load fuel emits CO2

Status: needs-triage

Follow-up from homer-parity 02a (#1466), asked for by GK B.

## Problem

The CO2 price (`SolverConfig.co2_price`, `co2_price_per_period`) and the emissions accounting charge an emitter per MWh generated: carrier `co2_emissions` / `efficiency`. A curve generator's no-load fuel (`fuel_curve_no_load` × `p_nom` per committed hour) is paid through `stand_by_cost`, but the fuel it burns emits no CO2 in the LP or in the results. An idling genset is therefore under-charged and under-reported for carbon.

## Wanted

The no-load fuel's CO2 (`no_load` × `p_nom` × carrier `co2_emissions` per committed hour) is:
- priced in the LP when a CO2 price is set, on the stand-by term and transient like the existing adder;
- counted wherever generator emissions are reported.

Say which way efficiency enters, so the slope and the existing per-MWh adder don't count the same fuel twice.

## Tests

- A hand calculation for a committable curve genset with a CO2 price: the no-load CO2 is paid exactly once per committed hour and not at all while the unit is off.
- With no CO2 price, or a no-load term of 0, results are unchanged.

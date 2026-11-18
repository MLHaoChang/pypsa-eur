# 02j — The logged cost decomposition counts the no-load CO2

Status: done

Follow-up from homer-parity 02e. It was left out of 02e because the file overlapped open PRs #1521 and #1522.

## Problem

`services/solver/diagnostics.py` `_log_cost_decomposition_post_solve` logs per-period `co2_emitted_t` and the CO2 surcharge from dispatch × co2_emissions / efficiency. It does not use a curve genset's slope (02e H1) or its no-load CO2, so the logged split differs from the Results emissions and from the LP's charge.

## Wanted

Read the same terms as `services/results/emissions.py`: `fuel_curve.fuel_per_mwh` for the per-MWh term, plus `fuel_curve.no_load_fuel_t`.

## Tests

- The logged CO2 for a committable curve genset equals `compute_emissions`.

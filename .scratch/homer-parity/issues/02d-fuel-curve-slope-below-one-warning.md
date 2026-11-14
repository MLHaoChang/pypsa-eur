# 02d — Warn on a fuel curve slope below 1 MWh fuel per MWh

Status: needs-triage

Follow-up from homer-parity 02a (#1466), asked for by GK B.

## Problem

A fuel curve slope below 1 MWh fuel/MWh generated means more than 100 % marginal efficiency. That is almost always a units slip, such as a slope in m³/kWh, or a mix-up between the slope and the efficiency. The guard accepts any slope ≥ 0, so the solve prices the generator's fuel too low without saying so.

## Wanted

A pre-run warning, not a refusal, on any curve generator whose slope is above 0 and below 1. It names the generator and the implied efficiency (1 / slope), and opens with the form label as the other fuel curve messages do. Before wording it, ask UX whether the asset form should show it too.

## Tests

- A slope of 0.8 warns; 2.5 and 0 do not.
- No warning on a generator without a curve.

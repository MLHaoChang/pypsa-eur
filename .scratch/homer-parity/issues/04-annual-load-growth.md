<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04 — Load growth as a rate per year across the multi-year optimisation and the finance years

Status: needs-triage
Type: task
Gated: yes (GK B; GK A for the IC/adequacy engine path)
Spec: [spec.md](../spec.md) (HOMER-GAPS-1, owner addition)

## What

The owner: "Load growth is also very important to be considered for multi year optimization. The older module already considered a load growth factor per year."

What master has (keyword pass, 2026-11-12):
- `load_scalers` / `load_scalers_by_carrier`: a load multiplier per investment period, per carrier (Model Horizon → `StepPeriodEconomics.tsx`; `solver_service.py`, `adequacy/demand.py`), applied to the LP of each period.
- Staged templates and the generic site form enter the load per growth stage.

What looks missing (HOMER Multi-Year: a load growth rate in % per year):
- An input as a rate per year (per carrier), from which the period multipliers are derived (compounded from the base year), instead of typing each period's multiplier by hand.
- Growth in the finance years: whether a year between two investment periods, and the years after the last one, use the period's volume flat or grow with the rate. A single-period project probably gets no growth over its whole finance life.
- Where the "older module" kept its per-year factor: find it (legacy `load_scalers` or another path) and say whether it was dropped.

## Likely shape

A typed "load growth, % per year" per carrier in Model Horizon (and the generic-site form), stored with the derived `load_scalers_by_carrier` and shown next to them; the finance engine scaling load-linked volumes (bill, served energy, sales tied to load) per operating year from the same rate, with a test that a single-period case grows its volumes year by year. The LP itself stays per period.

## Triage

Confirm each "missing" item on master, find the older module's factor, decide the finance-year rule with GK B, name the files and the reviewers, and set the model tier.

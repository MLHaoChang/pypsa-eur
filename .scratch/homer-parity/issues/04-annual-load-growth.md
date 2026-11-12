<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04 — Load growth as a rate per year across the multi-year optimisation and the finance years

Status: ready-for-agent
Model: Sonnet for 04a; Opus for 04b
Reviewer: GK B (GK A only if adequacy/demand.py changes); UX for 04a
Blocked by: 04b blocked by 04a and by GK B's ruling on the rule below (asked 2026-11-12)
Size: 04a + 04b, two PRs
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

## Comments

### Triage 2026-11-12

Master check (`23aa3bc8`):
- Confirmed present: `load_scalers` (legacy, all carriers) and `load_scalers_by_carrier`, one multiplier per period year (`models/schemas.py:712-721`, `solver_service.py:255-275`, `adequacy/demand.py`, `services/results/finance_case.py:755-770`, UI `pages/modelHorizon/StepPeriodEconomics.tsx:98,247-248`). Typed per period; no "% per year" input anywhere.
- Confirmed missing: a rate input; any per-year load multiplier in finance. `finance/cashflow.py:112-123` `_templates_by_year` holds the period template flat (nominal, price escalation only) between periods and after the last; `cashflow.py:249` multiplies only price escalation and degradation. No test has a single-period project whose volumes grow over its life.
- **"The older module":** not in this repo. `git log -S` for load_growth, growth_factor, growth_rate, load_growth_pct finds nothing; no code in `gui_streamlit/`, `gridspine/`, `templates/`, `config/` carries one. The only per-period factor is `load_scalers`, whose empty keys already sit in `backend/tests/fixtures/pre_u2_fork/*/solver_config.json:25-26`, so it predates U2 and is the likely older factor. It was not dropped: `services/legacy_import.py` copies the project tree and reads only `metadata.json`, so a legacy `solver_config.json` keeps its `load_scalers`, which today's `SolverConfig` reads as the all-carrier fallback. If the owner means another module (an earlier GridSpine or Streamlit version outside this checkout), ask for its name or path: this does not block 04a.

### Finance-year rule (decided with GK B; **proposal A sent 2026-11-12, update here on the reply**)
Growth rate g per carrier from base year y0; period multiplier m_p = (1+g)^(Y_p - y0), an explicitly typed scaler wins. In finance, load-linked lines (bill, served energy, load-tied sales) in year y are scaled by (1+g)^(y - first year of the active template): years between periods grow from that period's LP volume, years after the last period keep growing through the tenor, a single-period project grows from its period. No rate = today's flat behaviour; typed scalers only = geometric interpolation between periods, flat after the last. The LP is not re-solved after the last period, so the result card states that grown volume there is an assumption.

### Slices
- **04a** typed "load growth, % per year" per carrier in Model Horizon and the generic-site form; derived `load_scalers_by_carrier` stored and shown next to the rate. Sonnet.
- **04b** finance per-year load multiplier from the rate (plug-in at `cashflow.py:249` and `:245-247`, selected by line source), with the card note. Opus. Blocked by: 04a, GK B's ruling.

### Acceptance
- [ ] 04a: g=2 % with periods 2030/2035 derives 1.0/1.104 (hand-checked), a typed scaler overrides its period, validation rejects g <= -100 %.
- [ ] 04a: Model Horizon shows rate and derived multipliers together; Guided and Expert agree.
- [ ] 04b: a single-period case grows load-linked volumes year by year (test); between periods and after the last follow the rule; non-load lines unchanged; no rate = byte-identical to today.
- [ ] Release-note fragment per PR.

### Likely files
`models/schemas.py:712`, `services/solver_service.py:255`, `adequacy/demand.py`, `services/finance/cashflow.py`, `finance_case.py:755`, `frontend/src/pages/modelHorizon/StepPeriodEconomics.tsx`, `ModelHorizon.tsx`, `api/types.ts`.

### Tests
Extend `test_fca_load_scalers.py`; `test_finance_cashflow.py:157` pattern; `modelHorizonModel.test.ts`; mutants on `cashflow.py`.

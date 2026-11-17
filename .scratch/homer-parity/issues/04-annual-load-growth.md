<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04 — Load growth as a rate per year across the multi-year optimisation and the finance years

Status: ready-for-agent
Model: Sonnet for 04a; Opus for 04b
Reviewer: GK B (GK A only if adequacy/demand.py changes); UX for 04a
Blocked by: 04b blocked by 04a
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

### Finance-year rule (GK B ruling 2026-11-12: option B with R1-R6; supersedes the proposal)
GK B rejected growth after the last period: it books volume the LP never served or sized while supply costs stay flat, which biases NPV upward.
- **R1** After the last LP period volumes stay flat; no year books volume beyond what a solved period served.
- **R2** Between periods p and p+1, interpolate geometrically, anchored on both solved volumes (not on the typed rate): m(y) = (V_{p+1}/V_p)^((y-Y_p)/(Y_{p+1}-Y_p)). The year before Y_{p+1} never exceeds period p+1's LP volume. A single-period project therefore stays flat.
- **R3** The factor scales every dispatch-proportional line on that carrier's load: bill, served energy, load-tied sales, and also energy_import, variable opex, fuel and lost_production. Fixed lines (FOM, capex, fees) are never scaled. A line whose volume class cannot be determined refuses growth (fail closed); never scale revenue alone.
- **R4** No rate and no typed scalers: byte-identical output (cashflows, IC digest, `_network_input_fingerprint`). Derived scalers enter the fingerprint and digest only when they change an LP input or a cashflow. A typed scaler wins over the rate.
- **R5** The rate is finite and > -100 %. The base year y0 is required and at or before the first period. NaN, inf and out-of-range are refused, one test each.
- **R6** 04b is TDD: a hand-computed 2-period case (an interpolated year plus a flat after-last year), with hand mutants on the anchor, the after-last flat, the revenue-only scale and the no-rate identity.
- **Process:** 04b sends its plan to GK B before code; 04a's rate-to-scaler slice also goes to GK B for the digest and fingerprint gate.
- Consequence for the owner's "per year": the rate drives the derived period multipliers; finance years between periods follow the solved volumes. Years after the last period do not grow, by design (R1).

### Slices
- **04a** typed "load growth, % per year" per carrier in Model Horizon and the generic-site form; derived `load_scalers_by_carrier` stored and shown next to the rate. Sonnet.
- **04b** finance per-year load multiplier between periods per R1-R6 (plug-in at `cashflow.py:249` and `:245-247`, selected by line volume class). Opus. Blocked by: 04a (the GK B ruling is in).

### Acceptance
- [ ] 04a: g=2 % with y0=2030 and periods 2030/2035 derives 1.0/1.104 (hand-checked); a typed scaler overrides its period; validation per R5; no-rate identity per R4 (digest and fingerprint unchanged).
- [ ] 04a: Model Horizon shows rate and derived multipliers together; Guided and Expert agree.
- [ ] 04b: the R6 two-period case (interpolated year, flat after last) with the four hand mutants; fixed lines unchanged; undeterminable line refuses growth; no rate = byte-identical.
- [ ] Release-note fragment per PR.

### Likely files
`models/schemas.py:712`, `services/solver_service.py:255`, `adequacy/demand.py`, `services/finance/cashflow.py`, `finance_case.py:755`, `frontend/src/pages/modelHorizon/StepPeriodEconomics.tsx`, `ModelHorizon.tsx`, `api/types.ts`.

### Tests
Extend `test_fca_load_scalers.py`; `test_finance_cashflow.py:157` pattern; `modelHorizonModel.test.ts`; mutants on `cashflow.py`.

### Plan GO, 04a (2026-11-17)
- **GK B (plan GO with conditions):** (a) skip 1.0 entries, and derive REMOVES a previously derived entry whose factor is now 1.0 or whose period is gone (base year moved, period removed, rate 0), one test each. (b) No fixed upper bound on the rate; every derived factor must be finite and > 0 as a result and pass the typed-scaler validator, else its own 422. C1 every writer of `investment_periods` / `load_scalers_by_carrier` / the solver config goes through the seam or provably cannot change them (sweep children included). C2 derived vs typed decided on the server from stored state, never a client flag: a submitted value differing from the stored derived value becomes typed, an equal one stays derived, a cleared typed cell re-derives. C3 one reader normalises `load_scalers_derived`; a malformed mark reads as "not derived", never a 500. C4 rate keys use the scaler carrier canonicaliser and unknown-carrier check; base year refuses bool and non-int; `allow_inf_nan=False`. C5 the three fields in NOT_LP_FIELDS, with fingerprint and IC-digest tests (unchanged on bookkeeping-only change, moved on an effective change), red with the field removed. C6 a killed mutant per guard. C7 chat card shows rate, base year and every derived factor; export/import round-trip; old projects and native template sidecars unchanged; say whether reports name the growth assumption. C8 generic-site form waits for the owner.
- **UX (placement GO with conditions):** label "Load growth (% per year)"; visible default text for the start year; a key line under the grid (muted = from the rate, normal = typed; a typed cell wins for its period); a visible reset titled "Back to the rate"; validation beside the field, opening with the field label, no engine keys; plain carrier names in rows and titles touched; PR lists where the derived multipliers show elsewhere; shots in Guided and Expert (rate only, typed override with reset, validation error, empty).
- **Owner, LOADGROWTH-FORM-1 (2026-11-17):** the generic-site form gets a fill helper, "Grow from the first stage at X % per year", that fills the later stages' peak MW compounded from the first stage year; each stays editable and a typed peak wins; no double counting. Same label, unit and help text wherever it appears. Its own UX placement review; a separate PR after 04a.

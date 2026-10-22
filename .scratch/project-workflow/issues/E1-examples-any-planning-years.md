# E1. Examples for any planning years

Status: ready-for-agent
Type: task
Gated: yes, the engine gatekeeper (session_01Fz66v1SpJnjGHky1RbUuqC). Each slice needs its own OK on its head.
Model tier: opus
Size: L, four PRs (E1a–E1d)

Read [workspace reconciliation](../workspace-reconciliation.md) §3.3, §12 (Q3 = B) and §13 first.

## Decisions so far

- **Owner Q3 = B:** free start and end years at entry.
- **Owner E1-stages (2026-10-21, about 09:40 CEST):** with no dates entered, keep the example's spacing shifted to the start year and drop any stage at or after the end year. The Builder shows the resulting periods before Create.
- **Gatekeeper "go with conditions" (2026-10-21):**
  1. Weights come only from `auto_discount_periods`. Σ `ipw.years` == `analysis_years`, and the default `ipw` is byte-identical.
  2. Golden default: the network compared canonically, and the sidecars by bytes.
  3. A placeholder growth value is an assumed ledger row (the flow warns); it reads unknown in the peak check, DC-01 and the scorecard; the IC flags it with plain words in its notes; the run is allowed. The report appendix is a follow-up: [E1-report-template-ledger.md](E1-report-template-ledger.md).
  4. `analysis_years = end − start`, `financial_close = start − 1`, and the pack money year is not re-dated (the ledger says so).
  5. Every path read by year holds its first point before that year, ledgered "assumed, held from <year>". This covers CO₂, H₂/NH₃ prices and any energization read by year.
  6. `planning_invalid` is a typed 422. `start_year == end_year` is refused on a staged example.
  7. E1c needs the g2p session's agreement and E1d the IPP session's, before building.

## E1a: PR opened 2026-10-21 (resumed under PACE-2), master 2ecd7733 merged in

- Branch `claude/e1a-planning-years-staged-dc`, head 8d54220, on master 2354f8d2.
- PR body drafted: [E1/e1a-pr-body.md](E1/e1a-pr-body.md). Open the PR from it, against master.
- What it does: `Planning` / `planning_spec` / `NATIVE_PLANNING` in `eh_templates.py`; the staged DC parametrised (`_dc_plan`, `_dc_finance`, `_dc_tariff`, `_dc_staged_meta`); `template_meta` / `solver_config` / `island_config` / `write_sidecars(…, planning)`; the route's `planning` parameter (`routers/projects.py`); `site_peak.unset_periods` and `peak_check(unset=)`; the scorecard's unknown rows; `routers/results._ic_growth_flags`; the `baseline_text` growth flag text.
- Tests: `tests/test_e1_planning_staged_dc.py`, all passing locally. A local run of the 16 related suites passed, except `test_energy_hub_templates_e2e.py::test_every_feature_on_the_template[eh_ammonia_staged]`, which hits its 900 s poll limit (`/api/results/eh_study never finished`). E1a does not touch the ammonia template, and all 8 default builds are canonically identical to master. A master run of that test, to confirm it is the slow container, was stopped by the pause. Check CI's result first.
- Before reporting green: CI on the head, `scripts/coord/premerge.sh <pr>`, then the gatekeeper's OK on the exact head.

## Remaining slices

- **E1b: the staged H2 hub and microgrid** (`eh_staged_hubs.py`).
  - Make `YEARS`, `HORIZON_END`, `H2_OFFTAKE_MW` and `MG_LOAD_SCALE` functions of a `Planning`, the way the staged DC's are. Keep the module names at their default values.
  - Growth kinds: `h2_offtake_mw` and `load_scale`. Add both to `site_peak.PEAK_INPUTS` and `_GROWTH_KINDS`.
  - CO₂ path `CO2_PRICE["eur_per_t_by_year"]` (2027: 80, 2030: 120): before 2027 it holds the first point, ledgered "assumed, held from 2027".
  - Finance: the `financial_close`, `cod_by_asset` and `analysis_years` of `_h2_finance` and of the microgrid case.
  - Tariff `valid_from`, and `SYNTHETIC_TARIFF`.
  - Island sidecar candidate names.
  - Register in `NATIVE_PLANNING`, `_PLANNED_META` and `_PLANNED_ISLAND`.
  - Tests per template: the golden default, shifted years, uneven 2026 / 2029 / 2040 → 2050 solved, and the unknown case for an unset value.
- **E1c: ammonia staged** (`eh_ammonia.py`). The g2p session has nothing in flight there.
  - Its #489 regenerates the staged sidecars (value flows `single_owner@4`), so rebuild them through `_build.py` after #489 merges.
  - Keep `YEARS`, `DELIVERY_TPD`, `HB_*` and `LOST_EUR_PER_*` as module names with their default values; the 08 tests read them.
  - `co2_price(year)` and `buy_cost_eur_per_mwh` need the hold-first-point rule and its ledger row.
  - Ticket 09 waits on E1c.
- **E1d: the single-year templates and `eh_ipp_hybrid`.**
  - `eh_datacenter`, `eh_h2_hub` and `eh_microgrid`: `start_year` dates the representative week, `end_year` is the IC horizon (`MG_HORIZON_END`), and `start == end` is allowed.
  - The DC single-year `_single_year_phase` follows the week.
  - `eh_ipp_hybrid`: build on IPP #510 (`cases()`, `cases.json` in `write_sidecars`) and ticket 05 after they merge. `YEAR` becomes a parameter: snapshots, profiles, COD, `FINANCIAL_CLOSE = start − 2`, `ANALYSIS_YEARS = end − start`.
  - Re-date `SERIES_NOTES` `currency_year` together with FINANCE `currency_year` and the price-series dates, or `template_series.basis_conflicts` refuses the create.
  - `tests/test_eh_ipp_cases.py` checks the `old` values.
  - IPP GS-4 (#564, relayed by the coordinator 2026-10-21 22:15 CEST): the observed series `ipp_da_smard_2024` and `ipp_da_dk1_2024` are re-dated onto the template's 2030 calendar (hour of year kept, 29 Feb dropped, UTC kept) and labelled 2030 real money (assumed) to pass `basis_conflicts`. With free planning years, keep them on their native 2024 calendar and let the money-year rules apply instead of re-dating; today `eh_ipp.SERIES_NOTES` `currency_year` follows `YEAR`.

## Open items

- The report appendix: [E1-report-template-ledger.md](E1-report-template-ledger.md).
- Setting a placeholder: [E1-set-growth-placeholders.md](E1-set-growth-placeholders.md) (gatekeeper review of #540, option (a)).
- E1d: the planned-sidecar branch in `routers/projects.py` (~l.1737) skips the template_series `apply_refs`/pins step. Harmless for the DC (no price series); the IPP needs it (gatekeeper note).
- The chat tool's catalogue description does not mention `planning`. Coordinator (2026-10-21, 16:15 CEST): its own tiny PR right after E1a merges, with `smoke/assistant_inventory.py --write`, GK review. TS #512 and GS-EU #514 also touch `harness/catalogue.py`; whoever lands later reruns the inventory.
- The Builder's preview of the resulting periods is W2's (UI, with the UX reviewer).

## Comments

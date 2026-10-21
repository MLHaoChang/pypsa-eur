# The template `eh_ipp_hybrid`

Status: needs-triage
Type: task
Blocked by: 01, 02, 07 (owner Q-IPP1–5 answered: all A)
Gated: **yes** (G5: IC semantics on a generation-only site); UX for the wizard and StartCard lines
Model tier: opus
Size: L

Read [the spec](../spec.md) §2, §6 and §8 first. The id and the numbers follow the owner's
Q-IPP answers.

## What to build

1. **Module** `project_templates/eh_ipp.py`, registered from `eh_templates.py` like
   `eh_ammonia.py` (`register(T)`):
   - the network of spec §2.1, single period, 8 760 h of 2030;
   - CF profiles from `services.profile_gen.generate_profile(source="synthetic")` at the pin;
   - the three price shapes of §2.3, generated deterministically and written to `price_series/`
     (ticket 02);
   - every number in the ledger, with its label (`assumed`, `illustrative`, `pack`, `generated`);
   - no pin (section 1 warns "no site location"); the bidding zone DE-LU `assumed` in 7c;
   - the POI prices in 7a (spec §2.3): `import_price_ref` and `export_price_ref` = `da_base`
     (ticket 07); an import `Tariff` with a network-charge energy item > 0 and the injection item;
     no price on the Links themselves (`grid_import`'s own marginal cost is 0);
   - the ledger line "8 planned cases (editable once sensitivities land; make any now with
     Duplicate)".
2. **Sidecars**:
   - `eh_template.json`: meta, ledger, groups pre-fill, `not_needed` sections with their reasons;
   - `solver_config.json`: commercial with the `custom` value flows and the PPA, and finance per §6;
   - `cases.json` (ticket 04).

   The meta sets the initial Results tab to Commercial (no reorder). No `adequacy_stress_scenarios.json` unless the validator requires one; no island sidecar.
3. **G5**: the plants are forced-size candidates. Tests (gatekeeper's traps, spec §10 G5):
   - the IC builds with an empty counterfactual, and incremental = total;
   - the grid slack is not an `ipp` asset (no capex, vom or COD) and costs 0 both ways;
   - the import cost (the wholesale series and the tariff's `ic_energy_price` frames) is booked
     once, and `grid_import`'s own marginal cost is 0; the IC reconciles with the LP per stream;
   - the network charge is > 0 at every snapshot, so `circulation_risk_snapshots == 0`;
   - load-based metrics give None or "not applicable" (one test over the IC report);
   - the battery throughput cost is StorageUnit `marginal_cost` (per MWh discharged).
4. **Both modes**: every pre-filled parameter in spec §8.3 is editable in Guided and in Expert.
   The test walks the table against the created project's editors and flow items. The PR lists
   the locks of §8.3 with their reasons.
   **Section 7 wiring, no shadow value** (spec §8.3a): for each row, change the value through its
   section-7 route (5 for the battery capex) and assert the solve (`lp_input`) or the IC
   (`commercial`) changes; with the Commercial group off, the `lp_input` rows stay visible.
5. **The #418 checklist**:
   - `_build.py`: feasibility solve, with the solve time recorded;
   - `routers/projects.py` `_TEMPLATE_DEFAULT_NAMES`;
   - the `TEMPLATES` list in `NewProjectWizard.tsx` (the New project › Templates list; no wizard or parameters of its own: entry is Home card → Builder → the flow, #444 §3);
   - `StartCard.tsx` `PURPOSE`, and the order and count in `StartCard.test.tsx`;
   - the `harness/catalogue.py` description and enum;
   - `tests/test_validation_gen_costs.py` `_EXEMPT` (the grid slack);
   - `test_energy_hub_templates.py`;
   - the `.scratch/live-voice/capability-inventory.json` refresh;
   - `smoke/check_bundle.py` if a pack version is added;
   - a release-note fragment.
6. **Template test** `tests/test_eh_ipp.py`:
   - feasible;
   - the battery size is within its bounds;
   - export never exceeds the limit;
   - curtailment occurs at negative prices and at the limit;
   - capture rate below 1 for PV on `da_base`;
   - the PPA settlement and NPV/IRR are established (not `None`);
   - every price carries `illustrative`.

## Acceptance

Spec §8.2's state column holds on a freshly created project (`GET /flow`).

## Recorded at build (2026-10-21)

- **Feasibility solve** (`_build.py` `_verify_feasible`, HiGHS, 8 760 h): 3.6 s locally.
- **`da_base` targets** (spec §2.3), as ranges, never a forecast. The sources are approximate public figures for DE-LU; check the current ones:
  - mean day-ahead price 60–100 EUR/MWh (SMARD, Bundesnetzagentur: the annual average was roughly 95 in 2023 and 80 in 2024). Shipped: 75.8;
  - negative hours 100–500 a year (SMARD: roughly 300 in 2023 and 450 in 2024). Shipped: 148, at the conservative end, so the curtailment and battery value are not overstated;
  - PV capture rate 0.5–0.8 (netztransparenz.de monthly market values, "Marktwert Solar" over the base price). Shipped: 0.56;
  - wind capture rate 0.75–0.95 (the same source, "Marktwert Wind an Land"). Shipped: 0.89.
- **PPA prices** (7b, `illustrative`): wind 65 and PV 50 EUR/MWh, inside the German ranges that the public PPA price indices (LevelTen, Pexapark) reported for 2024–25, roughly 60–80 for onshore wind and 45–60 for solar.
- **Amended in the spec**: the `grid` bus carries `eh_poc` (the EH convention) and the export Link carries no role (`export_link` names it). The finance is nominal in 2030 money (gatekeeper). The pack's 7 % is real and pre-tax, so the WACC is `assumed`.


# The template `eh_ipp_hybrid`

Status: needs-triage
Type: task
Blocked by: 01, 02 (owner Q-IPP1–5 answered: all A)
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
   - every number in the ledger, with its label (`assumed`, `illustrative`, `pack`, `generated`).
2. **Sidecars**:
   - `eh_template.json`: meta, ledger, groups pre-fill, `not_needed` sections with their reasons;
   - `solver_config.json`: commercial with the `custom` value flows and the PPA, and finance per §6;
   - `cases.json` (ticket 04).

   The meta sets the initial Results tab to Commercial (no reorder). No `adequacy_stress_scenarios.json` unless the validator requires one; no island sidecar.
3. **G5**: the plants are forced-size candidates. Tests (gatekeeper's traps, spec §10 G5):
   - the IC builds with an empty counterfactual, and incremental = total;
   - the grid slack is not an `ipp` asset (no capex, vom or COD) and costs 0 both ways;
   - the import cost appears once as an energy-cost line, and the IC reconciles with the LP per
     stream;
   - the network charge is > 0 at every snapshot, so `circulation_risk_snapshots == 0`;
   - load-based metrics give None or "not applicable" (one test over the IC report);
   - the battery throughput cost is StorageUnit `marginal_cost` (per MWh discharged).
4. **Both modes**: every pre-filled parameter in spec §8.3 is editable in Guided and in Expert.
   The test walks the table against the created project's editors and flow items.
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

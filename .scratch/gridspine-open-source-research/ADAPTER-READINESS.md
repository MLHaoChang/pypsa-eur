# Adapter readiness and exact-component admission

Date: 2026-10-20. Status: proposed gate register; no new package executions or adapter numerical tests have occurred. A package-level recommendation is not component admission.

## Staged go / no-go

- **Plan gate:** independent review of architecture, features, source fit, dependencies and validation scope. Its exact verdict is in INDEPENDENT-REVIEW.md.
- **Build gate:** before implementing a product adapter, record the exact component/API/version and platform, fields/bases/boundaries, licensed dependency/runtime manifest, named reference fixtures and metric-specific tolerances, supported scope and refusal cases. Missing items permit bounded discovery, not assumed product support.
- **Availability gate:** real pinned worker and native round-trip, numerical/reference and adverse-case evidence, current required software checks and explicit assurance dispositions. Only the supported tested scope becomes available.
- **Domain gate:** independent scientific/customer validation using representative input data. Pending by the owner's earlier choice; never inferred from the other gates.

“Proceed with the plan” means follow these gates and dependencies. It is not a blanket scientific go for every deferred engine and not a new deployment or paid-call authorization.

## Initial technical-contract register

Fixture names below are **planned fixture IDs, not files or tests already present**. Exact tolerances and any experimental/vendor datasets must be recorded before the build gate, not chosen after seeing results. Exact accounting can reuse approved native tolerances; approximate models need justified domain-specific limits. A matched upstream result alone is not independent physical validation.

| Adapter | First component/scope | Named initial reference and refusal fixtures | Open build/availability items |
| --- | --- | --- | --- |
| H2Integrate H2 | One explicit fixed-size electrolyser component, not automatic system-controller/resizing modes | `h2_yield_fixed_v1`, `h2_zero_input_v1`, `h2_water_units_v1`, `h2_controller_conservation_v1`, `h2_time_basis_v1` | Component selection unresolved; source-observed PEM risks below must be fixed/reconciled or refused; runtime, reference and tolerances pending |
| H2Integrate NH3 | Candidate synloop on a declared stream-table/input boundary; simple model limited to annual reference | `nh3_feedstock_limit_v1`, `nh3_zero_h2_v1`, `nh3_n2_limit_v1`, `nh3_start_energy_v1` | Actual synloop component must pass H/N/energy and timestep/boundary tests. No chronological admission for simple ammonia model |
| BLAST-Lite | One chemistry/model class on declared temperature/SOC/duty range | `battery_idle_calendar_v1`, `battery_chronological_cycle_v1`, `battery_replacement_reset_v1`, `battery_representative_weight_refusal_v1` | Exact class/release, calibration/reference range and tolerances pending; isolated NumPy environment required |
| TESPy | One specified heat-pump/chiller/CHP map | `thermal_nominal_balance_v1`, `thermal_part_load_holdout_v1`, `thermal_extrapolation_refusal_v1` | Equipment/reference points and acceptable map error pending; convergence and bounded interpolation required |
| oemof-demand | One power or heat archetype through a pinned release/API | `demand_annual_energy_v1`, `demand_peak_independent_v1`, `demand_calendar_mapping_v1` | Resolve package/API migration, calendar and target sector; no measured-data claim |
| OpenDSM | One suitable meter/weather baseline method | `baseline_holdout_v1`, `baseline_insufficient_data_v1`, `baseline_dst_v1` | Licensed dataset, data-sufficiency criteria, holdout metric/tolerance and pinned NumPy>=2 environment pending |
| PySAM utility-rate oracle | Defined overlap tariff terms; reuse existing SAM infrastructure | `bill_interval_known_v1`, `bill_demand_ratchet_v1`, `bill_unsupported_terms_v1` | Exact matched tariff/time/netting/tax scope and rate-module version; no whole-bill claim from a supported subset |
| HPE physics subset | One calibrated IT/cooling boundary or derived reduced model | `dc_heat_balance_v1`, `dc_pue_no_double_count_v1`, `dc_deadline_v1` | Plant/weather data, model extraction license/provenance and held-out accuracy pending; no RL training stack |
| python-control | One explicit supported plant/controller and disturbance | `control_initialization_v1`, `control_heldout_step_v1`, `control_constraint_failure_v1` | Plant model, linearization validity, dynamic trace thresholds and numerical backend pending; no GFM certification |
| Other/deferred | do-mpc, detailed dynamics, WaterTAP, IDAES, PRAS, OpenDSS, haulage | A separate exact contract required | No product-adapter build go yet; user need, data and reference must justify selection |

## H2Integrate source findings that change admission

Inspected source is pinned to `17a25b7a7876a15c2def3d4d81c680b46577bb25`; observations below are source-level concerns, not executed bug reproductions or claims about every component/release.

1. [Simple ammonia compute](https://github.com/NatLabRockies/H2Integrate/blob/17a25b7a7876a15c2def3d4d81c680b46577bb25/h2integrate/converters/ammonia/simple_ammonia_model.py#L54) derives annual production from configured capacity/capacity factor, then divides by the hydrogen input array's length. It does not consume the array's hydrogen values to constrain production. Restrict this component to an explicitly annual reference/costing role; never use it as a feedstock-constrained chronological oracle. Validate a different component before using it for that role.
2. [PEM component](https://github.com/NatLabRockies/H2Integrate/blob/17a25b7a7876a15c2def3d4d81c680b46577bb25/h2integrate/converters/hydrogen/pem_electrolyzer.py#L174) declares water output in galUS/h but multiplies a kg/h-named source by 3.79. Reconcile the underlying source units and conversion in a numerical test before any water claim; do not silently accept or patch a figure without proof.
3. Its system-level control path clips hydrogen output after electricity, water and total outputs are assigned. A changed command therefore needs a joint conservation/aggregate test and an explicitly consistent curtailment/accounting model. Refuse that controller mode unless the exact implementation passes; do not build a planning envelope from inconsistent outputs.

Any correction is versioned, tested and recorded as a patch or adapter transformation with its own provenance. Do not silently correct a value and still label it an untouched independent upstream oracle. H2Integrate remains high-value **feasibility research**, with product support conditional on exact-component gates.

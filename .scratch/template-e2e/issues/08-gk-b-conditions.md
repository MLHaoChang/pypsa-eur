<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 08 — GK B plan conditions (to move into 08's Comments once #1613 merges)

Status: done (#1645; pending the move into issue 08 after #1613 merges)
Applies to: `.scratch/template-e2e/issues/08-silent-run-fallbacks.md` and the backend half of 06.

## Comments

**GK B plan GO, template-e2e 08 + 06-backend, slice 1.** It was given by GK B (63, session_01G7qCundFLhgXpNk7ehKZQT) to builder session_01G7qCuo15kNsNqjAaBLjvmL. The verbatim GO is not held by either successor (GK B 64 or this builder). The text below is **reconstructed** (GK B 64's option (c) on #1645) from the first builder's hand-off, the commits and the tests' own condition labels. GK B 64 checks it against the code at #1645's head; the numbering may not match the original GO. Each condition names the test that meets it, in `pypsa-gui/backend/tests/test_run_assumptions.py` unless another file is given.

- **Q1a.** With no VOLL, COPT and `/fmea_modes` keep running. `severity_eur` and `criticality_eur_per_year` are None, never €0, and that includes the sweep rows. A "Value of lost load: not set" assumption row is added, and `voll_eur_per_mwh` keeps the configured value. Tests: `test_adequacy_copt_endpoint.py` (the None € endpoint pins) and `test_the_worksheet_prices_and_ranks_sweep_rows_only_with_a_voll`. The FMEA screen still shows €0 for those figures; that is filed as issue 11 and accepted by UX 2.
- **Q2a.** The run record says "not recorded" (key absent) or "none" (`[]`), and the report keeps the two apart. With no study run, the report payload is unchanged. Only the open project's record is read. Tests: `test_the_report_reads_the_runs_rows_or_says_not_recorded`, `test_the_report_tells_none_from_not_recorded`, `test_a_recorder_failure_leaves_the_record_saying_not_recorded`, and `test_report_template_ledger.py` (the run-row filter).
- **Q3a.** The study start refuses an unusable VOLL as the typed `unsupported_scope` (`field: "voll"`), after the request's own checks. Readiness says it once, blocking, on the `voll` field. Tests: `test_the_study_start_refuses_without_a_voll` (per template), `test_readiness_blocks_run_on_a_missing_voll_once`, `test_a_non_voll_refusal_keeps_the_voll_said_once`, `test_the_voll_refusal_names_the_solver_settings_label`.
- **A1.** One pure function, `run_assumptions`, makes every row. It reads the network and never changes it. Tests: `test_run_assumptions_is_a_pure_read`, `test_rows_are_json_serialisable`.
- **A2.** For each source helper the rows reuse, one mutant fails both that helper's consumer test and a `run_assumptions` test.
  - `resolve_outage_params`: fails `test_adequacy_occurrence::test_resolve_falls_back_to_carrier_default` and `test_carrier_default_outage_rates_are_one_row_per_carrier`.
  - `link_attr_series`: fails `test_adequacy_link_supply::test_a_fuel_cell_on_an_unlimited_fuel_is_credited_at_its_delivered_cap` and `test_a_link_without_an_efficiency_is_assumed_lossless`.
- **A3.** Link efficiency comes from `eh_stages`' shared `link_attr_series`/`LINK_EFFICIENCY_DEFAULT`. A blank, a gap or a non-finite value, and a conversion link at exactly 1.0, are each assumed. Slack links and `EH_INTERNAL_ROLES` links are excluded. Tests: `test_a_link_without_an_efficiency_is_assumed_lossless`, `test_a_gap_in_an_efficiency_series_is_assumed`, `test_a_conversion_link_at_exactly_one_is_assumed_lossless`, `test_a_non_finite_link_efficiency_reads_lossless`.
- **A4.** Carrier-default rows are one per carrier and name every unit, by the carrier's own name. They include a split storage's Store, which the Monte Carlo reads. A unit whose rate the outages-in-availability flag zeroes assumes nothing. Tests: `test_a_carrier_row_names_every_unit`, `test_a_carrier_row_uses_the_carriers_own_name`, `test_a_split_storages_outage_default_is_assumed_from_its_store`, `test_a_unit_whose_rate_the_availability_flag_zeroes_assumes_nothing`.
- **A5.** An acknowledged assumption returns when its value changes. A stored or requested value retires its row. A field the template ledger already raises is not repeated. Tests: `test_an_acknowledged_assumption_returns_when_its_value_changes`, `test_a_stored_run_input_retires_its_row`, `test_a_request_value_that_differs_retires_the_goal_row`, `test_the_recorder_keeps_a_draws_row_the_request_repeats`, `test_a_ledger_assumed_field_is_not_repeated_as_a_run_default`.
- **A6.** The record is read from the study's private copy and written under the solver-state lock. Every study template records its rows. Tests: `test_the_recorder_reads_the_studys_private_copy`, `test_the_study_record_carries_its_assumptions` (per template).
- **A7.** `scripts/coord/mutants.py` runs on each changed service module, plus hand mutants on the VOLL guard. Survivors are killed or listed as equivalent with reasons in #1645's body (Mutants table). VOLL guard: `test_every_unusable_voll_is_refused`.

The note from GK B (64): `eh_ipp_hybrid` has VOLL 0, so readiness must block before the run. Test: `test_a_generation_only_site_with_a_load_is_held_to_the_study` (test_project_flow.py).

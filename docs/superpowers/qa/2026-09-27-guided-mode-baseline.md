# Guided mode: integration-gate baseline (2026-09-27)

This baseline was measured on `09045a0`. It differs from the spec's base commit `2b78c83` in docs only; `git diff --stat 2b78c83 09045a0 -- pypsa-gui` is empty. Before this note, only the ids listed here counted as pre-existing failures.

| Check | Command (spec §8.1) | Result |
|---|---|---|
| Full backend suite | `pytest tests/ -m "not slow"` (cwd `pypsa-gui/backend`) | `6033 passed, 31 skipped, 11 deselected in 2112.21s (0:35:12)` |
| Frontend typecheck | `npx tsc --noEmit -p .` | exit 0 |
| Frontend suite | `npx vitest run` | `Test Files 181 passed (181)`, `Tests 2033 passed (2033)` |

**Pre-existing failures: none.** Every later gate must therefore show zero failures.

The reviewer made a separate pre-baseline observation (tsc exit 0; vitest 181 files / 2033 passed, on `8e04e54`). It agrees with this baseline and is recorded as an observation only.

Outside the GUI gate, the repo-root `tests/gridspine/test_packaging.py` fails twice with `PackageNotFoundError`. `gridspine` is not pip-installed in the test venv, so the failure is environmental. That suite is not part of this gate.

## Route-inventory catch-up (review B8)

`tools/openapi_diff.py --phase0-fixture` took the inventory from 252 to 282 routes. It added 30 rows, three of which only moved, and removed none; this was checked with `comm -23` on the sorted old and new files, which returned no lines. There was no code change. `test_chat_tools_endpoint_map.py` passed: 15 tests.

```diff
diff --git a/pypsa-gui/backend/tests/fixtures/route_inventory_phase0.txt b/pypsa-gui/backend/tests/fixtures/route_inventory_phase0.txt
index 535363e..c7cefce 100644
--- a/pypsa-gui/backend/tests/fixtures/route_inventory_phase0.txt
+++ b/pypsa-gui/backend/tests/fixtures/route_inventory_phase0.txt
@@ -21,12 +21,34 @@ GET     /api/chat/health                                         -> chat_health_
 GET     /api/chat/history                                        -> chat_history_api_chat_history_get
 POST    /api/chat/import                                         -> chat_import_api_chat_import_post
 GET     /api/chat/metrics                                        -> chat_metrics_api_chat_metrics_get
+GET     /api/chat/profiles                                       -> get_chat_profiles_api_chat_profiles_get
 DELETE  /api/chat/settings/api-key                               -> delete_api_key_settings_api_chat_settings_api_key_delete
 GET     /api/chat/settings/api-key                               -> get_api_key_settings_api_chat_settings_api_key_get
 PUT     /api/chat/settings/api-key                               -> put_api_key_settings_api_chat_settings_api_key_put
+GET     /api/chat/settings/llm                                   -> get_llm_settings_api_chat_settings_llm_get
+POST    /api/chat/settings/llm/active                            -> post_llm_active_api_chat_settings_llm_active_post
+DELETE  /api/chat/settings/llm/profiles/{profile_id}             -> delete_llm_profile_api_chat_settings_llm_profiles__profile_id__delete
+PUT     /api/chat/settings/llm/profiles/{profile_id}             -> put_llm_profile_api_chat_settings_llm_profiles__profile_id__put
+DELETE  /api/chat/settings/llm/profiles/{profile_id}/key         -> delete_llm_profile_key_api_chat_settings_llm_profiles__profile_id__key_delete
+PUT     /api/chat/settings/llm/profiles/{profile_id}/key         -> put_llm_profile_key_api_chat_settings_llm_profiles__profile_id__key_put
+POST    /api/chat/settings/llm/profiles/{profile_id}/test        -> post_llm_profile_test_api_chat_settings_llm_profiles__profile_id__test_post
 POST    /api/chat/stream                                         -> chat_stream_api_chat_stream_post
 POST    /api/chat/{session_id}/abort                             -> chat_abort_api_chat__session_id__abort_post
 POST    /api/chat/{session_id}/confirm                           -> chat_confirm_api_chat__session_id__confirm_post
+POST    /api/chat/{session_id}/rewind                            -> chat_rewind_api_chat__session_id__rewind_post
+POST    /api/gridspine/projects                                  -> create_study_api_gridspine_projects_post
+GET     /api/gridspine/{name}/bundles/{hour}                     -> export_handoff_bundle_api_gridspine__name__bundles__hour__get
+GET     /api/gridspine/{name}/config                             -> get_config_api_gridspine__name__config_get
+PUT     /api/gridspine/{name}/config                             -> update_config_api_gridspine__name__config_put
+POST    /api/gridspine/{name}/dispatch-source                    -> set_dispatch_source_api_gridspine__name__dispatch_source_post
+GET     /api/gridspine/{name}/figures/{hour}/{figure}            -> fetch_result_figure_api_gridspine__name__figures__hour___figure__get
+GET     /api/gridspine/{name}/ledger                             -> get_assumption_ledger_api_gridspine__name__ledger_get
+GET     /api/gridspine/{name}/readback                           -> get_readback_api_gridspine__name__readback_get
+POST    /api/gridspine/{name}/readback/{hour}                    -> upload_readback_api_gridspine__name__readback__hour__post
+POST    /api/gridspine/{name}/run                                -> run_pipeline_api_gridspine__name__run_post
+GET     /api/gridspine/{name}/snapshots                          -> list_ranked_snapshots_api_gridspine__name__snapshots_get
+GET     /api/gridspine/{name}/status                             -> get_stage_status_api_gridspine__name__status_get
+PUT     /api/gridspine/{name}/templates/{unit_id}/{param}        -> edit_template_param_api_gridspine__name__templates__unit_id___param__put
 GET     /api/guides                                              -> list_guides_api_guides_get
 GET     /api/guides/{topic}                                      -> get_guide_api_guides__topic__get
 GET     /api/health                                              -> health_api_health_get
@@ -52,6 +74,7 @@ GET     /api/network/carriers                                    -> get_carriers
 POST    /api/network/carriers                                    -> create_carrier_api_network_carriers_post
 DELETE  /api/network/carriers/{name}                             -> delete_carrier_api_network_carriers__name__delete
 PUT     /api/network/carriers/{name}                             -> update_carrier_api_network_carriers__name__put
+GET     /api/network/catalog/{component}                         -> get_attribute_catalog_api_network_catalog__component__get
 POST    /api/network/cluster                                     -> apply_clustering_api_network_cluster_post
 GET     /api/network/generators                                  -> get_generators_api_network_generators_get
 POST    /api/network/generators                                  -> create_generator_api_network_generators_post
@@ -141,6 +164,7 @@ GET     /api/projects/{name}/asset_health                        -> get_asset_he
 PUT     /api/projects/{name}/asset_health                        -> put_asset_health_api_projects__name__asset_health_put
 GET     /api/projects/{name}/bundle                              -> download_bundle_api_projects__name__bundle_get
 GET     /api/projects/{name}/compare-state                       -> get_compare_state_api_projects__name__compare_state_get
+GET     /api/projects/{name}/eh_template                         -> get_eh_template_api_projects__name__eh_template_get
 GET     /api/projects/{name}/layout                              -> get_layout_api_projects__name__layout_get
 PUT     /api/projects/{name}/layout                              -> put_layout_api_projects__name__layout_put
 GET     /api/projects/{name}/members                             -> get_project_members_api_projects__name__members_get
@@ -154,7 +178,6 @@ POST    /api/projects/{name}/snapshots                           -> create_snaps
 DELETE  /api/projects/{name}/snapshots/{snapshot_id}             -> delete_snapshot_api_projects__name__snapshots__snapshot_id__delete
 POST    /api/projects/{name}/snapshots/{snapshot_id}/restore     -> restore_snapshot_api_projects__name__snapshots__snapshot_id__restore_post
 GET     /api/projects/{name}/statistics                          -> project_statistics_api_projects__name__statistics_get
-GET     /api/projects/{name}/eh_template                         -> get_eh_template_api_projects__name__eh_template_get
 GET     /api/projects/{name}/stress_profile_packs                -> get_stress_profile_packs_api_projects__name__stress_profile_packs_get
 GET     /api/projects/{name}/stress_scenarios                    -> get_stress_scenarios_api_projects__name__stress_scenarios_get
 PUT     /api/projects/{name}/stress_scenarios                    -> put_stress_scenarios_api_projects__name__stress_scenarios_put
@@ -186,16 +209,16 @@ POST    /api/results/coupling_loop                               -> post_couplin
 POST    /api/results/coupling_loop/abort                         -> post_coupling_loop_abort_api_results_coupling_loop_abort_post
 GET     /api/results/curtailment                                 -> get_curtailment_api_results_curtailment_get
 GET     /api/results/economics_by_carrier                        -> get_economics_by_carrier_api_results_economics_by_carrier_get
-GET     /api/results/emissions                                   -> get_emissions_api_results_emissions_get
 GET     /api/results/eh_dtc                                      -> get_eh_dtc_api_results_eh_dtc_get
 GET     /api/results/eh_dtc_planning                             -> get_eh_dtc_planning_api_results_eh_dtc_planning_get
 GET     /api/results/eh_levers                                   -> get_eh_levers_api_results_eh_levers_get
-GET     /api/results/eh_redundancy                               -> get_eh_redundancy_api_results_eh_redundancy_get
 GET     /api/results/eh_readiness                                -> get_eh_readiness_api_results_eh_readiness_get
+GET     /api/results/eh_redundancy                               -> get_eh_redundancy_api_results_eh_redundancy_get
 GET     /api/results/eh_reference_design                         -> get_eh_reference_design_api_results_eh_reference_design_get
 GET     /api/results/eh_study                                    -> get_eh_study_api_results_eh_study_get
 POST    /api/results/eh_study                                    -> post_eh_study_api_results_eh_study_post
 POST    /api/results/eh_study/abort                              -> post_eh_study_abort_api_results_eh_study_abort_post
+GET     /api/results/emissions                                   -> get_emissions_api_results_emissions_get
 GET     /api/results/fmea_modes                                  -> get_fmea_modes_api_results_fmea_modes_get
 GET     /api/results/fmea_sweep                                  -> get_fmea_sweep_api_results_fmea_sweep_get
 POST    /api/results/fmea_sweep                                  -> post_fmea_sweep_api_results_fmea_sweep_post
@@ -243,8 +266,15 @@ GET     /api/simulation/log_stream                               -> log_stream_a
 POST    /api/simulation/preflight                                -> preflight_api_simulation_preflight_post
 GET     /api/simulation/queue                                    -> list_queue_api_simulation_queue_get
 POST    /api/simulation/queue                                    -> enqueue_solve_api_simulation_queue_post
+POST    /api/simulation/queue/cancel_queued                      -> cancel_queued_api_simulation_queue_cancel_queued_post
 POST    /api/simulation/queue/clear_finished                     -> clear_finished_api_simulation_queue_clear_finished_post
+POST    /api/simulation/queue/pause                              -> pause_queue_api_simulation_queue_pause_post
+POST    /api/simulation/queue/resume                             -> resume_queue_api_simulation_queue_resume_post
 POST    /api/simulation/queue/{job_id}/abort                     -> abort_job_api_simulation_queue__job_id__abort_post
+POST    /api/simulation/queue/{job_id}/dismiss                   -> dismiss_job_api_simulation_queue__job_id__dismiss_post
+GET     /api/simulation/queue/{job_id}/log_history               -> job_log_history_api_simulation_queue__job_id__log_history_get
+GET     /api/simulation/queue/{job_id}/log_stream                -> job_log_stream_api_simulation_queue__job_id__log_stream_get
+POST    /api/simulation/queue/{job_id}/requeue                   -> requeue_job_api_simulation_queue__job_id__requeue_post
 POST    /api/simulation/run                                      -> run_api_simulation_run_post
 POST    /api/simulation/run_ac_pf                                -> run_ac_pf_api_simulation_run_ac_pf_post
 GET     /api/simulation/solver_config                            -> get_solver_config_api_simulation_solver_config_get
```

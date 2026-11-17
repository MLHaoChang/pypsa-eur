"""Every mutating route and what the viewer role does with it.

Read by `services/project_write_gate.py`; the format and the meaning of each
kind are in its docstring. One line per route: METHOD PATH KIND DETAIL.
`tests/test_viewer_role_gate.py` fails on a route with no line and on a line
whose route is gone, and pins the exact set of `allow` lines.
"""

# fmt: off
TABLE = """
POST   /api/admin/email/test  none instance administration
POST   /api/admin/legacy-projects/{legacy_name}/claim  none instance administration
POST   /api/admin/organizations  none instance administration
POST   /api/admin/users  none instance administration
POST   /api/admin/users/{user_id}/resend-set-password  none instance administration
POST   /api/auth/forgot-password  none sign-in and account
POST   /api/auth/login  none sign-in and account
POST   /api/auth/logout  none sign-in and account
POST   /api/auth/reset-password  none sign-in and account
POST   /api/auth/set-password  none sign-in and account
PUT    /api/campus-electrical/{name}/campus  write path:name
POST   /api/campus-electrical/{name}/dc-architecture/compare  write path:name
PUT    /api/campus-electrical/{name}/dc-backup  write path:name
POST   /api/campus-electrical/{name}/dc-backup/assess  write path:name
POST   /api/campus-electrical/{name}/draft  write path:name
POST   /api/campus-electrical/{name}/grid-codes/documents  write path:name
DELETE /api/campus-electrical/{name}/grid-codes/documents/{document_id}  write path:name
POST   /api/campus-electrical/{name}/grid-codes/documents/{document_id}/extract  write path:name
POST   /api/campus-electrical/{name}/grid-codes/drafts  write path:name
DELETE /api/campus-electrical/{name}/grid-codes/drafts/{profile_id}  write path:name
PUT    /api/campus-electrical/{name}/grid-codes/drafts/{profile_id}  write path:name
POST   /api/campus-electrical/{name}/grid-codes/drafts/{profile_id}/confirm  write path:name
POST   /api/campus-electrical/{name}/grid-codes/drafts/{profile_id}/publish  write path:name
DELETE /api/campus-electrical/{name}/grid-codes/published/{profile_id}  write path:name
DELETE /api/campus-electrical/{name}/grid-readiness/limits/{item}  write path:name
PUT    /api/campus-electrical/{name}/grid-readiness/limits/{item}  write path:name
PUT    /api/campus-electrical/{name}/island  write path:name
DELETE /api/campus-electrical/{name}/island/cuts  write path:name
POST   /api/campus-electrical/{name}/island/cuts/apply  write path:name
POST   /api/campus-electrical/{name}/island/cuts/apply/cancel  write path:name
DELETE /api/campus-electrical/{name}/island/cuts/{period}/{hour}/{kind}  write path:name
POST   /api/campus-electrical/{name}/island/gfm-settings  write path:name
POST   /api/campus-electrical/{name}/island/gfm-settings/apply  write path:name
POST   /api/campus-electrical/{name}/island/preset  write path:name
POST   /api/campus-electrical/{name}/island/study  write path:name
PUT    /api/campus-electrical/{name}/library  write path:name
POST   /api/campus-electrical/{name}/library/reset  write path:name
POST   /api/campus-electrical/{name}/milp  write path:name
POST   /api/campus-electrical/{name}/milp/cancel  write path:name
POST   /api/campus-electrical/{name}/run  write path:name
DELETE /api/changelog/  none the org's change log
POST   /api/chat/import  write active
POST   /api/chat/operations/{operation_id}/reconcile  allow controls the caller's own chat session; a confirmed write re-checks the role at execution (restrictions.require_current)
PUT    /api/chat/restrictions  none a personal rule; it cannot loosen the project role (harness/restrictions._require_role)
POST   /api/chat/runs/{task_id}/pause  write active
POST   /api/chat/runs/{task_id}/present  write active
POST   /api/chat/runs/{task_id}/research  write active
POST   /api/chat/runs/{task_id}/resume  write active
POST   /api/chat/runs/{task_id}/stop  write active
DELETE /api/chat/settings/api-key  none personal or instance chat settings
PUT    /api/chat/settings/api-key  none personal or instance chat settings
POST   /api/chat/settings/llm/active  none personal or instance chat settings
DELETE /api/chat/settings/llm/profiles/{profile_id}  none personal or instance chat settings
PUT    /api/chat/settings/llm/profiles/{profile_id}  none personal or instance chat settings
DELETE /api/chat/settings/llm/profiles/{profile_id}/key  none personal or instance chat settings
PUT    /api/chat/settings/llm/profiles/{profile_id}/key  none personal or instance chat settings
POST   /api/chat/settings/llm/profiles/{profile_id}/test  none personal or instance chat settings
POST   /api/chat/settings/llm/roles  none personal or instance chat settings
DELETE /api/chat/settings/mcp/servers/{server_id}  none personal or instance chat settings
PUT    /api/chat/settings/mcp/servers/{server_id}  none personal or instance chat settings
DELETE /api/chat/settings/mcp/servers/{server_id}/keys/{key_name}  none personal or instance chat settings
PUT    /api/chat/settings/mcp/servers/{server_id}/keys/{key_name}  none personal or instance chat settings
POST   /api/chat/settings/mcp/servers/{server_id}/test  none personal or instance chat settings
POST   /api/chat/skills/reload  none personal or instance chat settings
POST   /api/chat/stream  allow a chat turn; every non-read tool is refused at admission (harness/restrictions.require_allowed)
POST   /api/chat/ui-ack  allow controls the caller's own chat session; a confirmed write re-checks the role at execution (restrictions.require_current)
POST   /api/chat/{session_id}/abort  allow controls the caller's own chat session; a confirmed write re-checks the role at execution (restrictions.require_current)
POST   /api/chat/{session_id}/confirm  allow controls the caller's own chat session; a confirmed write re-checks the role at execution (restrictions.require_current)
POST   /api/chat/{session_id}/continuations/{job_id}/dismiss  allow controls the caller's own chat session; a confirmed write re-checks the role at execution (restrictions.require_current)
POST   /api/chat/{session_id}/goal  allow controls the caller's own chat session; a confirmed write re-checks the role at execution (restrictions.require_current)
POST   /api/chat/{session_id}/progress/stop  allow controls the caller's own chat session; a confirmed write re-checks the role at execution (restrictions.require_current)
POST   /api/chat/{session_id}/rewind  allow controls the caller's own chat session; a confirmed write re-checks the role at execution (restrictions.require_current)
POST   /api/dictation/transcribe  none transcribes audio; stores nothing
POST   /api/document_values/commercial/preview  allow declared non-mutating (assistant_route_parity non_mutating_routes): computes and returns, writes nothing
POST   /api/document_values/preview  allow declared non-mutating (assistant_route_parity non_mutating_routes): computes and returns, writes nothing
POST   /api/durable-conversations  allow the caller's own conversation thread and speech records; writes no project
POST   /api/durable-conversations/{thread_id}/checkpoint  allow the caller's own conversation thread and speech records; writes no project
POST   /api/durable-conversations/{thread_id}/profile  allow the caller's own conversation thread and speech records; writes no project
POST   /api/durable-conversations/{thread_id}/rebind  allow the caller's own conversation thread and speech records; writes no project
POST   /api/durable-conversations/{thread_id}/reconcile  allow the caller's own conversation thread and speech records; writes no project
POST   /api/durable-conversations/{thread_id}/resume  allow the caller's own conversation thread and speech records; writes no project
POST   /api/durable-conversations/{thread_id}/speech/barge-in  allow the caller's own conversation thread and speech records; writes no project
POST   /api/durable-conversations/{thread_id}/speech/end  allow the caller's own conversation thread and speech records; writes no project
POST   /api/durable-conversations/{thread_id}/speech/output/claim  allow the caller's own conversation thread and speech records; writes no project
POST   /api/durable-conversations/{thread_id}/speech/segment  allow the caller's own conversation thread and speech records; writes no project
POST   /api/durable-conversations/{thread_id}/speech/start  allow the caller's own conversation thread and speech records; writes no project
POST   /api/durable-conversations/{thread_id}/speech/usage  allow the caller's own conversation thread and speech records; writes no project
POST   /api/equipment_table/preview  allow declared non-mutating (assistant_route_parity non_mutating_routes): computes and returns, writes nothing
POST   /api/gridspine/projects  none creates a new project the caller owns
POST   /api/gridspine/{name}/capacity  write path:name
PUT    /api/gridspine/{name}/config  write path:name
POST   /api/gridspine/{name}/connection  write path:name
POST   /api/gridspine/{name}/dispatch-source  write path:name
POST   /api/gridspine/{name}/dispatch-source/external  write path:name
POST   /api/gridspine/{name}/readback/{hour}  write path:name
POST   /api/gridspine/{name}/run  write path:name
PUT    /api/gridspine/{name}/templates/{unit_id}/{param}  write path:name
POST   /api/io/import/csv  none replaces the open network with an unsaved scratch copy; writes no project
POST   /api/io/import/excel  none replaces the open network with an unsaved scratch copy; writes no project
POST   /api/io/import/matpower  none replaces the open network with an unsaved scratch copy; writes no project
POST   /api/io/import/netcdf  none replaces the open network with an unsaved scratch copy; writes no project
POST   /api/library/items/tariff/import_urdb  none the org library, not a project
PUT    /api/library/items/{kind}/{name}  none the org library, not a project
POST   /api/library/meter_data  none the org library, not a project
POST   /api/library/series  none the org library, not a project
POST   /api/library/series/upload  none the org library, not a project
POST   /api/library_reference/preview  allow declared non-mutating (assistant_route_parity non_mutating_routes): computes and returns, writes nothing
PUT    /api/local-settings/anthropic-key  none desktop settings
POST   /api/local-settings/reveal-log  none desktop settings
DELETE /api/mcp  none personal MCP connections
POST   /api/mcp  none personal MCP connections
POST   /api/memory/dismissals  none personal assistant memory
POST   /api/memory/dream  none personal assistant memory
PUT    /api/memory/dream/settings  none personal assistant memory
POST   /api/memory/items  none personal assistant memory
PATCH  /api/memory/items/{item_id}  none personal assistant memory
POST   /api/memory/items/{item_id}/{action}  none personal assistant memory
POST   /api/memory/parameters  none personal assistant memory
POST   /api/memory/suggestions/{item_id}/preview  none personal assistant memory
PATCH  /api/network/_bulk  write active
POST   /api/network/buses  write active
DELETE /api/network/buses/{name}  write active
PUT    /api/network/buses/{name}  write active
DELETE /api/network/buses/{name}/cascade  write active
POST   /api/network/buses/{name}/rename  write active
POST   /api/network/carriers  write active
DELETE /api/network/carriers/{name}  write active
PUT    /api/network/carriers/{name}  write active
POST   /api/network/cluster  write active
POST   /api/network/document_values/apply  write active
POST   /api/network/equipment_table/apply  write active
POST   /api/network/generators  write active
POST   /api/network/generators/upload_profile  write active
DELETE /api/network/generators/{name}  write active
PUT    /api/network/generators/{name}  write active
POST   /api/network/global_constraints  write active
DELETE /api/network/global_constraints/{name}  write active
PUT    /api/network/global_constraints/{name}  write active
PATCH  /api/network/investment_period_weightings  write active
POST   /api/network/investment_periods  write active
POST   /api/network/lengths/from_geometry  write active
POST   /api/network/library_reference/apply  write active
POST   /api/network/lines  write active
POST   /api/network/lines/recalculate_lengths  write active
POST   /api/network/lines/rescale_impedances  write active
DELETE /api/network/lines/{name}  write active
PUT    /api/network/lines/{name}  write active
POST   /api/network/links  write active
POST   /api/network/links/upload_profile  write active
DELETE /api/network/links/{name}  write active
PUT    /api/network/links/{name}  write active
POST   /api/network/loads  write active
POST   /api/network/loads/upload_profile  write active
DELETE /api/network/loads/{name}  write active
PUT    /api/network/loads/{name}  write active
POST   /api/network/memory_parameter/apply  write active
PUT    /api/network/meta  write active
POST   /api/network/reset  write active
POST   /api/network/shunt_impedances  write active
DELETE /api/network/shunt_impedances/{name}  write active
PUT    /api/network/shunt_impedances/{name}  write active
POST   /api/network/snapshots  write active
POST   /api/network/snapshots/multi_period  write active
POST   /api/network/snapshots/sample_weeks  write active
PATCH  /api/network/snapshots/weightings  write active
POST   /api/network/snapshots/weightings.csv  write active
POST   /api/network/spectrum/tier_split  write active
POST   /api/network/storage_units  write active
DELETE /api/network/storage_units/{name}  write active
PUT    /api/network/storage_units/{name}  write active
POST   /api/network/stores  write active
DELETE /api/network/stores/{name}  write active
PUT    /api/network/stores/{name}  write active
DELETE /api/network/timeseries  write active
POST   /api/network/timeseries/upload  write active
PUT    /api/network/timeseries/{component}/{attribute}  write active
POST   /api/network/transformers  write active
DELETE /api/network/transformers/{name}  write active
PUT    /api/network/transformers/{name}  write active
POST   /api/network/undo  write active
POST   /api/network/vintage_bounds/_cleanup_orphans  write active
DELETE /api/network/vintage_bounds/{component_class}/{name}  write active
PUT    /api/network/vintage_bounds/{component_class}/{name}  write active
POST   /api/profiles/estimate  allow declared non-mutating (assistant_route_parity non_mutating_routes): computes and returns, writes nothing
POST   /api/projects/from_template/{template_id}  none creates a new project the caller owns
POST   /api/projects/import-folder  none creates a new project the caller owns
POST   /api/projects/import_bundle  none creates a new project the caller owns
POST   /api/projects/unclaimed/{legacy_name}/import  none creates a new project the caller owns
POST   /api/projects/{base}/scenarios  write path:base
DELETE /api/projects/{name}  write path:name
POST   /api/projects/{name}  write path:name
PUT    /api/projects/{name}/asset_health  write path:name
POST   /api/projects/{name}/flow/ack  write path:name
POST   /api/projects/{name}/flow/ack-many  write path:name
PUT    /api/projects/{name}/flow/scope  write path:name
POST   /api/projects/{name}/intent-comparison  write path:name
POST   /api/projects/{name}/intent-comparison/abort  write path:name
PUT    /api/projects/{name}/layout  write path:name
PUT    /api/projects/{name}/map_layout  write path:name
POST   /api/projects/{name}/map_layout/import  write path:name
PUT    /api/projects/{name}/members  write path:name
POST   /api/projects/{name}/profiles/apply  write path:name
POST   /api/projects/{name}/profiles/apply-batch  write path:name
POST   /api/projects/{name}/profiles/availability/generate  write path:name
POST   /api/projects/{name}/profiles/defaults/apply  write path:name
POST   /api/projects/{name}/profiles/defaults/preview  write path:name
POST   /api/projects/{name}/profiles/from-current  write path:name
POST   /api/projects/{name}/profiles/generate  write path:name
POST   /api/projects/{name}/profiles/loads/generate  write path:name
POST   /api/projects/{name}/profiles/loads/upload  write path:name
POST   /api/projects/{name}/profiles/refine  write path:name
POST   /api/projects/{name}/profiles/series/generate  write path:name
POST   /api/projects/{name}/profiles/series/upload  write path:name
POST   /api/projects/{name}/profiles/shape  write path:name
POST   /api/projects/{name}/profiles/unrecorded/confirm  write path:name
DELETE /api/projects/{name}/profiles/weather  write path:name
PUT    /api/projects/{name}/profiles/weather  write path:name
POST   /api/projects/{name}/rename  write path:name
POST   /api/projects/{name}/report-charts  write path:name
DELETE /api/projects/{name}/report-charts/{chart_id}  write path:name
POST   /api/projects/{name}/reports  write path:name
POST   /api/projects/{name}/reports/generate  write path:name
POST   /api/projects/{name}/reports/generate/abort  write path:name
DELETE /api/projects/{name}/reports/{report_id}  write path:name
POST   /api/projects/{name}/reports/{report_id}/comments  allow a viewer may comment on a report (owner decision VIEWER-ROLE-1, Q3); the report itself is unchanged
POST   /api/projects/{name}/reports/{report_id}/comments/{comment_id}/address  write path:name
POST   /api/projects/{name}/reports/{report_id}/comments/{comment_id}/withdraw  write handler
POST   /api/projects/{name}/reports/{report_id}/export  allow a viewer may export a report file (owner decision VIEWER-ROLE-1, Q4); the stored report is unchanged
POST   /api/projects/{name}/reports/{report_id}/roundtrip  write path:name
POST   /api/projects/{name}/reports/{report_id}/sections/{section_id}/regenerate  write path:name
POST   /api/projects/{name}/reports/{report_id}/template  write path:name
POST   /api/projects/{name}/reports/{report_id}/template/plan  write path:name
PUT    /api/projects/{name}/reports/{report_id}/template/plan  write path:name
PATCH  /api/projects/{name}/scenario  write path:name
PATCH  /api/projects/{name}/settings  write path:name
PUT    /api/projects/{name}/site-anchor  write path:name
PUT    /api/projects/{name}/sites  write path:name
DELETE /api/projects/{name}/sites/{site_id}/context  write path:name
POST   /api/projects/{name}/sites/{site_id}/context  write path:name
POST   /api/projects/{name}/sites/{site_id}/scene/adopt  write path:name
POST   /api/projects/{name}/sites/{site_id}/scene/exports  write path:name
POST   /api/projects/{name}/sites/{site_id}/scene/proposals  allow prepares an in-memory proposal owned by the caller; nothing is written until apply, which is refused (read-tier tool site_scene_prepare)
POST   /api/projects/{name}/sites/{site_id}/scene/proposals/{proposal_id}/apply  write path:name
POST   /api/projects/{name}/sites/{site_id}/scene/proposals/{proposal_id}/cancel  write path:name
PUT    /api/projects/{name}/sites/{site_id}/thumbnail  write path:name
POST   /api/projects/{name}/snapshots  write path:name
DELETE /api/projects/{name}/snapshots/{snapshot_id}  write path:name
POST   /api/projects/{name}/snapshots/{snapshot_id}/restore  write path:name
PUT    /api/projects/{name}/stress_scenarios  write path:name
POST   /api/projects/{name}/studies/  write path:name
POST   /api/projects/{name}/studies/preview  allow declared non-mutating (assistant_route_parity non_mutating_routes): computes and returns, writes nothing
DELETE /api/projects/{name}/studies/{study_id}  write path:name
PATCH  /api/projects/{name}/studies/{study_id}  write path:name
POST   /api/projects/{name}/studies/{study_id}/findings/tornado  write path:name
POST   /api/projects/{name}/studies/{study_id}/findings/tornado/abort  write path:name
POST   /api/projects/{name}/studies/{study_id}/grid-extension-break-even  allow computes a break-even distance from a stored study and writes nothing (read-tier tool get_grid_extension_break_even)
PUT    /api/projects/{name}/studies/{study_id}/ledger  write path:name
POST   /api/projects/{name}/studies/{study_id}/report  write path:name
POST   /api/projects/{name}/studies/{study_id}/run  write path:name
POST   /api/projects/{name}/studies/{study_id}/run/abort  write path:name
POST   /api/projects/{name}/studies/{study_id}/supply-architectures  write path:name
POST   /api/projects/{name}/studies/{study_id}/supply-architectures/abort  write path:name
POST   /api/projects/{name}/uploads  write path:name
DELETE /api/projects/{name}/uploads/{file_id}  write path:name
PUT    /api/projects/{name}/worksheet  write path:name
POST   /api/projects/{project_id}/activate  allow opening a project to read it; activate itself saves nothing (the client's autosave is POST /api/projects/{name}, refused)
DELETE /api/projects/{project_id}/lock  allow releases a lock the caller holds; writes no project
POST   /api/projects/{project_id}/lock  write path:project_id
POST   /api/projects/{project_id}/lock/heartbeat  write path:project_id
POST   /api/results/billing/preview  allow declared non-mutating (assistant_route_parity non_mutating_routes): computes and returns, writes nothing
POST   /api/results/business_case  write active
POST   /api/results/business_case/abort  write active
PUT    /api/results/business_case/inputs  write active
POST   /api/results/coupling_loop  write active
POST   /api/results/coupling_loop/abort  write active
POST   /api/results/eh_study  write active
POST   /api/results/eh_study/abort  write active
POST   /api/results/fmea_sweep  write active
POST   /api/results/fmea_sweep/abort  write active
POST   /api/results/frontier  write active
POST   /api/results/frontier/abort  write active
POST   /api/results/investment_case  write active
POST   /api/results/investment_case/abort  write active
POST   /api/results/margin_loop  write active
POST   /api/results/margin_loop/abort  write active
POST   /api/results/mc  write active
POST   /api/results/mc/abort  write active
POST   /api/simulation/abort  write active
PUT    /api/simulation/commercial/value_flows  write active
POST   /api/simulation/document_values/apply  write active
PUT    /api/simulation/finance  write active
POST   /api/simulation/force_reset  write active
POST   /api/simulation/preflight  allow declared non-mutating (assistant_route_parity non_mutating_routes): computes and returns, writes nothing
POST   /api/simulation/queue  write handler
POST   /api/simulation/queue/cancel_queued  write handler
POST   /api/simulation/queue/clear_finished  none instance-wide queue control, super-admin only
POST   /api/simulation/queue/pause  none instance-wide queue control, super-admin only
POST   /api/simulation/queue/resume  none instance-wide queue control, super-admin only
POST   /api/simulation/queue/{job_id}/abort  write handler
POST   /api/simulation/queue/{job_id}/dismiss  none hides a finished job from the caller's own listing; touches no project
POST   /api/simulation/queue/{job_id}/requeue  write handler
POST   /api/simulation/run  write active
POST   /api/simulation/run_ac_pf  write active
PUT    /api/simulation/solver_config  write active
POST   /api/simulation/value_flows/template  write active
POST   /api/spectrum/trace  none analyses an uploaded trace in memory; stores nothing
"""

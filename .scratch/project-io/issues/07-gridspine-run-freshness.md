# 07 A planning study's run reads stale after an edit

Status: needs-triage

Area: GK A (engine). `gridspine/drivers/status.py` `stage_status` judges a run by which files are present; `gridspine_service.update_config` rewrites `config.json` and leaves `gridspine/run/` alone. So a planning study's run reads "completed" after an edit to its config or network, in place or after a copy (bundle, Save-As: issue 01 carries `run/` byte-equal and marks nothing current). A run should record what it was computed from, and the status should read stale when that no longer matches.

Journey fit: run → wait → results for planning projects, Guided and Expert; hand-off from setup (config edits) to results.

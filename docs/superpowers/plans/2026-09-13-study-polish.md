# Study-surface polish — plan

Follow-on to PRs #27–#30. No behaviour change.

1. Pin `NEVER_BOUND_WITH_MARGIN_COPY_V1` and `MARGIN_MULTI_PERIOD_WARNING_V1` on the planning-loop facade tripwire (already re-exported from `routers.results`).
2. DRY the five study abort POSTs behind `_abort_study(key, never_run_msg)`; keep per-route docstrings and 404 copy.
3. Add a live `margin_loop` section to `qa_adequacy_studies.py` mirroring the coupling-loop live path (empty-state already covered).

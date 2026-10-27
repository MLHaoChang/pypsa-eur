Demo project `dcsurf-demo` (app on :8000/:5173; restart with `bash restart.sh <repo root>`, run it as a file, never inline):
1. `python setup_pr1.py` (creates the project from the eh_datacenter template and drafts pq_demo), then `python setup_scorecard.py` (publishes pq_demo, adds process units, reactivates).
2. `python run_pr2.py` (solve, save, draft the campus file, run the study); for a resonance, `python add_cap.py` then `python rerun_pr2.py`; `V=2.0 python republish.py` for the grid-code-changed state.
3. Shoot: `node shoot_scan.mjs <expert|guided> <out> <name>`, `node shoot_scorecard.mjs <mode> <out> <name> <row ids…>` (run setup_scorecard.py before each), `node shoot_pr1.mjs <out>` (PATHS/TAG/N1/N2 env for the grid-code rows).

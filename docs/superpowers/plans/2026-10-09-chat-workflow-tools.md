# Chat workflow tools

Implement the five additions requested after the comprehensive API workflow:

Execution follows four steps: inspect existing contracts; implement/integrate
the tools; run offline and bounded live checks; document and publish a PR to
`master`. The implementation includes the existing normal confirmation path.

1. `project_readiness`: inspect a caller-authorized project, network validation,
   solver availability, saved/dirty state, time-series store, and run freshness.
2. `wait_for_job`: use the existing queue's authorized detail resolver, bound the
   local wait to 25 seconds, observe chat cancellation, and reuse progress frames.
3. `get_study_evidence`: read existing GridSpine tables, compute counts before
   paging, expose failed rows through filters, and cache bounded responses using
   project/run/config/source fingerprints. Read saved simulation comparisons.
4. `run_sensitivity_sweep`: execution-tier confirmation; validate 1–4 typed
   scenarios before creating any. Prepare isolated network contexts, preserve
   baseline/active binding, save children, and enqueue existing solve jobs.
   Reuse solved cases only when stored provenance and model inputs still match.
   Return partial preparation failures explicitly; never delete existing work.
5. `use_toolset`: provider-neutral ordered catalogue views. Preserve controls,
   project navigation, project-kind eligibility, and exact dispatch allowlists.
   Refresh after toolset switches and intentional project rebinding between model
   requests, including within one turn.

Verification: real project/queue/ACL fixtures, cancellation and terminal failures,
cached count/pagination/freshness oracles, baseline preservation and real HiGHS
sensitivity solves, reuse/conflict/partial failure checks, confirmation denial,
same-turn catalogue transition/allowlist checks, existing provider/harness/backend
regressions, frontend tests/build, and a bounded opt-in paid conversation. Reuse
the existing aggregate API ledger, retaining the 5 million total-token / $5 caps.
No extra calls to exhaust credit. Previous failed environments remain untouched.

Limits: waits return pending at the deadline; stopping a wait does not stop a job.
Sensitivity edits are static component parameters, not time-series rewrites or
GridSpine configuration sweeps. A large scenario preparation can still encounter
the harness's normal per-tool deadline. Source freshness is based on saved config
and source/artifact timestamps. GridSpine results remain steady-state evidence.

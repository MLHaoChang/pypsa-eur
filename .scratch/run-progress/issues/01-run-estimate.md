<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Slice 1: run estimate service and route

Status: ready-for-agent
Type: task
Blocked by: None

Spec: [../spec.md](../spec.md) §2 and §6 slice 1. `services/run_estimate.py`,
`solve_job_store.load_completed_runs`, `GET /api/simulation/queue/{job_id}/estimate`.

## Comments

2026-11-17: GK B (62) plan GO with conditions:
- (a) Key on the project only, no cross-project fallback. The estimate always
  carries its basis (sample count, run ids); every surface says "based on the
  last n runs of this project", never a promise.
- (b) Slice 6 may add the ETA to `wait_for_job` and the status output: after the
  verified-outcome block (#927 ordering); unknown stated as unknown, never 0 or
  blank; the ETA never changes poll intervals, timeouts or job state; spoken
  line bounded like other voice echoes; tool-schema, inventory and route-parity
  checks run. Its own plan once #1550/#1556 merge.
- E1 Eligibility: only `completed` rows with finite positive durations in UTC;
  naive/missing timestamp, finished ≤ started, unparseable config excluded, never
  a 500. Each case tested.
- E2 Mode key: name the fields, read through the queue's parser (no fork), test
  that a mode change resets the samples; say whether network size is in the key.
- E3 Last 5 eligible rows by finished_at; < 2 → unknown; state the even-n median;
  pin n=2 and n=5 with hand values; 1 → unknown, 2 → a value.
- E4 Route: an unseen project answers like a nonexistent one; kind validated;
  a viewer may read; at the trial merge run test_route_chat_disposition,
  test_project_route_acl_policy, test_golden_coverage, route_parity --check.
- E5 One killing mutant per guard: status filter, positive duration, mode
  match, the n<2 cut, the ACL.

2026-11-17: Built as a per-job route (`/{job_id}/estimate`) instead of
`?project_id=&kind=`: the status line needs the estimate of the run that is
running, keyed on that job's own settings snapshot. The kind and project come
from the job, so E4's kind check becomes "a job of an unknown kind returns
unknown". Visibility is `_visible_job_or_404`, the same byte-for-byte 404 as
`/log_history`. There is no viewer role in the code yet
(`.scratch/project-viewer-role`), so "a viewer may read" means the route checks
read access only, never the edit lock. Mode key per kind: solve = mode,
solver_name, multi_investment_periods via `_solver_config_from_dict`; campus and
profile_gen = the whole settings snapshot (their jobs store their own settings,
and an island-tuning action must not mix with a full run); gridspine = none.
SQLite reads timestamps back naive; `load_completed_runs` stamps them UTC at the
ORM seam (every writer stamps UTC), and the estimator still rejects naive input.

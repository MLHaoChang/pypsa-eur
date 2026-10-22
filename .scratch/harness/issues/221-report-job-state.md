<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# PR #221 — report worker state and recovery regression

Status: ready-for-human
Progress: report_job declared in STUDY_KEYS and project_context; no regression test for 409 reset with held worker found; PR #221 not merged
Type: task

The owner authorized completing the initial open PRs before CP-03. Cursor's tests-only source `00965f2b46e2d8a100adc2af69d39bdc3b3e8659` failed [Test 38547611378](https://github.com/MLHaoChang/pypsa-eur/actions/runs/38547611378) in backend shard 1: `test_state_dict_keys_match_solver_state_fields` found the undeclared `report_job` key. The strict guard is preserved.

Declare the report worker/control record in canonical per-project state and the transient study/job partition. A live worker prevents operations that would discard its controls; finished records clear on reset and are not persisted as solve results. Report generation reads captured evidence, so it does not join the live-network mutation group. Refusals describe its real status/cancel controls without claiming it re-solves the network. The existing native abort route is included in the route-derived abortability test.

The new authenticated regression first failed because reset returned 200 during a held real worker. It now requires 409 with the same accessible report ID, successful reset after completion, a cleared runtime status and a still-readable completed document. Inference is fake; workers, stores and HTTP guards are real. Cursor's corrupt-log/transcript fallback and missing/zero-length tests remain unchanged.

Local lifecycle/report/recovery selection: **191 passed**, 142.27 seconds. All three semantic suites plus neighboring report-tool/export/wait cases: **222 passed, one empty-parameter skip** (`KNOWN_GAPS` empty), 30.20 seconds. Expanded offline checked-answer gate: **133 required cases passed**, live generation `pending_opt_in`. Counts overlap and are not added. Local tests select this checkout's GridSpine via `PYTHONPATH`; an initial run accidentally imported another editable checkout and was not accepted as evidence. Selected E/F lint passes; the 22 full-file docstring diagnostics are identical to baseline. No assertion, skip or production guard was weakened.

The code changes no provider wire, profile configuration, route signature or tool catalogue. Exact-head remote backend shards and pilot/QA execution, fresh current-master review and ordinary protected merge remain required. No paid calls, reviewer contact, deployment or independent domain/calibration claim is made by this repair. Durable execution record: `/workspace/scratch/github-actions/cursor-221-continuation.json`.

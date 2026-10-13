<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 33 checked-answer tests fail in CI only

Status: ready-for-agent
Type: task

## What fails

The `GUI backend` job (`.github/workflows/test.yaml`, `pixi run gui-tests`) fails
the same 33 tests on master and on every PR. The first observation was master at
cf5d8b0c on 2026-10-12, [run 38315412784](https://github.com/MLHaoChang/pypsa-eur/actions/runs/38315412784):

- `tests/test_reliability_cases.py::test_stress_case[...]`: 23 stress cases, both
  `golden` and `value`, for the `single-first`, `single-discharge` and
  `equal-loads` stresses.
- `tests/test_reliability_transport.py::test_cost_scope_on_every_transport[...]`:
  all 10 parametrisations. Each fails with "Missing exactly one owned
  approved-answer publication receipt" (`tests/assurance_acceptance/support.py`).

They have one cause. No `answer_approved` frame is published, so every other
check in `harness/pilot_evaluation.score` also fails: `approval_count`,
`owned_answer_or_digest`, `profile_mismatch`, `project_binding`,
`required_slots` and `stream_contract`.

Actions was off from 2026-10-08 until 2026-10-12, so the commit that introduced
the failures is not known. Master merged about 170 PRs in that window without CI.

## What was ruled out

These were run locally on 2026-10-13 in a plain venv, not pixi. All passed:

1. Both files on their own (90 tests), on master and on the #98 head.
2. All 429 top-level `tests/test_*.py` files that sort before
   `test_reliability_cases.py`, followed by the transport tests: 8,801 passed.
   A pass with the full prefix means test order alone does not reproduce it.
3. The earlier step in the same job, `python tools/run_pilot_evaluation.py
   --expanded --report pilot-evaluation.json`, followed by the transport tests,
   in a clean master checkout.
4. `tests/assurance_acceptance/`, which collects before `tests/test_*`,
   followed by the transport tests (64 passed).

The difference left is the environment. CI uses pixi's `test` environment
(`pixi.toml`: features `test`, `dev`, `desktop`; solve group `gui`), and the
local run used a different set of package versions. `jsonschema`, which the
assurance tests import, is declared nowhere in this package's requirements
(#146 proposes declaring it), so a version difference there is a candidate.

## Next step

Install pixi (`v0.68.1`, as the workflow pins), run `pixi run gui-tests -- \
tests/test_reliability_transport.py`, and see whether it fails on its own in
that environment. If it does, compare package versions with a passing venv,
starting with `jsonschema`, `pydantic` and `httpx`. If it does not, bisect the
collection order under pixi.

Done means the `GUI backend` job is green on master with these 33 tests running,
not skipped.

<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Assistant answer-assurance first acceptance tranche

Historical transferred test results, not rerun acceptance evidence for this
revision. Current source audit/access and pending added requirements are recorded
in the [reliability assessment](2026-10-10-assistant-assurance-reliability.md).

The comprehensive design and initial acceptance tests passed independent review.
**Historical pre-implementation assessment.** Current pilot behavior and validation
are recorded in the [implementation status](../../../.scratch/assistant-assurance/implementation-status.md).
At the time of this assessment the gate was unimplemented. It records an intentional
RED baseline and existing regression controls, not a successful assurance release.
The [coverage ledger](../../../.scratch/assistant-assurance/coverage-ledger.md)
maps every requirement group to executable coverage or explicit remaining work.

## Scope and independent review

Specification: [implementation plan](../plans/2026-10-10-assistant-answer-assurance.md)
and [frozen protocol](../../../.scratch/assistant-assurance/protocol-v1.md).
Baseline: local origin/master `d43809ae`; already-merged dictation PR 106 retained.
No application source, providers, tool handlers, locks or other worktrees changed.

A separate agent reviewed the design and then the tests. Before final approval we
strengthened the suite to require actual provider HTTP requests, nonempty approved
answers and persisted history, correct event order, an integer mismatched generation,
exact independently recomputed canonical chart hashes and clearly classified legacy
storage corruption. Final verdict: GO for design publication and first opt-in RED
tranche, with full phase coverage, implementation and release approval still pending.

## Observed checks

| Check | Result | Meaning |
| --- | --- | --- |
| Opt-in backend acceptance | 43 failed, 4 passed; 47 cases | Expected missing-feature baseline; no collection/setup errors |
| Opt-in frontend acceptance | 11 failed, 1 passed; 12 cases | Expected missing-feature baseline; one existing voice availability control |
| Relevant backend regressions | 321 passed, 48 skipped | Existing behavior retained; 47 proposed acceptance skips plus deliberate golden recorder |
| Full default frontend suite | 3,529 passed, 12 skipped | Proposed assurance tests skipped; no existing test failures |
| Frontend TypeScript + Vite production build | Passed | New test file compiles; existing large-chunk advisory remains |
| Assistant inventory | Passed: 220 tools, 14 tabs, 17 panels | Registry/navigation schema parity only, not assurance behavior |
| Ruff lint/format for new Python tests | Passed | Static test quality |
| Documentation relative links and whitespace | Passed | No broken links in reviewed plan/test docs |

The four backend passes are two existing anonymous-auth refusals, existing CSRF
refusal, and existing unsolved-result chart refusal. They do not prove a new
assurance feature. Frontend voice availability can pass while the current UI ignores
approval frames; it is deliberately reported as a control, not an admission check.

Failure inspection shows missing owned-answer/evidence/ACK routes and publication
frames, ignored strict negotiation, unchecked model prose/thinking publication,
missing source/receipt metadata, ordinary completion ticks for noncompleted outcomes,
and no exact chart/delivery integrity chain. Legacy delivery accepts modified chart
provenance. These are baseline gaps, not newly introduced source regressions.

No real credentials were used in any test. FakeProvider and HTTP MockTransport supply
model responses. The API/router/turn loop, project handlers, tool dispatch, chart
renderer and authorized downloads use isolated existing fixtures. Existing provider
parity regressions also use HTTP mocks and actual handlers/HiGHS. No paid API tokens
or credits were spent.

## Reproduction

From repository root, use an interpreter with backend test dependencies:

```bash
PYPSA_GUI_TEST_ASSURANCE_ACCEPTANCE=1 \
PYPSA_GUI_ANSWER_ASSURANCE_MODE=strict \
python -m pytest -o addopts='' -q \
  pypsa-gui/backend/tests/assurance_acceptance --tb=short
```

Relevant backend regression selection used:

```bash
python -m pytest -o addopts='' -q \
  pypsa-gui/backend/tests/assurance_acceptance \
  pypsa-gui/backend/tests/test_chat_stream_attempt_seam.py \
  pypsa-gui/backend/tests/test_chat_pre_dispatch_validation.py \
  pypsa-gui/backend/tests/test_number_audit.py \
  pypsa-gui/backend/tests/test_report_generator.py \
  pypsa-gui/backend/tests/test_workflow_provider_parity.py \
  pypsa-gui/backend/tests/test_workflow_tools.py \
  pypsa-gui/backend/tests/test_assistant_capabilities.py \
  pypsa-gui/backend/tests/test_chat_history_per_user.py \
  pypsa-gui/backend/tests/test_chat_stream_session_ownership.py \
  pypsa-gui/backend/tests/test_chat_profile_binding.py \
  pypsa-gui/backend/tests/test_chat_pending_turn_wal.py \
  pypsa-gui/backend/tests/test_chat_tools_schema_match.py \
  pypsa-gui/backend/tests/test_chat_tools_endpoint_map.py \
  pypsa-gui/backend/tests/test_chat_turn_frame_contract.py \
  pypsa-gui/backend/tests/test_chat_e2e.py
```

From `pypsa-gui/frontend`:

```bash
PYPSA_GUI_TEST_ASSURANCE_ACCEPTANCE=1 npm test -- \
  src/components/ChatPanel.assuranceAcceptance.test.tsx --maxWorkers=2
npm test -- --maxWorkers=2
npm run build
```

The implementation branch intentionally defaults to skipping unimplemented
acceptance cases. Run the opt-in to see RED; a skip is never verification. Continue
with individual vertical red/green slices, not a bulk speculative gate implementation.

## Remaining blockers

GitHub publication is blocked by HTTP 401 “Bad credentials”. The replacement
GitHub secret was absent from this running session at the last access check.
No plan/test PR or master upload is claimed. Reviewed docs are committed locally
on `docs/assistant-answer-assurance`; tests use a separate branch/worktree.
Once authenticated, publish docs first; then base the acceptance-only changes
on resulting master, keeping separate reviewable PRs and avoiding duplicate docs.

Runtime implementation and full phase coverage remain pending: retained historical
run evidence, source capture before truncation, complete aggregates, reviewer schema
and bounded execution, ACL/abort/commit/recovery races, exact issued ACK success,
complete domain journeys, rollouts, real browser/device accessibility and measured
latency/calibrated engineering judgments. All are requirements in the ledger,
not hidden skips or certified behavior.

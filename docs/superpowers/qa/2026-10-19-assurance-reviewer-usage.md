# Checked-answer reviewer usage completeness

AI-prepared implementation, tests and validation record. Scope: A6.1 / P6 bounded reviewer failure handling.

A reviewer could return a passing interpretation with only input usage, only output usage, or cache-only usage. Missing counts were treated as zero. Three new cases through the real authenticated chat/publication harness reproduced this as incorrect `passed` answers before the fix. Require explicit nonnegative integer input/output token counts before admitting the reviewer verdict. Charge available valid counts before refusing incomplete usage. Preserve checked facts and withhold interpretation with the existing limitation; no repair or second reviewer call is added.

## Fresh validation

- Restored frozen default/test environments with Pixi 0.68.1 on osx-arm64, Python 3.13.0; frontend dependencies from `npm ci`.
- Before fix: three new public-boundary regressions failed (`passed` instead of `limited`).
- After fix: full `test_harness_reliability.py`: **65 passed**, zero skips.
- Full phased backend `tests/assurance_acceptance`: **54 passed**, zero skips.
- Expanded standalone evaluation: **133 passed**, zero skips, 138.00 seconds. Fulfillment **330/330**, incorrect calculations **0/330**, false acceptance **0/88**, false rejection **0/45**, unchecked publication **0/133**. Scripted finite-suite results, not population estimates.
- Related backend/provider/budget/publication/storage/frame/calibration selection: **204 passed / four opt-in skips**, 56.70 seconds. Skips are live provider probes and deliberate golden recording; no skip is counted as coverage.
- Frontend assurance component/utility suites: **62 passed / three files**. TypeScript and Vite production build passed, retaining the existing large-chunk advisory.
- Changed Python files pass Ruff; whitespace check passed. Editing-agent source review found no further issue in this bounded diff; no independent domain review is claimed.

Counts are separate selections and are not summed as unique coverage. No fresh full backend or full frontend result is claimed. Exact backend Python SHA-256, log hashes and evaluation counters are in `.scratch/assistant-assurance/reviewer-usage-validation.json`. The tested source was the dirty tree based on `0b33685c465e83b2a39c5964845b7e590873e631`; the recorded hash identifies its Python contents. Only documentation/evidence is added afterward.

## Reproduction

From the repository root, using `.pixi/envs/test/bin/python`:

```sh
python -m pytest -o addopts='' -q pypsa-gui/backend/tests/test_harness_reliability.py --tb=short
PYPSA_GUI_ANSWER_ASSURANCE_MODE=strict python -m pytest -o addopts='' -q pypsa-gui/backend/tests/assurance_acceptance --tb=short
```

From `pypsa-gui/backend`:

```sh
python tools/run_pilot_evaluation.py --expanded --report /tmp/expanded-evaluation.json
```

The related selection comprised `test_answer_assurance.py`, `test_assurance_metrics.py`, `test_assurance_storage.py`, `test_reliability_transport.py`, `test_pilot_evaluation_scoring.py`, `test_chat_budget_gates_seam.py`, `test_llm_provider_seam.py`, `test_harness_provider_parity.py`, `test_chat_turn_frame_contract.py` and `test_review_calibration.py`.

From `pypsa-gui/frontend`:

```sh
npm test -- --run src/components/ChatPanel.assuranceAcceptance.test.tsx src/utils/answerAssurance.test.ts src/components/CompanionOutcome.assurance.test.tsx --maxWorkers=2
npm run build
```

## Remaining scope

P0–P7 remain broader than this delivered slice. Independent domain review, actual representative provider observations/calibration, additional domain/operation adapters, browser/device journeys and release enablement remain open. The committed reviewer templates remain unresolved. No paid model calls, reviewer contact or production setting changes occurred. Remote CI status is separate from these local results.

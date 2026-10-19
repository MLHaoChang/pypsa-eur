# Report completion metadata race found by PR 376 CI

AI-prepared diagnosis, fix and verification. The initial Test workflow 39243528152 passed its 133-case checked-pilot gate, then failed QA job 118029854115: `qa_reports_phase2` observed a generated version with `generation={}` in the report listing. The other 32 drivers exited zero; the live provider probe was unprobed, not live-certified.

The report worker published `done`/`aborted` before saving generation metadata. A caller could observe terminal status before the receipt existed, or admit another report job during that write. A new authenticated HTTP test pauses the atomic metadata write and requires status to remain `running`; it then releases the write and checks the completed report listing. It failed deterministically before the fix (`done` instead of `running`). The standalone QA driver passed locally even before the fix, confirming why an ordinary rerun cannot certify this race away.

Save generation metadata before publishing terminal status. Pass the actual aborted outcome explicitly to the metadata writer, rather than deriving it from an already-published terminal record. Preserve ordinary failure handling, report versions and all numerical assertions. This is a lifecycle correction discovered while satisfying the owner's merge request; no domain engine, formula or provider generation change.

## Fresh local validation

- Deterministic new HTTP regression: one failure before correction.
- Report worker/HTTP/store selection: **57 passed**, no skips.
- Original failing `qa_reports_phase2.py`: **47/47 checks passed** after correction, including abort metadata and regeneration/export behavior.
- Combined report/harness/public-assurance/provider/budget/storage/frame/calibration selection: **380 passed / four existing opt-in skips**, 146.95 seconds.
- Expanded public-boundary evaluation: **133 passed**, no skips, 153.56 seconds; fulfillment **330/330**, incorrect calculations **0/330**, unchecked publication **0/133**, false acceptance **0/88**, false rejection **0/45**.
- Ruff on changed report files and whitespace checks passed. Editing-agent review only; no independent domain approval.
- The full local 33-driver runner was still in progress at publication; final status is recorded separately in the PR. No complete local backend run is claimed. Frontend sources are unchanged from the initial 62 passing assurance cases and successful production build.

The selections overlap and are not summed. Exact source hash, evaluation counters and log hashes are in `.scratch/assistant-assurance/report-generation-ci-validation.json`. The reported base commit reflects the original local metadata-equivalent source; its complete tree equals published parent `c468164700ebb954b9e2943c672c026423e606bb`. The backend Python hash identifies the actual tested follow-up contents.

## Reproduction

Using the restored Pixi test interpreter from the repository root:

```sh
python -m pytest -o addopts='' -q pypsa-gui/backend/tests/test_report_job_http.py -k done_waits_for_persisted_generation_summary --tb=short
python -m pytest -o addopts='' -q pypsa-gui/backend/tests/test_report_job_http.py pypsa-gui/backend/tests/test_report_job.py pypsa-gui/backend/tests/test_report_store.py --tb=short
python pypsa-gui/backend/tests/qa_reports_phase2.py
python pypsa-gui/backend/tests/run_qa_drivers.py
```

The combined selection additionally contains the same assurance/provider/budget/frame/calibration files listed in `2026-10-19-assurance-reviewer-usage.md`, plus the full harness reliability and phased acceptance suites. The expanded runner command is unchanged. No paid provider call, external review contact, production activation or gate bypass occurred.

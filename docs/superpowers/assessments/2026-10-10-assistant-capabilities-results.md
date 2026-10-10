# Assistant capabilities — implementation and acceptance

Design spec: [reviewed plan](../plans/2026-10-10-assistant-capabilities.md), published independently to master through [PR #105](https://github.com/MLHaoChang/pypsa-eur/pull/105), merge commit `722c9afa8dbbc3f9ed1d5dee69f97d8771d1f81c`. Implementation extends [PR #104](https://github.com/MLHaoChang/pypsa-eur/pull/104). The previous failed environment was untouched.

## Implemented

Fourteen additional tools share the existing catalogue/dispatcher/safety contracts:

- Discovery: `find_capabilities`.
- Durable tasks: `start_task`, `list_tasks`, `get_task`, `resume_task`, `cancel_task`, `resolve_task_step`.
- Files: `get_file_delivery`, `inspect_import`, `import_uploaded_network`.
- Changes: `preview_project_changes`, `apply_project_changes`.
- Outputs: `create_chart`, `build_delivery`.

Tasks checkpoint ordinary offered calls rather than recursively executing hidden tools. Each actual call still consumes the turn budget and receives normal confirmation, authorization, project-switch, lock, study and undo handling. Task ownership/project authority is rechecked; plans use atomic disk checkpoints, stable request deduplication and backward result references. New chat sessions/providers can discover and resume owned plans. Defaults compare canonically. Completed steps are retained; interrupted effectful calls and partial submissions require explicit confirmed review. Detached timeout workers block reconciliation. Failed/aborted jobs cannot complete a wait step. Interactive choice requests and project rebinding run outside durable action steps.

Preview changes are typed engineering inputs from the existing sensitivity allowlist. Validation operates on a private candidate; apply rechecks the unchanged fingerprint under the network lock and retains unsaved work/undo. Both chat and confirmation show the immutable old/new/unit diff. Scientific result charts require aligned dispatch and matching solve-input provenance, persisted with netCDF metadata. Safe cloning excludes the native solver model only from the private copy.

All existing agent exports gain authenticated download links, content hashes and project provenance. Native NC/HDF/CSV-ZIP/MATPOWER files can enter through the chat upload picker as metadata/IDs. Inspection samples CSV/Excel metadata, missing values and time-axis diagnostics without assuming units. Native import verifies the content hash and bounds archive expansion before using existing importers. Delivery ZIPs include hashes and chart provenance. Chat cards show progress, next steps, ordinary resume/cancel requests, previews and safe download links through existing stream events.

## Bugs found and fixed

1. A read safety tier did not imply an effect-free call: native exports create artifacts. Task recovery now treats exports as effectful.
2. Validation warnings are counts, not a list. Preview reads the real count/issues contract.
3. PyPSA refuses copying solved networks with attached native solver models. Private cloning preserves the live model and skips the model edge on the copy.
4. Dispatch-axis alignment cannot detect parameter edits. The solve service now records input provenance under its existing solve lock; charts reject unmatched results.
5. Ending a timed-out chat turn does not terminate its detached worker. Reconciliation now checks worker lifetime separately.
6. Terminal failed jobs and partially submitted sweeps were not successful task steps. They now remain incomplete or require review.
7. Applying a UUID alone was insufficient for human review. The confirmation card now includes the stored engineering diff without adding handler arguments.
8. Native Excel export attempted to append to an empty temporary file. Its writer now creates a fresh workbook; native Excel import uses PyPSA's reader to preserve snapshots and dynamic sheets, while custom component workbooks retain their previous parser.
9. MATPOWER import discarded bus demand and linear generator costs; reactive demand and line unit conversion also did not round-trip. Bus demand/costs, reactive demand and conversion between MATPOWER per-unit impedance and PyPSA physical units now survive the tested projection.

## Validation

| Check | Result |
| --- | --- |
| Broad backend, 106 relevant files | 2684 passed, 235 skipped; one environment prerequisite failure because Ruff was absent |
| Corrected prerequisite and final affected backend recheck | **451 passed**, including the formerly failing solver-facade Ruff check, new tasks/outcomes/provider workflows, chat end-to-end, registry/layout/dispatch and selection |
| Native formats/import/upload/security follow-up | **88 passed, 1 skipped**; all NC, CSV ZIP, Excel and MATPOWER round trips pass, plus demand/cost/horizon/impedance oracles |
| Entire final frontend | **3422 passed across 293 files** |
| Final focused chat/result cards | **22 passed** |
| TypeScript + production Vite build | **Passed** (existing large-bundle advisory remains) |
| Native provider transports | Four full task → preview → apply → HiGHS solve → chart → export → delivery sequences pass: native Anthropic, official OpenAI-compatible chat, remote compatible endpoint, local no-auth endpoint |
| Minimal live model workflow | **Passed**, production prompt/catalogue/selection, real OpenAI model `gpt-6-luna`, real handlers and HiGHS |
| Live success cache replay | **Skipped from successful source-keyed cache; zero additional API requests** |

Native provider transport tests use mocked streaming HTTP responses with split tool-call arguments, actual result IDs and continuation messages. They are not live Anthropic evidence. The live conversation used nine tool types and completed all six planned action steps; objective **200**, PNG and delivery ZIP/manifest verified, saved baseline bytes unchanged. No execution-handler or confirmation bypass was used.

Backend commands use `/workspace/chat-compat-venv/bin/python`, `MPLCONFIGDIR=/tmp/chat-mpl` and `pytest -o addopts=''`. Logs: `/tmp/assistant-backend-final.log`, `/tmp/assistant-backend-recheck.log`, `/tmp/assistant-formats-recheck.log`, `/tmp/assistant-ui-complete-last.log`, `/tmp/assistant-ui-build-last.log`, `/tmp/assistant-live.log`, `/tmp/assistant-live-cache.log`. Temporary logs are supplementary; this checked-in report is the durable handoff.

## Paid usage and remaining limits

This live phase used **10 completions**, **239678 charged tokens**, and **212990 reported cached input tokens**. Conservative upper cost: **$0.0601905**, calculated without discounting cached input. Successful source-keyed replay made no extra calls.

Cumulative ledger at the original implementation checkpoint: **642 requests**, **4229893 charged tokens**, **3325670 reported cached input tokens**, conservative upper cost **$1.11137975**. Authorization remains **5000000 tokens/$5**; the remaining token allowance at that checkpoint was **770107**. See the follow-up below for the latest totals. Ledger `/tmp/openai-comprehensive.json` is temporary; carry cumulative totals forward if the workspace is replaced. These figures are a conservative local accounting ledger, not the platform's live credit balance.

## Remaining boundaries and blockers

- Live Anthropic remains blocked by missing credentials; native transport and real-handler parity pass with mocked model streams.
- Official Responses transport and deliberately invalid-key live checks retain the previously recorded environment proxy limitation. Existing backend/frontend regression coverage still passes; this phase does not claim fresh live proof of either path.
- Old solved networks without input provenance require a fresh solve for result charts. Static charts do not require one.
- Typed previews cover bounded engineering parameter updates, not arbitrary topology rewrites; existing CRUD/batch tools perform those operations. Task plans remain bound to a saved active project; create/activate/import before starting a plan.
- Inspection is explicitly sampled. Units and column mappings need user input; it does not silently infer a full import contract.
- MATPOWER is a static Bus/Generator/Load/Line projection; it cannot preserve full PyPSA asset names, time horizons, storage, links, investment settings or general nonlinear costs. Use NC/project bundles for complete network transport. Excel native exports preserve the tested horizon and series; legacy custom component sheets follow the existing limited parser.
- This adds tested assistant operations; it does not establish live fixture coverage for every existing external engine/handler. Existing coverage reports and project-refinement/GridSpine tests remain the handoff for those gaps. There is no new universal arbitrary-code executor or promise that an unsupported application operation exists.
- Per-tool/first-token performance instrumentation and broader UI→API→tool coverage reporting remain follow-up improvements. This phase reduces model polling, payload size and duplicate writes/calls through existing queues, bounded evidence, metadata references, catalogue caching and durable checkpoints.

## Follow-up: live expected-result checks (2026-10-10)

Added `tests/test_assistant_recovery_live.py`: opt-in conversations using the production prompt, catalogue/selection, ordinary confirmations, streamed arguments, matched result IDs and real handlers. Successful cases have source-keyed caches and share the existing cumulative accounting ledger. No forced tool choices or execution doubles are used.

| Scenario | Evidence and outcome |
| --- | --- |
| Resume delivery in a new chat | **Passed**: discover task with `list_tasks`, resume with resolved references, complete all three steps without repeating the prior NC export. Independently verified both ZIP manifest hashes, PNG signature, exported network inputs/horizon, authenticated download and unchanged baseline bytes. 10 completions / 128324 tokens. |
| Concurrent edit makes preview stale | **Passed**: independent edit to cost 15; old cost-30 preview refused exactly once with `preview_stale`; fresh 15→30 preview applied; one HiGHS solve returned objective **300** and dispatch **5/5 MW**. Original preview remained unapplied and saved baseline bytes unchanged. 8 completions / 174375 tokens. |
| Excel export → inspect → import → new project → solve → save → evidence → NC export | **Scientific artifacts verified; corrected live assertion rerun pending**. The original model conversation completed with no tool errors and correctly reported objective **120**, **2 snapshots** and a download link. Its test failed because it disallowed two useful extra reads (`get_simulation_status`, `get_file_delivery`). A focused-toolset rerun executed the actions but reached its 14-completion test limit before its final reply. Independent postmortem checks on BOTH runs verified workbook dynamic demand **5/7 MW**, saved/exported dispatch **5/7 MW**, saved/exported objective **120**, file hashes and original baseline inputs. These postmortem checks do not certify an authenticated HTTP download for this case, and neither pytest run is marked passed. |
| Dynamic native formats, offline | **3 passed** for NC, CSV ZIP and Excel. Unequal objective weights **2/3** and demand **5/7 MW** produce **310 = 10 × (5×2 + 7×3)**, preserved in saved network and simulation evidence. Original baseline bytes unchanged. |
| Final local recovery suite | **40 passed, 3 paid tests skipped**; new live module passes Ruff and changes pass `git diff --check`. |
| Successful live cache replay | **2 cached skips, zero additional API calls**. |

The paid test oracle now permits bounded status/metadata reads while retaining the exact required action order and counts, so duplicate imports/solves/saves remain failures. Its per-case completion allowance is 18; the global reserve-before-send token/dollar gates remain authoritative. The final corrected Excel test has not been rerun live because insufficient authorized tokens remain. No confirmed production bug was found in this follow-up; fixes were to the acceptance oracle and its request allowance.

This follow-up used **43 completions**, **759097 charged tokens**, **601609 reported cached input tokens**, and **$0.19048375** conservative upper cost, including both Excel attempts. Latest cumulative ledger: **685 requests**, **4988990 charged tokens**, **3927279 reported cached input tokens**, **$1.3018635** conservative upper cost. Only **11010 tokens** remain under the **5000000-token/$5** authorization. Do not discard/reset the ledger or spend up to the dollar cap after reaching the token ceiling. A complete Excel rerun requires an additional authorized token allowance, even though the conservative dollar estimate remains below $5.

Commands: backend `pytest -o addopts='' -q tests/test_assistant_capabilities.py tests/test_assistant_task_outcomes.py tests/test_assistant_recovery_live.py`; paid cases require `OPENAI_API_KEY` and `PYPSA_GUI_TEST_LIVE_COMPREHENSIVE=1` and load `/tmp/openai-comprehensive.json`. Use `/workspace/chat-compat-venv/bin/python` and `MPLCONFIGDIR=/tmp/chat-mpl`. Logs: `/tmp/assistant-recovery-live.log`, `/tmp/assistant-preview-live.log`, `/tmp/assistant-excel-live.log`, `/tmp/assistant-excel-live-recheck.log`, `/tmp/assistant-artifact-postmortem.log`, `/tmp/assistant-recovery-offline-final.log`, `/tmp/assistant-recovery-cache.log`. Failed ledger records retain their original status and separately record the postmortem evidence/failure scope.

Frontend and provider production source did not change in this follow-up; the earlier full frontend/build/provider regression results remain the applicable evidence. Live Anthropic/Responses/invalid-key proxy blockers remain as recorded above. The previous failed environment was untouched.

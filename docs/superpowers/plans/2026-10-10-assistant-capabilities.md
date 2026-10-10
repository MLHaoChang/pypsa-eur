# Assistant capabilities and reliable task execution

The assistant should discover and use every supported application operation through the same provider-neutral catalogue and handlers. This phase extends PR #104; existing providers, solve queues, confirmations, locks and project permissions remain authoritative.

## Implementation sequence

1. **Discovery and durable tasks.** Add goal-based catalogue search and persisted, project/user-scoped task plans. Checkpoint ordinary dispatches rather than recursively executing hidden tools. Resume supplies the next exact call and resolves references to earlier results. Interrupted writes require explicit reconciliation; cancellation stops future steps and does not abort queued jobs. Validate catalogue names, signatures, references and bounds.
2. **Files and imports.** Standardize authenticated download metadata, content hashes and project provenance. Inspect tabular uploads without sending file contents to the model. Import uploaded native network files by ID using the existing import handlers and confirmation/rebinding rules.
3. **Changes and delivery.** Preview bounded typed engineering changes on an isolated network, report old/new values, units and validation, and persist the exact preview fingerprint. Apply only to the unchanged project through existing lock/study/undo gates. Generate charts from server-side numeric series and zip selected artifacts with a provenance manifest.
4. **Chat experience and validation.** Show task state, next steps and downloads in tool-result cards using existing stream events. Cache discovery metadata and reuse stored artifacts. Verify real handler execution, restart/recovery, denied confirmations, isolation, stale previews, native import/export round trips, chart/delivery contents, provider transports, frontend rendering and production build. Run a small paid API check only after offline checks pass and within the existing token/dollar ceilings.

## Contracts and boundaries

- A task never grants additional tools or permission. Every actual action is a normal offered tool call and counts against the existing turn budget. A task plan is bound to its saved project; create/activate a project before starting it. Project-rebinding operations run outside the task.
- Task IDs are opaque; access is rechecked against the project registry and task owner. An interrupted write is uncertain, never silently repeated. Explicit reconciliation receives normal destructive confirmation.
- Typed change previews reuse the sensitivity engineering input allowlist. Topology creation/deletion continues through existing CRUD/batch tools; broad arbitrary mutation or code execution is outside this change.
- Binary artifacts stay on the server. Download links require browser authentication. Inspectors report missing data and inferred time axes; they never assume units or silently remap columns.
- Existing GridSpine evidence, queues and validation are reused. No second solver or alternate orchestration service is introduced.

## Acceptance evidence

Record test commands/results, live usage and remaining fixture/credential gaps in a companion assessment. Update the existing PR against master after checks. Do not alter the previous failed environment or claim that untested external engines have passed.

## Design gate review (2026-10-10)

Reviewed against the harness README and the current dispatch, upload, solve-queue and provider-selection contracts. The spec passes the design gate with the following required safeguards; implementation is not yet approved by this review.

1. **No recursive hidden execution.** `resume_task` returns a next-step instruction, not a nested dispatcher invocation. Actual calls must pass the exact offered-name allowlist, turn count, argument preflight, confirmation, project-switch, lock, study and undo gates in `harness/loop.py` and `services/chat_tools.py`.
2. **Record effects before reporting success.** Reserve a matching step after confirmation and before executing its handler. Persist its outcome before emitting the successful result. A disconnect, timeout or exception after a write may have effects; do not infer that the write was rolled back. Denied confirmations never reserve a step. Solver and queue completion are different from submission; a wait timing out cannot complete an evidence-dependent task.
3. **Recovery across sessions.** Store JSON under the authorized project directory, with task/project/user identifiers and atomic replace. Recheck ownership and project access for every operation. Do not reconcile/retry a step while its originating session is still executing. An orphaned running step becomes uncertain. `resolve_task_step` must receive normal destructive confirmation and explicitly record whether the prior effect completed or may be retried.
4. **Typed previews are immutable plans.** Bind preview IDs to the project and user; retain typed changes, the baseline input fingerprint and previewed validation. Recheck the fingerprint and authorization before applying. One project lock covers the final compare-and-apply operation. Apply to a private candidate and commit only after all changes validate. Preserve unsaved work and rely on the existing undo snapshot for user recovery.
5. **Files are authenticated references.** Never accept a server filesystem path from model arguments. Resolve upload IDs using the authorized project directory. Bound row inspection, input bytes, chart dimensions and bundle size. Sanitize archive member names; avoid collisions and traversal. Delivery manifests include content hashes, project ID, and input/result freshness where relevant. Links require the normal browser session.
6. **Discovery is advisory.** Cached catalogue descriptions may explain prerequisites and schema, but do not confer access. Use the existing eligibility and toolset selection at the next model request. Control tools must remain visible in reduced toolsets. Discovery cannot claim that a tool has passed an end-to-end test without recorded evidence.

## Implementation handoff

At this checkpoint the only reviewed artifact is this spec. Work-in-progress code on `feat/chat-workflow-tools` is not merged by publishing the spec.

- Existing tested foundation: PR #104 (https://github.com/MLHaoChang/pypsa-eur/pull/104), commit `6786536f`, five workflow helpers, real solve queue, native/fake-stream provider parity, evidence cache, scoped toolsets and project-refinement skill. See `2026-10-09-chat-workflow-tools.md` and the matching assessment for prior acceptance evidence.
- Newly started implementation: `services/assistant_tasks.py` draft; catalogue/dispatch registration, lifecycle hooks and tests still required. Do not assume this draft meets the gates above. No new production tests have passed at the time of this checkpoint.
- Intended new service modules: `assistant_tasks.py` (durable checkpoints), `assistant_tools.py` (discovery, import inspection, artifacts, typed changes). Catalogue entries belong in `harness/catalogue.py`; handler registration and mutator gate sets belong in `services/chat_tools.py`. Task lifecycle hooks belong in the existing real dispatch seam. Provider SDKs must not enter either service module.
- Intended tool schemas: `find_capabilities(goal, limit)`; `start_task(title, steps, request_id)` with ordered `{id, tool, args}` steps and whole-value `$step_id.path` references into saved results (for example `$solve.job_id`); `get_task(task_id)`, `resume_task(task_id)`, `cancel_task(task_id)`, `resolve_task_step(task_id, outcome, result)`; `get_file_delivery(file_id)`, `inspect_import(file_id)`, `import_uploaded_network(file_id, format)`; `preview_project_changes(changes)`, `apply_project_changes(preview_id)`; `create_chart(component, attribute, names, source)`, `build_delivery(file_ids, filename)`.
- Task bounds: 1–12 steps; 16000 characters of plan arguments; unique short step IDs; backward references only; stable owner/project-scoped `request_id` deduplication; bounded stored results. Exclude task-control and project-rebinding tools from task steps; create/activate/import before starting the task. Exact next-step arguments are returned to the model. Completed steps are not re-executed by resume.
- File formats: use existing native NC, CSV bundle, Excel and MATPOWER import/export handlers; no custom parser replacing those handlers. Inspection is bounded CSV/Excel metadata, missing-value counts and time-axis diagnostics. Unsupported inspection formats should return supported import options, not guess content or units.
- Chart inputs: named numeric columns from existing server-side static tables or dynamic input/output series; explicit source; no arbitrary code/expression. Record row/snapshot coverage and input fingerprint. Delivery bundles contain only authorized upload IDs plus a JSON manifest.
- Frontend: preserve the closed SSE vocabulary. Render structured task/download metadata from existing `tool_result` payloads. Task resume/cancel controls send ordinary user requests and retain confirmation behavior; do not invoke a private bypass API.

## Required implementation acceptance gates

| Gate | Required evidence |
| --- | --- |
| Registry and portability | Catalogue, dispatcher and route-map parity; safety-tier/schema checks; layout contract; provider transports and agent-session isolation |
| Durable tasks | Real ordinary-dispatch workflow; request dedup/conflicts; references; disk recovery/new chat session; interrupted write review; concurrent resume refusal; no replay; denied confirmation leaves pending; cancel leaves jobs alone; access isolation |
| Change application | Real isolated preview, units/diff, no baseline mutation; changed fingerprint refusal; invalid/unsupported type refusal; atomic apply; study/foreign-lock refusal; undo recovery |
| Imports and artifacts | Bounded CSV/Excel inspection; missing/time-axis diagnostics; native export/import round trip with binding frames; content hashes and authenticated links; chart PNG; bounded zip manifest/traversal/collision cases; stale evidence refusal |
| Chat experience | Task and artifact cards, error/uncertain states, safe links, resume/cancel requests; existing chat/confirmation/history/provider-switch regressions; TypeScript and production build |
| Live model | Minimal real tool sequence after offline gates; existing ledger and cached success policy; no request made without confirmed pricing/credentials/budget |

## Execution environment and budget handoff

Use `/workspace/chat-compat-venv/bin/python` for backend tests and `MPLCONFIGDIR=/tmp/chat-mpl`. Backend commands use `pytest -o addopts=''`; frontend uses its existing Vitest and build scripts. Do not edit production source while a pytest run is active because source-inspection tests use imported function offsets.

The previous failed environment is excluded. Credentials remain managed secrets; do not paste or log them. Prior live-test ledger `/tmp/openai-comprehensive.json` records 632 requests, 3,990,215 charged tokens and a conservative upper cost of $1.05118925. Current authorization is a 5,000,000-token ceiling and a $5 spending cap. This temporary ledger may not survive a new environment: carry the cumulative totals forward before making further calls. Actual live Anthropic coverage is blocked by missing credentials; official Responses-API coverage was blocked by the environment proxy. Mocked native provider streams are not evidence of live provider access.

## Implementation refinements after testing

The feature branch now also has `list_tasks(limit)` so a new chat/provider can discover owned task IDs. Each step's raw arguments are bounded to 1800 characters; references resolving to larger arguments block with guidance to use file IDs. Task views preserve next-step/count metadata while clipping step details to the existing result budget. Optional default arguments compare canonically.

Timeout workers are tracked separately from chat-turn lifetime. Reconciliation refuses an unfinished worker even if its chat turn has already ended. Native export tools are treated as effectful for recovery despite their existing read tier, because they create upload artifacts.

Charts require both aligned dispatch axes and a matching solve-input fingerprint. The common solve service records the canonical input fingerprint under its existing network lock after restoring modelling transforms; it travels with netCDF metadata. Networks solved before this metadata existed must be solved again before using the new result-chart tool. Safe cloning excludes the solver-model edge only on the private copy; the live model is retained.

The chat upload picker and MIME allowlist include native netCDF/HDF network files, CSV ZIP bundles and MATPOWER text. Binary uploads enter the conversation as file metadata and IDs, never inline bytes. Native import verifies the stored content hash and bounds ZIP expansion before invoking the existing importer.

Task outcome gates also recognize terminal failed/aborted jobs, explicit failure payloads and partial submissions. A failed wait cannot complete a result-dependent plan. Partial writes require review. Failed or uncertain steps can receive explicit confirmed reconciliation after independent verification. Interactive `ask_user` calls run outside durable action steps; their answers are user messages, not completed tool results.

Applying a preview shows the immutable server-side old/new/unit diff in the normal confirmation card, without changing the handler's actual `preview_id` arguments. The preceding preview also renders as a table in chat. Reservation occurs before dirty/undo side effects, so refusing a replay does not create a false edit.

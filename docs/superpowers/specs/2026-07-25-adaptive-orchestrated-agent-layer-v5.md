# Implementation Plan — Adaptive Orchestrated Agent Layer (v5 — FINAL, confirmed for hand-off)

**Goal:** Add an LLM **orchestration layer** to an existing tool that has *no LLM capability today*. It accepts a task, decides **how** to handle it, and returns a result. Cloud-first (all Claude, no local hardware), designed so **local + hybrid execution** arrive later as *config changes, not rewrites*.

Written to be handed to **Claude Code**. Work it phase by phase (§17).

> **Revision notes.**
> **v4** replaced the old 3-mode switch with **triage over two orthogonal axes** — *shape* (atomic vs decompose, driven by separability) and *tier* (light vs heavy, driven by difficulty), with *sensitivity* as a fail-closed override — giving the hard-but-indivisible task a first-class home (single heavy agent) and unifying atomic + decompose on one executor. Independent review then moved sensitivity-gated escalation/fallback out of Router config into `resolve.py` (Router fallback is blind to per-task sensitivity — a Mode-2 privacy leak), made `NEEDS_DECOMPOSITION` atomic-entry-only, and pinned decomposition flat (one coordinator level, no recursion).
>
> **v5 — final confirmation pass (granularity coverage).** Verified every task granularity has an executable path and the plan is buildable in one go on cloud only. Two amendments: (1) **waves promoted from "optional" into the core** — the coordinator emits *waves* (parallel within a wave, sequential across waves), making dependent/sequential pipelines first-class instead of paying an Opus evaluator round-trip per stage; (2) **tier escalation on re-issue** — the evaluator may re-issue a failed subtask one tier up (public only): today that self-heals Sonnet→Opus, and in Mode 2 the identical mechanism becomes local→cloud escalation. Added the **granularity coverage matrix** (§1). Confirmed: every role resolves to a Claude deployment, nothing requires local hardware, and the single deliberate limitation is that `sensitivity=private` runs are **refused** until a local tier exists.

---

## 1. The routing model (the conceptual core)

Two decisions, made by **triage**, are orthogonal — difficulty and separability are different properties:

|                          | **Atomic** — 1 agent                      | **Decompose** — coordinator + workers         |
|--------------------------|-------------------------------------------|-----------------------------------------------|
| **Light** (cheap/local)  | simple task → single light agent          | big-but-easy → coordinator + light workers    |
| **Heavy** (strong/cloud) | hard-but-indivisible → single heavy agent | complex task → coordinator + heavy workers    |

- **Triage picks a cell**, not a "mode." A task is routed by `{shape, tier, sensitivity}`.
- **Atomic** → **one** agent at the chosen tier, no orchestration overhead (decomposition actively hurts an indivisible task).
- **Decompose** → the coordinator loop; each subtask carries its own tier (all-heavy, all-light, or **mixed** = the "hybrid" case).
- **Two things wear the name "orchestrator"** — kept separate:
  1. **Triage** — cheap, universal, in front of everything; decides the cell.
  2. **Coordinator** — the plan→delegate→evaluate→synthesize loop; runs only on the *decompose* branch.

Cloud-first tier mapping (today → later): **light** = `claude-sonnet-5` → `qwen3.6:27b`; **heavy** = `claude-opus-4-8` (unchanged). The tier axis *reuses* the model seam — expansion remaps `light` to local.

**Granularity coverage matrix (traceability — every task type has a path):**

| Task type | Triage result | Execution path | Proven by |
|---|---|---|---|
| Simple / easy | atomic + light | one light agent call | `test_atomic_path` |
| Hard but indivisible | atomic + heavy | one heavy agent, no decomposition | `test_atomic_path` |
| Parallel multitask (separable, independent) | decompose | one wave, parallel executors | `test_decompose_loop` |
| **Sequential pipeline (separable, dependent)** | decompose | **multi-wave plan, waves in order, parallel within each** | `test_sequential_waves` |
| Big but easy | decompose + light default | waves of light executors | `test_decompose_loop` |
| Mixed difficulty | decompose, per-subtask tier | light + heavy executors in one run | `test_coordinator` |
| Misjudged atomic (too big) | `NEEDS_DECOMPOSITION` | re-triage once → decompose | `test_executor` |
| Misjudged decompose (trivial) | 1×1 plan | short-circuit to one executor call | `test_coordinator` |
| Private (any shape) | sensitivity override | refused today; pinned local in Mode 2 | `test_resolve` |

---

## 2. Key decisions

| Decision | Choice | Why |
|---|---|---|
| Framework | **LangGraph** | Model-agnostic; explicit graph; checkpointing; conditional edges = triage branch, wave loop, replan loop. |
| Gateway | **LiteLLM Router** (in-process, built from YAML) | One seam between graph and provider; same YAML feeds a proxy later. |
| Triage model | **`claude-haiku-4-5`** | Cheap; runs on (nearly) every task's shape/tier scoring. |
| Coordinator (plan/eval/synth) | **`claude-opus-4-8`** | High-value reasoning, low volume. |
| Heavy agent | **`claude-opus-4-8`** | Atomic-heavy + heavy workers. |
| Light agent | **`claude-sonnet-5`** → Qwen later | Atomic-light + light workers. |
| Language | **Python** (assumed) | LangGraph + LiteLLM are Python-first. TS/Node → §22. |

**Not the Claude Agent SDK**: Claude-only subagents dead-end the local expansion; LiteLLM + raw API bills as normal usage.

---

## 3. Scope & execution boundary

- **v1 agents PRODUCE artifacts; no side effects.** Text/code/analysis/patches as data. Read-only tools only. No shell, writes, or mutation of the host tool.
- **Applying artifacts is the host tool's job**, under its own review.
- **Client-executed tools only** — no provider-side tools (they vanish for local backends, breaking parity).
- Side-effecting execution deferred to §15 (sandbox + approval).

Enforced as a `ToolPolicy` the executor applies.

---

## 4. Architecture — the seams

Nodes hardcode nothing; five seams make expansion real:

- **Model seam (LiteLLM Router):** logical roles resolve to deployments via YAML.
- **Tier→role resolver (`resolve.py`):** `resolve_role(tier, sensitivity) -> logical_role` (e.g. `agent-heavy`; the Router maps role→deployment). Encodes: `private` ⇒ must resolve to a **local** role (fail closed); `public+heavy` ⇒ `agent-heavy`; `public+light` ⇒ `agent-light`. **Escalation and local→cloud fallback also live here, not in Router `fallbacks`** — Router fallback cannot see per-task sensitivity, so the sensitivity-aware decision (public may escalate/fall back to cloud; private never) is made in code.
- **Prompt seam (`prompts.py`):** `get_prompt(role, logical_model)` — base per role + per-backend override.
- **Context seam (`stores.py`):** `ContextStore` holds named slices; tasks carry *references*; the executor injects only referenced slices, capped by `SUBTASK_CONTEXT_BUDGET` (**applies to the atomic path too**). Later-wave subtasks may reference earlier waves' outputs via artifact refs injected as context (budget still applies).
- **Routing seam (`triage.py`):** entry branches on triage's `shape`.

**Design-for-the-weakest-backend rules** (from day one): minimal executor I/O contract; subtask payload ≤ `SUBTASK_CONTEXT_BUDGET` (default 8000, sized for a future ~32K local window); per-deployment timeouts/retries; small subtasks also cut cloud tokens today.

---

## 5. Execution model

```
run_task(goal, context, sensitivity="public", shape=None, tier=None, thread_id=None)
  → TRIAGE (triage.py)                     # unless shape+tier supplied (override) or heuristic fast-path
        emits {shape, tier, sensitivity, confidence, rationale}
        sensitivity check is DETERMINISTIC/LOCAL (never a cloud call)
  ├─ shape == "atomic"
  │     → EXECUTOR once  (resolve_role(tier, sensitivity))          # the unified agent node
  │     → return {final_result = artifact, ...}
  │     (executor may raise NEEDS_DECOMPOSITION → re-triage once → decompose branch)
  └─ shape == "decompose"
        → PLAN:      coordinator (Opus)     # goal → WAVES of subtasks (≤ MAX_WAVES)
        →            parallel WITHIN a wave; waves run IN ORDER (sequential pipeline = multi-wave plan)
        →   (if plan is 1 wave × 1 subtask → short-circuit to a single EXECUTOR call, then return)
        → EXECUTE:   for each wave in order: fan-out → EXECUTOR per subtask at its tier
        →            (semaphore-capped; later waves may read earlier waves' artifacts)
        → EVALUATE:  after the FINAL planned wave: done? → SYNTHESIZE | residual subtasks → one new wave (iter+1)
        →            bounded by MAX_ITERATIONS
        → SYNTHESIZE: synthesizer (Opus)    # merge summaries → final; tolerate gaps
        → return {final_result, artifacts, ...}
```

- **Atomic and decompose share ONE executor** (`nodes/executor.py`). Atomic = `wrap_goal_as_task(goal, tier)` → one executor call. No second code path.
- **Within a wave, subtasks are independent** (reducer-safe parallel writes); **ordering is expressed as waves**. A strict A→B→C pipeline is a 3-wave plan — no evaluator call between planned waves, so sequential work no longer pays a per-stage Opus round-trip. The evaluator runs once after the final planned wave; the replan loop remains the recovery path.
- **Context isolation:** verbose executor output stays in its call; only summaries + artifact refs enter state.

---

## 6. Mis-triage recovery

- **Atomic → too big:** the executor may return `NEEDS_DECOMPOSITION` (structured signal). Entry re-triages **once** onto the decompose branch (`RE_TRIAGE_MAX=1`); decompose→atomic is **not** allowed (no ping-pong).
- **`NEEDS_DECOMPOSITION` *inside* a decompose run** (from a worker) = a **failed subtask handed to the evaluator**, which may split it finer next round. Never a re-triage — re-triage is exclusively an atomic-entry mechanism.
- **Flat decomposition:** exactly **one** coordinator level. Executors never spawn coordinators or sub-graphs; further breakdown always flows through the single evaluator→replan loop. Bounds recursion.
- **Decompose → trivially one part:** a 1×1 plan short-circuits to one executor call.
- **Low triage confidence:** below `TRIAGE_CONFIDENCE_MIN`, default to the safer cell (decompose + heavy; respect a caller `prefer_cheap` flag) and log for review.

---

## 7. State schema + LangGraph concurrency gotcha

Parallel executors write state on one superstep → concurrent writes to a key **fail without a reducer**. Results are appended. Artifacts live in `ArtifactStore`; state carries refs (inline only ≤2KB).

```python
from typing import Annotated, TypedDict, Literal
import operator

class TaskResult(TypedDict):
    task_id: str
    status: Literal["ok", "failed", "needs_decomposition"]
    summary: str                 # short; the only thing later LLM calls see by default
    artifact_refs: list[str]

class GraphState(TypedDict):
    goal: str
    context_refs: list[str]
    sensitivity: Literal["public", "private"]
    shape: Literal["atomic", "decompose"]
    default_tier: Literal["light", "heavy"]
    triage_meta: dict            # confidence, rationale, overridden?, re_triaged?
    waves: list[list]            # planned waves of subtasks; parallel within a wave, sequential across
    wave_index: int              # wave currently executing
    results: Annotated[list[TaskResult], operator.add]     # REDUCER or crash
    iteration: int               # replan iterations (recovery), distinct from wave_index
    cost_tokens: Annotated[int, operator.add]
    errors: Annotated[list, operator.add]
    final_result: str | None
```

Subtask fields: `id`, `description`, `context_refs`, `acceptance`, `tier`, `sensitivity`. Subtask `sensitivity` inherits the run default and may only **tighten**; `tier` defaults to `default_tier`, coordinator may raise a hard subtask to `heavy`.

---

## 8. Structured output & failure policy

- **Native tool-use** for triage (`emit_triage`), planning (`emit_plan`, emitting waves), evaluation (`emit_verdict`); Pydantic-validated; retry ≤ `SCHEMA_RETRIES`.
- **Executor failure:** retry ≤ `WORKER_MAX_RETRIES` (backoff); final failure → `status:"failed"`, graph never crashes; the evaluator may re-issue — **and may raise the re-issued subtask's tier** (light→heavy, `public` only; heavy is the ceiling). Today that self-heals Sonnet→Opus; in Mode 2 the same line of logic is local→cloud escalation. Synthesizer degrades gracefully and notes gaps.
- Keep the executor output schema small (weakest-backend rule).

---

## 9. Sensitivity — fail closed

- Run-level `sensitivity` (default `public`); may tighten per subtask.
- **Invariant (in `resolve.py`):** `private` must resolve to a **local** role. Today none exists → `run_task` **refuses a private run with a clear error**. Trivial now, load-bearing at Mode 2.
- Sensitivity is **deterministic/local** (caller flag + pattern rules) — never a cloud classifier (that already leaks). Only shape/tier scoring may use the cloud triage model, on non-sensitive content.

---

## 10. Concurrency, limits, cost, timeouts

- **Semaphore** caps simultaneous executors at `MAX_PARALLEL_WORKERS` (default 4), per wave.
- **Per-deployment `rpm`/`tpm`/`timeout`** in YAML (cloud 120s; commented local entry 600s).
- **Per-run token budget** (`PER_RUN_TOKEN_BUDGET`) accumulated in `cost_tokens` — **applies to atomic too**; exceed → abort with partial result.
- **Triage cost control:** explicit override skips triage; heuristic fast-path (very short/simple input → atomic-light with no LLM call, conservative); otherwise Haiku.
- **Telemetry (`telemetry.py`)** per call: `run_id, task_id, role, tier, sensitivity, wave, logical_model, resolved_deployment, tokens_in/out, cost, retries, status, duration` + every **triage decision** (`shape, tier, confidence, overridden, re_triaged`). **This log is the dataset the triage classifier is tuned on later** — a product feature, not debug noise.

---

## 11. Cost estimation & DRY_RUN

- `DRY_RUN=true` (or `run_task(..., dry_run=True)`) returns the **triage decision**, plus: atomic → tier + token/cost estimate; decompose → the **wave plan** with per-subtask tiers + aggregate estimate. **No executor runs.**
- DRY_RUN still incurs the (small) triage + planning calls; it skips only the expensive executor stage. `trace_id == run_id`, so a dry-run and its later real run correlate in telemetry.
- Estimates: token-count prompts + injected context × per-deployment output assumptions × price table in `config.py`. Directional; label it so.

---

## 12. Repository structure

```
agent_orchestrator/
  __init__.py
  config.py            # roles, limits, budgets, price table, ToolPolicy, triage thresholds
  models.py            # loads litellm.config.yaml -> litellm.Router; cost callback
  resolve.py           # resolve_role(tier, sensitivity) -> logical_role; escalation/fallback logic (fail-closed)
  prompts.py           # get_prompt(role, logical_model): base + per-backend overrides
  stores.py            # ContextStore + ArtifactStore
  state.py             # GraphState + reducers (§7)
  triage.py            # local sensitivity + shape/tier; override + heuristic fast-path; emit_triage
  graph.py             # triage branch, atomic path, wave loop, replan loop, semaphore, checkpointer
  nodes/
    __init__.py
    executor.py        # THE unified agent: one task -> summary + artifact refs; ToolPolicy; retries; NEEDS_DECOMPOSITION
    coordinator.py     # goal -> WAVES of independent, budgeted, tiered subtasks (emit_plan); 1x1 short-circuit
    evaluator.py       # done | residual subtasks (one new wave); may raise tier on re-issue (public only)
    synthesizer.py     # merge summaries -> final; tolerate gaps
  tools/
    __init__.py        # client-executed, read-only tool defs (host registers domain tools here)
  interface.py         # run_task(...) -> {final_result, artifacts, trace_id}; optional FastAPI
  telemetry.py         # per-call + per-triage logging (§10)
tests/
  conftest.py               # FakeModel fixture — NO real API in unit tests
  test_state_reducers.py
  test_resolve.py           # private fail-closed; tier mapping; escalation rules
  test_triage.py            # override skips LLM; local sensitivity; conservative low-confidence
  test_executor.py          # success; retry->failed; NEEDS_DECOMPOSITION signal
  test_atomic_path.py       # atomic-light and atomic-heavy return an artifact
  test_coordinator.py       # waves of independent, budgeted, tiered subtasks; 1x1 short-circuit
  test_sequential_waves.py  # multi-wave plan runs in order; parallel within a wave; ONE evaluator pass
  test_decompose_loop.py    # fan-out, reducer merge, replan, tier-raising re-issue, partial failure, budget abort
  test_executor_unified.py  # atomic path and decompose worker call the SAME executor
  test_seam_remap.py        # config-only backend remap; behavior identical (the expansion guarantee)
  test_smoke_live.py        # ONE real call; @pytest.mark.live; off by default
litellm.config.yaml
.env.example
pyproject.toml
PLAN.md
```

---

## 13. Integration with a host tool that has no LLM capability

Greenfield addition — nothing to reconcile. The host integrates in exactly three places:

1. **One call site.** `run_task(goal, context, sensitivity=..., dry_run=False, thread_id=None) -> {final_result, artifacts, trace_id}`. Plain data in, plain data out; the host never sees models, prompts, or the graph.
2. **(Optional) read-only domain tools.** Host registers read-only functions into `tools/` if agents need its data. No write access (§3).
3. **Applying artifacts.** Done by the host through its own logic/UI/review — the layer never mutates host state.

**Deployment shape:** Python host → import **in-process**. Other language / isolation wanted → run as a **sidecar service** (`POST /orchestrate`). Provide both; prefer in-process for Python.

**Secrets:** `ANTHROPIC_API_KEY` lives in the layer's environment, not host code.

---

## 14. Config

**`litellm.config.yaml`** — **`models.py` builds `litellm.Router` from this file** (plain `litellm.completion()` ignores `router_settings`).

```yaml
model_list:
  - model_name: triage-model
    litellm_params: { model: anthropic/claude-haiku-4-5-20251001, api_key: os.environ/ANTHROPIC_API_KEY, rpm: 200, timeout: 30 }
  - model_name: coordinator-model
    litellm_params: { model: anthropic/claude-opus-4-8, api_key: os.environ/ANTHROPIC_API_KEY, rpm: 50, tpm: 200000, timeout: 120 }
  - model_name: agent-heavy
    litellm_params: { model: anthropic/claude-opus-4-8, api_key: os.environ/ANTHROPIC_API_KEY, rpm: 50, tpm: 200000, timeout: 120 }
  - model_name: agent-light
    litellm_params: { model: anthropic/claude-sonnet-5, api_key: os.environ/ANTHROPIC_API_KEY, rpm: 100, tpm: 400000, timeout: 120 }
  # - model_name: agent-light-local        # (LATER) uncomment with hardware; light tier remaps here
  #   litellm_params: { model: ollama/qwen3.6:27b, api_base: http://localhost:11434, timeout: 600 }

router_settings:
  num_retries: 2
  # NOTE: no cross-tier fallbacks here — sensitivity-aware escalation/fallback lives in resolve.py (§4).
```

**`.env.example`:**

```
ANTHROPIC_API_KEY=sk-ant-...
TRIAGE_MODEL=triage-model
COORDINATOR_MODEL=coordinator-model
AGENT_HEAVY=agent-heavy
AGENT_LIGHT=agent-light
DEFAULT_SENSITIVITY=public
MAX_SUBTASKS_PER_WAVE=8
MAX_WAVES=4
MAX_PARALLEL_WORKERS=4
MAX_ITERATIONS=3
RE_TRIAGE_MAX=1
TRIAGE_CONFIDENCE_MIN=0.6
WORKER_MAX_RETRIES=2
SCHEMA_RETRIES=2
SUBTASK_CONTEXT_BUDGET=8000
PER_RUN_TOKEN_BUDGET=400000
DRY_RUN=false
```

---

## 15. Optional hardening (only when the workload demands)

- **Human-in-the-loop:** LangGraph `interrupt` before SYNTHESIZE (mandatory before any future side-effecting step).
- **Side-effecting workers:** only with sandbox + approval; revisit §3.
- **Tracing:** Langfuse/LangSmith via `telemetry.py`.
- **Learned triage:** replace heuristics with a classifier trained on the telemetry log (§10); complexity may use cloud on non-sensitive content only.

---

## 16. Testing strategy (CI stays free)

- Units mock `models.py` (`FakeModel`); zero real API in units, incl. a "sloppy backend" variant (occasional malformed tool-call) to exercise retries.
- `test_executor_unified.py` and `test_seam_remap.py` are the **guarantees**: one executor for both shapes; behavior invariant under a config-only backend swap.
- `test_sequential_waves.py` proves the sequential-pipeline granularity explicitly.
- `test_smoke_live.py`: one tiny real call, `@pytest.mark.live`, excluded by default.

---

## 17. Implementation phases (stop for review; commit each)

1. **Skeleton + model + resolve layer.** Repo (§12), env, YAML; `models.py` → `litellm.Router` + cost callback; `resolve.py` (fail-closed + escalation rules); `prompts.py` base templates. *Live smoke:* one round-trip via a logical role.
2. **Stores + state.** `stores.py`, `state.py` + reducers. *Tests:* reducer merge; store round-trip; `test_resolve`.
3. **Executor (the unified agent).** One task → summary + artifact refs; injects only referenced context (≤ budget); `ToolPolicy`; retries; `NEEDS_DECOMPOSITION`. *Tests:* success; retry→failed; decomposition signal.
4. **Triage.** Local sensitivity + shape/tier; override + heuristic fast-path; `emit_triage`; confidence handling. *Tests:* override skips LLM; private detected locally; low-confidence → safe default.
5. **Atomic path.** triage=atomic → `wrap_goal_as_task` → **one executor call** at resolved tier; budget/timeout apply. *Tests:* atomic-light + atomic-heavy return an artifact; `test_executor_unified`.
6. **Coordinator (decompose planner).** Goal → **waves** of independent, budgeted, tiered, tagged subtasks (`emit_plan`; ≤ `MAX_WAVES`, ≤ `MAX_SUBTASKS_PER_WAVE` each); 1×1 short-circuit. *Tests:* well-formed waves; within-wave independence; budget enforced.
7. **Wave execution + replan loop.** Waves run in order; within a wave, fan-out reuses the executor per subtask at its tier (semaphore); evaluator after the final planned wave; residual → one new wave; `MAX_ITERATIONS`; tier-raising re-issue (§8); checkpointer + `thread_id`. *Tests (FakeModel):* a 3-wave sequential plan executes in order with ONE evaluator pass; multi-round terminates; ceiling respected; partial failure tolerated.
8. **Synthesizer.** Merge → final; tolerate gaps. *Test:* coherent output with one failed subtask.
9. **Interface + host integration + DRY_RUN.** `run_task(...)`; private refusal; DRY_RUN returns triage + wave plan + estimate; optional FastAPI; wire the host call site; register read-only domain tools. *Tests:* end-to-end via a stub host; dry-run; private refusal.
10. **Guardrails + telemetry + mis-triage + seam test.** Budgets/timeouts/concurrency; §10 telemetry (incl. triage decisions + wave index); bounded re-triage; `test_seam_remap`. ✅ acceptance (§21).

---

## 18. Resumability

Checkpointer + stable `thread_id`: interrupted runs resume from the last completed superstep (wave-granular). Expose `thread_id` on `run_task`; document resume-by-reinvoking.

---

## 19. How to run with Claude Code

1. `PLAN.md` in repo root.
2. **Plan mode first:** *"Read PLAN.md. Propose the file tree and Phase 1 only."* Compare to §12.
3. **One phase per commit:** *"Implement Phase N, run its tests, stop."* Highest-risk: Phase 2 (reducers), Phase 3/5 (executor unification), Phase 7 (wave loop + replan).
4. **Verify current APIs** (LangGraph, LiteLLM, Anthropic SDK) before each phase; snippets are illustrative.
5. Guardrails in early — an unbounded Opus agent (atomic *or* coordinator) is the fastest way to burn budget.

---

## 20. Security

- Keys via env only; never in code/logs.
- Treat goal, context, and all tool/fetched content as **untrusted** (prompt injection): executors read-only, tool output never becomes privileged instructions, no secrets in prompts.
- Sensitivity classification never leaves the machine (§9).
- Pin exact model IDs; expect rotation.

---

## 21. Acceptance (whole system)

Across the grid on real tasks: a **simple** task runs as one light agent; a **hard-but-indivisible** task runs as one heavy agent (no decomposition); a **parallel multitask** decomposes into one wave run concurrently (≤ `MAX_PARALLEL_WORKERS`); a **sequential pipeline** executes as a multi-wave plan in order, parallel within each wave, with exactly **one** evaluator pass; only summaries + refs enter state; one injected failure is retried, and a tier-raising re-issue is exercised; the evaluator triggers ≤1 replan; the synthesizer returns a coherent deliverable; an atomic task signaling `NEEDS_DECOMPOSITION` re-triages exactly once; a **private** run is refused while no local tier exists; tokens stay under budget with per-call + per-triage cost logged; a killed run resumes from checkpoint; **`test_executor_unified.py`, `test_sequential_waves.py`, and `test_seam_remap.py` pass**; and all unit tests pass with zero real API calls.

---

## 22. Assumptions & what would sharpen this

- Python; the host can call a function or HTTP endpoint; v1 agents produce artifacts only; the host has no prior LLM code.
- TS/Node host → LangGraph.js + LiteLLM proxy over HTTP; every seam unchanged.
- To make it turnkey: **what the host tool is and what tasks the agents run** — then triage rules, prompts, and `tools/` become concrete instead of generic.

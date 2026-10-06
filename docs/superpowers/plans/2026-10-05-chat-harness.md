# Plan: the chat harness — one folder for everything a model-agnostic assistant needs

**Status:** v1.2, 2026-10-05. Phases 0, 1 and 2 landed on `claude/amazing-mendel-m087zw`; owner decisions Q1–Q14 taken (spec §8). The issue-06 follow-up and the parity probe (issue 09) are in. Issue 08 steps 1–4 are in (`harness/loop.py` with the alias; `sse`, `fence`, `results`, `history`, `metrics`, `ratelimit`, `session`, `confirm`, `providers/wiring`, `stub`, `budget` extracted under the patch-surface rule, moved tunables forwarded live); prompt assembly and the turn body remain. PR: MLHaoChang/pypsa-eur#86. Still owed: the live OpenAI run.
**Spec (contract-level):** [`.scratch/harness/spec.md`](../../../.scratch/harness/spec.md); issues under `.scratch/harness/issues/`.
**Requested:** 2026-10-05. The assistant should connect to Claude, OpenAI/Codex, Kimi or any other model and not feel different: one harness of functions, workflows and skills that any model drives the same way; a chat opens with a menu of what the user can do; the assistant can run a grill-style interview with recommendations the user picks from; and all of it grouped in one folder instead of spread over files.
**Builds on:** [`specs/2026-08-05-llm-provider-seam-design.md`](../specs/2026-08-05-llm-provider-seam-design.md) (the harness/provider split and the word "harness"), [`plans/2026-09-09-chat-turn-loop-decomposition.md`](2026-09-09-chat-turn-loop-decomposition.md) (how the loop is cut), [`specs/2026-09-27-guided-mode.md`](../specs/2026-09-27-guided-mode.md) (the Guided addendum and delegation texts), [`plans/2026-10-05-one-investment-engine-two-faces.md`](2026-10-05-one-investment-engine-two-faces.md) §5 U3 and §8 (the investment workflow's steps and tools).

## 1. What exists, measured

| Concern | Where it lives today | Provider-neutral? | Travels when the model changes? |
|---|---|---:|---:|
| Provider seam (`LLMProvider`, `LLMRequest`, `LLMEvent`) | `services/llm_provider.py` (90 lines) | yes | yes |
| Adapters | `services/llm_anthropic.py`, `llm_openai_compat.py`, `llm_fake.py` | n/a | n/a |
| Profiles (which model, which key, tools/vision flags) | `services/llm_config.py` | yes | yes |
| Tool catalogue: 182 tools, tiers as `Safety:` text, route map | `services/chat_tools_schema.py` (3,043 lines) | yes | yes |
| Tool handlers | `services/chat_tools.py` (5,939 lines; `DISPATCHERS` dict) | yes | yes |
| System prompt: 11 constants, FACTS/CHAINING split, hash-pinned | `services/chat_service.py` :2065–2833 | yes | yes |
| Loop, session, confirmation, budgets, WAL, lineage | `services/chat_service.py` (5,345 lines) | yes | yes |
| Guided addendum + the five step names | `chat_service.py` :2435–2461 | yes | yes |
| Guided "Let the assistant do this" texts | `frontend/src/pages/hubDesign/delegate.ts` | yes | yes, but it is frontend code |
| Chat-start "Try asking" chips | `frontend/src/components/ChatPanel.tsx` :1303–1349 (three arrays) | yes | yes, but it is frontend code |
| Structured question to the user | does not exist | — | — |
| Skills for the in-app assistant | do not exist | — | — |
| SSE frame vocabulary (16 names) | implicit in the `yield` sites of `chat_service.py` | yes | yes |

The seam is already cut, so the provider swap works today. What does not exist
is the place where behaviour that must travel is *put*: a workflow is a
frontend edit, a question is prose, a procedure is nothing. The folder is
the deliverable; the moves make it honest.

## 2. Target tree

```
pypsa-gui/backend/harness/
  README.md            the contract: what is here, what may import what, how to add a tool / workflow / skill
  __init__.py
  protocol.py          ← services/llm_provider.py (shim stays)          [phase 0]
  catalogue.py         ← services/chat_tools_schema.py (shim stays)     [phase 0]
  events.py            closed vocabulary of turn-loop frames + tripwire  [phase 0]
  workflows/           registry.py + *.md (front matter + "## Step:" sections) [phase 0 data, phase 2 behaviour]
  skills/              registry.py + <name>/SKILL.md (Agent Skills layout)   [phase 0 data, phase 2 behaviour]
  prompts/             *.md, loader reassembles the pinned bytes           [phase 1]
  providers/           anthropic.py, openai_compat.py, fake.py             [phase 1]
  session.py confirm.py budget.py history.py loop.py sse.py               [phase 3]
  mcp.py               external agents (decision Q8)                       [phase 4]
```

Dependency arrow: `services/* ← harness ← harness/providers`. A test greps
`harness/` (excluding `providers/`) for `anthropic`, `openai`, `cache_control`.

## 3. Phases

| Phase | Issues | Delivers | Gate |
|---|---|---|---|
| **0 — the folder** (done) | 01, 12 | package, README, protocol + catalogue moved with shims, `events.py` with the AST tripwire, workflow and skill registries with six workflow definitions and the `grill` skill, loader tests, glossary terms | identity tests; the chat/llm/tool test files unchanged and green; layering grep clean |
| **1 — the text** (done) | 02, 07, 10 | prompts in Markdown (byte-identical, hash-pinned), adapters under `harness/providers/`, bundle `datas` | pinned hashes unchanged; `test_llm_provider_seam.py` unchanged; both live probes run (ADR-0002) |
| **2 — the behaviour** (done) | 03, 04, 05, 06 | start menu endpoint + chips from it; `ask_user` + Choice card; `use_skill` + skill block; `start_workflow` + per-turn addendum; Guided mode consumes the `hub-design` workflow | Guided tests unchanged; Expert turns byte-identical; `FakeProvider` frame sequences for each new tool; vitest + tsc clean |
| **3 — the loop** (09 done) | 08, 09 | session, confirmation, budget, history, loop, sse moved under the harness; parity probe on stub + two live wires | the recorded frame sequence unchanged; manifest test scans the new paths; runbook names the runs |
| **4 — the outside** | 11 | MCP exposure (if Q8 is yes) | its own spec |

Phases 1 and 2 are independent of each other after phase 0; run them on
separate branches. Phase 3 waits for both.

## 4. Rules

1. **Behaviour-preserving moves.** A move is `git mv` + shim + identity test. Defects found go to `docs/superpowers/findings/`, not into the move.
2. **The system prompt is byte-identical** through every phase: the pinned hashes in `test_chat_profile_binding.py` are the gate, and new blocks (skill catalogue) are appended, never inserted into a pinned constant.
3. **Per-turn addenda, never the system prompt**, for anything that changes with the user's place (workflow step, Guided rules, ui_context). The cache argument in `_format_ui_context`'s docstring applies.
4. **Confirmation tiers are untouched.** A workflow step may say "a confirmation card follows"; it cannot change which tier gets a card.
5. **ADR-0002.** Any phase that touches the loop or adds a tool runs both live probes and names them in its report.
6. **Markdown is loaded from the package only** (spec D12).
7. **Model tiering.** Implementation of each issue: Opus-class or lower, one issue per agent, TDD. Plan and spec changes and gate verdicts: Fable.

## 5. Grill rounds 1–2 (owner decisions, 2026-10-05)

Fourteen questions, recorded in spec §8. The owner chose the recommended
answer on all but Q11: `hub-design` is offered in **both** modes. Q13 settles
what that means: the steps travel with the workflow; the plain-language rules
and the write-tier confirmation stay bound to Guided mode itself. Q14 sets the
phase-1 gate: live Anthropic probe plus the OpenAI stub, with the live OpenAI
run recorded as owed. Q12: this session carries phases 1 and 2.

## 6. Phase 0 record (2026-10-05)

See `harness/README.md` for the layout and the commit message for the test
evidence. The hourly-assumption audit (`tests/test_hourly_assumption_audit.py`)
now scans `harness/` too and pins the moved catalogue at its new path.

## 7. Phases 1 and 2 record (2026-10-05)

- Issue 02: eleven prompt fragments in `harness/prompts/*.md`; 27 sha256 pins unchanged; chat_service.py −370 lines.
- Issue 07: adapters under `harness/providers/` with alias shims. ADR-0002: live Anthropic probe PASSED on 45145a6; OpenAI wire on the stub; live OpenAI run owed (Q14).
- Issue 10: bundle datas + three `check_bundle` probes.
- Issue 03: `GET /api/chat/workflows`; chips from it, send on click (Q10). Full vitest 251 files / 2846 passed.
- Issue 04: `ask_user` → `choice_request` → ChoiceCard, non-blocking (Q4).
- Issue 05: `use_skill` + `_skills_block` (tools-on only, a new prompt part; the P25 snapshot test stubs it like the profile block).
- Issue 06: `ChatSession.workflow`, `start_workflow` / `advance_workflow` / `end_workflow`, `_workflow_addendum`; `_guided_mode_addendum` and `_GUIDED_STEPS` are derived from the `hub-design` workflow (Q5, Q13); Expert turns without a workflow byte-identical; Guided tests unchanged.
- Issue 06 follow-up: `workflow_state` frame after each workflow tool, `/chat/history.workflow`, the panel's workflow strip with Leave, `ui_context.workflow` while active.
- Issue 09: `run_chat_smoke.py --workflow` parity battery; runbook `runbooks/harness-parity-probe.md`. Stub 3/3, live Anthropic 3/3. Its first run caught the workflow tools' session binding being lost under `iterate_in_threadpool`; fixed at the dispatch site, red-first test added.
- Issue 08 (step 1): `services/chat_service.py` → `harness/loop.py` whole, alias at the old path, 27 patched names untouched; `harness/sse.py` extracted by AST; the loop's 12 provider-word code sites pinned.
  Gate after the move: every test_chat*/test_llm*/test_tool_*/test_guided*/test_harness*/test_report* file — 2908 passed, 5 skipped, 0 failed (773f009).
- Issue 08 (step 2): `fence`, `results`, `history`, `metrics` extracted; three tunables moved with their readers and seven test patch sites repointed; `MOVED_TUNABLES` tripwire.
  Gate after step 2: 2910 passed, 5 skipped, 0 failed (5439066).
- Issue 08 (step 3): `ratelimit`, `session`, `confirm` extracted; six tunables moved; the loop forwards moved tunables (PEP 562) and reads the one it still needs by attribute; 36 test sites repointed; tripwires for forwarding and bare reads.
  Gate after step 3: 2912 passed, 5 skipped, 0 failed (5180c4c).
- Issue 08 (step 4): provider wiring → `providers/wiring.py`, stub loop → `stub.py`; `_build_anthropic_client` forwarded; vendor-word pin lowered; three test sites repointed.
- Merge with master (PRs #74, #81, #82): alias-file changes ported onto the harness homes; both sides kept elsewhere; gates on the merged tree 3408 backend / 3251 frontend passed.
- Issue 08 (step 5): budget and retry tunables → `budget.py`; the loop reads them by attribute; 33 test sites repointed.

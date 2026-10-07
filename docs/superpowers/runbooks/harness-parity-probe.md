# Running the chat-harness parity probe

The harness's promise is that the assistant behaves the same on every
provider: the same start menu, the same workflow steps, the same Choice
card. This probe measures it (chat harness issue 09): one battery, driven
through `POST /api/chat/stream` against a running backend, on whichever
profile `--profile` names. It complements the ADR-0002 wire probes rather
than replacing them: those prove a provider works, this proves the harness
reads the same on each.

## The battery (`--workflow`)

0. `GET /api/chat/workflows` for `unbound`, `expert`, `guided` lists
   `open-project`, `build-network`, `hub-design` respectively.
1. **W1** — the `open-project` workflow's opening request ("… ask me which
   to open"). Expects the `ask_user` tool and a `choice_request` frame
   whose card marks exactly one option recommended.
2. **W2** — "Start the build-network workflow and tell me the first step."
   Expects `start_workflow` and a `workflow_state` frame at
   `build-network` / `orient`.
3. **W3** — "Please end the current workflow." Expects `end_workflow` and
   `workflow_state` with `workflow: null`.
4. **W5** — "Ask me which of the network checks … to run; I may pick
   several." (issue 15). Expects `ask_user` and a `choice_request` with
   `multi_select: true`. It runs before W4.
5. **W4** — "Think it through before you answer: …" (issue 20). No tool and
   no frame is required: whether a live model shows its reasoning depends on
   the profile. The stub always answers it with `reasoning_content`, so on
   the OpenAI wire it proves reasoning reaches the panel as `thinking`.

Every prompt must also report usage on `turn_done`; a wire that does not
fails the battery (the daily cap and the metrics would be blind there). The
summary prints each turn's own usage (the difference of the session totals)
as `in= cached= written= out=`, with the same meaning on every wire (see
`pypsa-gui/CHATBOT.md`, "Usage meter"), and `thinking` when the turn showed
reasoning.

Nothing in it writes a project. Every phrase is scripted by
`backend/smoke/stub_openai_endpoint.py`, so on the stub a red result means
our code; on a live model it also measures whether the model follows the
harness's tool descriptions.

## Running it

From `pypsa-gui/backend`, with a local-mode backend on :8000 (the recipe the
guided-mode gate uses: `PYPSAGUI_LOCAL_MODE=1`, `PYPSAGUI_APP_DATA_DIR` and
`PYPSAGUI_PROJECTS_ROOT` under a scratch directory, `ANTHROPIC_API_KEY`
set because the smoke's preflight checks it):

```bash
python -m uvicorn main:app --port 8000 &
python smoke/stub_openai_endpoint.py &                       # :11999
curl -X PUT localhost:8000/api/chat/settings/llm/profiles/stub-openai \
  -H 'content-type: application/json' \
  -d '{"label":"Stub OpenAI","preset":"custom","wire":"openai",
       "base_url":"http://127.0.0.1:11999/v1","model":"stub-model",
       "tools":true,"vision":false,"auth":"none",
       "fallback_model":null,"max_output_tokens":null}'
curl -X POST localhost:8000/api/chat/settings/llm/active \
  -H 'content-type: application/json' -d '{"profile_id":"stub-openai"}'
python smoke/run_chat_smoke.py --workflow --profile stub-openai
python smoke/run_chat_smoke.py --workflow --profile anthropic-sonnet     # live
```

Exit code 0 means every prompt passed and the cross-prompt checks on the
frame payloads held.

## Runs

| Date | Commit | Profile | Result |
|---|---|---|---|
| 2026-10-05 | bc1f7f5 + the dispatch-site fix below | `stub-openai` (OpenAI wire, scripted) | 1/3 — `start_workflow` and `end_workflow` answered `internal_error` |
| 2026-10-05 | same | `anthropic-sonnet` (live) | 1/3 — the same two failures |
| 2026-10-05 | the fix | `stub-openai` | **3/3** |
| 2026-10-05 | the fix | `anthropic-sonnet` (live, `claude-sonnet-5`) | **3/3**; after `start_workflow` the model followed the `orient` step on its own: `get_meta`, `list_components` ×5, then an `ask_user` card |
| 2026-10-06 | 9d856fa (issue 08 complete: `budget`, `compose`, `solver_bridge` extracted; master #78/#79/#85 merged) | `stub-openai` | **3/3** |
| 2026-10-06 | same | `anthropic-sonnet` (live) | **3/3** in 19 s; W1 two tools, W2 three (the model started the workflow and took its first step) |
| 2026-10-06 | issue 20 (W4 and usage added) | `stub-openai` | **4/4**; per turn `in=6 cached=16 written=0 out=6` on W1–W3 (two model calls each), W4 `in=3 cached=8` with `thinking` |
| 2026-10-06 | same | `anthropic-sonnet` (live) | **4/4** in 31 s; W1 `in=1359 cached=95062 written=47531`, W4 `in=34 cached=50612`; thinking shown on W1–W3, not on W4 |
| 2026-10-07 | issue 15 (W5 added) | `stub-openai` | **5/5** |
| 2026-10-07 | same | `anthropic-sonnet` (live) | **5/5** in 26 s; W5: the model called `ask_user` with `multi_select: true` from the tool description alone |

**What the first run found.** The workflow tools read the chat session from
a ContextVar that `run_turn` set at turn start. The route drives `run_turn`
through Starlette's `iterate_in_threadpool`, which runs every `next()` in a
fresh copy of the task's context, so the binding was gone by the time the
tool ran — on both wires, while every directly-driven test passed. The fix
binds the session at the dispatch site, in the same step as the context
copy the executor receives; `test_workflow_tools_survive_a_per_step_context_copy`
drives the generator the way the route does and is red without it. The
route's own comments document the same trap for `set_acting_user` and the
turn profile, which it binds from the event-loop task for that reason.

## What this does not establish

A green run on the stub says nothing about a real model's behaviour, and a
green run on one live wire says nothing about another. The live
OpenAI-compatible run is still owed (owner decision Q14); when a key or an
endpoint is available, run the same command with that profile and add the
row.

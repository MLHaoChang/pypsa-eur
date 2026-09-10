# pypsa-gui Chatbot Assistant

The chatbot panel embeds an in-app copilot powered by the Anthropic Messages
API. It can answer questions about the open network, drive every backend
tool the GUI itself exposes, and gate destructive / execution actions
behind explicit user confirmation.

## Setup

The assistant is **off** until two prerequisites are met:

1. The `anthropic` Python package is installed (pulled in by
   [backend/requirements.txt](backend/requirements.txt) — runs
   `pip install -r requirements.txt` from a fresh checkout).
2. An `ANTHROPIC_API_KEY` reaches the backend process. There are three ways,
   and they are tried in this order of authority:

   | Source | Set it by | Best for |
   |---|---|---|
   | The launching shell | `export ANTHROPIC_API_KEY=…` before starting uvicorn | CI, one-off runs |
   | `<app-data>/user.env` | **In the app** — see below | The packaged app |
   | `pypsa-gui/backend/.env` | Editing the gitignored file | A developer checkout |

   A value exported in the shell always wins; `user.env` beats `backend/.env`,
   so a key saved from the UI is not silently reverted by a stale `.env` on the
   next restart.

If either prerequisite is missing the panel surfaces
`error_kind='missing_api_key'` or `error_kind='sdk_not_installed'` explaining
the gap. Setting the key requires no restart of the frontend.

### Supplying the key in the packaged app

The distributed `.app` / `.exe` deliberately ships **no** `backend/.env` — that
file carries a real `ANTHROPIC_API_KEY` *and* the `SECRET_KEY` that signs
session cookies, and bundling it would publish both
(`backend/smoke/check_bundle.py` fails the build if it is ever present). So the
packaged app has to be told the key from inside itself:

1. Open the chat panel and send anything. The assistant answers with an
   **API key missing** banner.
2. The banner carries the key field. Paste an Anthropic key and press **Save**.
3. The assistant is usable immediately — no restart. The key is written to
   `user.env` in the app-data directory (`~/Library/Application Support/PyPSA
   Studio/user.env` on macOS, `%LOCALAPPDATA%\PyPSA Studio\user.env` on
   Windows), mode `0600`, and reloaded on every launch.

The same field also appears in a server deployment, but only for
**super-admins**: one `ANTHROPIC_API_KEY` is shared by every organisation on
the instance, so an org admin has no authority over it and is shown who to ask
instead. The desktop app's single seeded user is a super-admin, so this never
gets in the way there.

`user.env` is plaintext, like the `.env` it replaces. It is not the OS
keychain: that would mean bundling `keyring` plus a platform backend for each
of macOS and Windows, and it defends against a threat — another process reading
your files as you — that `backend/.env` already accepts. Only
`ANTHROPIC_API_KEY` is ever read from or written to it; anything else in the
file is ignored, so it cannot be used to set `SECRET_KEY` or repoint the
database.

The `/api/chat/health` endpoint reports `anthropic_api_key_present` without
ever echoing the key value, so you can probe the backend's view safely.
`GET /api/chat/settings/api-key` (super-admin only) additionally reports where
the live key came from and its last four characters — never more.

## Voice input

The composer mic button uses the browser **Web Speech API** (English,
`en-US`) to dictate into the prompt box. It does **not** auto-send — review
the text and press Send as usual.

- Supported primarily in **Chrome / Edge** (Safari best-effort). Unsupported
  browsers show a disabled mic with a tooltip.
- Audio is handled by the browser / OS speech service (Chromium may use a
  cloud speech backend depending on settings). No audio is uploaded to the
  pypsa-gui FastAPI process.
- Toggle the mic to start/stop; **Esc** also stops listening.

## Models

The header dropdown selects the model used for the next turn:

| Model | Identifier | When to use |
|---|---|---|
| Sonnet 4.6 (default) | `claude-sonnet-4-6` | Quick reads, single-tool calls, low-cost iteration. |
| Opus 4.8 | `claude-opus-4-8` | Multi-step model design, complex reasoning, dependent tool chains. |

Switching models takes effect on the *next* turn; an in-flight stream
continues on the previous model.

## Cost meter (M10)

The header shows the running token totals and a derived EUR estimate:

```
12,345 in / 6,789 out · €0.0234
```

The server only ever reports token counts; the EUR figure is derived
client-side from the per-model price constants in
[frontend/src/store/chatStore.ts](frontend/src/store/chatStore.ts). When
Anthropic ships a price update, bump `PRICING_USD_PER_MTOK` and
`PRICING_VERSION`. **No EUR field is ever written to chat.jsonl**, so
re-pricing a historic conversation is a pure render-time computation.

## Cost caps

The server enforces hard ceilings — once a cap is hit the stream emits
`session_done` with `reason='budget_exhausted'` (or, for the per-turn cap,
`tool_call_cap_exceeded`):

| Cap | Default | Constant |
|---|---|---|
| Output tokens / turn | 8,192 | `MAX_OUTPUT_TOKENS_PER_TURN` |
| Tool calls / turn | 25 | `MAX_TOOL_CALLS_PER_TURN` |
| Turns / session | 100 | `MAX_TURNS_PER_SESSION` |
| Output tokens / session | 200,000 | `MAX_OUTPUT_TOKENS_PER_SESSION` |

All four live as module-level constants in
[backend/services/chat_service.py](backend/services/chat_service.py); tune
them per deployment.

## Confirmation flow

| Tool tier | Card? | Typed confirmation? |
|---|---|---|
| `read` | no | no |
| `write` | no | no |
| `destructive` | yes (5 min TTL) | only for the highest-risk tools (see below) |
| `execution` | yes (5 min TTL) | no |
| `execution_long_running` | yes (5 min TTL) | no |

Tools that surface a **typed-confirmation** widget (Phase 4 polish — the
user must type the target name verbatim before Approve unlocks):

- `delete_project` — type the project name
- `save_project` / `save_project_as` — type the target name (force-overwrite UX, v4-MAJOR-1 / v6-F1)
- `restore_project_snapshot` — type the snapshot id
- `cascade_delete_bus` — type the bus name

If the TTL elapses without a decision the stream emits a `tool_error` with
`error_kind='confirmation_expired'` — the agent re-prompts with a fresh
token. Approving an expired token via the REST endpoint returns 409
`error_kind='confirmation_expired'`.

## Error flows

The error banner above the message list recognises:

- `project_exists` — Save-As / save-with-force into a name that already
  exists. The card shows the typed-confirmation widget; type the existing
  project name to acknowledge the overwrite (v4-MAJOR-1 / v6-F1).
- `descendants_exist` — `delete_project` on a parent with scenarios. The
  agent prompts you for `cascade=true` before retrying (v4-MINOR-1).
- `confirmation_expired` — TTL elapsed before Approve / Deny.
- `rate_limited` — Anthropic 429 or `RateLimitError`. The agent backs off.
- `unauthorized` — Anthropic rejected the API key. Update and retry.
- `missing_api_key` — `ANTHROPIC_API_KEY` not set in the backend env. The
  banner carries the key field itself; see **Supplying the key in the packaged
  app** above.
- `inactive_acting_user` — the signed-in account stopped being active partway
  through a turn (disabled by an administrator, say). The stream's tools refuse
  from that point on; sign in again.

The `cold_path` activate is **not** an error — the banner self-dismisses
to keep the conversation clean (v6-F2).

## Multi-tab safety

`chat.jsonl` is mutexed per-project via `ProjectContext.chat_state.lock`,
so two browser tabs writing to the same project produce a coherent,
non-torn file (M9). Rotation happens under the same lock (v4-MINOR-2), so
two tabs crossing the 5 MiB rotation threshold near-simultaneously yield
exactly one `chat.jsonl.1` backup rather than corrupting either file.

The lock does **not** prevent two tabs from each holding their own
in-memory `ChatSession`. Confirmation tokens are per-session; if you
approve a confirmation in tab A, tab B's UI still shows the card until
the session it's on issues its own next turn. This is by design — every
tool dispatch is authorized by exactly one card.

## Lineage rules (Phase 4)

`chat.jsonl` follows the project across every save / clone / snapshot
transition:

| Transition | Behaviour |
|---|---|
| Routine re-save (loaded == name) | no change |
| Save-As (`?rebind=true`) | chat.jsonl **moved** to new project dir; cache invalidated |
| Save-a-Copy (`?rebind=false`) | chat.jsonl **copied** to new project dir; active session continues on the source |
| `create_scenario` | chat.jsonl **copied** to scenario dir |
| `rename_project` | filesystem rename handles the file; persist_path cache invalidated |
| `create_project_snapshot` | chat.jsonl **included** in snapshot bundle |
| `restore_project_snapshot` | active chat.jsonl **overwritten** from snapshot bundle; cache invalidated |

All lineage operations are best-effort: a failure copying chat history
NEVER aborts the underlying project save / rename / restore.

## Explaining a result

`explain_investment(component_class, name)` (read tier) answers "why did the
model build — or not build — this?" in one call, and it is the tool the system
prompt routes every sizing question to.

The reason it exists is `binding_constraint`. In a capacity-expansion LP the
answer to "why is it this big" is almost always **which constraint stopped
it**, and no per-asset metric in the registry carries `p_nom_max` or
`p_nom_extendable`. The tool classifies the decision as one of:

| `binding_constraint` | What it means |
|---|---|
| `not_solved` | No fresh dispatch — there is no decision to explain |
| `not_extendable` | The LP could not size it; the capacity is an input |
| `at_upper_bound` | The **bound** set the size, not the economics |
| `at_lower_bound` | A non-zero floor is holding it up; it lost at the margin |
| `not_built` | Nothing blocked it — it was not worth building |
| `interior` | This size **is** the economic answer |

Alongside it: `asset_kpis` (the registry's cross-tab headline, read through
`get_asset_results` so it can never disagree with the Asset Detail tab),
`system_signals` (marginal price at the asset's buses, active CO₂ caps with
their shadow prices, and `congestion`), and `reading_notes`.

Two design rules the tests pin:

- **Evidence, never a verdict.** The payload carries numbers and one
  structural classification; the model writes the prose, and can only write it
  from fields that are present.
- **An empty list must say why it is empty.** `system_signals.congestion`
  carries a `note` distinguishing "no lines here", "no duals captured on this
  solve", "nothing binds", and "out of scope" — because all four otherwise
  read as *uncongested*, and only one of them is.

`reading_notes` also carries the zero-profit framing: an extendable asset at an
interior optimum earns ≈ zero net profit **by construction**, since the LP
builds until the marginal MW breaks even. Without that note a near-zero
`net_profit_eur` reads as a defect and gets a made-up cause.

## Reliability / solution-FMEA tools

The adequacy engines (`backend/services/adequacy/`) were reachable only from
the worksheet UI — the agent could read a solved plan's cost a dozen ways and
its reliability in none. Nine tools close that gap.

| Tool | Tier | What it does |
|---|---|---|
| `get_adequacy_results` | read | One dispatcher over the ten reliability GETs (`copt`, `fmea_modes`, `fmea_sweep`, `frontier`, `mc`, `mc_elcc_candidates`, `coupling_loop`, `margin_loop`, `adequacy`, `reserve_margin`) |
| `get_fmea_worksheet` | read | Per-project FMEA sidecar — expert rows + overlays |
| `get_stress_scenarios` | read | Per-project class-C scenario registry |
| `run_fmea_sweep` | execution | Class-B link-outage sweep + any class-C scenarios |
| `run_frontier_study` | execution | Cost-vs-availability curve (one full expansion solve per target) |
| `run_mc_study` | execution | Sequential Monte Carlo — LOLE / EUE, optional ELCC table |
| `run_coupling_loop` | execution | Drive a plan to an LOLE target on the **energy** lever (ENS cap) |
| `run_margin_loop` | execution | Same target, on the **firm-capacity** lever (reserve margin) |
| `abort_adequacy_study` | destructive | Stop any of the five studies at its next boundary |

Three properties are worth knowing before reading a transcript:

- **The GETs answer 204 when nothing has been computed.** A bare `Response`
  would reach the model as `<Response object at 0x…>` — the exact defect class
  the tools audit found in five export tools. `get_adequacy_results` maps every
  204 to `{"status": "no_data", "kind", "message"}`, where `message` names the
  missing precondition (never run / no solve / no target), so the agent reports
  the gap rather than zero risk.
- **The four starters are asynchronous by construction.** Each publishes a
  worker thread and returns `{"status": "running"}`; the agent polls the
  matching `get_adequacy_results` kind for rows, points or iterations. They are
  mutually exclusive with each other and with a foreground solve — a 409 means
  something else holds the network.
- **The engines carry different fidelities, and the system prompt says so.**
  `_ADEQUACY_GUIDE` (in `chat_service.py`) tells the agent that `copt` is an
  analytic screening convolution, `adequacy` is an `lp_proxy`, a met reserve
  margin is *not* a met reliability target, `mc` is a sampled estimate whose
  convergence must be quoted, and loop targets are horizon-basis hours rather
  than h/yr.

## What the assistant cannot do

- Mutate components while a solver run is in flight — the backend's
  middleware returns 409 for any POST/PUT/PATCH/DELETE under
  `/api/network/*` during a solve. The agent gets the same answer.
- Run two destructive tools in one turn (M7). The runtime emits
  `parallel_destructive_not_allowed` for each offender and asks the agent
  to retry serially.
- Drive Snakemake workflows. The v6 scope is the pypsa-gui REST surface
  only.
- Cross-tab session sharing — each tab has its own session_id and its
  own confirmation tokens.

## Security notes

- The API key never reaches the frontend. `/api/chat/health` reports
  only a boolean presence flag.
- `_redact_for_log` strips both the literal `ANTHROPIC_API_KEY` value AND
  any substring matching `sk-ant-*` before logging.
- `chat.jsonl` is gitignored via the existing `backend/projects/` rule.
- Confirmation tokens are server-stamped, single-use, TTL'd, and never
  surface in URLs.

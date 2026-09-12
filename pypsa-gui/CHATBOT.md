# pypsa-gui Chatbot Assistant

The chatbot panel embeds an in-app copilot. It can answer questions about the
open network, drive every backend tool the GUI itself exposes, and gate
destructive / execution actions behind explicit user confirmation.

It runs on **Anthropic by default, and on other providers by configuration** —
OpenAI, Moonshot (Kimi), Qwen (DashScope), or any OpenAI-compatible endpoint
including local ones (Ollama, LM Studio, self-hosted vLLM). Which provider is
used is a *profile*; see [Provider profiles](#provider-profiles) below. The
zero-config path is unchanged: an install that only ever sets
`ANTHROPIC_API_KEY` never has to meet the profile concept at all.

## Setup

The default (Anthropic) path is **off** until two prerequisites are met.
A non-Anthropic profile has its own prerequisites — see
[Provider profiles](#provider-profiles).

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

## Provider profiles

A **profile** is the unit you switch between: which provider, which model,
which credential, and what that model can do. Profiles are **instance-wide**
and only a super-admin edits them — on a server one API key is shared by every
organisation, and the desktop app's single seeded user is a super-admin, so
this never gets in the way there.

Two profiles exist with no configuration at all — `anthropic-sonnet` (active)
and `anthropic-opus` — both using the `ANTHROPIC_API_KEY` slot described
above. Nothing is written to disk until you add a profile, and an install that
never adds one behaves exactly as it did before profiles existed.

**Adding one.** Settings → the assistant's model section → pick a preset
(Anthropic, OpenAI, Moonshot, DashScope, Ollama, LM Studio) or *Custom
OpenAI-compatible*, then supply the model id and — for the cloud presets — a
key. Presets prefill the endpoint; the model id is always free text, so a
model newer than this build still works. **Test connection** makes one
1-token call and tells you which of *unreachable / unauthorized /
model not found / invalid request* you have, rather than failing silently.

**Where keys live.** Each profile derives its own slot in `<app-data>/user.env`
— a known name for a preset (`OPENAI_API_KEY`, `MOONSHOT_API_KEY`,
`DASHSCOPE_API_KEY`), or `PYPSA_GUI_LLM_KEY__<PROFILE_ID>` for a custom one.
The slot name is derived server-side and is **not** accepted from the client:
otherwise a profile could point its endpoint at an attacker's host while
naming the shared Anthropic slot as its credential. Ollama and LM Studio are
keyless. The shell-beats-file precedence above applies to every slot, so
`export OPENAI_API_KEY=…` behaves the way you would expect.

**Switching.** The chat panel's dropdown lists configured profiles. Switching
between two profiles on the same wire applies immediately; switching to a
different wire starts a new chat, because a conversation's stored history is
in one provider's block format and replaying it to another is at best a 400.
You can also ask the assistant to switch — it will show a confirmation card,
and (like the Settings route) it refuses unless you are a super-admin.

**Capabilities are declared, not assumed.** A profile says whether its model
can call tools and accept images. A tools-less profile is sent no tools and
gets a prompt with the tool-chaining guidance removed, and the panel says it
can answer but not act — rather than letting the model narrate actions it
cannot take. PDFs need the Anthropic wire; images work on either.

> **Verification status.** Per
> [ADR-0002](docs/adr/0002-chat-changes-need-a-live-api-probe.md) no test in
> `backend/tests/` constructs a real client, so a green suite does not verify
> a provider actually works. The live probes exist
> (`test_live_probe_anthropic_wire`, `test_live_probe_openai_wire_through_a_saved_profile`)
> but **skip unless explicitly enabled**, and a skip is not coverage. To run
> them: `PYPSA_GUI_TEST_LIVE_ANTHROPIC=1` with `ANTHROPIC_API_KEY` set, and
> `PYPSA_GUI_TEST_LIVE_OPENAI_PROFILE=<profile id>` for a saved
> OpenAI-compatible profile. `backend/smoke/run_chat_smoke.py --profile <id>`
> drives a fuller end-to-end pass against a running backend.
>
> Both probes have been run and passed — openai against a local Ollama on
> 2026-09-04, anthropic against the vendor on 2026-09-09. Runbooks:
> `docs/superpowers/runbooks/local-openai-wire-probe.md` and
> `anthropic-wire-probe.md` (repo root `docs/`, not this directory). That is a
> result for those commits, not standing coverage: they still skip by default,
> so the ADR's per-change rule is unchanged.

### Supplying the key in the packaged app

The distributed `.app` / `.exe` deliberately ships **no** `backend/.env` — that
file carries a real `ANTHROPIC_API_KEY` *and* the `SECRET_KEY` that signs
session cookies, and bundling it would publish both
(`backend/smoke/check_bundle.py` fails the build if it is ever present). So the
packaged app has to be told the key from inside itself:

1. Open the chat panel and send anything. The assistant answers with an
   **API key missing** banner. This inline paste-and-save flow is specific to
   the *built-in* Anthropic profile (the zero-config default); if the active
   profile is anything else, the banner instead names that profile and
   deep-links to Settings → the assistant's model section, because a custom
   profile's key has nothing to do with this route.
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
your files as you — that `backend/.env` already accepts. Only a **managed
key** may be read from or written to it — the fixed built-in provider slots
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `MOONSHOT_API_KEY`,
`DASHSCOPE_API_KEY`) plus one `PYPSA_GUI_LLM_KEY__<PROFILE_ID>` slot per saved
custom profile (see "Where keys live" under
[Provider profiles](#provider-profiles) above) — so it can never be used to
set `SECRET_KEY` or repoint the database. Every managed
value currently in effect, wherever it appears in backend logs or a persisted
chat transcript, is scrubbed before it is written — not just the ones shaped
like a known key format.

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

Voice input requires microphone permission. The packaged app's bundle now
declares `NSMicrophoneUsageDescription`, but whether macOS actually grants the
permission to the packaged app has not been verified — that requires building
and running the packaged app, which has not been done yet. If macOS has
denied it, the mic button is disabled and its tooltip says so.

Voice OUTPUT (the assistant speaking) needs no permission and is available in
the packaged app: measured at 219 voices in a real WKWebView. It is not wired
up yet; that is part of the assistant redesign, not this change.

## Models

The header dropdown selects the model used for the next turn:

| Model | Identifier | When to use |
|---|---|---|
| Sonnet 5 (default) | `claude-sonnet-5` | Quick reads, single-tool calls, low-cost iteration. |
| Opus 5 | `claude-opus-5` | Multi-step model design, complex reasoning, dependent tool chains. |

Switching models takes effect on the *next* turn; an in-flight stream
continues on the previous model.

## Usage meter (M10)

The header shows the running token totals for the session, e.g.:

```
12,345 in / 6,789 out · 234 cached
```

The server reports token counts (input / output / cache-read) and the client
renders them as-is — there is no cost figure, derived or otherwise. This app
does not publish per-model pricing it cannot verify, so no EUR (or USD)
estimate is shown anywhere. If that changes, it needs a verified pricing
source, not a hardcoded constant.

Cache-read tokens are shown because on a long session they dominate the
input count, and an in/out pair that ignores them under-reports the work
by the widest margin exactly when the session is longest.

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

## Interrupted turns and damaged history

A turn is written to `chat.jsonl` only once it has *completed*, so a backend
that dies mid-response would previously lose the user's own message with no
trace of it anywhere. A pending record is now written at turn start
(`chat.jsonl.pending`, via tmp + rename + fsync) and removed when the turn
ends. It survives a crash precisely because the code that removes it never
runs — so its presence after a restart *is* the signal.

`GET /api/chat/history` therefore reports two extra fields:

| Field | Meaning |
|---|---|
| `pending_turn` | A turn that started and was never answered. Reported **once**, then cleared. It is NOT added to `turns` — it has no assistant half, and writing it into the transcript would fabricate a conversation that did not happen. |
| `history_gap` | How many on-disk records were unreadable. Non-zero means `turns` is incomplete. |

A pending record whose session still has a turn in flight is left alone: a
second tab polling `/history` must not report a running turn as interrupted,
nor delete the record protecting it.

## Aborting a turn

Two paths reach the same `session.abort_event`:

- **Explicit** — `POST /api/chat/{session_id}/abort`, which the panel sends
  on close or from the Stop button.
- **Implicit** — a disconnect watcher polls `request.is_disconnected()` from
  the async `/stream` handler every `DISCONNECT_POLL_SECONDS` (2 s). A killed
  tab, a sleeping laptop or a dropped connection sends no abort, and without
  this the turn ran to completion: more tokens, and every remaining tool in
  the plan executed against a network nobody was watching.

The watcher is armed on the event loop and disarmed from the SSE generator's
`finally`, so no polling task outlives its stream. It relies on uvicorn's
`receive()` being level-triggered (it re-reports `http.disconnect` on every
call), which is what lets it coexist with Starlette's own disconnect
listener. It is an accelerator, not the guarantee — the explicit abort and
the generator's own teardown remain the backstops.

## Confirmation flow

| Tool tier | Card? | Typed confirmation? |
|---|---|---|
| `read` | no | no |
| `write` | no | no |
| `destructive` | yes (5 min TTL) | only for the highest-risk tools (see below) |
| `execution` | yes (5 min TTL) | no |
| `execution_long_running` | yes (5 min TTL) | no |

Before the card is issued, a destructive call's arguments are checked against
`chat_tools.PRE_DISPATCH_VALIDATORS` and refused with
`error_kind='invalid_tool_args'` if the call cannot succeed — deleting a
component that is not in the network, say. Without this the user was asked to
approve (and for `cascade_delete_bus`, to *retype the bus name* for) an
operation that would then 404. Validators are advisory: one that raises
leaves the tool exactly as callable as before. They cover network-local tools
only; project- and snapshot-level existence checks stay in the route handlers,
which already resolve tenancy correctly — a second copy of that logic would be
an existence oracle.

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

## The study write-up

`build_study_report` (read tier) assembles the client-facing reliability
write-up from everything the session established — **and everything it did
not**. The system prompt routes any request for a report or a summary of
findings through it.

`AdequacyReport` deliberately keeps the COPT screening and the sequential MC
as *siblings* rather than folding them in: each answers a question the report
doesn't ask, and folding them would grow the one shape every consumer parses
(spec §4, recorded decision). That's right for the wire and wrong for a client
deliverable, where the whole point is to put the screening, the proxy, the
sampler and the firm-capacity convention side by side. So this fuses — and the
fusion's entire job is to stop the fusion laundering anything:

- **Every section carries its own `engine` and `fidelity`**, read from the
  payload where the engine states them, with `stated_by_engine` marking the
  cases where the label is ours. A screening convolution and a sequential
  sampler must never become two numbers in one table with nothing between
  them.
- **`required_disclosures`** are the sentences the prose must contain, derived
  from what's actually present: a met reserve margin is not a met reliability
  target; a COPT figure is not comparable to a statutory standard; an
  unconverged MC mean is an interval, never a point value. Absent sections
  produce no disclosures — a caveat about a number nobody computed is noise.
- **`not_established`** is the negative space, stated. A report that silently
  omits "no Monte Carlo was run" reads as though LOLE was measured. The
  omission is the finding.
- **`evidence_gaps`** are what undermines the whole document rather than one
  section — a frozen demand profile, an island nothing can serve, a failure
  rate with no recorded source — drawn from the uploaded-data QA, the topology
  analyser and the provenance ledger. They belong *before* the numbers they
  undermine.

It writes no prose. The caller narrates, and can only narrate what is in the
payload.

On an unsolved network exactly one section comes back established — the COPT
screening, which is computed on demand and needs no solve. Everything else is
honestly absent, which is the report doing its job rather than a gap in it.

## Campaign mode — one budget across a chain of studies

Every adequacy engine caps itself: `MAX_FRONTIER_POINTS = 12`,
`MAX_CONTINGENCIES = 20`, `MAX_LOOP_SOLVES = 8`, `MAX_DRAWS = 2000`. **Nothing
capped chaining them.** An agent asked to "hit LOLE ≤ 3 h/yr at least cost"
can legitimately reach for a frontier, then a margin loop, then a coupling
loop, then a sweep — every call inside its own limit, ~50 full
capacity-expansion solves in total, unattended, on a shared solver.

`services/adequacy/campaign.py` is that missing accounting, with three tools:

| Tool | Tier | |
|---|---|---|
| `start_campaign` | write | Opens one, with an objective and a solve budget (default **30**) |
| `campaign_status` | read | Objective, budget, spend, and the log of studies run |
| `end_campaign` | write | Closes it and returns the final record |

While a campaign is open, the five `run_*` study tools charge against it and
refuse what would overrun. With no campaign open they behave exactly as
before — a single study asked for directly is not a campaign, and gating it
would be a behaviour change nobody asked for. **The panels are untouched**:
this gates the agent, not a human clicking one button at a time.

Four decisions worth knowing:

- **It is not prompt discipline.** A model cannot be asked to keep a running
  total of solves across a conversation and be relied on to stop. The gate is
  in the dispatcher, where the only way past it is not to call the tool.
- **Check, start, then record — never charge-then-refund.** A study that fails
  to start (409 while the mesh is busy, 422 for a missing VOLL) must not burn
  budget, and a refund path is a second place for the total to go wrong. The
  study mesh already allows at most one study in flight, so nothing slips
  between the check and the record.
- **Monte Carlo is charged zero, because it solves nothing.** Its cost is
  arithmetic over one snapshot. Charging it would price the one study that can
  be run freely as if it were the most expensive — and it stays runnable
  inside an exhausted campaign.
- **Estimates are read from the engines, not restated.** `MAX_LOOP_SOLVES`,
  `DEFAULT_TARGETS_PERMYRIAD` and `class_b_contingencies` are imported at
  estimate time; a second copy of any of them in the budget module is how the
  budget starts lying. The margin loop is charged `max_solves + 1` — its
  starting point is a *measurement* taken by a probing solve that runs before
  the budget.

`campaign_status().entries` is also the only record of what a campaign already
ran: each study surface holds just its latest result, so a second frontier
overwrites the first.

## Diagnosing an infeasible model

`diagnose_network` (read tier) is the shape check `validate_network` does not
do, and the system prompt routes every `infeasible` here before theorising.

Value checks — bounds, finiteness, references — all pass on a network that is
two disconnected halves, one holding the demand and the other the plant meant
to serve it. The LP answers that with `infeasible` and a linopy traceback that
names neither the island nor the demand, which is why "my model won't solve"
is the most expensive question a modeller asks.

`services/topology_analyzer.py` returns one row per island — `n_buses`,
`peak_load_mw`, `nameplate_mw`, `extendable`, `verdict`, `reason` — where
`verdict` is `ok` / `no_demand` / `no_supply` / `under_capacity`. Islanding and
unservable islands also reach preflight as warnings.

Three rules keep it from over-claiming:

- **Links count as connections.** PyPSA's `sub_networks` deliberately exclude
  them (a converter is not an impedance), but for the energy balance a link
  plainly connects its buses — reporting an electrolyser bus as islanded would
  send the user chasing a line that should not exist. Multi-port `bus2..bus4`
  count too.
- **A shortfall is claimed only when it is certain.** Nameplate is an upper
  bound on dispatch, so "peak demand exceeds total nameplate" is
  one-directional: when it fires the island genuinely cannot be served, and
  when it doesn't, nothing is claimed. Anything extendable in the island — a
  `Store` included, since it withdraws certainty without adding firm supply —
  and no claim is made at all.
- **Peak is per snapshot, not per load.** Two loads peaking in different hours
  must not add into a peak that never happens; that would manufacture a
  shortfall out of arithmetic.

Findings are warnings, never errors: an unservable island is an infeasibility
at VOLL = 0 and priced lost load above it, and the analyser does not know the
solver config — so it says which it is and blocks neither.

Bus membership is omitted from the tool's payload unless `include_buses=true`,
because on a large network it would crowd out the verdicts.

## Asset health — outage-rate provenance

`resolve_outage_params` resolves a per-asset failure rate three ways: `asset`
(typed on the component), `carrier_default` (the NERC GADS-style class
library), or `missing`. Two of the three explain themselves — the library
ships its own citation, and `missing` is the absence of a claim. **`asset`
does not**, and it is the tier every condition-based number lands in:

> λ = 0.031 because a drone survey found conductor damage on span 14

and

> λ = 0.031 because someone typed it

are entirely different claims that the model recorded identically.

`services/adequacy/asset_health.py` stores the difference as a per-project
`asset_health.json` sidecar, with two chat tools:

| Tool | Tier | |
|---|---|---|
| `get_asset_health` | read | The ledger, reconciled against the live network |
| `record_asset_health` | write | Whole-ledger replace |

Each entry requires a **`method`** (`inspection` / `sensor` / `lab_test` /
`vendor_datasheet` / `operating_history` / `fleet_statistic` /
`expert_judgement`) and a **`measured_at`** ISO date: a condition figure with
no method is not evidence, and one with no date is not a measurement.
`expert_judgement` is on the list deliberately — the alternative to naming it
is not rigour, it's people picking whichever label looks most defensible.

Two design rules:

- **The ledger never sets a rate.** Values go on the components through the
  ordinary edit paths (`bulk_update_components`); this file records what was
  applied and how it was obtained. That split is what lets the network
  *contradict* the ledger — and a ledger nothing can contradict is decoration,
  not provenance.
- **`provenance_report` is the reconciliation**, and it is where the value is:
  `unsourced` (a rate that overrides the carrier library with no recorded
  source — the finding to lead with), `drifted` (the measurement no longer
  matches what the engines will read), `orphaned`, `sourced`.

`get_asset_health` returns `provenance: null` with a note when the named
project is not the one in the foreground. Reconciling an on-disk ledger
against a *different* network would answer a question nobody asked, and look
authoritative doing it. The route itself stays pure — the server never mixes
foreground network state with on-disk project state; the fusion happens in the
chat tool, which knows which network it holds.

This is the interface a perception feed lands through — an inspection
programme, a DGA monitor, a vegetation-encroachment model — and it exists
before any such model does, because until it does none of them can land.

## Uploaded-data QA

`services/timeseries_qa.py` checks the time series a user uploads, and
`validate_for_run` folds its findings into preflight — so they reach the
Issues panel, the pre-solve log, and the agent's `validate_network` in one
place.

The distinction from `validation_service`: that module asks *will PyPSA fail
on this?*; this one asks *is the series what the user thinks it is?* Every
case here **solves cleanly** and produces a confident, wrong answer, which is
exactly why nothing downstream of the LP would ever mention it.

| Code | Catches |
|---|---|
| `timeseries_all_zero` | A profile of nothing — the asset can never dispatch, and the plan still reports optimal |
| `timeseries_frozen` | One value pasted down a column, or a sensor that stopped reporting |
| `timeseries_negative` | Availability below zero; negative demand (legal, but usually a sign error) |
| `timeseries_spike` | One cell ≥ 50× the series' own median — a misplaced decimal, and the peak sizes the fleet |
| `timeseries_scale_outlier` | A load three orders of magnitude from its peers — kW pasted into an MW column |

All five are **warnings** by the module's severity policy (error = PyPSA will
fail), so none can block a run: the user is told and decides.

Two properties the tests pin. Findings never fire on a short horizon — a
4-snapshot test network is legitimately flat, and that is not evidence. And a
zero series is reported once, as `all_zero`, not also as `frozen`: two
warnings for one defect is noise, and noise is how warnings die.

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

- No managed key ever reaches the frontend. `/api/chat/health` and the
  profile list report only presence/hint, never a value.
- `services/redaction.py` scrubs secrets in two passes, in this order: first
  by VALUE — every managed key currently in effect (every built-in slot and
  every `PYPSA_GUI_LLM_KEY__<PROFILE_ID>` slot, via
  `app_secrets.live_secret_values()`), substituted wherever it appears,
  regardless of shape — then by PATTERN (`sk-ant-*`, `key=value`,
  `bearer …`) for anything the value pass didn't already catch. The value
  pass is what makes a custom provider's key safe to log or persist even
  though its value doesn't look like an Anthropic key: pattern-only
  redaction would miss it entirely. Applied before both the backend log
  (`redact_for_log`) and the durable transcript (`redact_for_persist`,
  `chat.jsonl`) — see `backend/tests/test_no_split_merge_precondition.py`,
  which drives a real turn end-to-end to prove neither sink leaks a
  non-pattern-shaped managed value, and that the value-substitution pass is
  the reason: disable it alone and the same drive leaks.
- `chat.jsonl` is gitignored via the existing `backend/projects/` rule.
- Confirmation tokens are server-stamped, single-use, TTL'd, and never
  surface in URLs.

# MCP — the harness as a client (issue 19) and as a server (issue 11)

Status: draft for the owner, 2026-10-14. A document only: nothing here is built.
Issues: `issues/19-mcp-client.md` (Q18 decided its defaults), `issues/11-mcp-exposure.md`
(Q8 is open; §B.4 lays it out as choices).
Builds on: spec §8 Q8 ("later, own spec"), §9 Q18 (admin only, zero servers by
default, write tier unless the server marks a tool read-only, descriptions capped,
results fenced), the harness contract (`pypsa-gui/backend/harness/README.md`).

The two directions share one vocabulary and one rule: **an MCP tool is a
catalogue tool with a foreign handler, and the confirmation card, the fence, the
event log and the budgets apply to it unchanged.** Neither direction changes a
built-in tool's semantics, tier or route.

## What we have (master f5f3579, surveyed 2026-10-14)

Facts the design depends on, with the places they live:

| Fact | Where | Consequence for MCP |
|---|---|---|
| No MCP code or dependency anywhere; `mcp` is not installed and not in `gui-requirements.txt` or `pypsa-gui.spec` | — | A new pinned dependency, a measured `hiddenimports` entry, `smoke/check_bundle.py` |
| Tools are the static module list `catalogue.TOOLS` (244), each with a `DISPATCHERS` entry and a `TOOL_ROUTES` entry; parity tests pin `len(TOOLS) == len(DISPATCHERS)` | `harness/catalogue.py`, `services/chat_tools.py` | External tools must NOT enter `TOOLS`; they need a per-session list beside it |
| The tier is the `Safety: <tier>` text; an unknown or unmarked tool resolves to `read` (no card), documented as fail-open and kept safe only by a test over `TOOLS` | `confirm._safety_tier_for`, `catalogue.safety_tier_for` | An external tool looked up there would run **unconfirmed**. The external tier lookup must come first and fail closed |
| The tools payload is built per turn from `TOOLS` (`_tools_payload`, then `filter_tools`, then the 128-tool cut for `api.openai.com`); dispatch refuses a name not in the payload sent (`tool_not_offered`) | `providers/wiring.py`, `loop.py` | Adding the session's external tools to the payload is enough for the allowlist to cover them |
| Checked mode's `tool_guard` fails closed for any tool it does not map | `assurance.py` | External tools are refused in checked turns with no change |
| The card blocks the tool thread on a `threading.Event` released only by `POST /api/chat/{id}/confirm` from the session's owner; TTL 300 s; the token travels only in the SSE frame | `confirm.py`, `session.py`, `routers/chat.py` | Exposure (B) has no SSE consumer: the gate needs a new answer path |
| Web mode: cookie sessions only, no API tokens of any kind (`AuthToken` is for password resets) | `deps.py`, `auth_service.py` | Exposure in web mode needs a new token model |
| Desktop/local mode: one seeded super-admin, no cookie, no CSRF check; any local process can call the loopback API as that user; one active network per process | `local_mode.py`, `main.py` `_csrf_rejection`, `pypsa_service.py` | Local exposure adds no trust boundary the loopback API does not already lack, but should not widen it either |
| Install-level settings (LLM keys, profiles) are super-admin only, on the chat router so they work on desktop; secrets live in `user.env` (0600) under an allowlist (`is_managed_key`) | `routers/chat.py` `_require_super_admin`, `services/app_secrets.py` | The MCP server list follows the same pattern |
| Results are shaped (identity scrub, size cap, spill) and wrapped in `<untrusted_data>` with the delimiters neutralised | `results.py`, `fence.py`, `spill.py` | MCP results take the same path |
| `smoke/assistant_inventory.py --check` compares the static catalogue with a committed JSON | `.github/workflows/assistant-inventory.yaml` | Runtime tools stay out of the inventory by construction |
| `ROUTE_POLICY` classifies every write route and non-read chat tool for the project lock | `tests/test_write_surface_lock_policy.py` | External tools never touch the project; they get one explicit class |

---

## Part A — the client (issue 19), on Q18's defaults

An admin adds an MCP server (a tariff database, a weather feed, a company data
service); its tools appear in the catalogue for every provider, behind the same
card and fence as the built-in tools.

### A.1 Configuration

- **Who**: super-admin only (`_require_super_admin`), the same guard as the LLM
  keys and profiles. Q18 says "admin"; servers are per install, not per org, so
  an org admin in web mode cannot add one. On desktop the local user is the
  super-admin.
- **Where**: `<app data>/mcp-servers.json`, written atomically, mode 0600. Never
  read from a project folder, a bundle or an upload (the D12 rule that already
  holds for workflows and skills).
- **Default**: the file is absent, so there are zero servers and the feature is
  invisible: no tool, no prompt byte, no connection.
- **Entry**:

  ```json
  {"id": "tariffs", "enabled": true,
   "transport": "stdio", "command": "/usr/local/bin/tariff-mcp", "args": ["--ro"],
   "env": {"TARIFF_TOKEN": "PYPSA_GUI_MCP_KEY__TARIFFS"},
   "tiers": {"upload_tariff": "destructive"}}
  ```

  or `"transport": "http", "url": "https://…/mcp"`, which sends a bearer header
  whose value comes from a managed key.
- **Field rules**:
  - `id`: `[a-z0-9]{1,16}`, unique.
  - `env`: maps a variable the server process sees to a managed key name. A new
    family `PYPSA_GUI_MCP_KEY__<SLOT>` joins `is_managed_key`. The stdio child
    gets **only** these variables plus `PATH`/`HOME`/`LANG`, never the
    backend's whole environment (which holds every provider key).
  - `http`: `https` only, except a loopback host.
  - `tiers`: per-tool overrides that may **raise** a tier, never lower it (A.3).
- **Settings API**:
  - routes: `GET/PUT/DELETE /api/chat/settings/mcp/servers[/{id}]`, plus
    `POST …/{id}/test`, which connects, lists the tools and returns names, tiers
    and the capped descriptions for the admin to read before enabling;
  - guard: the same super-admin check as the other settings routes;
  - classification: `ROUTE_POLICY` classifies them as settings writes outside
    the project lock.
- **Panel**: a section beside the LLM profiles: the list, the enable toggle,
  the test result, and a plain warning that a server's descriptions and results
  reach the model.

### A.2 Lifecycle

- **Module**: `harness/mcp_client.py` owns every connection; the loop never
  imports it.
  - Connect lazily, at the first session start after the config changes.
  - Keep the connection per server, process-wide.
  - Reconnect on failure with the existing retry schedule (`budget.py`).
- **The tool list is a snapshot per chat session**, taken at session start (or at
  rebuild from the event log) and fixed for the session's life. That keeps the
  cached prompt prefix stable and the dispatch allowlist exact.
  - A server's `tools/list_changed` notification applies to the next new
    session, never mid-session.
  - A tool that disappeared is refused at call time with `mcp_unavailable`.
- **Supported**: `initialize`, `tools/list`, `tools/call`, cancellation (an
  aborted turn sends `notifications/cancelled`), ping.
- **Refused or ignored**: everything server-initiated or prompt-shaping.
  - Server `instructions` from `initialize` are dropped and never reach the
    prompt.
  - `prompts/*` and `resources/*` (and subscriptions) are not used.
  - `sampling/createMessage`, `elicitation/create` and `roots/list` requests
    from the server get a JSON-RPC "method not found".
  - Tasks are not used.
- **Timeouts**: the per-tool timeout (`budget.py`) applies; a slow call
  ends with `mcp_timeout` and a cancellation is sent.

### A.3 The catalogue entry

Each server tool becomes one session-scoped entry, shaped like a `TOOLS` entry:

| Field | Rule |
|---|---|
| `name` | `mcp__<server>__<tool>`, the tool part lower-cased and reduced to `[a-z0-9_]`, the whole at most 64 characters (the common name limit across both wires); a collision after reduction refuses the second tool at session start, never renames it silently |
| `description` | `External tool from MCP server "<id>". ` + the server's description, whitespace-collapsed, delimiters neutralised (`fence.py`), **capped at 1024 characters**, + ` Safety: <tier>` appended by the harness last, so a server cannot forge its own marker (any `Safety:` text in the server's description is removed first) |
| `input_schema` | the server's `inputSchema` if it is a JSON object schema of at most 8 KB serialised and nesting depth 6; `$ref` and remote references are refused; otherwise the tool is skipped with a logged reason |
| tier | `write` by default. Lowered to `read` only when the server sets `annotations.readOnlyHint: true` and `destructiveHint` is not true. Raised to `destructive` when `destructiveHint: true`. The admin's `tiers` override may raise any tool further, never lower it. |

The tier for an external tool is looked up in the session's external list
**before** `confirm._safety_tier_for`, and a name that matches the `mcp__`
prefix but is not in the list resolves to `write`. This is the one change to
the fail-open default: it applies only to `mcp__` names, so no built-in tool
changes behaviour.

### A.4 Where it plugs in

| Seam | Change |
|---|---|
| Tools payload | `providers/wiring._tools_payload_for_profile` appends `session.external_tools` after `TOOLS` (so the built-in prefix stays cached); the OpenAI 128-cut ranks them like any tool, control tools first |
| Dispatch | `TOOL_ROUTES` gains the sentinel `_MCP_CALL` (in `NON_HTTP_SENTINELS`); the loop's handler lookup asks `mcp_client.dispatcher_for(session, name)` when `DISPATCHERS` has no entry. Order unchanged: allowlist → `tool_guard` → validators → card → handler |
| Toolsets | external tools appear in the `all` domain and a new `external` domain only; `filter_tools`' substring match never pulls them into the built-in domains |
| `find_capabilities` | searches the session's external tools after the static index (no `lru_cache` over them), marking hits `external: true` |
| Checked mode | unchanged: `tool_guard` refuses them (`capability_unsupported`) |
| Project lock | `tool_edits_network` is false for every external tool; they are not lock-gated. `ROUTE_POLICY` gains one class, `EXTERNAL_MCP`, asserted for the sentinel |
| Inventory | unchanged: runtime tools never enter `TOOLS`; the inventory sees only the new sentinel |
| Card | `tool_pending_confirmation` keeps its name; its payload gains `external: {server}` so the panel can label the card "External tool from …". The args are shown as for any tool |
| Approvals | `approval/asked` records the full `mcp__…` name, as today |

### A.5 Results

The MCP result content passes through `results.py` like any tool result: identity
scrub, size cap, spill with `read_result`, then the `<untrusted_data>` fence.

- `text` → text.
- `structuredContent` → JSON text (preferred over the text copy when both are present).
- `image` → dropped with a one-line note in the first release (vision through tool results is a per-wire capability; a later issue).
- `resource_link` and embedded resources → dropped with a note.
- `isError: true` → a `tool_error` with `error_kind: mcp_tool_error`, the message fenced as error text.

New error kinds go into `tool-error-kinds.json` as literal dicts:

- `mcp_unavailable`: the server is down or the tool is gone;
- `mcp_timeout`;
- `mcp_tool_error`;
- `mcp_protocol_error`: a malformed response.

### A.6 The event log and replay

Rule 9 holds: tool calls and results are already logged. One new event at session
start, `mcp/catalogue {servers: [{id, tools: [{name, tier, schema_sha256}]}]}`,
records what the model was offered.

- **Rebuild**: a session rebuilt from its log takes a fresh snapshot. If a tool's
  `schema_sha256` changed, the new one is used and a second `mcp/catalogue`
  event is written.
- **Replay**: replay offers the recorded declarations. A call is dispatched only
  when the same server is configured; otherwise it gets `mcp_unavailable`. The
  replay report names it as an external dependency.

### A.7 Dependency and layering

- **Dependency**: the official Python MCP SDK, pinned in `gui-requirements.txt`
  and `backend/requirements.txt`.
  - Most of its dependencies (anyio, httpx, pydantic, starlette) are already in
    the bundle.
  - The `hiddenimports` entry is measured, not guessed (the `.spec` rule), and
    `check_bundle.py` imports it.
- **Fallback**: if the SDK's footprint or pins clash, a hand-written client over
  `httpx` plus a subprocess for stdio is the fallback. It needs only the four
  methods in A.2.
- **Layering**: `tests/test_harness_layout.py` gains a check that only
  `harness/mcp_client.py` (and, for B, `harness/mcp_server.py`) imports `mcp`,
  and that `loop.py` imports neither.

### A.8 Tests and parity

- **Backend**:
  - config validation and the env allowlist (the child sees no provider key);
  - the name and description shaping (cap, forged `Safety:` removed, delimiter
    neutralised);
  - tier mapping, including the "`mcp__` unknown → write" rule;
  - a write tool gets a card, a read-only one does not;
  - results fenced and spilled;
  - each error kind;
  - an abort sends a cancellation;
  - server-initiated requests refused;
  - the `mcp/catalogue` event;
  - checked mode refuses.
- **Parity battery**: `smoke/run_chat_smoke.py --workflow` gains a stub stdio MCP
  server (`smoke/mcp_stub_server.py`) with one read tool and one write tool. The
  probe asserts the read call runs without a card and the write call shows one,
  on each wire.
- **Frontend**: the settings section and the card's "External tool" label.

### A.9 Slices

1. `mcp_client.py`, config file, the payload, dispatch and tier seams, the
   results, the error kinds, the event, the stub server. No UI: the admin edits
   the JSON file. One PR.
2. The settings routes and panel section. One PR.

---

## Part B — exposure (issue 11), and Q8

An external agent (Claude Code, Codex CLI, a Kimi agent) drives a running pypsa-gui
through the same catalogue. **Whether, and how far, is Q8.**

### B.1 What exposure needs, whatever the scope

- **Module**: `harness/mcp_server.py`, a Streamable HTTP endpoint at `/api/mcp`
  on the existing app. stdio comes through a small shim, `pypsa-gui mcp`, which
  forwards to it.
- **Dispatch through the same path as the loop**: the loop's per-call dispatch
  (allowlist → `tool_guard` → validators → card → handler) is extracted into
  one function both callers use, so the external caller cannot skip a step.
- **The caller's identity and project**: each MCP connection gets its own chat
  session (`ChatSession`, owner = the authenticated user, id `mcp-<random>`),
  with the acting-user ContextVars and a turn cell bound as `chat_stream` binds
  them today.
- **Logging**: calls are recorded in that session's event log
  (`source: "mcp"`), so the trajectory and the approvals record show what an
  external agent did.
- **What is exposed**:
  - catalogue tools only;
  - never the harness control tools (`ask_user`, `use_skill`, workflows, goals,
    `use_toolset`), whose job is shaping the in-app model's turn;
  - never `_UI_EVENT` tools (there is no panel stream to drive);
  - `read_result` stays, so oversized results remain reachable.
- **Annotations**: `readOnlyHint` for `read`, `destructiveHint` for the
  destructive tiers. A `?toolset=<domain>` query narrows the 244-tool list for
  clients that load all tools up front.
- **External tools are not re-exported**: tools that came from A's servers are
  never exposed again.
- **Budgets**: the session and daily budgets do not apply (no model runs here),
  but the `/stream` rate limiter's token bucket does.

### B.2 The two hard parts

- **Authentication.** Web mode has cookie sessions only.
  - An external agent needs a credential it can hold, which means a new
    personal-token model: create, list, revoke and expire; stored as a hash like
    sessions; bound to one user and their org; shown once.
  - Desktop mode has no auth at all. A per-launch random token written to
    `<app data>/mcp-endpoint.json` (0600, with the ephemeral port) gives the
    endpoint more protection than the rest of the loopback API has today, at no
    cost to the user. The stdio shim reads the file.
- **The confirmation card.** The token reaches only the SSE consumer, and the
  tool thread blocks for up to 300 s. There are three ways to answer it:
  - **(g1)** never offer write tiers;
  - **(g2)** show the card in the app's chat panel, labelled "External agent
    (<client>) wants to …", and answer it there. The MCP call waits on the same
    event and TTL. A person must be at the app.
  - **(g3)** MCP elicitation: the agent's client asks its own user. Client
    support varies and a client may answer on the user's behalf. That puts the
    approval outside pypsa-gui, which the card rule (contract rule 6) does not
    allow, so this spec does not propose it.

### B.3 What exposure does not change

The tiers, the Guided write rule, auto-approve (destructive tiers only, never
`write`), the parallel-destructive refusal, the project lock, the approvals
record. An exposed write is exactly as guarded as an in-app one.

### B.4 Q8 — External agents over MCP: how far?

❓ **Q8 (reopened on this draft)**: Q8 said "later, own spec". This is the spec.
Which scope should exposure have?

1. **Read-only, this machine only (Recommended).**
   - **Scope**: desktop/local mode. The exposed tools are the `read` tier only
     (B.1's exclusions apply). Gate option g1.
   - **Auth**: the per-launch token.
   - **Consequence**:
     - Claude Code or Codex on the same machine can inspect networks, results
       and studies, but cannot change anything, so no card is ever needed.
     - No new auth model; web deployments get nothing.
     - About one PR after Part A slice 1.
     - Writing stays a later decision with this draft's g2 ready.
   - **Why recommended**: it delivers the "one contract for in-app and
     out-of-app agents" story for the common case at the smallest new surface,
     and every later option builds on it unchanged.
2. **Full catalogue, this machine only.**
   - **Scope**: as 1 plus every write tier, gate g2 (the card in the app's
     panel).
   - **Consequence**:
     - An external agent can build and change networks.
     - Each write waits for a person at the app (up to 300 s, then denied).
     - `ROUTE_POLICY` and the lock tests extend to the new caller.
     - About two PRs; the dispatch extraction (B.1) is the larger risk.
3. **Full catalogue, web deployments too.**
   - **Scope**: as 2, plus the personal-token model (B.2) for signed-in users of
     a hosted instance.
   - **Consequence**:
     - Remote agents can drive a shared instance.
     - A new credential type must be designed, reviewed and audited (creation,
       revocation, expiry, org scoping, rate limits).
     - The largest option, with a security review before release.
4. **Not now.**
   - **Scope**: Q8 stays "later"; issue 11 stays needs-info with this draft on
     file; Part A proceeds alone.
   - **Consequence**: nothing new to build or secure; external agents keep
     having no supported way in.

Part A does not depend on the answer: the client can go ahead on Q18's defaults
whichever option is picked, and B.1's dispatch extraction is shared work only
for options 2 and 3.

---

## Defaults this draft takes (say if any is wrong)

These are engineering choices inside Q18, not owner questions:

- "admin" is read as **super-admin**, the install-level role that owns the LLM
  keys;
- description cap of 1024 characters;
- schema cap of 8 KB, depth 6;
- `https` only, except loopback;
- the tool list is fixed per session;
- images are dropped in the first release;
- server instructions, prompts, resources, sampling and elicitation are
  unsupported;
- the SDK is preferred over a hand-written client.

## Out of scope

- MCP servers declared by a project or a bundle.
- Per-user (non-admin) servers.
- A server's prompts or resources entering the prompt.
- Re-exporting external tools.
- Any change to a built-in tool's tier, route or semantics.

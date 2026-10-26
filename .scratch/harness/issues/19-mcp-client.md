# 19 — External tools over MCP: the harness as an MCP client

Status: ready-for-agent (spec decided 2026-10-14: `../mcp-spec.md` Part A, build step 2; step 1, the tier fallback, merged as PR #220)
Progress: mcp_client.py slice 1 on master; slice 2 settings routes, panel section, PYPSA_GUI_MCP_KEY managed-key not found
Type: research
Blocked by: 08 (done), the MCP spec (done), PR #220 (done)

Source: assessment design 7; DeepSeek Harness `docs/subsystems/mcp.md`
(stdio and Streamable HTTP transports, per-server connection plugins, tool
names carry the server name, results pass permission checks and are
recorded; prompts, elicitation, tasks and resource subscriptions unsupported).

## Question

Should a user be able to add an MCP server (a tariff database, a weather
feed, a company data service) and have its tools appear in the catalogue for
every provider? If yes:

- Settings: a per-install list of servers (command or URL, env from the
  credential store, enabled flag), admin-only, never from a project bundle.
- Catalogue: tools are added at session start under `mcp__<server>__<tool>`
  with `Safety: write` unless the server marks the tool read-only, and every
  external tool not marked read-only gets the confirmation card in Expert and
  Guided alike (owner decision Q20; a built-in `write` tool is carded only in
  Guided); results go through the untrusted
  fence like any tool result (`harness/fence.py`).
- Routes: `TOOL_ROUTES` sentinel `_MCP_CALL`; the layering test keeps MCP
  code in `harness/providers/` or a new `harness/mcp_client.py`, never the loop.
- Dependency: the Python MCP SDK in `gui-requirements.txt` and the bundle.
- The parity battery gains a stub MCP server with one read and one write tool.

## Open points for Q18

The prompt-injection surface (a server's tool descriptions enter the system
prompt and its results the context); whether servers may be added by
non-admins; whether to ship with zero servers configured. Recommend: admin
only, zero by default, descriptions capped and fenced.

## Comments

2026-10-12 (re-scoped against master after 143 commits; nothing of this issue exists there yet):
Still deferred (Q18). Add to the open points: the OpenAI 128-tool
per-request selection and dispatch allowlist (`providers/wiring`),
`toolsets.filter_tools` domains, `find_capabilities` indexing, the
checked-mode `tool_guard` (fails closed for unmapped tools), and the
`smoke/assistant_inventory.py --check` CI step, which would flag tools that
appear at runtime.

2026-10-14: Specified in `.scratch/harness/mcp-spec.md` Part A, in two slices:

1. the client core, with no UI;
2. the settings routes and panel.

The spec answers the open points above:

- **Fail-open tier.** `confirm._safety_tier_for` failed open to `read` for a
  name that is not in `TOOLS`. PR #220 made it `destructive` for every such
  name; an `mcp__` name resolves through the session's external list first and
  falls through to that when it is not there. (Corrected 2026-10-14: the draft
  said `write`, which in Expert gets no card.)
- **Static guards.** External tools never enter `TOOLS`. The inventory and parity
  tests stay static; the payload and allowlist pick the tools up per session.
- **Descriptions.** They are capped at 1024 characters and lose any forged
  `Safety:` text.
- **Server child environment.** A stdio server sees only its own mapped keys.

2026-10-14 (decisions): The owner answered Q20: every external tool not marked
`readOnlyHint` gets a card in Expert too (`external and tier != "read"` in the
gate); built-in tools are unchanged. The defaults in the spec stand (super-admin,
tool list fixed per session, no images in the first release, no server
instructions, prompts or resources, sampling and elicitation refused). Build
order: the tier fallback (PR #220, done), then this issue's client core, then
the dispatch extraction, then issue 11.

2026-10-14 (slice 1 built): `harness/mcp_client.py` and its seams in the loop,
the gate and the tools payload, without the SDK (spec A.8a says why and what
differs from A.4: no `TOOL_ROUTES` sentinel, external tools only in the `all`
toolset, `find_capabilities` unchanged). Slice 2: the settings routes and panel
section, the `PYPSA_GUI_MCP_KEY__` family in `is_managed_key`, the parity probe.

2026-10-26 (slice 2 in progress, branch `claude/mcp-client-settings`, no PR yet;
plan sent to the gatekeeper, no verdict yet): built the
`PYPSA_GUI_MCP_KEY__` family in `is_managed_key`, the
`mcp_client` config helpers (`validate_entry`, `update_entries`, `probe`,
`forget`, `key_slot`), the super-admin routes `GET/PUT/DELETE
/api/chat/settings/mcp/servers[/{id}]`, `POST …/{id}/test` and, beyond A.1's
list, `PUT/DELETE …/{id}/keys/{name}` (name `bearer` or a listed variable;
slots are derived server-side and only the MCP family is reachable), the
settings section (`McpServerSettings.tsx`) and the card's "External tool"
label. Left: the parity probe in `run_chat_smoke.py --workflow` (register
the stub through PUT on a fresh session; `Run the tool mcp__<id>__lookup …`
gets no card, `set_value` gets one; DELETE in cleanup; a unit test in
`test_harness_provider_parity.py`), the release-note fragment,
`mp-code-review`, the PR with real-app screenshots, GK/UX OKs, CI.

2026-10-26 (slice 2 reworked to GK B's plan conditions): the web adds, edits,
toggles and deletes http entries only (PUT body `{enabled, url, tiers}`; an
id held as anything else is 409 and the file is left alone). Stdio entries are
written by hand and given back as they were; `/test` and `/keys` still work on
them. `validate_entry` refuses env names `LD_*`, `DYLD_*`, `PYTHON*`,
`NODE_OPTIONS`, `PATH`, `HOME`, `SHELL` and `_CHILD_ENV`; any 3xx is a failure
(redirects never followed); moving a server to another origin clears its bearer
token. The parity probe deviates from A's "stub stdio server": the stub gains
`--http PORT` and is registered through PUT, since the routes take http only.
Done: the probe, the release note, mp-code-review. Left: the PR, GK/UX OKs, CI.

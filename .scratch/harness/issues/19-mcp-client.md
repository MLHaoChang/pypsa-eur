# 19 — External tools over MCP: the harness as an MCP client

Status: done (slice 1 on master; slice 2 merged as PR #725; the last two A.8 tests 2026-10-27)
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

2026-10-27 (closed): both slices are on master (slice 2 as PR #725). Two
A.8 backend items had no test; `tests/test_harness_mcp_client.py` now pins
them: a checked turn refuses an external tool with `capability_unsupported`
and the server sees no `tools/call`; an oversized external result is spilled
behind a `read_result` locator inside the untrusted fence. No code change was
needed. Left out on purpose (A.8a): the Python MCP SDK; the hand-written
client stays until its footprint is worth taking.

# 19 — External tools over MCP: the harness as an MCP client

Status: needs-info (Q18 decided 2026-10-06; the joint spec is drafted, `../mcp-spec.md` Part A, awaiting the owner's read with Q8)
Type: research
Blocked by: 08 (done), the MCP spec (with 11)

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
  with `Safety: write` unless the server marks the tool read-only, so the
  confirmation card applies by default; results go through the untrusted
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

- **Fail-open tier.** `confirm._safety_tier_for` fails open to `read` for a name
  that is not in `TOOLS`. An `mcp__` name therefore resolves through the
  session's external list first, and to `write` when it is not there.
- **Static guards.** External tools never enter `TOOLS`. The inventory and parity
  tests stay static; the payload and allowlist pick the tools up per session.
- **Descriptions.** They are capped at 1024 characters and lose any forged
  `Safety:` text.
- **Server child environment.** A stdio server sees only its own mapped keys.

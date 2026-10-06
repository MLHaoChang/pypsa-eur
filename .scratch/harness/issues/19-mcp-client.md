# 19 — External tools over MCP: the harness as an MCP client

Status: needs-info (Q18 decided 2026-10-06: later, in one spec with issue 11; the defaults below are the owner's)
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

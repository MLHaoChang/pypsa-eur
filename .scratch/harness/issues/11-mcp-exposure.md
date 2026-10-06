# 11 — Expose the harness catalogue to external agents over MCP

Status: needs-info (owner decision Q8 in the spec)
Type: research
Blocked by: 08

Question: should Claude Code, Codex CLI, Kimi or other agents drive a running
pypsa-gui through the same catalogue? If yes: an MCP server in
`harness/mcp.py` that serves `harness.catalogue.TOOLS` and dispatches
through the same `DISPATCHERS`, with the confirmation gate mapped to MCP
elicitation and auth bound to a signed-in session. Needs its own spec; the
blocker is authentication and the confirmation gate, not the catalogue.

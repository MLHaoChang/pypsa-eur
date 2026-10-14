# 11 — Expose the harness catalogue to external agents over MCP

Status: needs-info (owner decision Q8, laid out as four options in `../mcp-spec.md` §B.4)
Type: research
Blocked by: 08

Question: should Claude Code, Codex CLI, Kimi or other agents drive a running
pypsa-gui through the same catalogue? If yes: an MCP server in
`harness/mcp.py` that serves `harness.catalogue.TOOLS` and dispatches
through the same `DISPATCHERS`, with the confirmation gate mapped to MCP
elicitation and auth bound to a signed-in session. Needs its own spec; the
blocker is authentication and the confirmation gate, not the catalogue.

## Comments

2026-10-14: The joint spec is drafted (`.scratch/harness/mcp-spec.md`, Part B).
What blocks exposure, from the code:

- web mode has cookie sessions only, so there is no credential an external agent
  can hold;
- the confirmation card's token travels only in the SSE frame and is answered by
  `POST /api/chat/{id}/confirm`.

Q8 is reopened on the draft as four scopes. The recommendation is read-only, on
this machine only.

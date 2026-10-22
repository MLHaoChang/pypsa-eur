# 11 — Expose the harness catalogue to external agents over MCP

Status: done (e1adb0db, no own PR)
Type: research
Blocked by: 08 (done), issue 19's client core, the dispatch extraction (`../mcp-spec.md` §Build order, step 3)

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

2026-10-14 (decision): Q8 is option 2. The full catalogue is exposed on this
machine only (desktop/local mode). Every write shows its card in the app's chat
panel (gate g2, 300 s TTL), and an external agent's calls are carded at `write`
and above in every mode. Web deployments are out, so no personal-token model is
built. Build steps 3 (dispatch extraction) and 4 (local exposure) in the spec.

2026-10-14 (built, build step 4): `/api/mcp` in local mode (`harness/mcp_server.py`,
`routers/mcp.py`), the endpoint file, the pending-cards route and the panel strip
(`ExternalAgentStrip`); spec §Build order step 4 lists what is in it. The stdio
shim is the remaining follow-up.

2026-10-14 (follow-up): the stdio shim `pypsa-gui/backend/mcp_stdio.py` and the
hardening from the review of #237 (spec §Build order step 4). A shim inside the
frozen desktop bundle (a `pypsa-gui mcp` subcommand) would be a launcher entry
point. **Owner decision 2026-10-14: not now.** The standalone script stays as it
is; do not add a bundle subcommand without a new decision.

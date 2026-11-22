Status: ready-for-agent

# Per-tool registry for the chat catalogue

Left out of RETRO-FRAG-1 by GK B (60), Q2 (a). On 17 Nov three open PRs each edited `services/chat_tools.py` and `routers/chat.py`; on master `harness/catalogue.py` (TOOLS, TOOL_ROUTES) had 29 commits and `chat_tools.py` (DISPATCHERS) 34 in the 30 days before.

These are code (JSON schemas, dispatcher functions), not data rows, so the JSON fragment pattern does not fit. Candidate: one module per tool (or tool family) that registers its schema, routes and dispatcher, discovered at import, with a checked order (`wiring._bounded_tools` breaks selection ties by TOOLS index) and the frozen app's hidden imports listed in `pypsa-gui.spec`.

Also open: `harness/assurance_coverage.py` ROUTE_SURFACES (runtime, 3 commits in 30 days), which can take the `.d/` pattern if it starts serialising PRs.

`scripts/coord/overlap_tests.py` does not map the new registry JSON bases (`tests/write_surface_lock_policy.json`, `tests/route_chat_disposition.json`, `services/not_via_chat.json`, `smoke/family_manifest.json`, `tests/golden_route_surfaces.json`) to their tests; RETRO-PAR-1 owns `scripts/coord/`.

## Triage (2026-11-20, master 87cf52a7)

Checked by code reading only. Still wanted: `harness/catalogue.py` and `chat_tools.py` are the shared files that serialise PRs. This is a design task: the builder sends GK B a plan first (one module per tool or family that registers its schema, routes and dispatcher, discovered at import, with a checked order), then builds. Acceptance: two PRs that each add a tool no longer touch the same file; `scripts/coord/overlap_tests.py` maps the new bases to their tests; the existing catalogue and parity tests pass unchanged. Slice by tool family (PR-SIZE-1). Gated: GK B. Blocked by: none.

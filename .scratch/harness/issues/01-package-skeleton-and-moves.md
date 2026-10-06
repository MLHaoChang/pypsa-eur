# 01 — Harness package skeleton; move the protocol and the catalogue

Status: ready-for-agent (done on `claude/amazing-mendel-m087zw`, 2026-10-05)
Type: task
Blocked by: —

Create `pypsa-gui/backend/harness/` with a README that is the map of the
assistant contract. Move `services/llm_provider.py` → `harness/protocol.py`
and `services/chat_tools_schema.py` → `harness/catalogue.py` as re-export
moves (git mv + shim at the old path). Add `harness/events.py` naming every
frame the turn loop yields, with an AST tripwire test. Add the `workflows/`
and `skills/` registries with their first Markdown definitions and loader
tests.

Done when: the identity tests pass; the chat/llm/tool test files pass
unchanged; the layering grep finds no provider word outside
`harness/providers/`; the hourly-assumption audit pins the moved file at its
new path.

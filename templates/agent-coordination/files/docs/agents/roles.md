# Roles

The one place for who is who. Every skill points here, so replacing a session
changes one line. Keep it current in the PR that replaces a session.

| Role | Session | Notes |
|---|---|---|
| Coordinator | see `docs/agents/coordinator.md` | Opens every other session, relays decisions, does every merge; rotates, so its id lives there |
| Gatekeeper | `<session_…>` | Reviews {{GATED_AREAS}}; strongest model |
| UX reviewer (optional) | `<session_…>` | Writes the project workflow design, then gates every UI PR on coherence |

- **Dashboard:** `<https://claude.ai/artifact/…>`
- **Sweep routine:** `<trig_…>`, every 6 hours, fires into the coordinator session
- **Owner's time zone:** {{TZ_LABEL}} (`{{TZ}}`)
- **Template home:** {{TEMPLATE_HOME}}, `templates/agent-coordination/`. Generic learnings go back there.

# 06 — `start_workflow` / `advance_workflow` tools and the per-turn addendum

Status: ready-for-agent (done 2026-10-05 (backend; the frontend `ui_context.workflow` rebind is a follow-up))
Type: task
Blocked by: 03, 04

`ChatSession.workflow: {id, step} | None`; `start_workflow(id)` sets it and
returns the first step's instructions; `advance_workflow(step)` moves it;
both `Safety: read`, `_service_call_`. `_format_ui_context` appends the
current step's body as a per-turn addendum after `</untrusted_data>`, in the
slot the Guided addendum uses (never the system prompt). `ui_context.workflow`
rebinds state after a reload. The `hub-design` workflow carries the existing
Guided wording; `_guided_mode_addendum` is replaced by the workflow's
step body, with a golden test proving the Guided turn's user content is
byte-identical before and after.

Done when: the Guided tests (`test_guided_mode_prompt.py`,
`test_guided_write_confirmation.py`) pass unchanged; a scripted turn that
starts `build-network` carries its step text; Expert turns without a
workflow are byte-identical.

## Comments

2026-10-05 (owner, Q11/Q13): `hub-design` is offered in Expert too. The
workflow's preamble (the Guided rules) is included only when `ui_mode` is
guided (`preamble_when: [guided]`); the write-tier confirmation stays on
`_confirm_tiers(guided)` as today. An Expert turn inside `hub-design` gets
the step body only.

2026-10-05 (implementation): state is `ChatSession.workflow`, bound to the
tools through a ContextVar beside the turn profile. The backend accepts
`ui_context.workflow = {id, step}` and rebinds a session that has no state;
the frontend does not send it yet (the session id is not persisted across a
reload, so the state is lost with the session). Follow-up: a `workflow_state`
frame after start/advance/end so the panel can show the step and send the
pair back after a reload. The hub-design panel still sends `guided_step`;
reconciling it with the workflow step is part of the same follow-up.

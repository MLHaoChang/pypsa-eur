# 06 — `start_workflow` / `advance_workflow` tools and the per-turn addendum

Status: ready-for-agent
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

# Floating companion launcher

Status: ready-for-agent
Type: task
Blocked by: none

The closed assistant is a floating character with Compose and Speak, as specified
in [the live conversation spec](../spec.md) under Companion launcher. Compose
opens the existing assistant dock and focuses its composer. Speak opens the same
dock and the reviewed dictation panel from PR #106. Neither action starts live
conversation, opens the microphone by itself, or reserves a canvas column.

The open dock, evidence cards and Start conversation control stay in
[issue 03](03-conversation-ui.md). Provider presets beyond the launcher are in
the companion plan, not a new wire.

Done when the collapsed launcher overlays the canvas, Compose and Speak do only
the actions above, reduced motion disables decorative animation, and the active
profile chip opens the existing profile picker. See
[the companion plan](../../../docs/superpowers/plans/2026-10-10-assistant-companion.md).

## Comments

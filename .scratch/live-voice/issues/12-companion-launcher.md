# Floating companion launcher

Status: ready-for-agent
Type: task
Blocked by: none

The closed assistant is a floating character with Compose, Dictate, and Live, as
specified in [the live conversation spec](../spec.md) under Companion launcher.
Compose opens the existing assistant console and focuses its composer. Dictate
opens the same console and the reviewed dictation panel from PR #106. Live is
visible on the companion and its click does not open the console, start the
microphone, or start a paid media session. Connecting that session, lighting
the character, and doing the work on the current page belong to
[issue 03](03-conversation-ui.md).

Provider presets beyond the launcher are in the companion plan, not a new wire.

Done when the collapsed launcher overlays the canvas, Compose and Dictate do
only the actions above, Live leaves the console closed, reduced motion disables
decorative animation, and the active profile chip opens the existing profile
picker. See
[the companion plan](../../../docs/superpowers/plans/2026-10-10-assistant-companion.md).

## Comments

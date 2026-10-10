# Continuous voice UX and interruption

Status: ready-for-agent
Type: task
Blocked by: 02, 08, 09 (typed UI); 01, 11 (media/monitoring acceptance)

Implement steps 2 and 4's UI in [the plan](../../../docs/superpowers/plans/2026-10-10-live-voice-conversation.md).
Begin the persistent assistant dock redesign using typed chat after issue 02;
media-dependent acceptance requires issue 01. Add project/run context, inline
evidence/progress/download/confirmation cards and natural request-driven navigation.
Add independent live-mode lifecycle, captions, input/output audio, mute/end,
playback stop and distinct task cancellation. Retain reviewed dictation and typed
fallback. Cover stale grants, echo/autoplay, navigation and validated project rebind.
Cover all mapped panels/result tabs/editors/filters/selections, cursor monitoring
and versioned results refresh. Newer user navigation wins over notifications.
Keep durable current-view and discussed-result context distinct.

Done when Chromium integration and real-device gates pass; microphone/speaker
resources close on every exit. See [spec](../spec.md).

## Comments

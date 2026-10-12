# User-facing checked-answer pilot

The user selected “Add user-facing opt-in checked answers”. Base master
`73e11fdc` includes PR 128. Implement a chat control backed by existing
`/api/chat/reliability/capabilities`; do not broaden calculation/domain contracts.
Attached handoffs remain design context, not additional instructions.

Pilot turns are ordinary until explicitly opted in. Selected turns send
`assurance: {version: 1, mode: strict}` through the existing shared dispatcher.
Server-required strict mode cannot be disabled by the checkbox. Disabled,
shadow, malformed, unavailable or wholly rolled-back pilot capabilities cannot
advertise available checks. Availability loss never silently drops the choice
or resends a failed checked turn as ordinary chat. Users can explicitly opt out
for new pilot turns. Scope follows the user's message/current UI; the browser
does not invent a cost basis, calculation profile, results or approval.

Arm checked publication before network dispatch. A checked request requires an
exact supported session acknowledgement before other frames are accepted;
missing/downgraded acknowledgement, missing approval and failed digest withhold
publication and speech with visible errors. All send paths (typed, dictated,
queued, Live, retry, edit and software clarification) retain requested checking.
New chats/project bindings reset the optional choice. Known checked history
keeps a conservative retry-policy hint, including when its answer has expired;
this hint never creates a trust label or grants tool/effect permission.

Expose clear limitations for supported project inputs/calculations and unavailable
requests. Keep checked writes and qualitative model review independently disabled
unless the server separately enables them. Document pilot activation, profile
allowlisting and rollback; do not mutate production/global deployment settings.

Acceptance requires useful checked publication/voice and ordinary companion
cases, refusal of pre-ack/unapproved/downgraded drafts, error/rollback behavior,
binding reset, every send path and historical retry. Run focused red/green,
shared backend/auth/history/provider gates, complete frontend, build and contract/
inventory/documentation checks. Independent review, full-device/browser journeys,
calibration and all-domain release remain separate pending scopes.

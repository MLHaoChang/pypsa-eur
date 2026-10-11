# Hosted streaming speech transport and account proof

Status: ready-for-agent
Type: task
Blocked by: 02, 09 (integrated live acceptance; adapter development can start earlier)

Implement step 3 of [the plan](../../../docs/superpowers/plans/2026-10-10-live-voice-conversation.md).
Read [the spec](../spec.md) and harness README first. Add provider-isolated session
and hosted STT/TTS/LiveKit adapters plus actor/CSRF-protected bounded routes.
Follow the hosted architecture decision, not the earlier GPT-Live-first draft.
Keep inference external, server credentials private and cloud-worker bridge tokens
short-lived/owner-scoped. Close failed/abandoned sessions. Prove a short budgeted
WebRTC + actual recognition/synthesis probe and usage before claiming access.

Done when step 3's mocked contracts and bounded live gate have sanitized evidence;
selected transport/API prices must fit the concrete probe budget before running.
Pin bridge controls to the authoritative app worker; prove hosted audio-worker
provisioning/resources. Record exact language/model/device support. Vendor selection
is conditional on measured gates, not an assumed compatibility guarantee.
Initial sizing and current pricing/deprecation findings are in the
[one-user cost assessment](../../../docs/superpowers/assessments/2026-10-10-single-user-voice-cost.md).
Start with Build; test quota accounting and cold starts. Validate a supported
TTS profile/API contract before production; the legacy OpenAI standalone TTS
models are scheduled for retirement on January 6, 2027. No silent model migration.

## Comments

2026-10-11: F-U1 mocked transport — `harness/providers/live_media.py` adds
provider-isolated `SttAdapter`/`TtsAdapter` protocols, a vendor-free
`SpeechProfile`, `CancelToken`, and generic `HttpSttAdapter`/`HttpTtsAdapter`
over an injected httpx client factory (no vendor endpoint/model baked in; no
redirects/retries/fallback; keys never in errors). Contracts in
`tests/test_live_transport_contracts_f_u1.py` (35 passed, `MockTransport` only,
real transport patched to fail): final utterance with exact numbers/units,
partial results refused, empty/malformed/oversize (input, body, header, text,
audio) refused before or after the call, pinned explicit language (auto/invalid
refused; response-language mismatch refused), cancel before and during an
in-flight request. Still open: real vendor adapters, WebRTC/LiveKit, token
routes, and the budgeted live probe with sanitized usage evidence.

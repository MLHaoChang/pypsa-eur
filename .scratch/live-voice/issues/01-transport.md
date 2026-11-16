# Hosted streaming speech transport and account proof

Status: in-progress (live probe pending owner keys, LV01-PROBE-LATER-1)
Progress: Mocked contracts for LiveKit/Deepgram/OpenAI adapters, startup wiring and the closed live-probe gate plus probe script on the claude/live-voice-01a-adapters and claude/live-voice-01b-probe-gate PRs; the live probe has not run (no keys, no approved budget)
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

2026-11-13: live-voice 01 build, probe later (LV01-PROBE-LATER-1). Session,
actor/CSRF routes, bridge tokens and settlement were already on master from
LC-07 (`services/live_speech.py`, `routers/durable_conversation.py`); this
slice adds the vendor adapters behind the `live_media` protocols:
`harness/providers/livekit_media.py` (CreateRoom with 60 s empty / 20 s
departure timeouts and 3 participants max, optional worker dispatch carrying the
bridge token in job metadata, browser grant = join + microphone + subscribe,
no data or admin, at most 900 s; a failed start deletes the room; DeleteRoom
"not found" counts as closed), `deepgram_stt.py` (streaming, pinned model and
language, `numerals=true`, interim captions plus final events, usage from
bytes sent and the service's own Metadata duration), `openai_tts.py` (pinned
model and voice, `/v1/audio/speech` contract, no automatic migration), and
`hosted_speech.py` (env config, `probe_gate`, price estimate, evidence
`sanitize`). `main` installs the transport only when `PYPSA_GUI_LIVE_SPEECH=1`.
The probe script is `pypsa-gui/backend/smoke/live_voice_probe.py`: without
`--run` it prints the gate; with it, it refuses until the gate is open.

Supported, as built and mocked (not yet measured live):
- Languages: pinned per deployment in `PYPSA_GUI_LIVE_LANGUAGES` (primary
  tags; auto-detect and `multi` refused). The probe has exact-number sentences
  for `en` and `de` only.
- STT: Deepgram streaming, model pinned in `PYPSA_GUI_LIVE_STT_MODEL` (no
  default; `nova-3` intended per the cost assessment), 48 kHz mono linear16
  from the worker, containerized WAV in the probe.
- TTS: OpenAI `/v1/audio/speech`, model and voice pinned in
  `PYPSA_GUI_LIVE_TTS_MODEL` / `PYPSA_GUI_LIVE_TTS_VOICE` (no default).
  `tts-1` and `tts-1-hd` are refused from 2027-01-06 (the assessment's
  retirement date). Check `gpt-4o-mini-tts` against OpenAI's deprecation page
  before the probe; a model change is an owner decision.
- Device: not measured. The browser WebRTC join is a manual step the probe
  records as `not_run`.

To run the probe the owner sets `LIVEKIT_URL`, `LIVEKIT_API_KEY`,
`LIVEKIT_API_SECRET`, `DEEPGRAM_API_KEY`, `OPENAI_API_KEY` (or
`PYPSA_GUI_OPENAI_API_KEY`), the three pinned model variables, the languages,
`PYPSA_GUI_LIVE_PROBE_BUDGET_USD` (at most 0.10; the estimate is about $0.025
per 60 s) and `PYPSA_GUI_LIVE_PROBE_APPROVAL` (the decision id). Prices are
taken from the 2026-10-10 assessment, and the gate refuses (`prices_stale`)
once they are 90 days old. Still open: the live probe and its evidence, the
hosted audio worker (it needs the thread id as well as the room in its dispatch
metadata, which `MediaTransport.open_session` does not pass yet), cold-start
and Build-quota measurement, and the browser join.

<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Multilingual assistant dictation

Status: implemented; automated and synthetic-audio checks passed. Human/device
accuracy checks remain open. Branch: `feat/multilingual-dictation`.

## User outcome and scope

Replace English-only dictation with a provider-independent audio service and
reviewable recording flow. Every chat provider consumes the same reviewed text;
neither chat model selection nor tool dispatch changes. Retain browser speech
recognition, with a selectable language, for installations without transcription
credentials. Keep file uploads and engineering tool handlers unchanged.

## Implementation steps

1. Authenticated `/api/dictation/config` and `/api/dictation/transcribe`, a
   neutral service seam, and an isolated transcription adapter. Fixed official
   endpoint and `gpt-4o-mini-transcribe` model; runtime-managed
   `PYPSA_GUI_OPENAI_API_KEY` or existing `OPENAI_API_KEY`. No secrets in the UI.
   Accept bounded WAV/WebM/MP4/MP3 input, validate container signatures, language
   and glossary limits, sanitize provider errors, and bound concurrency/rate.
   No automatic paid retries or chat profile fallback. Audio is not saved to a
   project or chat history.
2. Recorder with microphone selection, level meter, elapsed time, pause/resume
   and finish. A pause closes the microphone and transcribes the current segment;
   resume starts a new segment. Two-minute/10 MB segment limits bound normal use.
   Preview is available after each segment, not token-streamed realtime audio.
   Cancel/unmount/project switch/dock collapse/stream start abort work, release
   tracks and suppress late transcripts. Permission errors remain actionable.
   Completely flat metered input is discarded without a paid request; this is
   a signal check, not a speech classifier. Once submitted, upstream work may
   still finish or bill after client cancellation.
3. Review UI with Auto or manual language, editable domain terminology, editable
   transcript, explicit Insert into composer, and explicit browser fallback.
   Context is limited to user-approved glossary words (PyPSA, GridSpine, MW,
   MWh by default); no project contents are silently sent. Existing caret
   insertion and spoken-response behavior remain. Browser Auto uses navigator
   language and explains it does not perform automatic detection.
4. Backend contract/validation/auth/CSRF/provider-error tests, recorder lifecycle
   and UI integration tests, existing voice/upload regressions, typecheck/build.
   Opt-in real API smoke with a short known spoken fixture if credentials and
   endpoint allow it. Report smoke separately from real multilingual/noisy-audio
   accuracy, which needs representative recordings and human reference text.

## Acceptance gates

- AI Auto omits the API language hint; manual selection sends an ISO language.
- A Claude/local/remote chat can use AI dictation without changing chat profile.
- Permission denial, unsupported recording, absent keys, provider 401/403/429,
  timeout, malformed response, empty/silent audio, oversize audio, cancel and
  late completions cannot silently send text or restart the microphone.
- Recording bytes and transcripts remain temporary; only reviewed inserted text
  joins the draft. Paid calls happen only after explicit recording actions.
- Review preserves the returned words, numerals and units without a rewriting
  model. Recognition of domain values still requires user review.
- Existing browser dictation and tool/upload routes retain behavior.

## Follow-up accuracy matrix

Human recordings: English, German, Mandarin, mixed-language engineering terms;
quiet and office noise; MW versus MWh, decimal values, bus IDs and negations.
Measure word/character error and exact preservation of critical numbers/units.
An artificial voice smoke validates the integration, not this accuracy matrix.
Offline transcription and realtime streaming are future adapters, not shipped
claims in this phase. A packaged WebView needs an actual-device permission test.

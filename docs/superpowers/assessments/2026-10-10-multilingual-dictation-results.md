<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Multilingual dictation implementation and checks

Implemented on `feat/multilingual-dictation`. Design and acceptance gates:
[plan](../plans/2026-10-10-multilingual-dictation.md).

## Delivered behavior

- Voice controls choose AI transcription or browser recognition. AI Auto omits
  the language hint; fourteen manual languages are offered. Browser Auto uses
  `navigator.language`, with an explicit explanation that it does not detect
  language. Missing AI configuration opens setup/fallback choices rather than
  silently changing engines.
- Microphone selection, level meter, elapsed time, pause/transcribe, resume as
  another segment, editable terminology and editable review. Only explicit
  Insert adds text to the draft; ordinary Send starts the chat. Preview appears
  after each paused segment, not through a realtime transcription socket.
- Project switch, dock collapse, stream start, Escape, hidden page, discard and
  unmount close tracks and invalidate late callbacks/results. Flat metered audio
  is rejected locally. Retry is manual; no automatic paid attempts.
- Session/CSRF-protected temporary transcription endpoint with 10 MB server byte
  cap, a two-minute client segment cap, container checks, bounded vocabulary,
  one concurrent request/user, four globally and eight attempts/minute/user.
  Provider failures are sanitized; a provider key failure does not sign out the
  application user. Uploaded audio is closed and not retained in project uploads.
- Fixed `gpt-4o-mini-transcribe` adapter reads `PYPSA_GUI_OPENAI_API_KEY` or the
  existing `OPENAI_API_KEY`. It sends no project files or chat history. Chat
  provider/model selection and engineering tool handlers are independent.

## Verified

- Full frontend suite: 3,466 tests passed across 297 files. A subsequent focused
  check covers the final pending-permission stop-button correction.
- Final focused frontend suite: 66 tests passed across nine files. Includes
  cancellation while permission is pending, stale result suppression, recording
  byte/time limits, permission/device errors, metering cleanup, flat input,
  language selection, retries, pause/resume, review and composer insertion.
- Backend regressions: 207 passed, two skipped (opt-in tests), including dictation,
  harness layering, existing uploads/traversal guards, streamed frames, catalogue
  parity and tool execution/continuation across the existing providers.
  Final dictation/harness-layering check: 62 passed, one paid-test opt-in skip.
- Production TypeScript and Vite build passed. Existing large-chunk/dynamic-import
  notices remain; no new build errors.
- Ruff lint and format checks passed for all five new Python modules.
- Real Chromium with a simulated microphone passed: actual WebM MediaRecorder,
  signal meter, multipart manual-language request, review before insertion,
  hardware-track cleanup and no transcription on dock cancellation. Transcription
  responses were mocked in this browser probe. Reusable script:
  `pypsa-gui/frontend/scripts/smoke-dictation.mjs`.
- Actual ChatPanel integration passed with the Claude profile: reviewed text
  entered the composer, no stream started before Send, and the ordinary request
  retained `profile_id=anthropic-sonnet` and `input_mode=voice`.
- One live OpenAI call passed through the real authenticated multipart route:
  `tests/test_dictation_live.py`, 5.93 seconds of synthetic English audio. Returned:
  “Create a project with 7.5 MW of load, do not use 75 MW.” Decimal value, units
  and negation passed assertions. This is an integration smoke, not an accuracy
  certificate for real accents, languages or noise.

## Reproduce

Generate the synthetic fixture (ffmpeg must include the flite filter):

```sh
ffmpeg -hide_banner -loglevel error -f lavfi \
  -i "flite=text='Create a project with seven point five megawatts of load. Do not use seventy five megawatts.':voice=slt" \
  -ar 16000 -ac 1 /tmp/dictation-live-english.wav -y
```

From the frontend directory, with Playwright and Chromium available:

```sh
PYPSA_GUI_DICTATION_AUDIO=/tmp/dictation-live-english.wav \
  CHROMIUM_BIN=/usr/bin/chromium node scripts/smoke-dictation.mjs
```

The browser script creates and removes an isolated temporary page and server.
It makes no paid calls and reports the location of synthetic audio/screenshots.

From the backend directory, with configured credentials and the existing
authorized run ledger (preserve it; do not reset accumulated spend):

```sh
PYPSA_GUI_LIVE_DICTATION=1 \
  PYPSA_GUI_DICTATION_AUDIO=/tmp/dictation-live-english.wav \
  PYPSA_GUI_DICTATION_LEDGER=/tmp/openai-comprehensive.json \
  python -m pytest -o addopts='' -q -rs tests/test_dictation_live.py
```

The paid smoke allows one <=10-second fixture call, retains a conservative $0.01
reservation in the preceding $5-cap ledger and skips matching completed cases.
Pricing checked 2026-10-10 at https://developers.openai.com/api/docs/pricing:
`gpt-4o-mini-transcribe` estimated $0.003/minute. The $0.01 reservation is not an
observed charge. Cumulative conservative ledger after this call: 699 requests,
$1.382893; platform remaining balance is not queried.

## Remaining validation and scope

Human English/German/Mandarin and mixed-language recordings, accents, background
noise, bus IDs and exact MW/MWh/decimal/negation scoring need reference transcripts.
Packaged macOS/Windows WebView permission and device behavior need actual devices;
Chromium simulation cannot establish those. Offline transcription and realtime
streamed preview are future adapters. Meter-less browsers cannot reject flat
audio locally. Cancelling client work cannot guarantee an already submitted
upstream request stops processing or billing. Always review engineering values.

The previous failed environment was untouched. No provider/tool registry or
simulation handler changed. No microphone recording or key was committed.

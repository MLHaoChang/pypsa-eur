# Chat Voice-to-Text (Web Speech API) — Design Spec

**Status:** Draft for review  
**Date:** 2026-07-26  
**Product:** `pypsa-gui` chat assistant (`ChatPanel`)  
**Owner:** hao (PM) / agent implementation  

## 1. Goal

Let the user dictate **English** speech into the chat composer so the existing assistant can process it as normal typed text. Voice never bypasses Send; the agent loop and tools stay unchanged.

## 2. Locked decisions

| # | Decision | Choice |
|---|---|---|
| D1 | STT engine | Browser **Web Speech API** (`SpeechRecognition` / `webkitSpeechRecognition`) |
| D2 | After recognition | **Fill composer only** — no auto-send |
| D3 | Mic interaction | **Toggle** — click to start, click again or Esc to stop |
| D4 | Text placement | **Insert at cursor** (replace current selection if any) |
| D5 | Language (v1) | Fixed `en-US` |
| D6 | Backend | **None** — no audio upload, no new API key, no FastAPI route |
| D7 | Scope | Chat composer in `ChatPanel` only |

## 3. Non-goals (v1)

- Other languages / locale picker  
- Whisper / cloud STT fallback  
- Auto-send on final transcript  
- Push-to-talk (hold)  
- Persisting or uploading audio  
- Voice output (TTS)  
- Orchestrator / Analyze-mode special casing  

## 4. User experience

### 4.1 Controls

- Add a **mic** control in the composer toolbar column next to the existing attach (📎) and paste (📋) buttons in `ChatPanel.tsx`.
- States:
  - **Unsupported** — button disabled; `title` / tooltip: voice input needs Chrome or Edge (or Safari where available).
  - **Idle** — click starts listening (browser may prompt for microphone permission).
  - **Listening** — accent / active styling; click or Esc stops.
  - **Error** — brief toast + return to idle (permission denied, `no-speech`, `audio-capture`, `network`, aborted).
- Touch targets: reuse `useIsCoarsePointer` 44×44 px pattern already used for attach/paste.

### 4.2 Composer behaviour

- `continuous = true`, `interimResults = true`.
- **Interim** results: show a non-destructive preview (e.g. muted line under the textarea or inline ghost text). Do **not** commit interim strings into `input` state (avoids cursor thrash and broken undo).
- **Final** results: insert into the controlled `input` string at `selectionStart`/`selectionEnd` via a pure helper; restore focus and caret after the inserted span.
- If the composer already has text, insertion never wipes unrelated content outside the selection.
- Listening does **not** disable typing; user may edit while idle. While listening, prefer keeping focus on the textarea after each final insert.
- Send, Shift+Enter, attachments, and streaming guards behave exactly as today. Mic should be disabled while `streaming` is true (same as attach).

### 4.3 Lifecycle stop conditions

Stop recognition (best-effort `stop()` / `abort()`) when:

- User toggles mic off  
- User presses Esc (while chat panel focused / listening)  
- Chat panel closes / unmounts  
- `currentProject` switches (`resetForProjectSwitch` path)  
- Component unmount  

Do not leave the mic hot across project switches.

## 5. Architecture

```
[Mic toggle] → useSpeechToText
                 ├─ feature detect
                 ├─ SpeechRecognition (en-US, continuous, interim)
                 └─ onFinal(text) → insertAtCursor(value, selStart, selEnd, text)
                                      → setInput(next) + restore selection

[Send] → existing chatStore /api/chat/stream path (unchanged)
```

No changes to `chat_service.run_turn`, tool dispatch, or Anthropic client setup.

### 5.1 Files (planned)

| File | Role |
|---|---|
| `frontend/src/utils/speechToText.ts` | Pure: feature detect helper types, `insertAtCursor`, result text extraction |
| `frontend/src/utils/speechToText.test.ts` | Vitest for insert/detect helpers |
| `frontend/src/hooks/useSpeechToText.ts` | Recognition lifecycle, listening flag, interim preview, error mapping |
| `frontend/src/components/ChatPanel.tsx` | Mic button + wire hook to `input` / `textareaRef` |
| `pypsa-gui/CHATBOT.md` | Short “Voice input” setup note (browser support) |

### 5.2 Browser support

- Construct via `window.SpeechRecognition || window.webkitSpeechRecognition`.
- If missing: unsupported UI (no runtime throw).
- Primary targets: Chromium (Chrome / Edge). Safari support is best-effort if the constructor exists.
- Firefox: typically unsupported → disabled mic + tooltip.

Permission denial is a normal error path (toast), not a crash.

## 6. Security & privacy

- Audio is processed by the **browser / OS speech service** (Chromium may use a cloud speech backend depending on browser settings). Document this honestly in `CHATBOT.md`.
- No audio bytes are sent to the pypsa-gui FastAPI process.
- Transcript text is ordinary composer content and follows existing chat persistence / redaction rules once the user sends.
- Treat dictated text like typed text (untrusted user content) — no new trust boundary beyond existing chat.

## 7. Accessibility

- Mic button: `aria-label` toggles between “Start voice input” / “Stop voice input”; `aria-pressed` when listening.
- Announce errors via existing toast path.
- Do not rely on colour alone for listening state (icon change and/or `aria-pressed`).

## 8. Testing

- **Unit (Vitest, node env):** `insertAtCursor` cases — empty, middle, replace selection, emoji/surrogate-safe index via string slice; feature-detect helper with mocked `window`.
- **Manual:** Chrome — permission grant/deny, interim→final, insert mid-sentence, Esc stop, project switch stops mic, Send after dictate.
- No pytest / backend tests (no server surface).

## 9. Rollout

1. Land helpers + hook behind the mic control (default on when API present).  
2. No feature flag required for local single-user GUI; unsupported browsers simply hide capability via disabled control.  
3. Follow-up (not v1): locale picker, push-to-talk, Whisper fallback.

## 10. Acceptance criteria

- [ ] On Chromium with mic permission, toggle listens and final English phrases appear at the caret in the composer.  
- [ ] Interim text does not permanently corrupt the draft.  
- [ ] User must still press Send to invoke the assistant.  
- [ ] Unsupported browsers show a disabled mic with an explanatory tooltip.  
- [ ] Esc / toggle-off / project switch / unmount stop recognition.  
- [ ] Vitest coverage for `insertAtCursor` (+ detect helper).  
- [ ] `CHATBOT.md` documents browser requirement and privacy note.  

## 11. Spec self-review

- No placeholders (`TODO` / `TBD` none).  
- Consistent with locked decisions D1–D7.  
- No backend scope creep.  
- Ambiguity resolved: interim = preview only; finals = insert at selection.  
- Single subsystem (chat composer STT) — no split needed.  

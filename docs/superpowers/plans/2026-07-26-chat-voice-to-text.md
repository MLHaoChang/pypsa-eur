# Chat Voice-to-Text (Web Speech) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) or subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add English Web Speech mic toggle to the chat composer that inserts final transcripts at the caret without auto-sending.

**Architecture:** Pure helpers (`insertAtCursor`, feature detect, result parse) + a small session controller + `useSpeechToText` hook wired into `ChatPanel`. No backend.

**Tech Stack:** React 19, Vitest (node), Web Speech API (`SpeechRecognition` / `webkitSpeechRecognition`).

**Spec:** `docs/superpowers/specs/2026-07-26-chat-voice-to-text-design.md`

## Global Constraints

- Frontend-only; no FastAPI / audio upload.
- `lang=en-US`, continuous + interim; finals commit, interim preview only.
- Toggle mic; Esc / project switch / unmount stop listening.
- Mic disabled while `streaming` or API unsupported.
- One commit per completed task.

---

### Task 1: Pure helpers + Vitest

**Files:**
- Create: `pypsa-gui/frontend/src/utils/speechToText.ts`
- Create: `pypsa-gui/frontend/src/utils/speechToText.test.ts`

**Produces:** `insertAtCursor`, `getSpeechRecognitionCtor`, `parseSpeechResults`, `speechErrorMessage`

- [ ] Write failing tests for insert + parse + error map
- [ ] Implement helpers
- [ ] Run `npm test -- --run src/utils/speechToText.test.ts` — PASS
- [ ] Commit

### Task 2: Speech session controller + hook

**Files:**
- Create: `pypsa-gui/frontend/src/hooks/useSpeechToText.ts`
- Create: `pypsa-gui/frontend/src/hooks/useSpeechToText.test.ts` (controller logic via injectable mock recognition)

**Produces:** `useSpeechToText({ enabled, onFinal, onError })` → `{ supported, listening, interim, toggle, stop }`

- [ ] Write failing tests with mock `SpeechRecognition`
- [ ] Implement controller + hook
- [ ] Run Vitest — PASS
- [ ] Commit

### Task 3: ChatPanel mic + CHATBOT.md

**Files:**
- Modify: `pypsa-gui/frontend/src/components/ChatPanel.tsx`
- Modify: `pypsa-gui/CHATBOT.md`

- [ ] Wire mic button, interim preview, Esc/project-switch stop, insert-at-cursor
- [ ] Document Voice input in CHATBOT.md
- [ ] `tsc -b` + Vitest suite for speech files — PASS
- [ ] Commit

### Task 4: Manual e2e checklist (Chrome)

- [ ] Mic unsupported path (mock) already covered by unit tests
- [ ] Smoke: frontend builds; document manual Chrome mic grant/deny for operator

---

## Spec coverage

| Spec item | Task |
|---|---|
| Web Speech, en-US, toggle, fill only | 2–3 |
| Insert at cursor | 1, 3 |
| Interim preview only | 2–3 |
| Stop on Esc / project switch / unmount | 3 |
| Unsupported tooltip | 3 |
| Vitest insertAtCursor | 1 |
| CHATBOT.md privacy/browser | 3 |

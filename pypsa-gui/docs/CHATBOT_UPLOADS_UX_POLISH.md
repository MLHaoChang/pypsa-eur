# Chat panel — Phase D UX polish

Plan for the 5 deferred UX items from the post-Phase-D 3-agent QA pass.

Scope is **UX-only** — backend contracts stay frozen. Total effort ≈ 0.75 day
if shipped sequentially in the suggested order; 0.5 day with the small-batches
order (B → D → E → A → C) since A and C share the empty-state primer
infrastructure.

---

## 1. Empty-state priming when no project loaded

### Problem
On a fresh tab with no project loaded, the chat panel renders blank + grayed-out
upload buttons. Tooltip says "Load a project first" but only on hover. New users
have no path forward.

### Behaviour
- Render an **empty-state block** ABOVE the prompt area when `currentProject == null` AND `messages.length === 0`:

  ```
  ┌──────────────────────────────────────────┐
  │  💬 Load a project to start chatting     │
  │                                          │
  │  The assistant works on the network in   │
  │  the active project — uploads, exports,  │
  │  and chat history all attach to it.      │
  │                                          │
  │  [📁 Open project]   [➕ New project]    │
  └──────────────────────────────────────────┘
  ```

- `[📁 Open project]` → dispatches a UI event that focuses the existing
  `ProjectTabs` dropdown (we have `uiStore.setSlidePanel` and the project picker
  already exists in the sidebar). Use the same store action `ProjectTabs.tsx`
  uses internally so this stays a thin redirect, not a duplicate picker.
- `[➕ New project]` → opens `NewProjectWizard` modal (already mounted via
  `App.tsx` — toggle its visibility via `uiStore.setShowNewProjectWizard(true)`,
  whatever the existing action is named).
- Block disappears the moment a project becomes active OR the first message lands.

### Files
| File | Change |
|---|---|
| `frontend/src/components/ChatPanel.tsx` | New subcomponent `ChatEmptyState`; render conditionally between `<ErrorBanner />` and the messages container |
| `frontend/src/store/uiStore.ts` | Verify which action opens NewProjectWizard; expose via `useUIStore` selector |
| `frontend/src/layout/Sidebar.tsx` | Verify it exposes a focus-able tab dropdown via store, OR add a `focusProjectPicker()` store action used by both Sidebar and ChatPanel |

### Tests
- Manual: chat panel on fresh tab shows the block; click both buttons → each
  opens the right modal.
- Vitest (component test using `@testing-library/react`) — but the frontend has
  no vitest harness; defer test until that's set up. Cover via the
  e2e_chat_uploads.sh walkthrough once Phase D's e2e exists.

### Effort
~1.5 h.

### Risks
- Two store actions may already exist for these flows but be named oddly; ~20 min
  of audit needed before coding.

---

## 2. Multi-file upload progress toast with per-file rows

### Problem
Today's `handleUploadFiles` iterates the FileList and fires `toast.success` /
`toast.error` per file. Dragging 5 files → 5 separate toasts stack up and the
user loses track of overall progress.

### Behaviour
- Single sticky toast for the batch with per-row progress:

  ```
  ┌────────────────────────────────────────┐
  │ Uploading 5 files                      │
  │ ━━━━━━━━━━━━━━━━━━━━━━━━━━  3/5       │
  │  ✓ demand.xlsx          1.2 MB         │
  │  ✓ forecast.pdf         8.4 MB         │
  │  … solar.png            ↑12%           │
  │  ⌛ wind.png            queued          │
  │  ⌛ topology.png        queued          │
  └────────────────────────────────────────┘
  ```

- On batch start (`handleUploadFiles` with > 1 file), call
  `toast.custom(<UploadProgressToast …/>, { duration: Infinity, id: 'upload-batch-<ts>' })`.
- The custom toast renders from a `useUploadProgress` hook backed by a Zustand
  slice (`uploadProgress: { batchId, rows: { fileId|name, status, sizeBytes, errorKind? } }`).
- Each `uploadFile` call updates its row's status (`queued → uploading → ok | failed`).
- Auto-dismiss after batch settle + 3 s OR on explicit `[×]` click.
- **Single-file uploads keep the existing simple toast** (don't pay batch
  overhead for one file).

### Files
| File | Change |
|---|---|
| `frontend/src/store/chatStore.ts` | New slice `uploadBatches: Record<batchId, BatchProgress>`; actions `beginBatch(batchId, fileNames)`, `markRow(batchId, name, status, error?)`, `endBatch(batchId)` |
| `frontend/src/components/UploadProgressToast.tsx` | NEW — receives batchId, subscribes to slice; renders rows + overall bar |
| `frontend/src/components/ChatPanel.tsx` | `handleUploadFiles` wraps the loop in a batch when `files.length > 1`; calls store actions; mounts the toast once via `toast.custom` |

### Tests
- Manual: drag 5 files, verify single toast with rows.
- Verify failure case: file #3 fails → row gets red `✗` + `error_kind`, batch
  continues with #4 and #5.

### Effort
~2 h (custom toast component + state plumbing).

### Risks
- `react-hot-toast`'s `toast.custom` re-renders on every state update — make
  the toast component a controlled selector subscription so the whole toast
  tree re-renders cheaply.
- `XMLHttpRequest` (or `fetch` + a body progress stream) is needed for true
  per-file upload progress; fall back to "indeterminate" (animated bar) per
  row if streaming progress is too painful — the on/off indicator is the
  important UX win, not the percentage.

---

## 3. Default-OFF attach vs Default-ON — DECISION + tweaks

### Problem
The UX agent recommended switching newly-uploaded chips to "default-OFF"
(unchecked) so a user uploading 3 PDFs to review doesn't accidentally send all
three on their next message. The current default-ON matches the locked plan's
"sticky chips" intent but the visual signal (50 % opacity + checkbox) is a weak
deterrent.

### Recommendation: **KEEP default-ON, sharpen the signals**

Reasoning:
- The primary chatbot use case is "upload demand.xlsx, ask the agent to apply
  it" — default-ON matches that one-step intent (no extra checkbox-click before
  sending).
- For "uploaded for reference, don't send" the user already has to uncheck —
  sharpening the signal is cheaper than retraining the muscle memory.
- Default-OFF inverts the "exports stay attached" intent for agent_export
  chips in particular: an export the agent just made should remain attached for
  follow-up references (e.g. "what's wrong with the file you just made?").

### Behaviour changes (sharpen the signals)
- **Pre-send confirmation chip in the Send button**: instead of
  `Send (📎3)`, render the per-attachment filenames inline:

  ```
  Send  ▸  📎 demand.xlsx · forecast.pdf · 1 more
  ```

  Truncate to 2 names + " · N more". Hover the send button → tooltip lists
  every attached filename.

- **First-send acknowledgement**: track `localStorage[chat:firstSendAck]`. On
  the FIRST send with ≥ 1 attached file, surface a confirm modal:

  ```
  About to send "what does this show?" with 3 files attached.
  [Send with attachments]   [Send without files]   [Cancel]
  ```

  Setting `localStorage[chat:firstSendAck] = "1"` skips the modal on every
  future send. Same modal is bypassed when the user actively unchecks chips.

- **Auto-uncheck after send (opt-in)**: Add a header gear with a single
  setting "Auto-uncheck after send" stored in localStorage. Off by default
  (matches sticky-chip intent); on for users who repeatedly send unrelated
  follow-ups and don't want chips clinging.

### Files
| File | Change |
|---|---|
| `frontend/src/components/ChatPanel.tsx` | Expand `Send (📎N)` to filename list; add first-send modal (small dialog component); read `localStorage[chat:firstSendAck]` and `chat:autoUncheckAfterSend` |
| `frontend/src/store/chatStore.ts` | (optional) tiny action `markFirstSendAcknowledged()` |

### Tests
- Manual: fresh session, upload one file, type a message, click Send → modal
  appears. Click "Send with attachments" → message sends; second send doesn't
  modal.

### Effort
~1 h.

### Risks
- The first-send modal adds friction; A/B in a follow-up if we want hard data,
  but the localStorage flag means a returning user pays the cost exactly once.

---

## 4. PDF page truncation indicator in chip strip

### Problem
A user uploads a 250-page PDF. Backend records
`meta.truncated_to_100_pages = true` AND
`meta.page_count = 250`. The chat panel renders a normal chip with no
indication that only pages 1-100 will reach the agent. Surprise on the agent's
response: "the appendix is fine" when the appendix is on page 240.

### Behaviour
- The `UploadMetaUI` slice already carries `page_count` from
  `read_upload_meta`; extend it to also carry `truncated_to_100_pages`.
- In `UploadChipStrip`, when `u.mime === 'application/pdf' && u.truncated_to_100_pages === true`:

  ```
  📄 thick.pdf  8.4 MB  ⚠ 100/250 pgs
  ```

  - Amber `⚠` icon with title
    `"Only the first 100 pages will be attached to the next message. The full file is still on disk and can be downloaded."`
  - Single-line layout — no extra modal or banner.
- Optional: any non-truncated PDF with `page_count > 50` gets a soft hint:
  `📄 medium.pdf  5.0 MB · 80 pgs` — gives the user advance warning they're
  close to the cap.

### Files
| File | Change |
|---|---|
| `frontend/src/store/chatStore.ts` | `UploadMetaUI` adds `page_count?: number; truncated_to_100_pages?: boolean` |
| `frontend/src/components/ChatPanel.tsx` | Map these from API responses (`listUploads`, `uploadFile`); render badge in `UploadChipStrip` |
| `frontend/src/api/uploads.ts` | Already returns them on `UploadMeta` — no change |

### Tests
- Existing backend test `test_pdf_page_count.test_large_pdf_flags_truncated`
  verifies the metadata is correct; this is purely a render change. Manual
  verify: upload a 120-page PDF, see the `⚠ 100/120 pgs` badge.

### Effort
~30 min.

### Risks
- None — purely additive.

---

## 5. Touch-device fallback hint for drag-drop overlay

### Problem
On iPad / Android tablet, drag-drop doesn't work natively. A user tries to drag
a file from Files app, nothing happens. The drag-drop overlay never triggers
because there's no `dragenter` event from the OS file picker.

### Behaviour
- Detect coarse pointer via `window.matchMedia('(pointer: coarse)').matches`
  (already documented in the plan and used in other parts of the codebase per
  the `BottomPanel` touch handling).
- When `coarse === true`:
  - Hide the drag-drop overlay logic entirely (never `setDragActive(true)` even
    on accidental synthetic drag events).
  - Add a one-time **bottom-of-prompt hint** on first chat-panel open:

    ```
    Tip: tap 📎 to pick a file or 📋 to paste from clipboard
    ```

  - Dismiss after 5 s OR on first interaction with the prompt; persist
    `localStorage[chat:touchHintShown] = "1"`.
  - The 📎 file picker button is enlarged to 44 × 44 px on coarse pointers
    (meets WCAG 2.5.5 minimum touch target). Tailwind class swap:
    `coarse:px-3 coarse:py-3` via a small `useIsCoarsePointer()` hook.

### Files
| File | Change |
|---|---|
| `frontend/src/components/ChatPanel.tsx` | Wrap `onDragEnter` / `onDragOver` / `onDragLeave` / `onDrop` in a `if (!isCoarsePointer)` short-circuit; render the touch hint on first mount when coarse; conditional sizing on the 📎 + 📋 buttons |
| `frontend/src/hooks/useIsCoarsePointer.ts` | NEW small hook returning `matchMedia('(pointer: coarse)').matches` reactively |

### Tests
- Manual: emulate touch device in DevTools, verify drag-drop is silently
  disabled and the hint appears once.
- Verify `localStorage[chat:touchHintShown]` is set after dismissal — second
  visit doesn't re-show.

### Effort
~45 min.

### Risks
- DevTools touch emulation doesn't always flip `pointer: coarse`; physical
  device verification needed.

---

## Suggested implementation order

| # | Item | Effort | Reason for ordering |
|---|---|---|---|
| 1 | **#4 PDF truncation badge** | 30 min | Tiny, additive, no design risk — get this win out of the way |
| 2 | **#5 Touch hint** | 45 min | Self-contained; touches a single block of code |
| 3 | **#1 Empty-state primer** | 1.5 h | Reuses the existing project-picker action audit |
| 4 | **#3 Send-button polish + first-send modal** | 1 h | Builds on the empty-state pattern; same modal infrastructure |
| 5 | **#2 Multi-file progress toast** | 2 h | Largest; ship last with focused testing |

**Total: ~6 h** (0.75 day with verification + manual browser checks).

### Cross-cutting

- **localStorage keys** added (snake-cased, namespaced under `chat:`):
  `chat:firstSendAck`, `chat:autoUncheckAfterSend`, `chat:touchHintShown`.
  All cleared on a future "Reset chat preferences" gear option.
- **No backend changes needed.** Phase D backend contracts cover everything.
- **No new dependencies.** All UI built from existing primitives (`react-hot-toast`,
  Zustand, Tailwind).

---

## Open questions before implementation

1. **Item #1 — Sidebar focus action**: does `uiStore` already expose
   `setShowNewProjectWizard` and a way to focus the project tabs picker? If
   not, do we add them under `uiStore` or under a thin `chatUiActions.ts`
   shim?
2. **Item #2 — true vs indeterminate per-file progress**: I recommend
   indeterminate (animated bar per row) to keep the work tight. Confirm vs.
   per-byte progress (which needs `XMLHttpRequest` + custom progress events).
3. **Item #3 — auto-uncheck-after-send setting placement**: header gear
   (clean) vs. inline next to Send button (discoverable). I recommend a
   small `[⚙]` next to the cost meter.
4. **Item #5 — coarse-pointer touch target sizing**: bump 📎 + 📋 to
   44 × 44 px, or leave them at desktop size and rely on the hint to
   educate? I recommend 44 × 44 px for WCAG compliance on tablets.

Answer those 4 and I can start shipping in the order above.

/**
 * Phase 3 chatbot integration v6 — chatStore.
 *
 * Per-project conversation UI state (M6 location: src/store/, NOT src/stores/
 * which doesn't exist). Tracks the active session id, the visible message
 * list, the live pending-confirmation card (only one at a time), the per-
 * session usage accumulator (M10 — raw token counts only; there is no cost
 * figure, derived or otherwise), and an abort_event proxy.
 *
 * Project-switch reset: `resetForProjectSwitch()` clears every UI-side
 * field; the backend ProjectContext carries `chat_state` per project so
 * the on-disk chat.jsonl history is preserved separately.
 */
import { create } from 'zustand'

export type ChatRole = 'user' | 'assistant' | 'tool' | 'system'

export interface ChatMessage {
  id: string
  role: ChatRole
  // Plain text for user / assistant; for tool messages the formatted
  // single-line summary of the call.
  content: string
  // For tool-related messages — references back to the tool_use that
  // produced this row so confirmation cards / progress can attach.
  tool_use_id?: string
  tool_name?: string
  // Phase D — file_ids that were attached to this user turn (for replay).
  // Read-only chip strip renders below the message bubble.
  attachment_file_ids?: string[]
  // Task 13 — accumulated `thinking` SSE deltas for this assistant turn.
  // Rendered as a collapsible block above the answer; absent (not empty
  // string) on every turn the model didn't emit extended thinking for, so
  // the UI can gate the `<details>` on presence rather than length.
  thinking?: string
  ts: number
}

export interface PendingConfirmationCard {
  tool_use_id: string
  tool_name: string
  args: Record<string, unknown>
  safety_tier: string
  confirmation_token: string
  ttl_seconds: number
  expires_at_epoch_ms: number
}

export interface ChatUsageAcc {
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_create_tokens: number
}

export interface ChatErrorState {
  // The agent emitted an error frame — display in the panel header.
  error_kind: string
  message: string
}

// The EUR cost estimate lived here and was deleted deliberately: it derived
// from a hardcoded price table and a hardcoded USD_PER_EUR, both of which go
// stale silently and cannot be caught by a test. The meter now shows token
// counts, which are exact. Do not reintroduce a price table without a live
// rate source.

interface ChatState {
  // Identity
  sessionId: string | null
  // Task 13 — which configured profile the NEXT stream request should bind
  // to. `null` means "the server's active profile" and is the default: the
  // dropdown always shows a real profile (falling back to
  // `active_profile_id` from the profiles fetch for display), but the STORE
  // stays null until the user actually picks one, so a request never
  // re-asserts a stale choice over an admin's `set_active_profile` or an A8
  // fallback. See `ChatStreamRequest.profile_id` in `api/chat.ts`.
  profileId: string | null
  // Task 13 — one-shot flag set by `startNewChat`. The chat.jsonl hydration
  // effect in ChatPanel consumes (reads + clears) it via
  // `consumeSuppressHydrationOnce` before deciding whether to replay
  // history — without this, clearing `sessionId` and `messages` for a fresh
  // chat is immediately undone by the effect re-hydrating the OLD
  // `last_session_id` the next time it runs (it is keyed on `sessionId`
  // precisely so a project's real hydration-on-mount/switch still works).
  suppressHydrationOnce: boolean
  // Conversation
  messages: ChatMessage[]
  // The single pending confirmation card. The runtime allows ONE pending
  // card at a time (M7 — parallel destructives are rejected at the agent
  // layer, so the UI never has to handle two).
  pending: PendingConfirmationCard | null
  // Live tool-progress (the latest tool_progress payload per tool_use_id).
  toolProgress: Record<string, { kind: string; line: string }[]>
  // Usage (token count) meter
  usage: ChatUsageAcc
  // Stream state
  streaming: boolean
  error: ChatErrorState | null
  // The cleanup function returned by createChatStream. Set when a stream
  // is open; cleared (and called) on abort/close/project switch.
  streamCleanup: (() => void) | null
  // Phase A — chatbot uploads. Per-project upload registry, kept in sync
  // with the backend's `/api/projects/{name}/uploads` list. The slice is
  // populated by ChatPanel.tsx's hydration effect on project mount + by
  // the `uploadFile` mutation success handler. Cleared on project switch
  // (reset_for_project_switch wipes the slice).
  uploads: UploadMetaUI[]
  // Phase D — file_ids whose "attach to next message" checkbox is ON.
  // Default-ON for every newly-added upload (uploads slice's addUpload
  // also marks the file as attached). User unchecks a chip to skip it
  // on the next send. Persists across turns; cleared on project switch.
  attachedFileIds: string[]
  // Phase D — counter of agent-exported files the user has NOT yet
  // acknowledged (clicked / previewed / downloaded). Renders as a
  // header badge "Exports: N"; decrements on chip interaction.
  unseenExportCount: number
  // Phase D polish #2 — multi-file upload progress batches. Keyed by
  // `batchId` so concurrent batches don't collide. Single-file uploads
  // use the simple `toast.success` / `toast.error` path; only multi-file
  // batches surface a sticky per-row toast.
  uploadBatches: Record<string, UploadBatch>

  // Actions
  setSessionId: (id: string | null) => void
  setProfileId: (id: string | null) => void
  appendMessage: (msg: Omit<ChatMessage, 'id' | 'ts'>) => void
  /**
   * Append a streaming token delta. If the last message in the list is an
   * assistant bubble, the delta is concatenated into its content; otherwise
   * a fresh assistant message is created. Keeps `token` SSE frames from
   * producing one-row-per-token spam in the message list.
   */
  appendTokenDelta: (delta: string) => void
  /**
   * Same accumulation shape as `appendTokenDelta`, but writes the trailing
   * assistant bubble's `thinking` field instead of `content` — `thinking`
   * SSE frames stream separately from `token` frames and render in their own
   * collapsible block.
   */
  appendThinkingDelta: (delta: string) => void
  /**
   * Replace the entire message list (used by the mount-time chat.jsonl
   * replay so a reload doesn't lose the conversation).
   */
  setMessages: (msgs: ChatMessage[]) => void
  setPending: (c: PendingConfirmationCard | null) => void
  appendToolProgress: (toolUseId: string, frame: { kind: string; line: string }) => void
  accrueUsage: (delta: Partial<ChatUsageAcc>) => void
  setStreaming: (v: boolean) => void
  setError: (e: ChatErrorState | null) => void
  setStreamCleanup: (fn: (() => void) | null) => void
  /**
   * End the current turn: close the SSE and clear the streaming flag.
   *
   * The connection belongs to the TURN, not to whichever component happened
   * to open it. ChatPanel used to be the only thing that closed it, from its
   * unmount handler — and back when ChatPanel was the `'chat'` SlidePanel it
   * unmounted itself whenever the agent sent a `ui_event` navigating to
   * another panel, which killed the turn mid-answer.
   *
   * That trigger is gone: ChatPanel now lives in AssistantDock and navigation
   * does not unmount it. The ownership rule stands regardless, because the
   * remaining unmount paths (ErrorBoundary fallback after a render crash,
   * project switch, HMR) can still land mid-turn, and none of them should
   * decide a turn is over. Terminal frames call this instead; ChatPanel's
   * unmount handler closes only an idle stream.
   */
  closeStream: () => void
  // Upload slice actions
  setUploads: (list: UploadMetaUI[]) => void
  addUpload: (meta: UploadMetaUI) => void
  removeUpload: (fileId: string) => void
  // Phase D polish #2 — batch progress actions.
  beginUploadBatch: (batchId: string, files: Array<{ name: string; size: number }>) => void
  markUploadRow: (
    batchId: string,
    rowId: string,
    patch: Partial<UploadBatchRow>,
  ) => void
  endUploadBatch: (batchId: string) => void
  // Phase D — attach-state actions
  setAttachedFileIds: (list: string[]) => void
  toggleAttached: (fileId: string) => void
  // Export-badge actions
  bumpUnseenExport: () => void
  acknowledgeExport: (fileId: string) => void
  clearUnseenExports: () => void

  // Lifecycle: clear UI-side conversation state on a project switch.
  resetForProjectSwitch: () => void
  /**
   * Task 13 — begin a fresh conversation WITHOUT a project switch (the
   * cross-wire profile-switch confirm, and any future explicit "New chat"
   * affordance). Nulls `sessionId` and clears the visible conversation, same
   * as `resetForProjectSwitch`'s conversation half, but additionally arms
   * `suppressHydrationOnce` — a project switch WANTS the new project's
   * chat.jsonl replayed on the next hydration pass; this does not.
   */
  startNewChat: () => void
  /**
   * Read-and-clear `suppressHydrationOnce` in one step so the hydration
   * effect can act on the value it saw without a second render racing a
   * fresh `startNewChat()` in between the read and the clear.
   */
  consumeSuppressHydrationOnce: () => boolean
}

// Phase D polish #2 — multi-file upload progress batch shapes.
export type UploadRowStatus = 'queued' | 'uploading' | 'ok' | 'failed'

export interface UploadBatchRow {
  // Stable id within the batch (the File name is fine; if duplicates exist
  // we append an index suffix at batch creation time so the row map stays
  // unique).
  rowId: string
  filename: string
  sizeBytes: number
  status: UploadRowStatus
  errorKind?: string
  errorMessage?: string
}

export interface UploadBatch {
  batchId: string
  startedAt: number
  rows: UploadBatchRow[]
}

// Re-exported subset of the API type — keeping the store's surface narrow
// avoids dragging the full server-side ImporMeta into every consumer.
export interface UploadMetaUI {
  file_id: string
  filename: string
  mime: string
  size: number
  kind: 'user_upload' | 'agent_export'
  uploaded_at: number
  // Phase D polish #4 — PDF page count + truncation flag. Server-stamped
  // on upload; rendered as a badge in the chip strip.
  page_count?: number | null
  truncated_to_100_pages?: boolean | null
}

let _msgCounter = 0
function newMessageId() {
  _msgCounter += 1
  // Plain monotonic id — message ordering matters more than uniqueness.
  return `m${Date.now()}-${_msgCounter}`
}

export const useChatStore = create<ChatState>((set, get) => ({
  sessionId: null,
  profileId: null,
  suppressHydrationOnce: false,
  messages: [],
  pending: null,
  toolProgress: {},
  usage: {
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_create_tokens: 0,
  },
  streaming: false,
  error: null,
  streamCleanup: null,
  uploads: [],
  attachedFileIds: [],
  unseenExportCount: 0,
  uploadBatches: {},

  setSessionId: (id) => set({ sessionId: id }),
  setProfileId: (id) => set({ profileId: id }),
  appendMessage: (msg) => set((s) => ({
    messages: [...s.messages, { ...msg, id: newMessageId(), ts: Date.now() }],
  })),
  setMessages: (msgs) => set({ messages: msgs }),
  appendTokenDelta: (delta) => set((s) => {
    if (!delta) return s
    const last = s.messages[s.messages.length - 1]
    if (last && last.role === 'assistant') {
      // Append the delta to the trailing assistant bubble.
      const updated: ChatMessage = { ...last, content: last.content + delta }
      return { messages: [...s.messages.slice(0, -1), updated] }
    }
    // No active assistant message — start a fresh one.
    return {
      messages: [...s.messages, {
        id: newMessageId(), ts: Date.now(),
        role: 'assistant', content: delta,
      }],
    }
  }),
  appendThinkingDelta: (delta) => set((s) => {
    if (!delta) return s
    const last = s.messages[s.messages.length - 1]
    if (last && last.role === 'assistant') {
      const updated: ChatMessage = { ...last, thinking: (last.thinking ?? '') + delta }
      return { messages: [...s.messages.slice(0, -1), updated] }
    }
    // `thinking` frames precede `token` frames within a turn, so the first
    // one usually creates the assistant bubble rather than joining it.
    return {
      messages: [...s.messages, {
        id: newMessageId(), ts: Date.now(),
        role: 'assistant', content: '', thinking: delta,
      }],
    }
  }),
  setPending: (c) => set({ pending: c }),
  appendToolProgress: (toolUseId, frame) => set((s) => {
    const prev = s.toolProgress[toolUseId] ?? []
    // Cap retained lines so long solves (PHASE/VALIDATION spam) cannot
    // unbounded-grow the Zustand slice; keep the newest 500 for the
    // collapsible progress panel under the tool row.
    return {
      toolProgress: {
        ...s.toolProgress,
        [toolUseId]: [...prev.slice(-499), frame],
      },
    }
  }),
  accrueUsage: (delta) => set((s) => ({
    usage: {
      input_tokens: s.usage.input_tokens + (delta.input_tokens ?? 0),
      output_tokens: s.usage.output_tokens + (delta.output_tokens ?? 0),
      cache_read_tokens: s.usage.cache_read_tokens + (delta.cache_read_tokens ?? 0),
      cache_create_tokens: s.usage.cache_create_tokens + (delta.cache_create_tokens ?? 0),
    },
  })),
  setStreaming: (v) => set({ streaming: v }),
  setError: (e) => set({ error: e }),
  setStreamCleanup: (fn) => set({ streamCleanup: fn }),
  closeStream: () => {
    const cleanup = get().streamCleanup
    try { cleanup?.() } catch { /* idempotent */ }
    set({ streamCleanup: null, streaming: false })
  },

  setUploads: (list) => set({ uploads: list }),
  addUpload: (meta) => set((s) => {
    // Dedup against existing entries by file_id so a re-upload of the same
    // bytes doesn't render a duplicate chip.
    const existing = s.uploads.find((u) => u.file_id === meta.file_id)
    if (existing) return s
    // Default-ON: a newly-uploaded file is attached to the NEXT message
    // unless the user unchecks it. The chip strip surfaces a checkbox so
    // the user can opt-out per chip; the "send" path reads `attachedFileIds`.
    const nextAttached = s.attachedFileIds.includes(meta.file_id)
      ? s.attachedFileIds
      : [meta.file_id, ...s.attachedFileIds]
    return { uploads: [meta, ...s.uploads], attachedFileIds: nextAttached }
  }),
  removeUpload: (fileId) => set((s) => ({
    uploads: s.uploads.filter((u) => u.file_id !== fileId),
    attachedFileIds: s.attachedFileIds.filter((id) => id !== fileId),
  })),

  setAttachedFileIds: (list) => set({ attachedFileIds: list }),
  toggleAttached: (fileId) => set((s) => ({
    attachedFileIds: s.attachedFileIds.includes(fileId)
      ? s.attachedFileIds.filter((id) => id !== fileId)
      : [...s.attachedFileIds, fileId],
  })),
  bumpUnseenExport: () => set((s) => ({ unseenExportCount: s.unseenExportCount + 1 })),
  acknowledgeExport: () => set((s) => ({
    unseenExportCount: Math.max(0, s.unseenExportCount - 1),
  })),
  clearUnseenExports: () => set({ unseenExportCount: 0 }),

  beginUploadBatch: (batchId, files) => set((s) => {
    // Dedup filename collisions within one batch by suffixing an index.
    const seen = new Map<string, number>()
    const rows: UploadBatchRow[] = files.map((f) => {
      const count = (seen.get(f.name) ?? 0) + 1
      seen.set(f.name, count)
      const rowId = count === 1 ? f.name : `${f.name} (${count})`
      return { rowId, filename: f.name, sizeBytes: f.size, status: 'queued' }
    })
    return {
      uploadBatches: {
        ...s.uploadBatches,
        [batchId]: { batchId, startedAt: Date.now(), rows },
      },
    }
  }),
  markUploadRow: (batchId, rowId, patch) => set((s) => {
    const batch = s.uploadBatches[batchId]
    if (!batch) return s
    return {
      uploadBatches: {
        ...s.uploadBatches,
        [batchId]: {
          ...batch,
          rows: batch.rows.map((r) => (r.rowId === rowId ? { ...r, ...patch } : r)),
        },
      },
    }
  }),
  endUploadBatch: (batchId) => set((s) => {
    if (!s.uploadBatches[batchId]) return s
    const { [batchId]: _gone, ...rest } = s.uploadBatches
    return { uploadBatches: rest }
  }),

  resetForProjectSwitch: () => {
    // Call the active cleanup (closes any open SSE) BEFORE clearing the ref.
    const cleanup = get().streamCleanup
    try { cleanup?.() } catch { /* idempotent */ }
    set({
      sessionId: null,
      messages: [],
      pending: null,
      toolProgress: {},
      usage: {
        input_tokens: 0, output_tokens: 0,
        cache_read_tokens: 0, cache_create_tokens: 0,
      },
      streaming: false,
      error: null,
      streamCleanup: null,
      uploads: [],
      attachedFileIds: [],
      unseenExportCount: 0,
      uploadBatches: {},
    })
  },

  startNewChat: () => {
    // Unlike `resetForProjectSwitch`, this does NOT touch `streamCleanup` /
    // `streaming` — the dropdown that triggers the cross-wire confirm is
    // disabled while streaming, so there is no live turn to interrupt here.
    set({
      sessionId: null,
      messages: [],
      pending: null,
      toolProgress: {},
      error: null,
      usage: {
        input_tokens: 0, output_tokens: 0,
        cache_read_tokens: 0, cache_create_tokens: 0,
      },
      suppressHydrationOnce: true,
    })
  },
  consumeSuppressHydrationOnce: () => {
    const v = get().suppressHydrationOnce
    if (v) set({ suppressHydrationOnce: false })
    return v
  },
}))

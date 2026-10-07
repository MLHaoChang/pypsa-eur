/**
 * Phase 3 chatbot integration v6 — frontend chat API.
 *
 * Thin wrappers over `POST /api/chat/stream` (SSE), `POST /api/chat/{id}/confirm`,
 * and `POST /api/chat/{id}/abort`. Every consumer (ChatPanel.tsx + chatStore)
 * routes through these so we have ONE place to enforce credentials /
 * cleanup / log discipline.
 *
 * SSE quirks:
 *   * The native EventSource only supports GET — and our /stream is POST
 *     (we need to ship a JSON body with session_id + message + model). So
 *     we use `fetch(... { body, ... }).body.getReader()` instead and
 *     hand-parse the `event:`/`data:` framing. The reader cleanup pattern
 *     (reader.cancel() in onClose) is the equivalent of `es.close()`.
 *   * CLAUDE.md rule: every SSE consumer must return a cleanup function.
 *     `createChatStream` returns `() => void` so the caller can wire it
 *     into a React `useEffect` return.
 */
import { client } from './client'
import { rawFetchHeaders } from './csrf'
import type { UiContext } from '../utils/uiContext'

/**
 * A model id. Deliberately a bare `string`, no longer a closed union.
 *
 * It used to be `'claude-sonnet-5' | 'claude-opus-5'`, kept "in sync with
 * chat_service.DEFAULT_MODEL / OPUS_MODEL". That stopped being possible once
 * a user can configure OpenAI, Moonshot, Qwen, Ollama or any
 * OpenAI-compatible endpoint: the set is open, lives in the user's profile
 * store, and a model newer than this build must still work.
 *
 * The VALIDATOR moved rather than disappeared — `services/llm_config.py`
 * resolves a profile server-side, so a request can only ever name a
 * CONFIGURED profile. Widening this type does not widen what the backend
 * accepts.
 */
export type ChatModel = string

export interface ChatStreamRequest {
  session_id?: string
  message?: string
  /**
   * Which configured profile to bind this session to. Preferred over
   * `model`; the server resolves it and refuses an unconfigured id — as a
   * typed `unknown_profile_id` error frame on a 200 SSE stream, not a 4xx,
   * since a non-2xx SSE body is discarded here. No turn is run and the
   * session keeps whatever profile it was already bound to.
   * OMIT IT to mean "the server's active profile" — sending one
   * unconditionally would re-assert a stale choice every turn and undo both
   * an admin's `set_active_profile` and any A8 fallback.
   */
  profile_id?: string
  /**
   * Legacy selector, kept so an older client keeps working. The server maps
   * the two built-in model strings onto their profiles and translates
   * anything unrecognised to the ACTIVE profile with a warning — it does not
   * refuse, because free-text passthrough is a documented contract.
   */
  model?: ChatModel
  // The Phase 2 stub `script` is ALSO accepted by the backend; production
  // callers omit it (the real run_turn drives via Anthropic SDK). Tests can
  // inject scripted sequences via this field.
  script?: Array<Record<string, unknown>>
  // Phase C — list of upload file_ids to forward as multimodal content
  // blocks (images + PDFs). Order is preserved by the server. Excel /
  // Word / CSV files are NOT valid here — agents go through the
  // read_excel_sheet tool instead.
  attachment_file_ids?: string[]
  // Deixis — what the user is LOOKING AT when they hit send, so "why is this
  // so high?" has a referent. Built by `utils/uiContext.ts`, which is also
  // where the identifiers-only rule is written down; the server enforces the
  // same allowlist, so attaching values here fails closed.
  ui_context?: UiContext
  // 'voice' when the turn was dictated. A field rather than something the
  // server infers, because reconstructing it later from timing or content is
  // guesswork — and speech reciprocity (a spoken turn gets a spoken answer)
  // depends on getting it right.
  input_mode?: 'voice' | 'text'
}

export interface ChatFrame {
  event: string
  data: Record<string, unknown>
}

/**
 * Open an SSE-style stream to /api/chat/stream. The fetch body carries the
 * POST payload; the response body's ReadableStream is hand-parsed for SSE
 * frames. Returns a cleanup function the caller MUST invoke on unmount.
 *
 * The returned promise resolves when the stream ENDS (server sends final
 * frame OR fetch errors). The caller doesn't normally await — they wire
 * onFrame/onError callbacks and call the cleanup on unmount.
 */
export function createChatStream(
  req: ChatStreamRequest,
  onFrame: (frame: ChatFrame) => void,
  onError?: (err: unknown) => void,
): () => void {
  const controller = new AbortController()

  ;(async () => {
    try {
      const resp = await fetch('/api/chat/stream', {
        method: 'POST',
        // Raw fetch bypasses the axios CSRF interceptor, so the header is
        // added here — without it every chat turn 403s once a session exists.
        headers: { 'Content-Type': 'application/json', ...rawFetchHeaders('POST') },
        body: JSON.stringify(req),
        signal: controller.signal,
      })
      if (!resp.ok) {
        // Carry the server's `error_kind` on the Error so a caller can act on
        // it. `session_not_yours` in particular is RECOVERABLE — the client is
        // holding a session id that belongs to another user (GET /history
        // hands `last_session_id` to any co-member of the project), and the
        // cure is to start a fresh session rather than to keep retrying the
        // same id forever.
        let kind: string | undefined
        let detailMsg: string | undefined
        try {
          const body = await resp.json()
          const detail = (body as { detail?: unknown })?.detail
          if (detail && typeof detail === 'object') {
            kind = (detail as { error_kind?: string }).error_kind
            detailMsg = (detail as { message?: string }).message
          }
        } catch {
          // Not JSON, or an empty body — fall back to the status line.
        }
        const err = new Error(detailMsg ?? `chat stream HTTP ${resp.status}`)
        ;(err as Error & { kind?: string }).kind = kind
        onError?.(err)
        return
      }
      const reader = resp.body?.getReader()
      if (!reader) {
        onError?.(new Error('chat stream: no response body'))
        return
      }
      const decoder = new TextDecoder('utf-8')
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) return
        buffer += decoder.decode(value, { stream: true })
        // SSE frames are separated by blank lines (\n\n).
        let idx: number
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const block = buffer.slice(0, idx)
          buffer = buffer.slice(idx + 2)
          let event = ''
          let dataRaw = ''
          for (const line of block.split('\n')) {
            if (line.startsWith('event:')) event = line.slice(6).trim()
            else if (line.startsWith('data:')) dataRaw += line.slice(5).trim()
          }
          if (!event) continue
          let data: Record<string, unknown> = {}
          try { data = dataRaw ? JSON.parse(dataRaw) : {} } catch {
            data = { raw: dataRaw }
          }
          onFrame({ event, data })
        }
      }
    } catch (err) {
      if ((err as { name?: string }).name === 'AbortError') return  // expected on cleanup
      onError?.(err)
    }
  })()

  return () => {
    try { controller.abort() } catch { /* idempotent */ }
  }
}

export interface ConfirmRequest {
  token: string
  decision: 'approve' | 'deny'
}

export async function postChatConfirm(
  sessionId: string,
  req: ConfirmRequest,
): Promise<{ ok: boolean; tool_name?: string; decision?: string }> {
  const r = await client.post(`/chat/${sessionId}/confirm`, req)
  return r.data
}

export async function postChatAbort(sessionId: string): Promise<{ ok: boolean }> {
  const r = await client.post(`/chat/${sessionId}/abort`)
  return r.data
}

/**
 * Drop the last `turns` turns from the session's API history.
 *
 * What makes retry / edit-and-resend real rather than cosmetic: the array
 * replayed to the model lives on the server, so clearing the browser alone
 * leaves the answer being retried in context — and the model reads its own
 * last answer and repeats it.
 *
 * `dropped: 0` with `ok: true` is a legitimate outcome (unknown session, or a
 * turn in flight, which the server refuses to rewind under).
 */
export async function postChatRewind(
  sessionId: string, turns = 1,
): Promise<{ ok: boolean; dropped: number }> {
  const r = await client.post(`/chat/${sessionId}/rewind`, { turns })
  return r.data
}

export interface ChatHealth {
  ok: boolean
  /**
   * Literally "the ANTHROPIC_API_KEY slot is set" — unchanged semantics, not
   * "the active profile has a key". Kept as an alias so existing consumers
   * (and the backend's own smoke scripts) keep working; use `chat_ready` for
   * the question "can the assistant answer right now".
   */
  anthropic_api_key_present: boolean
  default_model: ChatModel
  confirmation_ttl_seconds: number
  /**
   * The active profile, minimally. This endpoint is UNAUTHENTICATED-adjacent
   * (it sits behind the global auth middleware but is the panel's cheap
   * probe), so it exposes nothing enumerable: no profiles list, no base_url,
   * no key hints. Use `GET /chat/profiles` for the list.
   */
  active_profile?: { id: string; label: string; wire: 'anthropic' | 'openai' }
  /** Active profile resolves AND its auth requirement is satisfied. */
  chat_ready?: boolean
}

export async function getChatHealth(): Promise<ChatHealth> {
  const r = await client.get('/chat/health')
  return r.data
}

// ── The start menu (chat harness issue 03) ──────────────────────────────────
//
// `GET /chat/workflows?context=…` is the harness's workflow registry filtered
// for where the user is: `unbound` (no project), `expert` or `guided`. The
// panel renders it as the greeting chips. It is one source of truth for every
// LLM provider, so switching the profile never changes what the chips offer,
// and it needs no API key.
export type WorkflowContext = 'unbound' | 'expert' | 'guided'

export interface WorkflowMenuEntry {
  id: string
  /** The chip label. */
  title: string
  /** One sentence; the chip tooltip. */
  intent: string
  /** The message the chip sends (owner decision Q10: sent, not prefilled). */
  opening_request: string
  steps: { id: string; title: string }[]
}

export interface WorkflowMenu {
  context: WorkflowContext
  workflows: WorkflowMenuEntry[]
}

export async function getWorkflowMenu(context: WorkflowContext): Promise<WorkflowMenu> {
  const r = await client.get('/chat/workflows', { params: { context } })
  return r.data
}

// ── `ask_user` → the Choice card (chat harness issue 04) ────────────────────
//
// The `choice_request` frame carries the structured question the assistant
// asked through `ask_user`. The panel renders it as a card; the pick is sent
// as the NEXT user message (owner decision Q4: the turn does not block).
export interface ChoiceOption {
  label: string
  description?: string
  recommended?: boolean
}

export interface ChoiceRequestFrame {
  tool_use_id?: string
  title: string
  question: string
  options: ChoiceOption[]
  allow_free_text: boolean
}

// ── The session's workflow step (chat harness issue 06) ─────────────────────
//
// `workflow_state` arrives after start_workflow / advance_workflow /
// end_workflow, and `GET /chat/history` carries the same shape, so a reload
// shows the strip again while the server session is resident.
export interface WorkflowState {
  id: string
  title: string
  step: string
  step_title: string
  step_index: number
  step_count: number
}

// ── U-1 — supplying the Anthropic API key from inside the app ──────────────
//
// The packaged app ships no `backend/.env` (it would carry a real key and the
// session-signing SECRET_KEY), so these routes are the only supported way it
// can be given one. They live under `/chat`, not `/admin`, because the whole
// admin router 404s in local mode — which is the desktop app.
export interface ApiKeySettings {
  configured: boolean
  /** 'settings' when it came from this UI, 'environment' when the shell set it. */
  source: 'settings' | 'environment' | null
  /** Last four characters, e.g. `…wxyz`. Never the key. */
  hint: string | null
  /** A shell-set value is masking the stored one, so saving looks like a no-op. */
  overridden_by_environment: boolean
  storage_path: string
}

export async function getApiKeySettings(): Promise<ApiKeySettings> {
  // `skipErrorToast` because a 403 here is an expected STATE, not a failure:
  // the route is super-admin only, and an ordinary member reaching a chat panel
  // with no key configured would otherwise get a red toast telling them off for
  // something the UI asked on their behalf.
  const r = await client.get('/chat/settings/api-key', { skipErrorToast: true })
  return r.data
}

export async function putApiKeySettings(value: string): Promise<ApiKeySettings> {
  const r = await client.put('/chat/settings/api-key', { value })
  return r.data
}

export async function deleteApiKeySettings(): Promise<ApiKeySettings> {
  const r = await client.delete('/chat/settings/api-key')
  return r.data
}

export interface ChatTurn {
  ts: number
  session_id: string
  /** Resolved model id. Kept alongside `profile_id`, never replaced by it —
   *  four consumers (import validation, history rehydration, an e2e pin, and
   *  this type) key on it, and old records on disk carry only this. */
  model: ChatModel
  /** Which profile produced the turn. Absent on records written before
   *  profiles existed; the reader falls back to resolving `model`. */
  profile_id?: string
  user: string
  assistant: Array<Record<string, unknown>>
  usage: {
    input_tokens: number
    output_tokens: number
    cache_read_tokens: number
    cache_create_tokens: number
  }
  // Phase C — file_ids of uploads attached to this user message. Omitted
  // when the turn carried no attachments.
  attachment_file_ids?: string[]
  // A turn that produced output and ended without completing (abort, a cap,
  // a stream error, a disconnect). Display only: `assistant` is what the user
  // saw, and the backend never replays this turn to the model.
  interrupted?: boolean
  interrupted_reason?: string
}

// A turn that started and never finished, recovered from the server's
// pending-turn WAL. Carries the user's message only — there is no assistant
// half, which is precisely what makes it worth reporting.
export interface InterruptedTurn {
  ts: number
  session_id: string
  model: ChatModel
  user: string
  // NO `profile_id` here, deliberately, and not an oversight: the pending-turn
  // WAL record (`chat_service.begin_pending_turn`) writes only ts/session_id/
  // model/user. It exists to say "your message was interrupted" and let the
  // user resend — it is never promoted into the transcript, so it needs no
  // provenance. `ChatTurn` (a real, answered turn) does carry `profile_id`.
}

export interface ChatHistory {
  turns: ChatTurn[]
  last_session_id: string | null
  /** The bound session's workflow step (chat harness issue 06), or null. */
  workflow?: WorkflowState | null
  bound_project: string | null
  // How many on-disk records were unreadable. Non-zero means `turns` is
  // INCOMPLETE — say so rather than rendering a quietly shorter conversation.
  history_gap: number
  // Reported once, then cleared server-side; null on a clean reload.
  pending_turn: InterruptedTurn | null
  // P28 A3 — the profile the resumed session is bound to (its next turn runs
  // on it when the request names none); null with no turns or when that
  // profile is no longer configured. Optional: older backends omit it.
  bound_profile_id?: string | null
}

export async function getChatHistory(limit = 200): Promise<ChatHistory> {
  const r = await client.get('/chat/history', { params: { limit } })
  return r.data
}

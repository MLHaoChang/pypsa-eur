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

// Latest Sonnet + Opus. Keep in sync with the backend DEFAULT_MODEL/OPUS_MODEL
// (chat_service.py) and PRICING_USD_PER_MTOK (chatStore.ts).
export type ChatModel = 'claude-sonnet-4-6' | 'claude-opus-4-8'

export interface ChatStreamRequest {
  session_id?: string
  message?: string
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
        onError?.(new Error(`chat stream HTTP ${resp.status}`))
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

export interface ChatHealth {
  ok: boolean
  anthropic_api_key_present: boolean
  default_model: ChatModel
  confirmation_ttl_seconds: number
}

export async function getChatHealth(): Promise<ChatHealth> {
  const r = await client.get('/chat/health')
  return r.data
}

export interface ChatTurn {
  ts: number
  session_id: string
  model: ChatModel
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
}

export interface ChatHistory {
  turns: ChatTurn[]
  last_session_id: string | null
  bound_project: string | null
}

export async function getChatHistory(limit = 200): Promise<ChatHistory> {
  const r = await client.get('/chat/history', { params: { limit } })
  return r.data
}

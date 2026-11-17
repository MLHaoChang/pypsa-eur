import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createChatStream,
  createResumableProgressStream,
  postChatProgressStop,
  PROGRESS_BACKOFF_MS,
  type ChatFrame,
  type ProgressStreamEnd,
} from './chat'
import { client } from './client'

// Live-voice 11c-2: the detached progress stream, kept across drops.
// GK B 60 conditions F1-F4 (give-up, teardown, foreign epoch, dedupe) and R2.

vi.mock('./csrf', () => ({ rawFetchHeaders: () => ({}) }))

const encoder = new TextEncoder()

function sse(event: string, id: string | null, data: Record<string, unknown> = {}): string {
  return `event: ${event}\n${id ? `id: ${id}\n` : ''}data: ${JSON.stringify(data)}\n\n`
}
const progress = (id: string) => sse('tool_progress', id, { tool_use_id: 'tu', kind: 'PHASE', line: id })
const snapshot = (id: string, monitorActive = true) =>
  sse('job_snapshot', id, { monitor_active: monitorActive, jobs: [] })

/** A body that yields `chunks`, then closes (the server ended it) or breaks (a drop). */
function body(source: string[], end: 'close' | 'drop'): Response {
  const chunks = [...source]
  return {
    ok: true,
    status: 200,
    body: new ReadableStream({
      // One chunk per read, so a drop comes after every chunk was delivered.
      pull(controller) {
        const next = chunks.shift()
        if (next !== undefined) controller.enqueue(encoder.encode(next))
        else if (end === 'close') controller.close()
        else controller.error(new TypeError('network error'))
      },
    }),
  } as unknown as Response
}

function refused(status: number, errorKind: string): Response {
  return {
    ok: false,
    status,
    json: async () => ({ detail: { error_kind: errorKind, message: errorKind } }),
  } as unknown as Response
}

function open() {
  const frames: ChatFrame[] = []
  const ends: ProgressStreamEnd[] = []
  const close = createResumableProgressStream('sess-1', (f) => frames.push(f), (e) => ends.push(e))
  return { frames, ends, close }
}

const fetchMock = () => vi.mocked(globalThis.fetch)
const sentId = (call: number) =>
  ((fetchMock().mock.calls[call][1] as RequestInit).headers as Record<string, string>)['Last-Event-ID']
const shownIds = (frames: ChatFrame[]) => frames.map((f) => f.id)

beforeEach(() => {
  vi.useFakeTimers()
  globalThis.fetch = vi.fn() as unknown as typeof fetch
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('the SSE id line', () => {
  it('is carried on the frame, and a frame without one has no id key', async () => {
    fetchMock().mockResolvedValue(body([progress('aa.bb:3'), sse('tok', null, { text: 'x' })], 'close'))
    const frames: ChatFrame[] = []
    createChatStream({ message: 'hi' } as never, (f) => frames.push(f))
    await vi.advanceTimersByTimeAsync(0)
    expect(frames[0].id).toBe('aa.bb:3')
    expect('id' in frames[1]).toBe(false)
  })
})

describe('createResumableProgressStream', () => {
  it('first connects without an id, then resumes a drop with Last-Event-ID after the first wait', async () => {
    fetchMock()
      .mockResolvedValueOnce(body([progress('e.1:1'), progress('e.1:2')], 'drop'))
      .mockResolvedValueOnce(body([progress('e.1:3')], 'close'))
    const { frames, ends } = open()
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock()).toHaveBeenCalledTimes(1)
    expect(fetchMock().mock.calls[0][0]).toBe('/api/chat/sess-1/progress')
    expect(sentId(0)).toBeUndefined()
    await vi.advanceTimersByTimeAsync(PROGRESS_BACKOFF_MS[0] - 1)
    expect(fetchMock()).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(fetchMock()).toHaveBeenCalledTimes(2)
    expect(sentId(1)).toBe('e.1:2')
    expect(shownIds(frames)).toEqual(['e.1:1', 'e.1:2', 'e.1:3'])
    expect(ends).toEqual(['monitor_ended'])
  })

  it('C2: shows a replayed overlap once across a reconnect', async () => {
    fetchMock()
      .mockResolvedValueOnce(body([progress('e.1:1'), progress('e.1:2')], 'drop'))
      .mockResolvedValueOnce(body([progress('e.1:2'), progress('e.1:3')], 'close'))
    const { frames } = open()
    await vi.advanceTimersByTimeAsync(PROGRESS_BACKOFF_MS[0])
    expect(shownIds(frames)).toEqual(['e.1:1', 'e.1:2', 'e.1:3'])
  })

  it('F4: a snapshot below the mark resets it, so newer frames above the snapshot show', async () => {
    fetchMock().mockResolvedValueOnce(body(
      [progress('e.1:5'), snapshot('e.1:3'), progress('e.1:4'), progress('e.1:4')], 'close'))
    const { frames } = open()
    await vi.advanceTimersByTimeAsync(0)
    expect(shownIds(frames)).toEqual(['e.1:5', 'e.1:3', 'e.1:4'])
  })

  it('F4: the mark is per epoch, so a new epoch starts low', async () => {
    fetchMock().mockResolvedValueOnce(body([progress('e.1:9'), progress('f.2:1'), progress('e.1:9')], 'close'))
    const { frames } = open()
    await vi.advanceTimersByTimeAsync(0)
    expect(shownIds(frames)).toEqual(['e.1:9', 'f.2:1'])
  })

  it('stops without reconnecting when the server closes the stream (the monitor is gone)', async () => {
    fetchMock().mockResolvedValueOnce(body([progress('e.1:1')], 'close'))
    const { ends } = open()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(fetchMock()).toHaveBeenCalledTimes(1)
    expect(ends).toEqual(['monitor_ended'])
    expect(vi.getTimerCount()).toBe(0)
  })

  it('stops on a snapshot that says the monitor is inactive, after delivering it', async () => {
    fetchMock().mockResolvedValueOnce(body([snapshot('e.1:4', false), progress('e.1:5')], 'drop'))
    const { frames, ends } = open()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(shownIds(frames)).toEqual(['e.1:4'])
    expect(ends).toEqual(['monitor_ended'])
    expect(fetchMock()).toHaveBeenCalledTimes(1)
    expect((fetchMock().mock.calls[0][1] as RequestInit).signal?.aborted).toBe(true)
  })

  it('retries a server error (5xx) like a drop', async () => {
    fetchMock()
      .mockResolvedValueOnce(refused(503, 'unavailable'))
      .mockResolvedValueOnce(body([progress('e.1:1')], 'close'))
    const { frames, ends } = open()
    await vi.advanceTimersByTimeAsync(PROGRESS_BACKOFF_MS[0])
    expect(shownIds(frames)).toEqual(['e.1:1'])
    expect(ends).toEqual(['monitor_ended'])
  })

  it('starts the try count again once a frame arrives', async () => {
    const drops = PROGRESS_BACKOFF_MS.length + 2
    for (let i = 1; i <= drops; i++) fetchMock().mockResolvedValueOnce(body([progress(`e.1:${i}`)], 'drop'))
    fetchMock().mockResolvedValueOnce(body([], 'close'))
    const { frames, ends } = open()
    await vi.advanceTimersByTimeAsync(PROGRESS_BACKOFF_MS[0] * (drops + 1))
    expect(frames).toHaveLength(drops)
    expect(ends).toEqual(['monitor_ended'])
  })

  describe('F1: giving up leaves no timer', () => {
    it('after the tries run out', async () => {
      fetchMock().mockRejectedValue(new TypeError('offline'))
      const { ends } = open()
      const total = PROGRESS_BACKOFF_MS.reduce((a, b) => a + b, 0)
      await vi.advanceTimersByTimeAsync(total - 1)
      expect(ends).toEqual([])
      await vi.advanceTimersByTimeAsync(1)
      expect(fetchMock()).toHaveBeenCalledTimes(PROGRESS_BACKOFF_MS.length + 1)
      expect(PROGRESS_BACKOFF_MS.map((_, i) => sentId(i + 1))).toEqual(
        PROGRESS_BACKOFF_MS.map(() => undefined))
      expect(ends).toEqual(['gave_up'])
      expect(vi.getTimerCount()).toBe(0)
      await vi.advanceTimersByTimeAsync(60_000)
      expect(fetchMock()).toHaveBeenCalledTimes(PROGRESS_BACKOFF_MS.length + 1)
    })

    it('on a 400 (a malformed id), at once', async () => {
      fetchMock().mockResolvedValue(refused(400, 'invalid_progress_cursor'))
      const { ends } = open()
      await vi.advanceTimersByTimeAsync(60_000)
      expect(fetchMock()).toHaveBeenCalledTimes(1)
      expect(ends).toEqual(['gave_up'])
      expect(vi.getTimerCount()).toBe(0)
    })

    it('on a second foreign-epoch 409, after the one reconnect without an id', async () => {
      fetchMock()
        .mockResolvedValueOnce(body([progress('e.1:4')], 'drop'))
        .mockResolvedValue(refused(409, 'progress_cursor_foreign'))
      const { ends } = open()
      await vi.advanceTimersByTimeAsync(PROGRESS_BACKOFF_MS[0])
      await vi.advanceTimersByTimeAsync(60_000)
      expect(fetchMock()).toHaveBeenCalledTimes(3)
      expect(sentId(1)).toBe('e.1:4')
      expect(sentId(2)).toBeUndefined()
      expect(ends).toEqual(['gave_up'])
      expect(vi.getTimerCount()).toBe(0)
    })

    it('a 409 of another kind gives up without the reconnect', async () => {
      fetchMock().mockResolvedValue(refused(409, 'wrong_replica'))
      const { ends } = open()
      await vi.advanceTimersByTimeAsync(60_000)
      expect(fetchMock()).toHaveBeenCalledTimes(1)
      expect(ends).toEqual(['gave_up'])
    })
  })

  it('F3: the reconnect after a foreign 409 drops the refused epoch mark', async () => {
    fetchMock()
      .mockResolvedValueOnce(body([progress('e.1:9')], 'drop'))
      .mockResolvedValueOnce(refused(409, 'progress_cursor_foreign'))
      .mockResolvedValueOnce(body([progress('e.1:1'), progress('e.1:2')], 'close'))
    const { frames, ends } = open()
    await vi.advanceTimersByTimeAsync(PROGRESS_BACKOFF_MS[0])
    await vi.waitFor(() => expect(fetchMock()).toHaveBeenCalledTimes(3))
    await vi.advanceTimersByTimeAsync(0)
    expect(sentId(2)).toBeUndefined()
    expect(shownIds(frames)).toEqual(['e.1:9', 'e.1:1', 'e.1:2'])
    expect(ends).toEqual(['monitor_ended'])
  })

  describe('F2: close() ends everything', () => {
    it('clears a pending wait, so nothing is fetched later', async () => {
      fetchMock().mockResolvedValueOnce(body([progress('e.1:1')], 'drop'))
      const { ends, close } = open()
      await vi.advanceTimersByTimeAsync(0)
      expect(vi.getTimerCount()).toBe(1)
      close()
      expect(vi.getTimerCount()).toBe(0)
      await vi.advanceTimersByTimeAsync(20_000)
      expect(fetchMock()).toHaveBeenCalledTimes(1)
      expect(ends).toEqual([])
    })

    it('aborts the fetch in flight and reports no end', async () => {
      fetchMock().mockReturnValueOnce(new Promise<Response>(() => {}))
      const { ends, close } = open()
      const init = fetchMock().mock.calls[0][1] as RequestInit
      expect(init.signal?.aborted).toBe(false)
      close()
      expect(init.signal?.aborted).toBe(true)
      await vi.advanceTimersByTimeAsync(20_000)
      expect(fetchMock()).toHaveBeenCalledTimes(1)
      expect(ends).toEqual([])
    })
  })
})

describe('postChatProgressStop', () => {
  it('POSTs /chat/{id}/progress/stop', async () => {
    const post = vi.spyOn(client, 'post').mockResolvedValue({ data: { ok: true, stopped: true } })
    await expect(postChatProgressStop('sess/1')).resolves.toEqual({ ok: true, stopped: true })
    expect(post).toHaveBeenCalledWith('/chat/sess%2F1/progress/stop')
  })
})

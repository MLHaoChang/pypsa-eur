import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createChatStream, type ChatFrame } from './chat'

// Improvement #13 — the SSE frame parser.
//
// Every frame the chat panel renders arrives through this function, and it
// was the one seam in the chat path with no test at all. It is also
// hand-rolled by necessity: the native EventSource is GET-only and /stream
// is a POST, so the `event:`/`data:` framing is parsed by hand off a
// ReadableStream — which means the framing edge cases are ours to get
// right, not the platform's.
//
// The one that matters most is chunk splitting. A frame is not a network
// chunk: TCP can deliver `event: tok` in one read and the `data:` line in
// the next, and a parser that assumed otherwise would work perfectly on a
// fast local connection and drop tokens over a real one.

vi.mock('./csrf', () => ({ rawFetchHeaders: () => ({}) }))

const encoder = new TextEncoder()

/** A Response whose body yields `chunks` in order, then closes. */
function streamingResponse(chunks: string[]): Response {
  return {
    ok: true,
    status: 200,
    body: new ReadableStream({
      start(controller) {
        for (const c of chunks) controller.enqueue(encoder.encode(c))
        controller.close()
      },
    }),
  } as unknown as Response
}

function collect(resp: Response | Promise<Response>) {
  vi.mocked(globalThis.fetch).mockResolvedValue(resp as Response)
  const frames: ChatFrame[] = []
  const errors: unknown[] = []
  const cleanup = createChatStream(
    { session_id: 's1', message: 'hi' },
    (f) => frames.push(f),
    (e) => errors.push(e),
  )
  return { frames, errors, cleanup }
}

beforeEach(() => {
  globalThis.fetch = vi.fn() as unknown as typeof fetch
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('createChatStream', () => {
  it('parses a well-formed frame', async () => {
    const { frames } = collect(streamingResponse([
      'event: token\ndata: {"delta":"hello"}\n\n',
    ]))

    await vi.waitFor(() => expect(frames).toHaveLength(1))
    expect(frames[0]).toEqual({ event: 'token', data: { delta: 'hello' } })
  })

  it('parses several frames delivered in one chunk', async () => {
    const { frames } = collect(streamingResponse([
      'event: session_init\ndata: {"session_id":"s1"}\n\n'
      + 'event: token\ndata: {"delta":"a"}\n\n'
      + 'event: turn_done\ndata: {"usage":{"output_tokens":2}}\n\n',
    ]))

    await vi.waitFor(() => expect(frames).toHaveLength(3))
    expect(frames.map((f) => f.event))
      .toEqual(['session_init', 'token', 'turn_done'])
  })

  it('reassembles a frame split across two network chunks', async () => {
    // The failure this guards: a parser that treated a chunk as a frame
    // would emit nothing here, and would do it only against a real network.
    const { frames } = collect(streamingResponse([
      'event: token\ndata: {"del',
      'ta":"split"}\n\n',
    ]))

    await vi.waitFor(() => expect(frames).toHaveLength(1))
    expect(frames[0].data).toEqual({ delta: 'split' })
  })

  it('reassembles a frame split mid-delimiter', async () => {
    // The nastiest boundary: the \n\n that ends the frame is itself torn in
    // half, so neither chunk contains a complete terminator.
    const { frames } = collect(streamingResponse([
      'event: token\ndata: {"delta":"x"}\n',
      '\nevent: turn_done\ndata: {}\n\n',
    ]))

    await vi.waitFor(() => expect(frames).toHaveLength(2))
    expect(frames.map((f) => f.event)).toEqual(['token', 'turn_done'])
  })

  it('concatenates a data payload spread over several data: lines', async () => {
    // SSE permits splitting one payload across repeated `data:` lines, and
    // a long tool result is exactly where a server would do it.
    const { frames } = collect(streamingResponse([
      'event: tool_result\ndata: {"a":1,\ndata: "b":2}\n\n',
    ]))

    await vi.waitFor(() => expect(frames).toHaveLength(1))
    expect(frames[0].data).toEqual({ a: 1, b: 2 })
  })

  it('ignores a block that carries no event line', async () => {
    // Keepalive comments exist to hold the connection open through a long
    // solve; emitting them as frames would put junk in the transcript.
    const { frames } = collect(streamingResponse([
      ': keepalive\n\n',
      'event: token\ndata: {"delta":"after"}\n\n',
    ]))

    await vi.waitFor(() => expect(frames).toHaveLength(1))
    expect(frames[0].event).toBe('token')
  })

  it('survives an unparseable payload and keeps streaming', async () => {
    // A malformed frame must not take the turn down with it — the frames
    // after it are the ones carrying the answer.
    const { frames, errors } = collect(streamingResponse([
      'event: token\ndata: {not json\n\n',
      'event: turn_done\ndata: {"usage":{}}\n\n',
    ]))

    await vi.waitFor(() => expect(frames).toHaveLength(2))
    expect(frames[0].data).toEqual({ raw: '{not json' })
    expect(frames[1].event).toBe('turn_done')
    expect(errors).toEqual([])
  })

  it('reports an HTTP failure instead of emitting frames', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(
      { ok: false, status: 429, body: null } as unknown as Response,
    )
    const frames: ChatFrame[] = []
    const errors: unknown[] = []
    createChatStream({}, (f) => frames.push(f), (e) => errors.push(e))

    await vi.waitFor(() => expect(errors).toHaveLength(1))
    expect((errors[0] as Error).message).toContain('429')
    expect(frames).toEqual([])
  })

  it('reports a response that carries no body', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(
      { ok: true, status: 200, body: null } as unknown as Response,
    )
    const errors: unknown[] = []
    createChatStream({}, () => {}, (e) => errors.push(e))

    await vi.waitFor(() => expect(errors).toHaveLength(1))
    expect((errors[0] as Error).message).toMatch(/no response body/)
  })

  it('reports a transport failure', async () => {
    vi.mocked(globalThis.fetch).mockRejectedValue(new Error('network down'))
    const errors: unknown[] = []
    createChatStream({}, () => {}, (e) => errors.push(e))

    await vi.waitFor(() => expect(errors).toHaveLength(1))
    expect((errors[0] as Error).message).toBe('network down')
  })

  it('treats cleanup as a normal end, not an error', async () => {
    // The cleanup path runs on every unmount. Surfacing its AbortError
    // would put an error banner in front of the user every time they
    // closed the panel.
    const abort = Object.assign(new Error('aborted'), { name: 'AbortError' })
    vi.mocked(globalThis.fetch).mockRejectedValue(abort)
    const errors: unknown[] = []
    const cleanup = createChatStream({}, () => {}, (e) => errors.push(e))
    cleanup()

    await new Promise((r) => setTimeout(r, 10))
    expect(errors).toEqual([])
  })

  it('returns a cleanup that is safe to call twice', async () => {
    const { cleanup } = collect(streamingResponse([
      'event: token\ndata: {"delta":"x"}\n\n',
    ]))

    expect(() => { cleanup(); cleanup() }).not.toThrow()
  })

  it('aborts the underlying request when cleaned up', async () => {
    const { cleanup } = collect(streamingResponse([
      'event: token\ndata: {"delta":"x"}\n\n',
    ]))
    await vi.waitFor(() => expect(globalThis.fetch).toHaveBeenCalled())

    const init = vi.mocked(globalThis.fetch).mock.calls[0][1] as RequestInit
    expect(init.signal?.aborted).toBe(false)
    cleanup()
    // Without a live abort the fetch outlives the panel, and the server
    // keeps generating a turn nobody will read — the client half of QA #14.
    expect(init.signal?.aborted).toBe(true)
  })
})

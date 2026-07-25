import { describe, expect, it, vi } from 'vitest'
import { SpeechSession, type SpeechSessionHandlers } from './speechSession'

function mockCtor() {
  const instances: Array<{
    continuous: boolean
    interimResults: boolean
    lang: string
    onresult: ((ev: SpeechRecognitionEvent) => void) | null
    onerror: ((ev: SpeechRecognitionErrorEvent) => void) | null
    onend: ((ev: Event) => void) | null
    start: ReturnType<typeof vi.fn>
    stop: ReturnType<typeof vi.fn>
    abort: ReturnType<typeof vi.fn>
  }> = []
  class MockRecognition {
    continuous = false
    interimResults = false
    lang = ''
    onresult: ((ev: SpeechRecognitionEvent) => void) | null = null
    onerror: ((ev: SpeechRecognitionErrorEvent) => void) | null = null
    onend: ((ev: Event) => void) | null = null
    start = vi.fn(() => {})
    stop = vi.fn(() => {})
    abort = vi.fn(() => {})
    constructor() {
      instances.push(this)
    }
  }
  return { MockRecognition, instances }
}

function handlers(): SpeechSessionHandlers & {
  onFinal: ReturnType<typeof vi.fn<(text: string) => void>>
  onInterim: ReturnType<typeof vi.fn<(text: string) => void>>
  onListeningChange: ReturnType<typeof vi.fn<(listening: boolean) => void>>
  onError: ReturnType<typeof vi.fn<(message: string) => void>>
} {
  return {
    onFinal: vi.fn<(text: string) => void>(),
    onInterim: vi.fn<(text: string) => void>(),
    onListeningChange: vi.fn<(listening: boolean) => void>(),
    onError: vi.fn<(message: string) => void>(),
  }
}

describe('SpeechSession', () => {
  it('start configures en-US continuous recognition and reports listening', () => {
    const { MockRecognition, instances } = mockCtor()
    const h = handlers()
    const session = new SpeechSession(MockRecognition as unknown as new () => SpeechRecognition, h)
    session.start()
    expect(instances).toHaveLength(1)
    expect(instances[0].lang).toBe('en-US')
    expect(instances[0].continuous).toBe(true)
    expect(instances[0].interimResults).toBe(true)
    expect(instances[0].start).toHaveBeenCalledOnce()
    expect(h.onListeningChange).toHaveBeenCalledWith(true)
  })

  it('delivers final text and interim separately', () => {
    const { MockRecognition, instances } = mockCtor()
    const h = handlers()
    const session = new SpeechSession(MockRecognition as unknown as new () => SpeechRecognition, h)
    session.start()
    const results = {
      length: 2,
      0: { isFinal: true, 0: { transcript: 'add a bus ' } },
      1: { isFinal: false, 0: { transcript: 'named' } },
    }
    instances[0].onresult?.({ results, resultIndex: 0 } as unknown as SpeechRecognitionEvent)
    expect(h.onFinal).toHaveBeenCalledWith('add a bus ')
    expect(h.onInterim).toHaveBeenCalledWith('named')
  })

  it('does not re-insert earlier finals when resultIndex advances', () => {
    const { MockRecognition, instances } = mockCtor()
    const h = handlers()
    const session = new SpeechSession(MockRecognition as unknown as new () => SpeechRecognition, h)
    session.start()
    const results = {
      length: 2,
      0: { isFinal: true, 0: { transcript: 'you to set the ' } },
      1: { isFinal: true, 0: { transcript: 'time series' } },
    }
    instances[0].onresult?.({ results, resultIndex: 0 } as unknown as SpeechRecognitionEvent)
    instances[0].onresult?.({ results, resultIndex: 1 } as unknown as SpeechRecognitionEvent)
    expect(h.onFinal.mock.calls.map((c) => c[0])).toEqual([
      'you to set the ',
      'time series',
    ])
  })

  it('toggle stops an active session', () => {
    const { MockRecognition, instances } = mockCtor()
    const h = handlers()
    const session = new SpeechSession(MockRecognition as unknown as new () => SpeechRecognition, h)
    session.toggle()
    expect(instances[0].start).toHaveBeenCalled()
    session.toggle()
    expect(instances[0].stop).toHaveBeenCalled()
    expect(h.onListeningChange).toHaveBeenLastCalledWith(false)
  })

  it('maps permission errors and clears listening', () => {
    const { MockRecognition, instances } = mockCtor()
    const h = handlers()
    const session = new SpeechSession(MockRecognition as unknown as new () => SpeechRecognition, h)
    session.start()
    instances[0].onerror?.({ error: 'not-allowed' } as SpeechRecognitionErrorEvent)
    expect(h.onError).toHaveBeenCalledWith(expect.stringMatching(/permission/i))
    expect(h.onListeningChange).toHaveBeenCalledWith(false)
  })

  it('restarts on end while still wanting to listen', () => {
    const { MockRecognition, instances } = mockCtor()
    const h = handlers()
    const session = new SpeechSession(MockRecognition as unknown as new () => SpeechRecognition, h)
    session.start()
    instances[0].start.mockClear()
    instances[0].onend?.(new Event('end'))
    expect(instances[0].start).toHaveBeenCalledOnce()
  })
})

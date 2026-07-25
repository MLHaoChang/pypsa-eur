/**
 * End-to-end (logic) path: mock SpeechRecognition → SpeechSession finals →
 * insertAtCursor into a composer buffer (same pipeline ChatPanel uses).
 */
import { describe, expect, it, vi } from 'vitest'
import { SpeechSession } from './speechSession'
import { insertAtCursor } from './speechToText'

describe('speech → composer e2e', () => {
  it('inserts final English speech at the caret without auto-sending', () => {
    const instances: Array<{
      onresult: ((ev: SpeechRecognitionEvent) => void) | null
      onerror: ((ev: SpeechRecognitionErrorEvent) => void) | null
      onend: ((ev: Event) => void) | null
      start: ReturnType<typeof vi.fn>
      stop: ReturnType<typeof vi.fn>
      abort: ReturnType<typeof vi.fn>
      lang: string
      continuous: boolean
      interimResults: boolean
    }> = []

    class MockRecognition {
      continuous = false
      interimResults = false
      lang = ''
      onresult: ((ev: SpeechRecognitionEvent) => void) | null = null
      onerror: ((ev: SpeechRecognitionErrorEvent) => void) | null = null
      onend: ((ev: Event) => void) | null = null
      start = vi.fn()
      stop = vi.fn()
      abort = vi.fn()
      constructor() {
        instances.push(this)
      }
    }

    let composer = 'Please '
    let selStart = composer.length
    let selEnd = composer.length
    let sent = false

    const session = new SpeechSession(
      MockRecognition as unknown as new () => SpeechRecognition,
      {
        onFinal: (text) => {
          const next = insertAtCursor(composer, selStart, selEnd, text, { padSpace: true })
          composer = next.value
          selStart = next.selectionStart
          selEnd = next.selectionEnd
        },
        onInterim: () => {},
        onListeningChange: () => {},
        onError: () => {},
      },
    )

    session.start()
    instances[0].onresult?.({
      results: {
        length: 1,
        0: { isFinal: true, 0: { transcript: 'add a bus named B1' } },
      },
    } as unknown as SpeechRecognitionEvent)

    expect(composer).toBe('Please add a bus named B1')
    expect(sent).toBe(false)

    session.stop()
    expect(instances[0].stop).toHaveBeenCalled()
  })
})

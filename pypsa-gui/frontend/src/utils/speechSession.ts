import {
  parseSpeechResults,
  speechErrorMessage,
  type SpeechRecognitionConstructor,
} from './speechToText'

export type SpeechSessionHandlers = {
  onFinal: (text: string) => void
  onInterim: (text: string) => void
  onListeningChange: (listening: boolean) => void
  onError: (message: string) => void
}

/**
 * Framework-free Web Speech session (toggle + continuous restart).
 * Used by `useSpeechToText` and unit-tested with a mock Recognition ctor.
 */
export class SpeechSession {
  private recognition: SpeechRecognition | null = null
  private wantListen = false

  constructor(
    private readonly Ctor: SpeechRecognitionConstructor,
    private readonly handlers: SpeechSessionHandlers,
  ) {}

  get isListening(): boolean {
    return this.wantListen && this.recognition != null
  }

  start(): void {
    this.stop({ silent: true })
    this.wantListen = true
    const recognition = new this.Ctor()
    recognition.lang = 'en-US'
    recognition.continuous = true
    recognition.interimResults = true

    recognition.onresult = (ev: SpeechRecognitionEvent) => {
      const { finalText, interimText } = parseSpeechResults(ev.results)
      this.handlers.onInterim(interimText)
      if (finalText.trim()) this.handlers.onFinal(finalText)
    }

    recognition.onerror = (ev: SpeechRecognitionErrorEvent) => {
      if (ev.error === 'aborted') return
      this.handlers.onError(speechErrorMessage(ev.error))
      this.wantListen = false
      this.handlers.onListeningChange(false)
      this.handlers.onInterim('')
    }

    recognition.onend = () => {
      if (this.wantListen) {
        try {
          recognition.start()
          this.handlers.onListeningChange(true)
          return
        } catch {
          this.wantListen = false
        }
      }
      this.handlers.onListeningChange(false)
      this.handlers.onInterim('')
      if (this.recognition === recognition) this.recognition = null
    }

    this.recognition = recognition
    try {
      recognition.start()
      this.handlers.onListeningChange(true)
    } catch (e) {
      this.wantListen = false
      this.recognition = null
      this.handlers.onListeningChange(false)
      this.handlers.onError(
        e instanceof Error ? e.message : 'Could not start voice input.',
      )
    }
  }

  stop(opts: { silent?: boolean } = {}): void {
    this.wantListen = false
    const r = this.recognition
    this.recognition = null
    if (!opts.silent) {
      this.handlers.onListeningChange(false)
      this.handlers.onInterim('')
    }
    if (!r) return
    try {
      r.onresult = null
      r.onerror = null
      r.onend = null
      r.stop()
    } catch {
      try {
        r.abort()
      } catch {
        /* ignore */
      }
    }
  }

  toggle(): void {
    if (this.wantListen) this.stop()
    else this.start()
  }
}

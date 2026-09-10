import { useCallback, useEffect, useRef, useState } from 'react'
import { SpeechSession } from '../utils/speechSession'
import {
  getSpeechRecognitionCtor,
  type SpeechRecognitionConstructor,
  type SpeechWindow,
} from '../utils/speechToText'

export type UseSpeechToTextOptions = {
  /** When false, start/toggle are no-ops (e.g. while chat is streaming). */
  enabled?: boolean
  /** Committed final transcript chunk (may be called multiple times). */
  onFinal: (text: string) => void
  /** User-facing error string (toast). `aborted` is suppressed upstream. */
  onError?: (message: string) => void
  /** Inject window / ctor for tests. */
  speechWindow?: SpeechWindow
  RecognitionCtor?: SpeechRecognitionConstructor | null
}

export type UseSpeechToTextResult = {
  supported: boolean
  available: boolean
  permissionDenied: boolean
  listening: boolean
  interim: string
  toggle: () => void
  stop: () => void
}

/**
 * Toggle Web Speech recognition for the chat composer.
 * Interim text is preview-only; finals are delivered via `onFinal`.
 */
export function useSpeechToText(opts: UseSpeechToTextOptions): UseSpeechToTextResult {
  const {
    enabled = true,
    onFinal,
    onError,
    speechWindow,
    RecognitionCtor,
  } = opts

  const Ctor =
    RecognitionCtor !== undefined
      ? RecognitionCtor
      : getSpeechRecognitionCtor(speechWindow)

  const supported = Ctor != null
  const [listening, setListening] = useState(false)
  const [interim, setInterim] = useState('')
  const [permissionDenied, setPermissionDenied] = useState(false)
  // `supported` answers "does the API exist"; `available` answers "can it
  // actually be used". They differ in WKWebView, which is the packaged app.
  const available = supported && !permissionDenied

  const sessionRef = useRef<SpeechSession | null>(null)
  const onFinalRef = useRef(onFinal)
  const onErrorRef = useRef(onError)

  useEffect(() => {
    onFinalRef.current = onFinal
  }, [onFinal])
  useEffect(() => {
    onErrorRef.current = onError
  }, [onError])

  const stop = useCallback(() => {
    sessionRef.current?.stop()
    sessionRef.current = null
    setListening(false)
    setInterim('')
  }, [])

  const ensureSession = useCallback((): SpeechSession | null => {
    if (!Ctor) return null
    if (sessionRef.current) return sessionRef.current
    sessionRef.current = new SpeechSession(Ctor, {
      onFinal: (text) => onFinalRef.current(text),
      onInterim: setInterim,
      onListeningChange: setListening,
      onError: (msg) => onErrorRef.current?.(msg),
      onPermissionDenied: () => setPermissionDenied(true),
    })
    return sessionRef.current
  }, [Ctor])

  const toggle = useCallback(() => {
    if (!available || !enabled) return
    const session = ensureSession()
    if (!session) return
    session.toggle()
  }, [available, enabled, ensureSession])

  useEffect(() => {
    if (!enabled) stop()
  }, [enabled, stop])

  useEffect(() => () => stop(), [stop])

  return { supported, available, permissionDenied, listening, interim, toggle, stop }
}

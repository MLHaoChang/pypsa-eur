/** Pure helpers for Web Speech → chat composer (Track: voice-to-text). */

export type InsertAtCursorOptions = {
  /** When true, insert a space before `insert` if the left neighbour is non-whitespace. */
  padSpace?: boolean
}

export type InsertAtCursorResult = {
  value: string
  selectionStart: number
  selectionEnd: number
}

/** Insert (or replace selection) at caret indices; returns next value + caret after insert. */
export function insertAtCursor(
  value: string,
  selectionStart: number,
  selectionEnd: number,
  insert: string,
  opts: InsertAtCursorOptions = {},
): InsertAtCursorResult {
  if (!insert) {
    const s = clamp(selectionStart, 0, value.length)
    const e = clamp(selectionEnd, 0, value.length)
    return { value, selectionStart: s, selectionEnd: Math.max(s, e) }
  }
  const start = clamp(Math.min(selectionStart, selectionEnd), 0, value.length)
  const end = clamp(Math.max(selectionStart, selectionEnd), 0, value.length)
  let chunk = insert
  if (opts.padSpace && start > 0) {
    const left = value[start - 1]
    if (left && !/\s/.test(left) && !/^\s/.test(chunk)) {
      chunk = ` ${chunk}`
    }
  }
  const next = value.slice(0, start) + chunk + value.slice(end)
  const caret = start + chunk.length
  return { value: next, selectionStart: caret, selectionEnd: caret }
}

function clamp(n: number, lo: number, hi: number): number {
  if (!Number.isFinite(n)) return lo
  return Math.max(lo, Math.min(hi, Math.trunc(n)))
}

export type SpeechRecognitionConstructor = new () => SpeechRecognition

/** Window-like object for feature detection (tests may pass a stub). */
export type SpeechWindow = Pick<Window, never> & {
  SpeechRecognition?: SpeechRecognitionConstructor
  webkitSpeechRecognition?: SpeechRecognitionConstructor
}

/** Resolve the browser SpeechRecognition constructor, or null if unsupported. */
export function getSpeechRecognitionCtor(
  win: SpeechWindow | undefined | null = typeof window !== 'undefined' ? window : undefined,
): SpeechRecognitionConstructor | null {
  if (!win) return null
  return win.SpeechRecognition ?? win.webkitSpeechRecognition ?? null
}

export type ParsedSpeechResults = {
  finalText: string
  interimText: string
}

/**
 * Split a SpeechRecognitionResultList into committed finals vs live interim.
 *
 * Important: pass `resultIndex` from the SpeechRecognitionEvent. The API
 * re-sends earlier final results on later events; only indices >= resultIndex
 * are new. Re-inserting all finals causes the "you to set the you to set the…"
 * duplication after thinking pauses.
 */
export function parseSpeechResults(
  results: SpeechRecognitionResultList,
  resultIndex = 0,
): ParsedSpeechResults {
  let finalText = ''
  let interimText = ''
  const len = results?.length ?? 0
  const start = Math.max(0, Math.min(resultIndex, len))
  for (let i = start; i < len; i++) {
    const result = results[i]
    const piece = result?.[0]?.transcript ?? ''
    if (!piece) continue
    if (result.isFinal) finalText += piece
    else interimText += piece
  }
  return { finalText, interimText }
}

/**
 * Is this error code a permission denial rather than a transient failure?
 *
 * Measured: in a cocoa WKWebView the `webkitSpeechRecognition` constructor
 * exists and `.start()` fires `not-allowed`, because the app bundle declares
 * no NSMicrophoneUsageDescription. Callers must distinguish this from
 * `no-speech` or `network`, which are worth retrying.
 */
export function isPermissionError(errorCode: string): boolean {
  return errorCode === 'not-allowed' || errorCode === 'service-not-allowed'
}

/** User-facing toast copy for SpeechRecognitionErrorEvent.error codes. */
export function speechErrorMessage(errorCode: string): string {
  switch (errorCode) {
    case 'not-allowed':
    case 'service-not-allowed':
      return 'Microphone access was denied. Allow it in System Settings → '
        + 'Privacy & Security → Microphone, then try again.'
    case 'no-speech':
      return 'No speech detected — try again.'
    case 'audio-capture':
      return 'No microphone available.'
    case 'network':
      return 'Speech recognition network error — check connectivity.'
    case 'aborted':
      return 'Voice input stopped.'
    default:
      return `Voice input error: ${errorCode || 'unknown'}`
  }
}

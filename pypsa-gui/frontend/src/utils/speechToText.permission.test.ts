// Measured in a real cocoa WKWebView (backend/smoke/probe_webview_speech.py):
//   webkitSpeechRecognition: "function"   ← constructor IS present
//   .start()                → error: not-allowed
// So a presence check reports "supported" in the exact environment where voice
// cannot work, which is how the packaged app came to show an enabled mic
// button that always fails.
import { describe, expect, it } from 'vitest'
import { getSpeechRecognitionCtor, isPermissionError } from './speechToText'

describe('isPermissionError', () => {
  it('classifies the two denial codes', () => {
    expect(isPermissionError('not-allowed')).toBe(true)
    expect(isPermissionError('service-not-allowed')).toBe(true)
  })

  it('does not classify unrelated failures as denial', () => {
    // Negative control: a predicate returning true for everything would pass
    // the test above on its own.
    for (const code of ['no-speech', 'audio-capture', 'network', 'aborted', '']) {
      expect(isPermissionError(code)).toBe(false)
    }
  })
})

describe('getSpeechRecognitionCtor', () => {
  it('still reports the constructor when only the webkit prefix exists', () => {
    // This is WKWebView's shape. The function is correct; the CALLER was wrong
    // to treat this as "voice works".
    const win = { webkitSpeechRecognition: function () {} } as never
    expect(getSpeechRecognitionCtor(win)).not.toBeNull()
  })
})

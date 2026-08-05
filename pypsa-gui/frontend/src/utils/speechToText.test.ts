import { describe, expect, it } from 'vitest'
import {
  getSpeechRecognitionCtor,
  insertAtCursor,
  parseSpeechResults,
  speechErrorMessage,
  type SpeechRecognitionConstructor,
} from './speechToText'

describe('insertAtCursor', () => {
  it('inserts into empty string', () => {
    expect(insertAtCursor('', 0, 0, 'hello')).toEqual({
      value: 'hello',
      selectionStart: 5,
      selectionEnd: 5,
    })
  })

  it('inserts in the middle', () => {
    expect(insertAtCursor('ab', 1, 1, 'X')).toEqual({
      value: 'aXb',
      selectionStart: 2,
      selectionEnd: 2,
    })
  })

  it('replaces the current selection', () => {
    expect(insertAtCursor('hello world', 6, 11, 'there')).toEqual({
      value: 'hello there',
      selectionStart: 11,
      selectionEnd: 11,
    })
  })

  it('clamps out-of-range indices', () => {
    expect(insertAtCursor('hi', 99, 99, '!')).toEqual({
      value: 'hi!',
      selectionStart: 3,
      selectionEnd: 3,
    })
  })

  it('adds a leading space when joining onto non-whitespace', () => {
    expect(insertAtCursor('hello', 5, 5, 'world', { padSpace: true })).toEqual({
      value: 'hello world',
      selectionStart: 11,
      selectionEnd: 11,
    })
  })

  it('does not pad when the caret follows whitespace', () => {
    expect(insertAtCursor('hello ', 6, 6, 'world', { padSpace: true })).toEqual({
      value: 'hello world',
      selectionStart: 11,
      selectionEnd: 11,
    })
  })
})

describe('parseSpeechResults', () => {
  it('splits final and interim transcripts', () => {
    const results = {
      length: 2,
      0: { isFinal: true, 0: { transcript: 'hello ' } },
      1: { isFinal: false, 0: { transcript: 'world' } },
    }
    expect(parseSpeechResults(results as unknown as SpeechRecognitionResultList)).toEqual({
      finalText: 'hello ',
      interimText: 'world',
    })
  })

  it('ignores finals before resultIndex (pause / continuous events)', () => {
    const results = {
      length: 2,
      0: { isFinal: true, 0: { transcript: 'you to set the ' } },
      1: { isFinal: true, 0: { transcript: 'time series' } },
    }
    // Second event: only index 1 is new — must not re-emit the first phrase.
    expect(
      parseSpeechResults(results as unknown as SpeechRecognitionResultList, 1),
    ).toEqual({
      finalText: 'time series',
      interimText: '',
    })
  })
})

describe('speechErrorMessage', () => {
  it('maps known error codes', () => {
    expect(speechErrorMessage('not-allowed')).toMatch(/denied/i)
    expect(speechErrorMessage('no-speech')).toMatch(/speech/i)
    expect(speechErrorMessage('audio-capture')).toMatch(/microphone/i)
  })
})

describe('getSpeechRecognitionCtor', () => {
  it('returns null when unsupported', () => {
    expect(getSpeechRecognitionCtor({})).toBeNull()
  })

  it('prefers standard then webkit', () => {
    const Fake = function SpeechRecognition() {} as unknown as SpeechRecognitionConstructor
    expect(getSpeechRecognitionCtor({ SpeechRecognition: Fake })).toBe(Fake)
    const Webkit = function webkitSpeechRecognition() {} as unknown as SpeechRecognitionConstructor
    expect(getSpeechRecognitionCtor({ webkitSpeechRecognition: Webkit })).toBe(Webkit)
  })
})

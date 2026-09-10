import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cancelSpeech, isSpeechOutAvailable, plainTextForSpeech, speak } from './speechOut'

// Speech OUTPUT — the other half of the microphone.
//
// From the spec ("Speech"):
//
//   "Modal reciprocity. A turn begun with the microphone is answered aloud; a
//    typed turn is answered in text. Plus a global mute. This matches how
//    people already expect assistants to behave and requires no settings trip:
//    the spoken mode is chosen by the act of using the microphone."
//
// Speech input already exists (speechToText.ts / speechSession.ts). Output did
// not: `grep -rn speechSynthesis frontend/src` returned nothing. So the
// assistant could hear and could not answer.
//
// The reason this is a module and not four lines in ChatPanel is the markdown.
// The assistant's replies are GitHub-flavoured markdown — that is why
// ChatMarkdown exists — and handing that to a speech synthesiser reads the
// punctuation out loud. "**Onshore Wind 3** produces 42 MW" becomes "asterisk
// asterisk Onshore Wind 3 asterisk asterisk". A table is worse.

class FakeUtterance {
  text: string
  lang = ''
  onend: (() => void) | null = null
  constructor(text: string) { this.text = text }
}

function installFakeSynthesis() {
  const spoken: FakeUtterance[] = []
  const synth = {
    speak: vi.fn((u: FakeUtterance) => { spoken.push(u) }),
    cancel: vi.fn(),
    speaking: false,
  }
  vi.stubGlobal('speechSynthesis', synth)
  vi.stubGlobal('SpeechSynthesisUtterance', FakeUtterance)
  return { synth, spoken }
}

beforeEach(() => { vi.unstubAllGlobals() })
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('availability', () => {
  it('is available when the platform provides the API', () => {
    installFakeSynthesis()
    expect(isSpeechOutAvailable()).toBe(true)
  })

  // jsdom has no speechSynthesis, and neither does every browser this ships
  // to. The spec measured 219 voices in the packaged app's WKWebView, but
  // "measured on one platform" is not "present everywhere".
  it('is absent when the platform does not', () => {
    vi.stubGlobal('speechSynthesis', undefined)
    expect(isSpeechOutAvailable()).toBe(false)
  })

  it('speaking on a platform without the API is a no-op, not a crash', () => {
    vi.stubGlobal('speechSynthesis', undefined)
    expect(() => speak('hello')).not.toThrow()
    expect(() => cancelSpeech()).not.toThrow()
  })
})

describe('what actually gets spoken', () => {
  it('speaks the text', () => {
    const { spoken } = installFakeSynthesis()
    speak('Onshore Wind 3 produces 42 megawatts')
    expect(spoken.map(u => u.text)).toEqual(['Onshore Wind 3 produces 42 megawatts'])
  })

  it('cancels anything already in flight before starting', () => {
    const { synth } = installFakeSynthesis()
    speak('first')
    speak('second')
    // Two turns in quick succession must not overlap into gibberish; the
    // newer answer wins, the same way the newer answer wins on screen.
    expect(synth.cancel).toHaveBeenCalled()
  })

  it('says nothing for empty or whitespace-only text', () => {
    const { synth } = installFakeSynthesis()
    speak('')
    speak('   \n  ')
    expect(synth.speak).not.toHaveBeenCalled()
  })
})

describe('plainTextForSpeech', () => {
  it('strips emphasis markers', () => {
    expect(plainTextForSpeech('**Onshore Wind 3** is _large_'))
      .toBe('Onshore Wind 3 is large')
  })

  it('strips headers and list bullets', () => {
    expect(plainTextForSpeech('## Results\n- one\n- two'))
      .toBe('Results. one. two')
  })

  it('reads link text, not the URL', () => {
    expect(plainTextForSpeech('see [the results](https://example.com/x?y=1)'))
      .toBe('see the results')
  })

  // A fenced block of YAML or a netcdf traceback read aloud is unbearable and
  // tells the user nothing. Announcing its presence is the useful reduction.
  it('replaces a code block with a mention of it', () => {
    const out = plainTextForSpeech('Try this:\n```python\nn.optimize()\n```\ndone')
    expect(out).not.toContain('n.optimize()')
    expect(out.toLowerCase()).toContain('code')
  })

  it('replaces a table with a mention of it', () => {
    const md = '| carrier | MW |\n| --- | --- |\n| wind | 42 |\n| solar | 17 |'
    const out = plainTextForSpeech(md)
    expect(out).not.toContain('---')
    expect(out.toLowerCase()).toContain('table')
  })

  // Unbounded text handed to a synthesiser is minutes of talking the user
  // cannot skim, and cannot easily stop once the panel has moved on.
  it('clamps a very long answer', () => {
    const out = plainTextForSpeech('word '.repeat(5000))
    expect(out.length).toBeLessThan(1200)
  })

  it('leaves ordinary prose alone', () => {
    expect(plainTextForSpeech('The solve is optimal at 1.2 billion euro.'))
      .toBe('The solve is optimal at 1.2 billion euro.')
  })
})

import { afterEach, describe, expect, it, vi } from 'vitest'
import { plainSpeech, speakLiveLine, stopLiveSpeech } from './companionVoice'

describe('companionVoice', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('strips markdown so Live can speak plain language', () => {
    expect(plainSpeech('**cloud-demo** has `1` bus.\n\n- item')).toBe('cloud-demo has 1 bus. item')
  })

  it('speaks a cleaned line through the browser speech API', () => {
    const speak = vi.fn()
    const cancel = vi.fn()
    vi.stubGlobal('speechSynthesis', { speak, cancel })
    vi.stubGlobal('SpeechSynthesisUtterance', class {
      text: string
      rate = 1
      pitch = 1
      constructor(text: string) { this.text = text }
    })
    speakLiveLine('Open the **Results** tab.')
    expect(cancel).toHaveBeenCalledOnce()
    expect(speak).toHaveBeenCalledOnce()
    expect(speak.mock.calls[0][0].text).toBe('Open the Results tab.')
    stopLiveSpeech()
    expect(cancel).toHaveBeenCalledTimes(2)
  })
})

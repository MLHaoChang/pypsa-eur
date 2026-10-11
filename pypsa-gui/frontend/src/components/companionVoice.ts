/** Interim Live voice until LiveKit / hosted TTS (issues 01/03). */

export function plainSpeech(text: string, maxChars = 420): string {
  return text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*]\([^)]*\)/g, ' ')
    .replace(/\[[^\]]*]\([^)]*\)/g, ' ')
    .replace(/[*_~>#]+/g, ' ')
    .replace(/^\s*[-*]\s+/gm, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, maxChars)
}

export function speakLiveLine(text: string): void {
  if (typeof window === 'undefined') return
  const spoken = plainSpeech(text)
  if (!spoken) return
  const synth = window.speechSynthesis
  if (!synth || typeof SpeechSynthesisUtterance === 'undefined') return
  synth.cancel()
  const utter = new SpeechSynthesisUtterance(spoken)
  utter.rate = 1.04
  utter.pitch = 1
  synth.speak(utter)
}

export function stopLiveSpeech(): void {
  if (typeof window === 'undefined') return
  window.speechSynthesis?.cancel()
}

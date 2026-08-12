/**
 * Speech output — the other half of the microphone.
 *
 * From the spec ("Speech"): "Modal reciprocity. A turn begun with the
 * microphone is answered aloud; a typed turn is answered in text. Plus a
 * global mute. […] the spoken mode is chosen by the act of using the
 * microphone." Speech INPUT already existed; output did not, so the assistant
 * could hear and could not answer.
 *
 * Two things live here rather than in ChatPanel, and both are the reason this
 * is a module at all:
 *
 *   1. THE MARKDOWN. Assistant replies are GitHub-flavoured markdown — that is
 *      what ChatMarkdown renders — and a synthesiser reads the punctuation
 *      aloud. "**Onshore Wind 3** produces 42 MW" becomes "asterisk asterisk
 *      Onshore Wind 3 asterisk asterisk". A results table is worse: it is a
 *      minute of pipes and dashes.
 *
 *   2. THE PLATFORM. `speechSynthesis` is absent in jsdom and is not
 *      guaranteed everywhere this ships. Every entry point degrades to a
 *      no-op rather than throwing, because a missing voice must never be able
 *      to break a turn that has already succeeded on screen.
 *
 * No permission is required for output (unlike input, which is
 * permission-blocked in the packaged app today), so there is no consent flow
 * to run and nothing to fall back to when it is unavailable — it simply does
 * not speak.
 */

/** Roughly a paragraph. Past this, spoken output stops being useful. */
const MAX_SPOKEN_CHARS = 1000

function synth(): SpeechSynthesis | null {
  try {
    const s = (globalThis as { speechSynthesis?: SpeechSynthesis }).speechSynthesis
    const Utter = (globalThis as { SpeechSynthesisUtterance?: unknown }).SpeechSynthesisUtterance
    return s && Utter ? s : null
  } catch {
    return null
  }
}

export function isSpeechOutAvailable(): boolean {
  return synth() !== null
}

/**
 * Markdown → something worth hearing.
 *
 * Deliberately a reduction, not a conversion: code blocks and tables are
 * REPLACED by a mention of themselves rather than read out. Someone who wants
 * the table can look at it — it is on screen, four inches from their ear.
 */
export function plainTextForSpeech(markdown: string): string {
  let t = markdown ?? ''

  // Fenced code first, before anything else can chew on its contents.
  t = t.replace(/```[\s\S]*?```/g, ' (code omitted) ')
  t = t.replace(/`([^`]*)`/g, '$1')

  // A table is any run of consecutive pipe-leading lines. Matched as a block
  // so a two-row and a twenty-row table both reduce to one mention.
  t = t.replace(/(?:^[ \t]*\|.*\|[ \t]*$\n?)+/gm, ' (a table is shown) ')

  // Links: keep the label, drop the URL — a spoken URL is unusable.
  t = t.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
  t = t.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')

  // Headers and list bullets become sentence breaks, so the synthesiser's
  // prosody has something to work with instead of running them together.
  t = t.replace(/^\s{0,3}#{1,6}\s+(.*)$/gm, '$1.')
  t = t.replace(/^\s*[-*+]\s+/gm, '')
  t = t.replace(/^\s*\d+\.\s+/gm, '')
  t = t.replace(/^\s*>\s?/gm, '')

  // Emphasis / strikethrough markers.
  t = t.replace(/\*\*([^*]+)\*\*/g, '$1')
  t = t.replace(/__([^_]+)__/g, '$1')
  t = t.replace(/\*([^*]+)\*/g, '$1')
  t = t.replace(/(?<![A-Za-z0-9_])_([^_]+)_(?![A-Za-z0-9_])/g, '$1')
  t = t.replace(/~~([^~]+)~~/g, '$1')

  // Horizontal rules carry nothing audible.
  t = t.replace(/^\s*([-*_])\1{2,}\s*$/gm, '')

  // Line breaks become sentence breaks; collapse the punctuation pile-ups
  // that leaves behind ("Results.. one").
  t = t.split('\n').map(l => l.trim()).filter(Boolean).join('. ')
  t = t.replace(/\.\s*\./g, '.')
  t = t.replace(/\s+/g, ' ').trim()

  if (t.length > MAX_SPOKEN_CHARS) {
    // Cut at a sentence end where possible so it does not stop mid-word.
    const cut = t.slice(0, MAX_SPOKEN_CHARS)
    const lastStop = cut.lastIndexOf('. ')
    t = (lastStop > MAX_SPOKEN_CHARS / 2 ? cut.slice(0, lastStop + 1) : cut)
      + ' The rest is on screen.'
  }
  return t
}

/** Speak `text`. Cancels anything already in flight. No-op when unavailable. */
export function speak(text: string): void {
  const s = synth()
  if (!s) return
  const body = (text ?? '').trim()
  if (!body) return
  try {
    // Two answers in quick succession must not overlap into gibberish. The
    // newer one wins, the same way it does on screen.
    s.cancel()
    const Utter = (globalThis as unknown as {
      SpeechSynthesisUtterance: new (t: string) => SpeechSynthesisUtterance
    }).SpeechSynthesisUtterance
    s.speak(new Utter(body))
  } catch {
    /* A voice that fails must not take the turn down with it. */
  }
}

/** Stop immediately. Safe to call when nothing is speaking, or unavailable. */
export function cancelSpeech(): void {
  try {
    synth()?.cancel()
  } catch {
    /* noop */
  }
}

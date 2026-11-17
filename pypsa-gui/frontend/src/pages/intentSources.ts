// The one word shown after an intent-comparison input for where its value comes from (walkthrough bug 06).
// Keys are the backend's `intent_inputs.KINDS`; `intentSources.parity.test.ts` fails when they drift.
import type { SourceKind } from '../api/intentComparison'

export const SOURCE_WORDS: Record<SourceKind | 'typed', string> = {
  project: 'project',
  assumed: 'assumed',
  load_envelope: 'load envelope',
  grid_code: 'grid code',
  typed: 'entered here',
}

/** The source word for a field: what the user typed wins, else the value's own kind; none when unknown. */
export function sourceWord(kind: SourceKind | undefined, typed: boolean): string | null {
  if (typed) return SOURCE_WORDS.typed
  return kind ? SOURCE_WORDS[kind] : null
}

/** A job record's source text for one input, as the same word ("request" → entered here). */
export function usedWord(source: string | undefined): string | null {
  if (!source) return null
  if (source === 'request') return SOURCE_WORDS.typed
  if (source === 'project') return SOURCE_WORDS.project
  if (source.startsWith('assumed') || /assum/i.test(source)) return SOURCE_WORDS.assumed
  if (source.includes('envelope')) return SOURCE_WORDS.load_envelope
  return SOURCE_WORDS.project
}

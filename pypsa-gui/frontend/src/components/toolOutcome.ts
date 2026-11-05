/**
 * Issue 04 (S1b): the chat tool line for a result that carries the backend's
 * `_outcome` (an independent, identity-checked read of the job). The mark
 * follows that outcome, never a blanket ✓.
 */
import { ACTIVITY_STATUS_WORDS } from './activityWords'

/** Every outcome the backend's `_outcome.outcome` may carry (harness/outcomes.py). */
export const TOOL_OUTCOMES = [
  'submitted', 'running', 'cancel_requested', 'completed', 'partial',
  'failed', 'cancelled', 'denied', 'uncertain',
] as const
export type ToolOutcome = (typeof TOOL_OUTCOMES)[number]

const IN_FLIGHT: ReadonlySet<string> = new Set(['submitted', 'running', 'cancel_requested'])

export interface ToolOutcomeMark {
  outcome: ToolOutcome
  /** "…" in flight, ✓ completed, ✕ failed, "–" otherwise. */
  glyph: '…' | '✓' | '✕' | '–'
  /** Lowercase word from ACTIVITY_STATUS_WORDS. */
  word: string
  tone: 'text' | 'warn' | 'muted'
  inFlight: boolean
}

export interface ToolOutcomeBlock { outcome: string; job_id: string | null }

/** The `_outcome` block of a tool result, or null when it has none or a malformed one. */
export function toolOutcomeOf(result: unknown): ToolOutcomeBlock | null {
  const block = (result as { _outcome?: unknown } | null | undefined)?._outcome
  if (!block || typeof block !== 'object') return null
  const { outcome, job_id: jobId } = block as { outcome?: unknown; job_id?: unknown }
  if (typeof outcome !== 'string') return null
  return { outcome, job_id: typeof jobId === 'string' && jobId ? jobId : null }
}

/** The mark for one outcome; an unknown outcome string reads as uncertain (fail closed). */
export function toolOutcomeMark(outcome: string): ToolOutcomeMark {
  const known = (TOOL_OUTCOMES as readonly string[]).includes(outcome) ? outcome as ToolOutcome : 'uncertain'
  const word = ACTIVITY_STATUS_WORDS[known].toLowerCase()
  if (IN_FLIGHT.has(known)) return { outcome: known, glyph: '…', word, tone: 'text', inFlight: true }
  if (known === 'completed') return { outcome: known, glyph: '✓', word, tone: 'text', inFlight: false }
  if (known === 'failed') return { outcome: known, glyph: '✕', word, tone: 'warn', inFlight: false }
  return { outcome: known, glyph: '–', word, tone: 'muted', inFlight: false }
}

/** The transcript line: `<glyph> <tool> (<word>)`. */
export function toolOutcomeLine(tool: string, outcome: string): string {
  const mark = toolOutcomeMark(outcome)
  return `${mark.glyph} ${tool} (${mark.word})`
}

interface OutcomeMessage { id: string; role: string; tool_result?: unknown; ts: number }

export interface CurrentToolOutcome { mark: ToolOutcomeMark; ts: number }

/**
 * What one tool line shows now. An in-flight outcome gives way to the newest
 * later `_outcome` read of the SAME job id in the transcript (the backend's
 * identity-checked read; no guess from unrelated status). With no later read
 * it stays as it was at its own time, which the caller words "at HH:MM".
 */
export function currentToolOutcome(
  messages: ReadonlyArray<OutcomeMessage>, index: number,
): CurrentToolOutcome | null {
  const own = messages[index]
  const block = own ? toolOutcomeOf(own.tool_result) : null
  if (!own || own.role !== 'tool' || !block) return null
  let mark = toolOutcomeMark(block.outcome)
  let ts = own.ts
  if (!mark.inFlight || !block.job_id) return { mark, ts }
  for (let i = index + 1; i < messages.length; i++) {
    const later = messages[i]
    if (later.role !== 'tool') continue
    const next = toolOutcomeOf(later.tool_result)
    if (next && next.job_id === block.job_id) {
      mark = toolOutcomeMark(next.outcome)
      ts = later.ts
    }
  }
  return { mark, ts }
}

/** "14:02" in the viewer's clock, for "running at 14:02". */
export function outcomeClock(ts: number): string {
  const d = new Date(ts)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** The words after the glyph: the word, plus "at HH:MM" while in flight. */
export function toolOutcomeWords(current: CurrentToolOutcome): string {
  return current.mark.inFlight ? `${current.mark.word} at ${outcomeClock(current.ts)}` : current.mark.word
}

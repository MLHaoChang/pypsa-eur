/**
 * Issue 04 (S1b): the chat tool line for a result that carries the backend's
 * `_outcome` (an independent, identity-checked read of the job). The mark
 * follows that outcome, never a blanket ✓.
 */
import { ACTIVITY_STATUS_WORDS, TOOL_OUTCOME_REASON_WORDS } from './activityWords'

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

export interface ToolOutcomeBlock { outcome: string; job_id: string | null; reason: string | null }

/** The tools whose ordinary-turn result always carries `_outcome` (backend harness/outcomes.py JOB_TOOLS). */
export const JOB_TOOLS: ReadonlySet<string> = new Set(['run_simulation', 'run_ac_pf_stage', 'wait_for_job', 'abort_simulation'])

/**
 * A result cut by the backend (`{_truncated, preview}`) keeps `_outcome` only
 * as the start of the preview text: read that leading object strictly.
 */
function outcomeFromPreview(preview: string): unknown {
  const head = '{"_outcome": '
  if (!preview.startsWith(head) || preview[head.length] !== '{') return null
  let depth = 0
  let inString = false
  for (let i = head.length; i < preview.length; i++) {
    const c = preview[i]
    if (inString) {
      if (c === '\\') i++
      else if (c === '"') inString = false
    } else if (c === '"') inString = true
    else if (c === '{') depth++
    else if (c === '}' && --depth === 0) {
      try { return JSON.parse(preview.slice(head.length, i + 1)) } catch { return null }
    }
  }
  return null
}

/**
 * The `_outcome` block of a tool result. The handler's own fields are never
 * read. A job tool's returned result whose block is missing, malformed or of
 * another version reads uncertain (fail closed); no result, or any other tool
 * without a block, gives null.
 */
export function toolOutcomeOf(result: unknown, tool?: string): ToolOutcomeBlock | null {
  const r = result as { _outcome?: unknown; _truncated?: unknown; preview?: unknown } | null | undefined
  const raw = r?._outcome ?? (r?._truncated === true && typeof r.preview === 'string' ? outcomeFromPreview(r.preview) : null)
  if (raw && typeof raw === 'object') {
    const { version, outcome, job_id: jobId, reason } = raw as Record<string, unknown>
    if (version === 1 && typeof outcome === 'string') {
      return { outcome, job_id: typeof jobId === 'string' && jobId ? jobId : null,
        reason: typeof reason === 'string' ? reason : null }
    }
  }
  if (raw != null || (result != null && tool !== undefined && JOB_TOOLS.has(tool))) {
    return { outcome: 'uncertain', job_id: null, reason: 'unreadable' }
  }
  return null
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

/** Plain words for why an outcome is unknown, or null when the reason adds nothing. */
export function toolOutcomeReasonWord(outcome: string, reason: string | null): string | null {
  if (toolOutcomeMark(outcome).outcome !== 'uncertain' || !reason) return null
  return TOOL_OUTCOME_REASON_WORDS[reason] ?? null
}

/** The transcript line: `<glyph> <tool> (<word>[ — <reason>])`. */
export function toolOutcomeLine(tool: string, block: ToolOutcomeBlock): string {
  const mark = toolOutcomeMark(block.outcome)
  const why = toolOutcomeReasonWord(block.outcome, block.reason)
  return `${mark.glyph} ${tool} (${mark.word}${why ? ` — ${why}` : ''})`
}

interface OutcomeMessage {
  id: string; role: string; tool_name?: string; tool_result?: unknown; ts: number; origin_project?: string | null
}

export interface CurrentToolOutcome { mark: ToolOutcomeMark; ts: number; why: string | null }

/**
 * What one tool line shows now. An in-flight outcome gives way to the newest
 * later `_outcome` read of the SAME job id in the transcript (the backend's
 * identity-checked read; no guess from unrelated status) in the same project;
 * a line never takes an earlier read, and a null job id never matches. With no later read
 * it stays as it was at its own time, which the caller words "at HH:MM".
 */
export function currentToolOutcome(
  messages: ReadonlyArray<OutcomeMessage>, index: number,
): CurrentToolOutcome | null {
  const own = messages[index]
  const block = own && own.role === 'tool' ? toolOutcomeOf(own.tool_result, own.tool_name) : null
  if (!own || !block) return null
  let current = block
  let ts = own.ts
  if (toolOutcomeMark(block.outcome).inFlight && block.job_id) {
    for (let i = index + 1; i < messages.length; i++) {
      const later = messages[i]
      if (later.role !== 'tool' || (later.origin_project ?? null) !== (own.origin_project ?? null)) continue
      const next = toolOutcomeOf(later.tool_result, later.tool_name)
      if (next && next.job_id === block.job_id) {
        current = next
        ts = later.ts
      }
    }
  }
  return { mark: toolOutcomeMark(current.outcome), ts, why: toolOutcomeReasonWord(current.outcome, current.reason) }
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "14:02" in the viewer's clock, or "4 Nov 14:02" for an earlier day. */
export function outcomeClock(ts: number, now: number = Date.now()): string {
  const d = new Date(ts)
  const n = new Date(now)
  const time = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  const sameDay = d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate()
  return sameDay ? time : `${d.getDate()} ${MONTHS[d.getMonth()]} ${time}`
}

/** The words after the glyph: the word, plus "at HH:MM" while in flight, or why it is unknown. */
export function toolOutcomeWords(current: CurrentToolOutcome, now?: number): string {
  if (current.mark.inFlight) return `${current.mark.word} at ${outcomeClock(current.ts, now)}`
  return current.why ? `${current.mark.word} — ${current.why}` : current.mark.word
}

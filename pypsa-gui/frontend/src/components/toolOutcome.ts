/**
 * Issue 04 (S1b): the chat tool line for a result that carries the backend's
 * `_outcome` (an independent, identity-checked read of the job). The mark
 * follows that outcome, never a blanket ✓.
 */
import {
  ACTIVITY_STATUS_WORDS, TOOL_OUTCOME_FILE_REASON_WORDS, TOOL_OUTCOME_PARTIAL_REASON_WORDS, TOOL_OUTCOME_REASON_WORDS,
} from './activityWords'

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

export interface ToolOutcomeBlock {
  outcome: string; job_id: string | null; reason: string | null
  /** A write's read-back counts (issue 04 S2), when the result carries them. */
  counts?: { requested: number; done: number } | null
}

/** What a write tool's read-back counts (issue 04 S2): "3 of 5 updated". */
const WRITE_VERBS: Record<string, string> = {
  bulk_update_components: 'updated', batch_create_components: 'added',
  delete_component: 'deleted', batch_delete_components: 'deleted',
  // Issue 04 S3: an import counts the file's rows found in the network.
  import_network_nc: 'imported', import_csv_bundle: 'imported', import_excel: 'imported',
  import_matpower: 'imported', import_uploaded_network: 'imported',
}

/** The tools whose `_outcome` reads a network file (issue 04 S3, backend IMPORT_TOOLS | EXPORT_TOOLS). */
const FILE_TOOLS: ReadonlySet<string> = new Set([
  'import_network_nc', 'import_csv_bundle', 'import_excel', 'import_matpower', 'import_uploaded_network',
  'export_network_nc', 'export_csv_bundle', 'export_excel', 'export_matpower',
])

const count = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v) && v >= 0

function countsOf(raw: unknown): ToolOutcomeBlock['counts'] {
  const { requested, done } = (raw ?? {}) as Record<string, unknown>
  return count(requested) && count(done) && done <= requested ? { requested, done } : null
}

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
    const { version, outcome, job_id: jobId, reason, counts } = raw as Record<string, unknown>
    if (version === 1 && typeof outcome === 'string') {
      return { outcome, job_id: typeof jobId === 'string' && jobId ? jobId : null,
        reason: typeof reason === 'string' ? reason : null, counts: countsOf(counts) }
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
export function toolOutcomeReasonWord(outcome: string, reason: string | null, tool?: string): string | null {
  if (!reason) return null
  const state = toolOutcomeMark(outcome).outcome
  if (state === 'partial') return TOOL_OUTCOME_PARTIAL_REASON_WORDS[reason] ?? null
  if (state !== 'uncertain') return null
  if (tool && FILE_TOOLS.has(tool) && reason in TOOL_OUTCOME_FILE_REASON_WORDS) return TOOL_OUTCOME_FILE_REASON_WORDS[reason]
  return TOOL_OUTCOME_REASON_WORDS[reason] ?? null
}

/** "3 of 5" for a partly done write that carries its counts, else null. */
export function toolOutcomeCount(tool: string, block: ToolOutcomeBlock): string | null {
  if (toolOutcomeMark(block.outcome).outcome !== 'partial' || !block.counts || !(tool in WRITE_VERBS)) return null
  return `${block.counts.done} of ${block.counts.requested}`
}

/** The transcript line: `<glyph> <tool> (<word>[: <reason> | : <n> of <m> <verb>])`. */
export function toolOutcomeLine(tool: string, block: ToolOutcomeBlock): string {
  const mark = toolOutcomeMark(block.outcome)
  const why = toolOutcomeReasonWord(block.outcome, block.reason, tool)
  const n = toolOutcomeCount(tool, block)
  const detail = why ?? (n ? `${n} ${WRITE_VERBS[tool]}` : null)
  return `${mark.glyph} ${tool} (${mark.word}${detail ? `: ${detail}` : ''})`
}

interface OutcomeMessage {
  id: string; role: string; tool_name?: string; tool_result?: unknown; ts: number; origin_project?: string | null
}

export interface CurrentToolOutcome {
  mark: ToolOutcomeMark; ts: number; why: string | null
  /** "3 of 5" and its verb for a partly done write; null otherwise. */
  count?: { text: string; verb: string } | null
}

/**
 * What one tool line shows now. An in-flight outcome gives way to the newest
 * later `_outcome` read of the SAME job id in the transcript (the backend's
 * identity-checked read; no guess from unrelated status) in the same project;
 * a line never takes an earlier read, and a null job id never matches. With no later read
 * it stays as it was at its own time, which the caller words "as of HH:MM".
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
  const n = own.tool_name ? toolOutcomeCount(own.tool_name, current) : null
  return { mark: toolOutcomeMark(current.outcome), ts, why: toolOutcomeReasonWord(current.outcome, current.reason, own.tool_name),
    count: n && own.tool_name ? { text: n, verb: WRITE_VERBS[own.tool_name] } : null }
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "14:02" in the viewer's clock, or "4 Nov 14:02" for an earlier day ("as of 14:02"). */
export function outcomeClock(ts: number, now: number = Date.now()): string {
  const d = new Date(ts)
  const n = new Date(now)
  const time = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  const sameDay = d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate()
  return sameDay ? time : `${d.getDate()} ${MONTHS[d.getMonth()]} ${time}`
}

/** The words after the glyph: the word, plus "as of HH:MM" while in flight, or why it is unknown. */
export function toolOutcomeWords(current: CurrentToolOutcome, now?: number): string {
  if (current.mark.inFlight) return `${current.mark.word} as of ${outcomeClock(current.ts, now)}`
  if (current.why) return `${current.mark.word}: ${current.why}`
  return current.count ? `${current.mark.word}: ${current.count.text} ${current.count.verb}` : current.mark.word
}

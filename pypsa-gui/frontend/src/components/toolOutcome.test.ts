/** Issue 04 (S1b): the chat tool-line mark follows the backend's `_outcome`. */
import { describe, expect, it } from 'vitest'
import { ACTIVITY_STATUS_WORDS } from './activityWords'
import {
  TOOL_OUTCOMES, currentToolOutcome, toolOutcomeLine, toolOutcomeMark, toolOutcomeOf, toolOutcomeWords,
} from './toolOutcome'

describe('tool outcome marks', () => {
  it('has a word for every outcome the backend emits', () => {
    expect(TOOL_OUTCOMES.filter(o => !(o in ACTIVITY_STATUS_WORDS))).toEqual([])
  })

  it.each([
    ['submitted', '…', 'queued', 'text', true],
    ['running', '…', 'running', 'text', true],
    ['cancel_requested', '…', 'stopping', 'text', true],
    ['completed', '✓', 'done', 'text', false],
    ['failed', '✕', 'failed', 'warn', false],
    ['cancelled', '–', 'cancelled', 'muted', false],
    ['partial', '–', 'partly done', 'muted', false],
    ['uncertain', '–', 'outcome unknown', 'muted', false],
    ['denied', '–', 'denied', 'muted', false],
  ] as const)('%s → %s %s', (outcome, glyph, word, tone, inFlight) => {
    expect(toolOutcomeMark(outcome)).toEqual({ outcome, glyph, word, tone, inFlight })
  })

  it('reads an unknown outcome as uncertain and never as done', () => {
    expect(toolOutcomeMark('success').outcome).toBe('uncertain')
    expect(toolOutcomeLine('run_simulation', 'success')).toBe('– run_simulation (outcome unknown)')
  })

  it('writes the transcript line from the outcome, never a blanket ✓', () => {
    expect(toolOutcomeLine('run_simulation', 'running')).toBe('… run_simulation (running)')
    expect(toolOutcomeLine('wait_for_job', 'completed')).toBe('✓ wait_for_job (done)')
    expect(toolOutcomeLine('run_ac_pf_stage', 'failed')).toBe('✕ run_ac_pf_stage (failed)')
  })

  it.each([undefined, null, {}, { _outcome: null }, { _outcome: 'running' }, { _outcome: { outcome: 3 } }])(
    'finds no outcome in %j', result => expect(toolOutcomeOf(result)).toBeNull())

  it('reads the outcome and job id; an empty job id is none', () => {
    expect(toolOutcomeOf({ _outcome: { outcome: 'running', job_id: 'fg:1' } })).toEqual({ outcome: 'running', job_id: 'fg:1' })
    expect(toolOutcomeOf({ _outcome: { outcome: 'running', job_id: '' } })).toEqual({ outcome: 'running', job_id: null })
  })
})

const at = (h: number, m: number) => new Date(2026, 10, 5, h, m).getTime()
const tool = (id: string, outcome: string, jobId: string | null, ts: number) => ({
  id, role: 'tool', ts, tool_result: { _outcome: { outcome, job_id: jobId } },
})

describe('current tool outcome', () => {
  it('words an in-flight line as of its own time when nothing later read the job', () => {
    const now = currentToolOutcome([tool('a', 'running', 'fg:1', at(14, 2))], 0)!
    expect(now.mark.outcome).toBe('running')
    expect(toolOutcomeWords(now)).toBe('running at 14:02')
  })

  it('takes the newest later read of the same job', () => {
    const messages = [
      tool('a', 'running', 'fg:1', at(14, 2)),
      { id: 'b', role: 'assistant', ts: at(14, 3) },
      tool('c', 'cancel_requested', 'fg:1', at(14, 4)),
      tool('d', 'cancelled', 'fg:1', at(14, 6)),
    ]
    const now = currentToolOutcome(messages, 0)!
    expect(now.mark.outcome).toBe('cancelled')
    expect(toolOutcomeWords(now)).toBe('cancelled')
    expect(currentToolOutcome(messages, 2)!.mark.outcome).toBe('cancelled')
  })

  it('keeps an in-flight line when a later read is of another job or has no job id', () => {
    const messages = [
      tool('a', 'running', 'fg:1', at(14, 2)),
      tool('b', 'completed', 'fg:2', at(14, 5)),
      tool('c', 'completed', null, at(14, 6)),
    ]
    expect(toolOutcomeWords(currentToolOutcome(messages, 0)!)).toBe('running at 14:02')
  })

  it('never lets a line without a job id take a later read', () => {
    const messages = [tool('a', 'running', null, at(14, 2)), tool('b', 'completed', null, at(14, 5))]
    expect(currentToolOutcome(messages, 0)!.mark.outcome).toBe('running')
  })

  it('keeps a settled outcome even when a later read of the job differs', () => {
    const messages = [tool('a', 'failed', 'fg:1', at(14, 2)), tool('b', 'running', 'fg:1', at(14, 5))]
    expect(currentToolOutcome(messages, 0)!.mark.outcome).toBe('failed')
  })

  it('gives nothing for a message without an outcome', () => {
    expect(currentToolOutcome([{ id: 'a', role: 'tool', ts: 0, tool_result: { ok: true } }], 0)).toBeNull()
    expect(currentToolOutcome([{ id: 'a', role: 'assistant', ts: 0, tool_result: { _outcome: { outcome: 'running' } } }], 0)).toBeNull()
  })
})

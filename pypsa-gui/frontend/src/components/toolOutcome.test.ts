/** Issue 04 (S1b): the chat tool-line mark follows the backend's `_outcome`. */
import { describe, expect, it } from 'vitest'
import { ACTIVITY_STATUS_WORDS } from './activityWords'
import {
  TOOL_OUTCOMES, currentToolOutcome, outcomeClock, toolOutcomeLine, toolOutcomeMark, toolOutcomeOf, toolOutcomeWords,
} from './toolOutcome'

const block = (outcome: string, job_id: string | null = 'fg:1', reason: string | null = null) => ({ outcome, job_id, reason })

describe('tool outcome marks', () => {
  it('has a word for every outcome the backend emits', () => {
    // harness/outcomes.py OUTCOMES, plus denied.
    expect([...TOOL_OUTCOMES].sort()).toEqual(['cancel_requested', 'cancelled', 'completed', 'denied', 'failed',
      'partial', 'running', 'submitted', 'uncertain'])
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
    expect(toolOutcomeLine('run_simulation', block('success'))).toBe('– run_simulation (outcome unknown)')
  })

  it('writes the transcript line from the outcome, never a blanket ✓', () => {
    expect(toolOutcomeLine('run_simulation', block('running'))).toBe('… run_simulation (running)')
    expect(toolOutcomeLine('wait_for_job', block('completed'))).toBe('✓ wait_for_job (done)')
    expect(toolOutcomeLine('run_ac_pf_stage', block('failed'))).toBe('✕ run_ac_pf_stage (failed)')
  })

  it.each([
    ['adapter_pending', '– wait_for_job (outcome unknown: this kind of job cannot be checked yet)'],
    ['no_submission', '– wait_for_job (outcome unknown: no job from this call was found)'],
    ['read_failed', '– wait_for_job (outcome unknown)'],
  ])('words an unknown outcome with reason %s', (reason, line) => {
    expect(toolOutcomeLine('wait_for_job', block('uncertain', 'ad:1', reason))).toBe(line)
  })

  it.each([undefined, null, {}, { ok: true }])('finds no outcome for a non-job tool result %j', result => {
    expect(toolOutcomeOf(result, 'list_components')).toBeNull()
  })

  it('finds no outcome when a job tool has not returned yet', () => {
    expect(toolOutcomeOf(undefined, 'run_simulation')).toBeNull()
  })

  it.each([
    { completed: true, status: 'completed' },
    { _outcome: null, status: 'completed' },
    { _outcome: 'completed' },
    { _outcome: { outcome: 3, version: 1 } },
    { _outcome: { outcome: 'completed' } },
    { _outcome: { outcome: 'completed', version: 2 } },
    { _truncated: true, length: 9000, preview: '{"status": "completed", "_outcome": {"version": 1, "outcome": "completed"}}' },
    { _truncated: true, length: 9000, preview: '{"_outcome": {"version": 1, "outcome": "comp' },
  ])('fails closed to uncertain for a job tool result %j', result => {
    expect(toolOutcomeOf(result, 'run_simulation')).toEqual({ outcome: 'uncertain', job_id: null, reason: 'unreadable' })
  })

  it('never reads the handler fields when the outcome is present', () => {
    expect(toolOutcomeOf({ _outcome: { version: 1, outcome: 'running', job_id: 'fg:1', reason: null },
      completed: true, status: 'completed' }, 'run_simulation')?.outcome).toBe('running')
  })

  it('recovers the outcome from the start of a truncated preview', () => {
    const full = { _outcome: { version: 1, outcome: 'running', job_id: 'fg:"x}', reason: null }, log: 'y'.repeat(5000), status: 'completed' }
    const preview = JSON.stringify(full, null, 0).replace(/^\{"_outcome":/, '{"_outcome": ').slice(0, 4000) + '...'
    expect(toolOutcomeOf({ _truncated: true, length: 9000, preview }, 'run_simulation'))
      .toEqual({ outcome: 'running', job_id: 'fg:"x}', reason: null })
  })

  it('reads the outcome, job id and reason; an empty job id is none', () => {
    expect(toolOutcomeOf({ _outcome: { version: 1, outcome: 'running', job_id: 'fg:1' } }))
      .toEqual({ outcome: 'running', job_id: 'fg:1', reason: null })
    expect(toolOutcomeOf({ _outcome: { version: 1, outcome: 'uncertain', job_id: '', reason: 'adapter_pending' } }))
      .toEqual({ outcome: 'uncertain', job_id: null, reason: 'adapter_pending' })
  })
})

const at = (h: number, m: number) => new Date(2026, 10, 5, h, m).getTime()
const tool = (id: string, outcome: string, jobId: string | null, ts: number, project: string | null = 'Demo') => ({
  id, role: 'tool', ts, tool_name: 'wait_for_job', origin_project: project,
  tool_result: { _outcome: { version: 1, outcome, job_id: jobId, reason: null } },
})

describe('current tool outcome', () => {
  it('words an in-flight line as of its own time when nothing later read the job', () => {
    const now = currentToolOutcome([tool('a', 'running', 'fg:1', at(14, 2))], 0)!
    expect(now.mark.outcome).toBe('running')
    expect(toolOutcomeWords(now, at(18, 0))).toBe('running as of 14:02')
    expect(toolOutcomeWords(now, at(18, 0) + 86400000)).toBe('running as of 5 Nov 14:02')
  })

  it('dates a time from an earlier day only', () => {
    expect(outcomeClock(at(9, 5), at(23, 59))).toBe('09:05')
    expect(outcomeClock(new Date(2026, 0, 31, 23, 59).getTime(), new Date(2026, 1, 1, 0, 1).getTime())).toBe('31 Jan 23:59')
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
    expect(toolOutcomeWords(currentToolOutcome(messages, 0)!, at(15, 0))).toBe('running as of 14:02')
  })

  it('keeps an in-flight line when a later read of the same job id is in another project', () => {
    const messages = [tool('a', 'running', 'fg:1', at(14, 2)), tool('b', 'completed', 'fg:1', at(14, 5), 'Other')]
    expect(currentToolOutcome(messages, 0)!.mark.outcome).toBe('running')
  })

  it('never lets an earlier read override a later line', () => {
    const messages = [tool('a', 'completed', 'fg:1', at(14, 2)), tool('b', 'running', 'fg:1', at(14, 5))]
    const later = currentToolOutcome(messages, 1)!
    expect(later.mark.outcome).toBe('running')
    expect(toolOutcomeWords(later, at(15, 0))).toBe('running as of 14:05')
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
    expect(currentToolOutcome([{ id: 'a', role: 'tool', ts: 0, tool_name: 'list_components', tool_result: { ok: true } }], 0)).toBeNull()
    expect(currentToolOutcome([{ id: 'a', role: 'tool', ts: 0, tool_name: 'run_simulation' }], 0)).toBeNull()
    expect(currentToolOutcome([{ id: 'a', role: 'assistant', ts: 0, tool_result: { _outcome: { outcome: 'running' } } }], 0)).toBeNull()
  })
})

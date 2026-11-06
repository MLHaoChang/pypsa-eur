/** Issue 04 (S1b): the chat tool-line mark follows the backend's `_outcome`. */
import { describe, expect, it } from 'vitest'
import { ACTIVITY_STATUS_WORDS } from './activityWords'
import {
  TOOL_OUTCOMES, currentToolOutcome, outcomeClock, refusalDetail, toolOutcomeLine, toolOutcomeMark, toolOutcomeOf, toolOutcomeReasonWord, toolOutcomeWords,
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
      .toEqual({ outcome: 'running', job_id: 'fg:"x}', reason: null, counts: null })
  })

  it('reads the outcome, job id and reason; an empty job id is none', () => {
    expect(toolOutcomeOf({ _outcome: { version: 1, outcome: 'running', job_id: 'fg:1' } }))
      .toEqual({ outcome: 'running', job_id: 'fg:1', reason: null, counts: null })
    expect(toolOutcomeOf({ _outcome: { version: 1, outcome: 'uncertain', job_id: '', reason: 'adapter_pending' } }))
      .toEqual({ outcome: 'uncertain', job_id: null, reason: 'adapter_pending', counts: null })
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

describe('write outcomes (issue 04 S2)', () => {
  const write = (tool: string, outcome: string, counts: unknown) => ({
    id: 'w', role: 'tool', ts: at(14, 2), tool_name: tool, origin_project: 'Demo',
    tool_result: { _outcome: { version: 1, outcome, reason: null, counts } },
  })

  it.each([
    ['bulk_update_components', '– bulk_update_components (partly done: 3 of 5 updated)'],
    ['batch_create_components', '– batch_create_components (partly done: 3 of 5 added)'],
    ['delete_component', '– delete_component (partly done: 3 of 5 deleted)'],
    ['batch_delete_components', '– batch_delete_components (partly done: 3 of 5 deleted)'],
  ])('puts the count on a partly done %s line', (tool, line) => {
    const block = toolOutcomeOf({ _outcome: { version: 1, outcome: 'partial', counts: { requested: 5, done: 3 } } }, tool)!
    expect(toolOutcomeLine(tool, block)).toBe(line)
    expect(toolOutcomeWords(currentToolOutcome([write(tool, 'partial', { requested: 5, done: 3 })], 0)!))
      .toBe(line.slice(line.indexOf('(') + 1, -1))
  })

  it.each([
    ['completed', { requested: 5, done: 5 }, 'done'],
    ['partial', null, 'partly done'],
    ['partial', { requested: 5 }, 'partly done'],
    ['partial', { requested: 2, done: 3 }, 'partly done'],
    ['partial', { requested: 5, done: -1 }, 'partly done'],
  ])('keeps the bare word for %s with counts %j', (outcome, counts, words) => {
    expect(toolOutcomeWords(currentToolOutcome([write('bulk_update_components', outcome, counts)], 0)!)).toBe(words)
  })

  it('never counts a job tool', () => {
    const block = toolOutcomeOf({ _outcome: { version: 1, outcome: 'partial', counts: { requested: 5, done: 3 } } }, 'run_ac_pf_stage')!
    expect(toolOutcomeLine('run_ac_pf_stage', block)).toBe('– run_ac_pf_stage (partly done)')
  })
})

describe('undo outcomes (issue 04 S3)', () => {
  it.each([
    ['completed', null, '✓ undo_last (done)'],
    ['uncertain', 'entry_moved', '– undo_last (outcome unknown: a different change was undone)'],
    ['uncertain', 'not_restored', '– undo_last (outcome unknown: the network does not match its state before that change)'],
    ['failed', 'nothing_to_undo', '✕ undo_last (failed)'],
  ])('words an undo that read back %s (%s)', (outcome, reason, line) => {
    const block = toolOutcomeOf({ _outcome: { version: 1, outcome, reason } }, 'undo_last')!
    expect(toolOutcomeLine('undo_last', block)).toBe(line)
  })
})

describe('import and export outcomes (issue 04 S3)', () => {
  const counts = (done: number) => ({ requested: 7, done, extra: 0, allowed_extras: [] })
  it.each([
    ['import_network_nc', 'completed', null, counts(7), '✓ import_network_nc (done)'],
    ['import_csv_bundle', 'partial', 'rows_missing', counts(5), '– import_csv_bundle (partly done: 5 of 7 imported)'],
    ['import_network_nc', 'partial', 'extra_rows', counts(7),
      '– import_network_nc (partly done: the network has rows the file does not have)'],
    ['import_uploaded_network', 'uncertain', 'not_as_file', counts(7),
      '– import_uploaded_network (outcome unknown: the network does not match the imported file)'],
    ['import_excel', 'uncertain', 'adapter_pending', null, '– import_excel (outcome unknown: this file format cannot be checked yet)'],
    ['export_network_nc', 'completed', null, null, '✓ export_network_nc (done)'],
    ['export_csv_bundle', 'uncertain', 'not_as_network', null,
      '– export_csv_bundle (outcome unknown: the exported file does not match the network)'],
    ['export_network_nc', 'uncertain', 'export_file_count', null,
      '– export_network_nc (outcome unknown: the exported file could not be identified)'],
    ['export_matpower', 'uncertain', 'adapter_pending', null, '– export_matpower (outcome unknown: this file format cannot be checked yet)'],
  ])('words %s that read back %s (%s)', (tool, outcome, reason, c, line) => {
    const block = toolOutcomeOf({ _outcome: { version: 1, outcome, reason, counts: c } }, tool)!
    expect(toolOutcomeLine(tool, block)).toBe(line)
  })

  it('keeps the job words for a job that is adapter_pending', () => {
    const block = toolOutcomeOf({ _outcome: { version: 1, outcome: 'uncertain', job_id: 'ad:x', reason: 'adapter_pending' } }, 'wait_for_job')!
    expect(toolOutcomeLine('wait_for_job', block)).toBe('– wait_for_job (outcome unknown: this kind of job cannot be checked yet)')
  })

  it('gives no partial word for a reason without one', () => {
    expect(toolOutcomeReasonWord('partial', 'rows_missing', 'import_network_nc')).toBeNull()
  })
})

describe('denied outcomes (issue 04 S4a)', () => {
  it.each([
    ['confirmation_denied', 'you declined it'],
    ['confirmation_expired', 'the approval expired'],
    ['confirmation_revoked', 'the approval was withdrawn'],
    ['required_guard_failed', 'the request did not pass a required check'],
    ['confirmation_args_changed', 'the request changed after you approved it'],
    ['no_progress_stop', 'stopped after repeating the same failing request'],
    ['aborted', 'the request was stopped before you answered'],
    ['user_restriction', 'a rule you set for this project blocks changes'],
    ['approval_unavailable', 'the approval could not be recorded'],
    ['round_change_cap', 'the assistant used up the changes allowed for this request'],
    ['unknown_decision', 'your answer to the approval could not be read'],
    ['project_locked', 'another change to this project was in progress'],
    ['project_lock_unavailable', "the project's edit lock could not be checked"],
    ['operation_pending', 'the same request may still be running'],
    ['operation_conflict', 'an earlier request may still be running'],
  ])('words a refusal of kind %s', (reason, words) => {
    const block = toolOutcomeOf({ _outcome: { version: 1, outcome: 'denied', reason } }, 'bulk_update_components')!
    expect(toolOutcomeLine('bulk_update_components', block)).toBe(`– bulk_update_components (denied: ${words})`)
  })

  it('never marks a denial ✕ and shows no raw key for an unworded kind', () => {
    const block = toolOutcomeOf({ _outcome: { version: 1, outcome: 'denied', reason: 'some_new_kind' } }, 'delete_component')!
    expect(toolOutcomeLine('delete_component', block)).toBe('– delete_component (denied)')
  })

  it.each(['run_simulation', 'import_network_nc'])('words other_context for %s as another project', (tool) => {
    expect(toolOutcomeReasonWord('uncertain', 'other_context', tool)).toBe('another project was opened while it was being checked')
  })
})

describe('refusal details (issue 04 S4a)', () => {
  it.each([
    ["'bulk_update_components' did not run: updates.p_nom: missing required 'names'.", 'required_guard_failed',
      'bulk_update_components did not run: updates.p_nom: missing required names.'],
    ["this round's changes are spent; 'x' did not run", 'round_change_cap', "this round's changes are spent; x did not run"],
    ["deny on confirmation for 'delete_component'", 'confirmation_denied', null],
    ['blocked: confirmation_expired', 'confirmation_expired', null],
    ['refused as no_progress_stop', 'required_guard_failed', null],
    ['  ', 'aborted', null],
    ['Your saved project rule blocks non-read tools.', 'user_restriction', 'A rule you set for this project blocks changes.'],
    ["approval for 'delete_component' could not be recorded", 'approval_unavailable',
      'The app could not save the approval: its data folder may be full or not writable.'],
    [undefined, 'aborted', null],
    ["The same solve_queue_enqueue request from earlier may still be running; its outcome is unknown.", 'operation_pending',
      'An earlier request with the same settings may still be running, so it was not started again. Ask the assistant to check it or stop it.'],
    ["The solve queue already has this project's solve, queued or running, so it was not queued again.", 'operation_pending',
      'An earlier request with the same settings may still be running, so it was not started again. Ask the assistant to check it or stop it.'],
    ["An earlier run_simulation request with other settings may still be running.", 'operation_conflict',
      'An earlier request with other settings may still be running, so this one was not started. Ask the assistant to check it or stop it first.'],
  ])('%s → %s', (message, kind, detail) => {
    expect(refusalDetail(message, kind)).toBe(detail)
  })
})

describe('raise, timeout and extra edits (issue 04 S4b)', () => {
  const line = (tool: string, outcome: Record<string, unknown>) =>
    toolOutcomeLine(tool, toolOutcomeOf({ _outcome: { version: 1, ...outcome } }, tool)!)

  it('puts the extra count first', () => {
    expect(line('bulk_update_components', { outcome: 'partial', reason: 'extra_edits',
      counts: { requested: 1, done: 1, not_done: 0, unknown: 0, extra: 2 } }))
      .toBe('– bulk_update_components (partly done: 2 extra changes it was not asked to make)')
    expect(line('bulk_update_components', { outcome: 'partial', reason: 'extra_edits',
      counts: { requested: 1, done: 1, extra: 1 } }))
      .toBe('– bulk_update_components (partly done: 1 extra change it was not asked to make)')
  })

  it('keeps the short count beside the extra count', () => {
    expect(line('bulk_update_components', { outcome: 'partial', reason: 'extra_edits',
      counts: { requested: 5, done: 3, extra: 2 } }))
      .toBe('– bulk_update_components (partly done: 3 of 5 updated · 2 extra changes)')
  })

  it('words extra edits without a count', () => {
    expect(line('bulk_update_components', { outcome: 'partial', reason: 'extra_edits' }))
      .toBe('– bulk_update_components (partly done: it also changed things it was not asked to)')
    expect(line('bulk_update_components', { outcome: 'partial', reason: 'extra_edits',
      counts: { requested: 1, done: 1, extra: -1 } }))
      .toBe('– bulk_update_components (partly done: it also changed things it was not asked to)')
  })

  it.each([
    ['tool_timeout', 'no answer in time'],
    ['tool_not_started', 'no answer in time'],
    ['changed_before_error', 'it stopped with an error after changing the network'],
    ['not_as_requested', 'it changed the values, but not to what was asked'],
  ])('words an unknown outcome of reason %s', (reason, words) => {
    expect(line('bulk_update_components', { outcome: 'uncertain', reason }))
      .toBe(`– bulk_update_components (outcome unknown: ${words})`)
  })

  it('marks a write proven unchanged after a raise as failed', () => {
    expect(line('undo_last', { outcome: 'failed', reason: null })).toBe('✕ undo_last (failed)')
  })

  it('says the same in the current-outcome words Guided uses', () => {
    const msg = { id: 'a', role: 'tool', tool_name: 'bulk_update_components', ts: 0,
      tool_result: { _outcome: { version: 1, outcome: 'partial', reason: 'extra_edits', counts: { requested: 1, done: 1, extra: 2 } } } }
    expect(currentToolOutcome([msg], 0)!.why).toBe('2 extra changes it was not asked to make')
  })
})

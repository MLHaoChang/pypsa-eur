// Study harness issue 08: one run in each state the plan card shows, as
// `/api/chat/runs/{task_id}` serves it. Used by the tests and by
// `scripts/run-monitor-shots.mjs` (the PR's screenshots).
import type { Envelope, RunAction, RunMonitorView, RunStage } from '../../api/runMonitor'

export const TASK_ID = '019a5b49-3a72-5681-92f2-6fcc38f99cd7'

export const ENVELOPE: Envelope = {
  id: 'e1', state: 'proposed', expires_at: null,
  edits: [
    { tool: 'update_component', target: { component: 'Generator', name: 'genset_new_*', attr: 'p_nom_max' }, bounds: { min: 0, rel: 0.5 } },
    { tool: 'update_component', target: { component: 'Generator', name: 'pv_new_*', attr: 'p_nom_max' }, bounds: { min: 0, rel: 0.5 } },
    { tool: 'update_component', target: { component: 'StorageUnit', name: 'bess_new_*', attr: 'p_nom_max' }, bounds: { min: 0, rel: 0.5 } },
    { tool: 'update_component', target: { component: 'StorageUnit', name: 'bess_new_*', attr: 'max_hours' }, bounds: { min: 1, max: 8 } },
  ],
  runs: [{ tool: 'solve_queue_enqueue' }],
  budget: { solves: 6, rounds: 6, wall_time_h: 8 },
  research: { allowed: false, cost_cap_eur: 2 },
  notify: { email: false }, used: 0, refused: 0,
}

const ORDER = ['intake', 'setup', 'inputs', 'input_gate', 'solve', 'result_gate', 'explore', 'report', 'handoff']
const SKIPPED: Record<string, string> = { explore: 'Exploring cases is not part of a run yet.', report: 'The run report is not part of a run yet.' }

// The grid_only@1 profile's own skip reasons (`services/run_profiles.py`).
const GRID_SKIPPED: Record<string, string> = {
  explore: 'Grid-only runs explore no cases by default.',
  report: 'No report from grid-only runs yet: the report would read results the load flow has overwritten.',
}

function stages(at: string, current: Partial<RunStage> = {}, done = false): RunStage[] {
  const index = ORDER.indexOf(at)
  return ORDER.map((stage, i) => SKIPPED[stage] ? { stage, status: 'skipped', reason: SKIPPED[stage] }
    : done || i < index ? { stage, status: 'passed' }
    : i === index ? { stage, status: 'active', ...current } : { stage, status: 'pending' })
}

const PASS = [
  { rule: 'in.validate.errors', verdict: 'pass', fact: 'validate.errors', value: 0 },
  { rule: 'in.readiness.ready', verdict: 'pass', fact: 'readiness.ready', value: true },
]
const FAIL = [
  { rule: 'in.validate.errors', verdict: 'pass', fact: 'validate.errors', value: 0 },
  { rule: 'in.diagnose.unserved_islands', verdict: 'fail', fact: 'diagnose.unserved_islands', value: 1 },
]

const MONITOR = {
  budgets: { cost_eur: { used: 0.42, limit: 5, unmetered: false } },
  unattended: { available: true }, email: { available: true },
}
const GRANTED = { ...ENVELOPE, state: 'granted' as const, expires_at: '2026-10-28T17:00:00Z', used: 3 }
const GRANTED_BUDGETS = { ...MONITOR.budgets, solves: { used: 3, of: 6 }, expires_at: '2026-10-28T17:00:00Z' }

/** `run_monitor._actions`: the run buttons that can work now. A run held on
 *  a stop reason or a failed stage already waits for the user: no Pause. */
function actions(status: string, run: RunMonitorView['run']): RunAction[] {
  const over = status === 'cancelled' || status === 'completed'
  const failed = run.stages.find(s => s.stage === run.stage)?.status === 'failed'
  const resumable = !over && ['paused', 'needs_user'].includes(run.status) && (Boolean(run.reason) || failed)
  const held = run.status === 'needs_user' && (failed || !(run.reason ?? 'waiting_').startsWith('waiting_'))
  return ([['pause', !over && ['running', 'waiting_job', 'needs_user'].includes(run.status) && !held],
    ['resume', resumable], ['stop', !over]] as const).filter(([, ok]) => ok).map(([name]) => name)
}

function view(over: Partial<RunMonitorView> & { run: RunMonitorView['run'] }): RunMonitorView {
  const out = { task_id: TASK_ID, title: 'Own site study', status: 'ready', monitor: MONITOR, ...over } as RunMonitorView
  return { ...out, monitor: { ...out.monitor, actions: actions(out.status, out.run) } }
}

const run = (stage: string, extra: Partial<RunMonitorView['run']> & { stages: RunStage[] }) =>
  ({ profile: 'eh_site@1', mode: 'stepwise', status: 'running', stage, ...extra })

const TARIFF_GAP = { tool: 'attach_tariff', target: { component: 'Settings', name: 'Commercial', attr: 'import_tariff_ref' },
  source: { kind: 'library_tariff' } }
const REVIEW = view({ status: 'ready', run: run('intake', { mode: 'unattended', status: 'needs_user', stages: stages('intake') }), envelope: ENVELOPE })

/** Every state the PR's screenshots show, by key. The `envelope_*` keys render
 *  in the plan-review Choice card's Envelope section; the others in the plan
 *  card. `STATE_STEPS` says what to click after rendering one. */
export const STATES: Record<string, RunMonitorView> = {
  proposal: view({ run: run('inputs', { stages: stages('inputs') }), envelope: ENVELOPE, paused_at_card: 'inputs' }),
  envelope_approval: REVIEW,
  envelope_narrowed: REVIEW,
  // SH-26: the attach_tariff gap's held tariffs, two held and none.
  envelope_held_two: { ...REVIEW, envelope: { ...ENVELOPE, gaps: [{ ...TARIFF_GAP, held_state: 'held', held: [
    { name: 'nl-commercial', version: 1, hash: 'a1b2c3d4'.padEnd(64, '0') },
    { name: 'nl-peak-shaving', version: 3, hash: '99887766'.padEnd(64, '1') }] }] } },
  envelope_held_one: { ...REVIEW, envelope: { ...ENVELOPE, gaps: [{ ...TARIFF_GAP, held_state: 'held', held: [
    { name: 'nl-commercial', version: 1, hash: 'a1b2c3d4'.padEnd(64, '0') }] }] } },
  envelope_held_none: { ...REVIEW, envelope: { ...ENVELOPE, gaps: [{ ...TARIFF_GAP, held_state: 'none', held: [] }] } },
  envelope_unattended_disabled: { ...REVIEW, monitor: { ...MONITOR, unattended: { available: false, reason: 'no_provider' } } },
  envelope_email_disabled: { ...REVIEW, monitor: { ...MONITOR, email: { available: false } } },
  gate_pass: view({ run: run('input_gate', { stages: stages('input_gate', { gate: PASS }) }), envelope: GRANTED,
    monitor: { ...MONITOR, budgets: GRANTED_BUDGETS } }),
  gate_fail: view({ status: 'needs_review', run: run('input_gate', { status: 'needs_user', stages: stages('input_gate', { status: 'failed', gate: FAIL }) }),
    envelope: GRANTED, monitor: { ...MONITOR, budgets: GRANTED_BUDGETS } }),
  loop: view({ run: run('solve', { stages: stages('solve', { loop: { round: 2, of: 6 } }) }), envelope: GRANTED,
    monitor: { ...MONITOR, budgets: GRANTED_BUDGETS } }),
  waiting: view({ run: run('solve', { status: 'needs_user', reason: 'waiting_card', waiting: { kind: 'card', ref: 'solve.1' },
    stages: stages('solve') }), monitor: { ...MONITOR, waiting: { kind: 'card', ref: 'solve.1' }, card: {
    tool_use_id: 'run-solve.1-ab12', tool_name: 'solve_queue_enqueue', args: {}, safety_tier: 'execution',
    confirmation_token: 'tok1', ttl_seconds: 300, session_id: `run:${TASK_ID}`, expires_at_epoch_ms: Date.now() + 300_000 } } }),
  paused: view({ run: run('solve', { status: 'paused', reason: 'user', stages: stages('solve') }), envelope: GRANTED,
    monitor: { ...MONITOR, budgets: GRANTED_BUDGETS } }),
  stop_confirm: view({ run: run('solve', { stages: stages('solve') }), envelope: GRANTED,
    monitor: { ...MONITOR, budgets: GRANTED_BUDGETS } }),
  stopped: view({ status: 'cancelled', run: run('solve', { status: 'cancelled', stages: stages('solve') }),
    envelope: { ...GRANTED, state: 'revoked' } }),
  finished: view({ status: 'completed', run: run('handoff', { status: 'completed', stages: stages('handoff', {}, true) }),
    envelope: GRANTED, monitor: { ...MONITOR, budgets: GRANTED_BUDGETS } }),
  finished_report: view({ status: 'completed', run: run('handoff', { status: 'completed',
    stages: stages('handoff', {}, true).map(s => (s.stage === 'report' ? { stage: 'report', status: 'passed' } : s)) }),
    envelope: GRANTED, monitor: { ...MONITOR, budgets: GRANTED_BUDGETS } }),
  // Study harness 15: a grid-only run, finished, with explore and the report skipped by the profile.
  finished_grid_only: view({ status: 'completed', run: run('handoff', { profile: 'grid_only@1', status: 'completed',
    stages: stages('handoff', {}, true).map(s => s.stage in GRID_SKIPPED
      ? { stage: s.stage, status: 'skipped', reason: GRID_SKIPPED[s.stage] } : s) }),
    envelope: GRANTED, monitor: { ...MONITOR, budgets: GRANTED_BUDGETS } }),
  // GK B (62): a reason on a stage that was not skipped (passed, failed) is never shown as a skip reason.
  finished_with_stage_reasons: view({ status: 'completed', run: run('handoff', { status: 'completed',
    stages: stages('handoff', {}, true).map(s => s.stage === 'solve' ? { ...s, reason: 'A solve note' }
      : s.stage === 'input_gate' ? { ...s, status: 'failed', reason: 'A gate note' } : s) }),
    envelope: GRANTED, monitor: { ...MONITOR, budgets: GRANTED_BUDGETS } }),
  over_budget: view({ status: 'needs_review', run: run('solve', { status: 'needs_user', reason: 'budget', stages: stages('solve') }),
    monitor: { ...MONITOR, budgets: { cost_eur: { used: 5.01, limit: 5, unmetered: false } } } }),
  lost_send: view({ status: 'needs_review', run: run('input_gate', { status: 'needs_user', reason: 'uncertain', stages: stages('input_gate') }),
    monitor: { ...MONITOR, budgets: { ...MONITOR.budgets, reserved: { unit: 'eur', spent: 2.81, open: 0, at_bound: 1 } } } }),
  lost_sends: view({ status: 'needs_review', run: run('input_gate', { status: 'needs_user', reason: 'uncertain', stages: stages('input_gate') }),
    envelope: GRANTED, monitor: { ...MONITOR, budgets: { ...GRANTED_BUDGETS, cost_eur: { used: 0, limit: 5, unmetered: true },
      reserved: { unit: 'eur', spent: 5.6, open: 0, at_bound: 2 } } } }),
  loop_notice_sent: view({ status: 'needs_review', run: run('input_gate', { status: 'needs_user', reason: 'exhausted', stages: stages('input_gate'), loop: { ended: 'exhausted', notice: { sent: true } } }),
    monitor: MONITOR }),
  loop_notice_failed: view({ status: 'needs_review', run: run('input_gate', { status: 'needs_user', reason: 'exhausted', stages: stages('input_gate'), loop: { ended: 'exhausted', notice: { sent: false, error: 'send_failed' } } }),
    monitor: MONITOR }),
  loop_notice_no_address: view({ status: 'needs_review', run: run('input_gate', { status: 'needs_user', reason: 'exhausted', stages: stages('input_gate'), loop: { ended: 'exhausted', notice: { sent: false, error: 'no_address' } } }),
    monitor: MONITOR }),
  loop_notice_no_mailer: view({ status: 'needs_review', run: run('input_gate', { status: 'needs_user', reason: 'exhausted', stages: stages('input_gate'), loop: { ended: 'exhausted', notice: { sent: false, error: 'no_mailer' } } }),
    monitor: MONITOR }),
  loop_notice_uncertain: view({ status: 'needs_review', run: run('input_gate', { status: 'needs_user', reason: 'exhausted', stages: stages('input_gate'), loop: { ended: 'exhausted', notice: { sent: false, error: 'uncertain' } } }),
    monitor: MONITOR }),
  no_lost_send: view({ run: run('input_gate', { stages: stages('input_gate') }),
    monitor: { ...MONITOR, budgets: { ...MONITOR.budgets, reserved: { unit: 'eur', spent: 0.3, open: 0.2, at_bound: 0 } } } }),
  solves_used_up: view({ status: 'needs_review', run: run('solve', { status: 'needs_user', reason: 'envelope_exhausted', stages: stages('solve') }),
    envelope: { ...GRANTED, state: 'exhausted', used: 6 }, monitor: { ...MONITOR, budgets: { ...GRANTED_BUDGETS, solves: { used: 6, of: 6 } } } }),
  expired: view({ status: 'needs_review', run: run('solve', { status: 'paused', reason: 'envelope_expired', stages: stages('solve') }),
    envelope: { ...GRANTED, state: 'expired' } }),
  solver_error: view({ status: 'needs_review', run: run('solve', { status: 'needs_user', reason: 'step_failed', stages: stages('solve') }) }),
  uncertain: view({ status: 'needs_review', run: run('solve', { status: 'needs_user', reason: 'step_uncertain', stages: stages('solve') }) }),
  solve_stopped: view({ status: 'needs_review', run: run('solve', { status: 'needs_user', reason: 'step_cancelled', stages: stages('solve') }) }),
  // LC-48e: the data request is answered, but the chat's other task runs, so the resume was refused.
  held_by_switch: view({ run: run('inputs', { status: 'needs_user', stages: stages('inputs'),
    held: { kind: 'switch_in_progress', active: { kind: 'task', id: 'task-2', title: 'Wait for result', status: 'running' },
      since: '2026-11-17T02:00:00+00:00' } }), envelope: GRANTED, monitor: { ...MONITOR, budgets: GRANTED_BUDGETS } }),
  research_confirm: view({ run: run('inputs', { status: 'needs_user', reason: 'waiting_research_confirm',
    waiting: { kind: 'research_confirm', ref: 'src_a' }, stages: stages('inputs') }), envelope: GRANTED,
    monitor: { ...MONITOR, budgets: GRANTED_BUDGETS, waiting: { kind: 'research_confirm', ref: 'src_a' }, research: {
      steps: [{ stage: 'inputs', status: 'done', gaps: [] }],
      rows: [
        { row: 'src_a', gap: 'import_tariff', value: 142.5, unit: 'EUR/MWh', basis: 'energy charge, excl. VAT',
          jurisdiction: 'DE', status: 'proposed', confirmable: true, source: 'sourced', url: 'https://tariffs.example.org/industrial-2026',
          title: 'Industrial tariff sheet 2026', quote: 'The energy charge for medium-voltage customers is EUR 142.50 per MWh',
          fetched_at: '2026-11-04T06:00:00Z' },
        { row: 'src_b', gap: 'import_tariff', value: 150, unit: 'EUR/MWh', basis: 'energy charge, excl. VAT',
          jurisdiction: 'DE', status: 'proposed', confirmable: false, source: 'unverified source', reason: 'url_not_fetched' },
      ] } } }),
  // The card's unverified branch: a waiting row the server could not verify (it waits only on sourced rows).
  research_unverified: view({ run: run('inputs', { status: 'needs_user', reason: 'waiting_research_confirm',
    waiting: { kind: 'research_confirm', ref: 'src_b' }, stages: stages('inputs') }), envelope: GRANTED,
    monitor: { ...MONITOR, budgets: GRANTED_BUDGETS, waiting: { kind: 'research_confirm', ref: 'src_b' }, research: {
      steps: [{ stage: 'inputs', status: 'done', gaps: [] }],
      rows: [
        { row: 'src_b', gap: 'import_tariff', value: 150, unit: 'EUR/MWh', basis: 'energy charge, excl. VAT',
          jurisdiction: 'DE', status: 'proposed', confirmable: false, source: 'unverified source', reason: 'value_not_in_quote' },
      ] } } }),
  research_cap: view({ run: run('inputs', { stages: stages('inputs') }), envelope: GRANTED,
    monitor: { ...MONITOR, budgets: { ...GRANTED_BUDGETS, research_eur: { used: 2, limit: 2 } }, research: { rows: [],
      steps: [{ stage: 'inputs', status: 'cap_reached', gaps: ['import_tariff'], kind: 'research_cap_reached' }] } } }),
  out_of_date: view({ status: 'needs_review', run: run('inputs', { status: 'needs_user', reason: 'brief_stale', stages: stages('inputs') }) }),
}

/** The clicks a screenshot makes after rendering a state, by test id. */
export const STATE_STEPS: Partial<Record<keyof typeof STATES, { untickEdits?: number[]; solves?: number; click?: string }>> = {
  envelope_narrowed: { untickEdits: [1], solves: 4 },
  stop_confirm: { click: 'run-stop' },
}

// ── SH-07d PR B: report revision drafts on a finished run (shots) ───────────

export const DRAFT_REPORT_ID = 'r-shots'
const DRAFT_OFFER = { report_id: DRAFT_REPORT_ID, max_requests: 10, in_flight: ['requested', 'drafting'], may_act: true }
const DRAFT_EDIT = { kind: 'paragraph_text' as const, md: 'The site connects at 20 kV through one 4 MVA transformer.' }
const draftRow = (id: string, status: string, over: Record<string, unknown> = {}) => ({
  id, comment_id: 'c1', report_id: DRAFT_REPORT_ID, status, at: '2026-11-19T08:00:00Z', ...over })
const withDrafts = (rows: ReturnType<typeof draftRow>[], offer: Partial<typeof DRAFT_OFFER> = {}): RunMonitorView => {
  const base = STATES.finished_report
  return { ...base, monitor: { ...base.monitor, revision_report: { ...DRAFT_OFFER, ...offer }, revision_drafts: rows } }
}
/** The report's comments and document the drafts block reads. */
export const DRAFT_COMMENTS = { report_id: DRAFT_REPORT_ID, latest_version: 1, digest: 'd', comments: [
  { comment_id: 'c1', text: 'Say the connection voltage and the transformer size in one sentence.',
    anchor: { section_id: 'grid', block_index: 1 }, status: 'open', created_at: '2026-11-19T07:50:00Z' },
  { comment_id: 'c2', text: 'The tariff table needs its source year.', anchor: { section_id: 'grid', table_id: 't1' },
    status: 'open', created_at: '2026-11-19T07:55:00Z' },
] }
export const DRAFT_DOC = { report_id: DRAFT_REPORT_ID, version: 1, title: 'Own site study', sections: [
  { section_id: 'grid', heading: 'Grid connection', blocks: [
    { type: 'bullets', items: ['20 kV'] }, { type: 'paragraph', md: 'Text' },
    { type: 'table_ref', table_id: 't1', caption: 'Import tariffs' }] }] }
export const DRAFT_STATES: Record<string, RunMonitorView> = {
  drafts_none: withDrafts([]),
  drafts_drafted: withDrafts([draftRow('d1', 'drafted', { edit: DRAFT_EDIT })]),
  drafts_in_flight: withDrafts([draftRow('d1', 'drafting')]),
  drafts_confirmed: withDrafts([draftRow('d1', 'applied', { edit: DRAFT_EDIT, revision: { version: 2, digest: 'e' } })]),
  drafts_discarded: withDrafts([draftRow('d1', 'discarded', { edit: DRAFT_EDIT })]),
  drafts_rejected: withDrafts([draftRow('d1', 'rejected', { reasons: ['changes_number'] })]),
  drafts_refused: withDrafts([draftRow('d1', 'refused', { error: 'budget' }),
    draftRow('d2', 'refused', { error: 'revision_stale_base', edit: DRAFT_EDIT })]),
  drafts_at_limit: withDrafts(Array.from({ length: 10 }, (_, i) => draftRow(`d${i}`, 'discarded', { edit: DRAFT_EDIT }))),
  drafts_viewer: withDrafts([draftRow('d1', 'drafted', { edit: DRAFT_EDIT })], { may_act: false }),
}

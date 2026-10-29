// Study harness issue 08: one run in each state the plan card shows, as
// `/api/chat/runs/{task_id}` serves it. Used by the tests and by
// `scripts/run-monitor-shots.mjs` (the PR's screenshots).
import type { Envelope, RunMonitorView, RunStage } from '../../api/runMonitor'

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

function view(over: Partial<RunMonitorView> & { run: RunMonitorView['run'] }): RunMonitorView {
  return { task_id: TASK_ID, title: 'Own site study', status: 'ready', monitor: MONITOR, ...over } as RunMonitorView
}

const run = (stage: string, extra: Partial<RunMonitorView['run']> & { stages: RunStage[] }) =>
  ({ profile: 'eh_site@1', mode: 'stepwise', status: 'running', stage, ...extra })

export const STATES: Record<string, RunMonitorView> = {
  proposal: view({ run: run('inputs', { stages: stages('inputs') }), envelope: ENVELOPE, paused_at_card: 'inputs' }),
  gate_pass: view({ run: run('input_gate', { stages: stages('input_gate', { gate: PASS }) }), envelope: GRANTED,
    monitor: { ...MONITOR, budgets: GRANTED_BUDGETS } }),
  gate_fail: view({ status: 'needs_review', run: run('input_gate', { status: 'needs_user', stages: stages('input_gate', { status: 'failed', gate: FAIL }) }),
    envelope: GRANTED, monitor: { ...MONITOR, budgets: GRANTED_BUDGETS } }),
  loop: view({ run: run('solve', { stages: stages('solve', { loop: { round: 2, of: 6 } }) }), envelope: GRANTED,
    monitor: { ...MONITOR, budgets: GRANTED_BUDGETS } }),
  waiting: view({ run: run('solve', { status: 'needs_user', reason: 'waiting_card', waiting: { kind: 'card', ref: 'solve.1' },
    stages: stages('solve') }), monitor: { ...MONITOR, waiting: { kind: 'card', ref: 'solve.1' }, card: {
    tool_use_id: 'run-solve.1-ab12', tool_name: 'solve_queue_enqueue', args: {}, safety_tier: 'execution',
    confirmation_token: 'tok1', ttl_seconds: 300, session_id: `run:${TASK_ID}`, expires_at_epoch_ms: 4102444800000 } } }),
  paused: view({ run: run('solve', { status: 'paused', reason: 'user', stages: stages('solve') }), envelope: GRANTED,
    monitor: { ...MONITOR, budgets: GRANTED_BUDGETS } }),
  stopped: view({ status: 'cancelled', run: run('solve', { status: 'cancelled', stages: stages('solve') }),
    envelope: { ...GRANTED, state: 'revoked' } }),
  finished: view({ status: 'completed', run: run('handoff', { status: 'completed', stages: stages('handoff', {}, true) }),
    envelope: GRANTED, monitor: { ...MONITOR, budgets: GRANTED_BUDGETS } }),
  over_budget: view({ status: 'needs_review', run: run('solve', { status: 'needs_user', reason: 'budget', stages: stages('solve') }),
    monitor: { ...MONITOR, budgets: { cost_eur: { used: 5.01, limit: 5, unmetered: false } } } }),
  expired: view({ status: 'needs_review', run: run('solve', { status: 'paused', reason: 'envelope_expired', stages: stages('solve') }),
    envelope: { ...GRANTED, state: 'expired' } }),
  solver_error: view({ status: 'needs_review', run: run('solve', { status: 'needs_user', reason: 'step_failed', stages: stages('solve') }) }),
  out_of_date: view({ status: 'needs_review', run: run('inputs', { status: 'needs_user', reason: 'brief_stale', stages: stages('inputs') }) }),
}

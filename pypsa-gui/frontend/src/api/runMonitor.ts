// Study harness issue 08: the plan card's run monitor (backend
// `services/run_monitor.py`, `/api/chat/runs/{task_id}`). Every call is a
// user's click or the open plan card's own refresh; none is a tool call.
import { client } from './client'

export interface GateRule {
  rule: string; verdict: 'pass' | 'fail' | string; fact: string
  value: unknown; unit?: string; reason?: string
}
export interface RunStage {
  stage: string; status: 'pending' | 'active' | 'passed' | 'failed' | 'skipped' | string
  reason?: string; gate?: GateRule[]
  /** Loop round n of N, once the loop stages send it (issues 04 and 05). */
  loop?: { round: number; of: number }
}
export interface EnvelopeEdit {
  tool: string
  target: { component: string; name: string; attr: string }
  bounds?: { min?: number; max?: number; rel?: number }
  enum?: (string | number)[]
}
export interface EnvelopeRun { tool: string; kind?: string }
export interface EnvelopeBudget { solves: number; rounds: number; wall_time_h: number }
export interface Envelope {
  id: string; state: 'proposed' | 'granted' | 'expired' | 'revoked' | 'exhausted'
  expires_at: string | null
  edits: EnvelopeEdit[]; runs: EnvelopeRun[]; budget: EnvelopeBudget
  research: { allowed: boolean; cost_cap_eur: number }
  notify: { email: boolean }
  used: number; refused: number
}
/** The run session's live card: the same payload the chat's
 *  `tool_pending_confirmation` frame carries, plus its session. */
export interface RunCard {
  tool_use_id: string; tool_name: string; args: Record<string, unknown>; safety_tier: string
  confirmation_token: string; ttl_seconds: number; session_id: string; expires_at_epoch_ms: number
  edit_card?: unknown; sweep_card?: unknown
}
export type UnattendedBlock = 'runner_off' | 'no_provider' | 'locked'
export interface RunMonitorView {
  task_id: string; title: string; status: string
  run: {
    profile: string; mode: string; status: string; stage: string; reason?: string
    gate_due?: boolean; next_stage?: string | null; stages: RunStage[]
    waiting?: { kind: string; ref: string }
  }
  paused_at_card?: string
  brief_stale?: string
  envelope?: Envelope
  monitor: {
    budgets: {
      cost_eur: { used: number; limit: number; unmetered: boolean }
      solves?: { used: number; of: number }
      expires_at?: string
    }
    unattended: { available: boolean; reason?: UnattendedBlock }
    email: { available: boolean }
    card?: RunCard
    waiting?: { kind: string; ref: string }
  }
}

const base = (taskId: string) => `/chat/runs/${encodeURIComponent(taskId)}`

export async function getRun(taskId: string): Promise<RunMonitorView> {
  return (await client.get<RunMonitorView>(base(taskId))).data
}

/** The user has the plan card open: a card the run waits on is issued now. */
export async function presentRun(taskId: string): Promise<void> {
  await client.post(`${base(taskId)}/present`)
}

export async function pauseRun(taskId: string): Promise<RunMonitorView> {
  return (await client.post<RunMonitorView>(`${base(taskId)}/pause`)).data
}

export async function resumeRun(taskId: string, sessionId: string | null): Promise<RunMonitorView> {
  return (await client.post<RunMonitorView>(`${base(taskId)}/resume`,
    sessionId ? { session_id: sessionId } : {})).data
}

export async function stopRun(taskId: string): Promise<RunMonitorView> {
  return (await client.post<RunMonitorView>(`${base(taskId)}/stop`)).data
}

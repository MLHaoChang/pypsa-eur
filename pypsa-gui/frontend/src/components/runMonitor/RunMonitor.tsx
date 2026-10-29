// Study harness issue 08: the LC-23 plan card for a harness run (design §10,
// "Monitor"). The stage rows, the current stage expanded (its gate rules,
// loop round, budgets used), what waits for the user, Pause and Stop. Guided
// and Expert show the same card; Expert adds the ids in a closed Details.
// The card never switches panels on its own: every jump is the user's "›"
// click, through the mode-aware openers (`openFlowTargetInPlace` in Guided,
// `openFlowTarget` in Expert) so "Back to …" works (UX condition 6). Stop is
// an inline second step (UX ruling B).
import { useCallback, useEffect, useRef, useState } from 'react'
import { isAxiosError } from 'axios'
import {
  getRun, pauseRun, presentRun, resumeRun, stopRun,
  type GateRule, type RunMonitorView, type RunStage,
} from '../../api/runMonitor'
import { useChatStore } from '../../store/chatStore'
import { useUIStore, type SlidePanel } from '../../store/uiStore'
import type { FlowTarget } from '../../api/flow'
import { openFlowTarget, openFlowTargetInPlace } from '../../layout/flowNavigation'
import { PANEL_SECTION } from '../../layout/flowSections'
import { manualNavigation } from '../../utils/navigationEpoch'
import {
  FACT_UNITS, FACT_WORDS, STAGE_STATUS_WORDS, euros, stageWords, stopWords, when, type RunFix,
} from './runWords'

const POLL_MS = 4000
const PRESENT_EVERY_MS = 60_000
const OVER = new Set(['completed', 'cancelled'])

/** Open a panel on the user's click, through the mode-aware opener (in place
 *  over the cards in Guided, the panel in Expert), with "Back to <origin>". */
export function openPanel(panel: SlidePanel): void {
  manualNavigation()
  const target: FlowTarget = {
    section: PANEL_SECTION[panel] ?? 'results', panel, component: null, entity: null, field: null,
  }
  if (useUIStore.getState().uiMode === 'guided') openFlowTargetInPlace(target)
  else openFlowTarget(target)
}

function ruleWords(r: GateRule): string {
  const unit = r.unit ?? FACT_UNITS[r.fact]
  const value = typeof r.value === 'number' && Number.isFinite(r.value)
    ? ` · ${r.value}${unit ? ` ${unit}` : ''}` : ''
  return `${FACT_WORDS[r.fact] ?? 'A check'}${value}`
}

function Budgets({ view }: { view: RunMonitorView }) {
  const b = view.monitor.budgets
  const parts = [b.cost_eur.unmetered ? 'Cost could not be counted'
    : b.cost_eur.limit > 0 ? `Cost ${euros(b.cost_eur.used)} of ${euros(b.cost_eur.limit)}`
    : `Cost ${euros(b.cost_eur.used)} · no cost cap`]
  if (b.solves) parts.push(`Solves ${b.solves.used} of ${b.solves.of}`)
  if (b.expires_at) parts.push(`Expires ${when(b.expires_at)}`)
  return <div data-testid="run-budgets">{parts.join(' · ')}</div>
}

function CurrentStage({ stage, view }: { stage: RunStage; view: RunMonitorView }) {
  return <div className="ml-4 mt-0.5 space-y-0.5 text-muted" data-testid="run-current-stage">
    {(stage.gate ?? []).map((r, i) => <div key={i} data-testid="run-gate-rule" data-verdict={r.verdict}>
      {ruleWords(r)} · <span className={r.verdict === 'pass' ? 'text-text' : 'text-amber-400'}>
        {r.verdict === 'pass' ? 'pass' : 'fail'}</span>
    </div>)}
    {stage.loop && <div data-testid="run-loop">Round {stage.loop.round} of {stage.loop.of}</div>}
    <Budgets view={view} />
  </div>
}

function Link({ children, onClick, disabled, testId }: {
  children: string; onClick: () => void; disabled?: boolean; testId?: string
}) {
  return <button type="button" disabled={disabled} onClick={onClick} data-testid={testId}
    className="text-accent hover:underline disabled:opacity-40">{children} ›</button>
}

export default function RunMonitor({ task, disabled, onRequest }: {
  /** The run's view as the tool result carried it; refreshed from the server. */
  task: Record<string, unknown>; disabled: boolean; onRequest: (text: string) => void
}) {
  const guided = useUIStore((s) => s.uiMode) === 'guided'
  const taskId = String(task.task_id)
  const [view, setView] = useState<RunMonitorView | null>(null)
  const [gone, setGone] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)
  const [stopping, setStopping] = useState(false)
  const presented = useRef<{ ref: string; at: number } | null>(null)
  const answered = useRef<Set<string>>(new Set())

  const refresh = useCallback(async () => {
    try {
      setView(await getRun(taskId))
      setFailed(null)
    } catch (err) {
      const kind = isAxiosError(err) ? (err.response?.data as { detail?: { error_kind?: string } })?.detail?.error_kind : null
      if (kind === 'task_not_found') setGone(true)
      else setFailed('The run could not be read just now; it goes on on the server.')
    }
  }, [taskId])

  // Refresh while the card is open and the run goes on.
  const over = view !== null && OVER.has(view.status)
  useEffect(() => {
    if (gone || over) return
    void refresh()
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refresh()
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [refresh, gone, over])

  // The user has the plan card open: a card the run waits on is issued now,
  // its TTL starting at display (issue 08 AC).
  const waitingRef = view?.monitor.waiting?.ref ?? null
  const hasCard = Boolean(view?.monitor.card)
  useEffect(() => {
    if (!waitingRef || hasCard || document.visibilityState !== 'visible') return
    const last = presented.current
    if (last && last.ref === waitingRef && Date.now() - last.at < PRESENT_EVERY_MS) return
    presented.current = { ref: waitingRef, at: Date.now() }
    void presentRun(taskId).then(refresh).catch(() => undefined)
  }, [waitingRef, hasCard, taskId, refresh])

  // The run's card is today's Confirmation card, answered on the run's session.
  const card = view?.monitor.card ?? null
  useEffect(() => {
    if (!card || answered.current.has(card.confirmation_token)) return
    const chat = useChatStore.getState()
    if (chat.pending || chat.streaming) return
    answered.current.add(card.confirmation_token)
    chat.setPending({ ...card })
  }, [card])

  const act = async (call: () => Promise<RunMonitorView>) => {
    setBusy(true)
    try {
      setView(await call())
    } catch (err) {
      const kind = isAxiosError(err) ? (err.response?.data as { detail?: { error_kind?: string } })?.detail?.error_kind : null
      if (kind === 'task_not_found') setGone(true)
      else setFailed(kind === 'no_chat_session' ? 'Open the chat once, then try again.' : 'That did not go through; try again.')
    } finally {
      setBusy(false)
    }
  }
  const sessionId = useChatStore((s) => s.sessionId)
  const fix = (which: RunFix) => {
    if (which === 'resume') void act(() => resumeRun(taskId, sessionId))
    else if (which === 'issues') openPanel('issues')
    else if (which === 'results') openPanel('results')
    else if (which === 'solve_log') openPanel('solveQueue')
    else if (which === 'budget') onRequest(`Use get_task with task_id ${taskId}. The run used up its budget: show me its envelope so I can change the budget.`)
    else if (which === 'new_brief') onRequest('The inputs changed outside the run. Draft a new brief for me to review.')
    else onRequest(`Use get_task with task_id ${taskId}. Inspect the uncertain step's effects and explain what needs verification before reconciliation.`)
  }

  if (gone) return <p data-testid="run-gone">This run is no longer available to you.</p>
  const run = view?.run
  const title = view?.title ?? String(task.title ?? 'Run')
  if (!view || !run) return <div data-testid="run-monitor">
    <div className="font-medium">{title}</div>
    <p className="text-muted">{failed ?? 'Reading the run…'}</p>
  </div>

  const stages = run.stages
  const current = stages.find(s => s.stage === run.stage)
  const stoppedAt = stageWords(run.stage)
  const cancelled = view.status === 'cancelled'
  const finished = run.status === 'completed'
  const gateFailed = run.status === 'needs_user' && current?.status === 'failed'
  const failedRules = (current?.gate ?? []).filter(r => r.verdict !== 'pass').length
  const stop = !gateFailed && (run.status === 'paused' || (run.status === 'needs_user' && run.reason && run.reason !== 'waiting_card'))
    ? stopWords(run.reason, run.stage) : null
  const going = !cancelled && !finished && ['running', 'waiting_job', 'needs_user'].includes(run.status)
  const pausedAt = view.paused_at_card
  const state = cancelled ? `Stopped at ${stoppedAt}` : finished ? 'Finished'
    : gateFailed ? `${stoppedAt} failed` : stop ? stop.words
    : pausedAt ? 'Waiting for you' : run.status === 'needs_user' ? 'Waiting for you'
    : `Running · ${stoppedAt}`

  return <div data-testid="run-monitor" data-run-task={taskId} data-run-status={run.status} data-task-status={view.status}>
    <div className="font-medium">{title}</div>
    <div data-testid="run-state">{state}</div>
    <ol aria-label="Run stages" className="my-1 space-y-0.5">
      {stages.map(s => {
        const now = s.stage === run.stage && !finished
        return <li key={s.stage} data-testid="run-stage" data-stage={s.stage} data-status={s.status}
          aria-current={now ? 'step' : undefined} className={now ? 'font-medium' : s.status === 'skipped' ? 'text-muted' : ''}>
          {stageWords(s.stage)} · {now && s.status === 'pending' ? 'now' : STAGE_STATUS_WORDS[s.status] ?? 'to do'}
          {now && !cancelled && <CurrentStage stage={s} view={view} />}
        </li>
      })}
      <li data-testid="run-report-row">
        <Link testId="run-report-link" onClick={() => openPanel('reports')}>Report</Link>
      </li>
    </ol>

    {gateFailed && <div data-testid="run-failure">
      {stoppedAt} failed: {failedRules} {failedRules === 1 ? 'rule' : 'rules'} did not pass ·{' '}
      <Link onClick={() => fix(run.stage === 'result_gate' ? 'results' : 'issues')}>
        {run.stage === 'result_gate' ? 'Open results' : 'See the issues'}</Link>{' '}
      <span className="text-muted">then</span>{' '}
      <Link disabled={busy} onClick={() => fix('resume')}>Check again</Link>
    </div>}
    {stop && !cancelled && <div data-testid="run-failure">
      {stop.words}{stop.fix && <>: <Link disabled={busy} onClick={() => fix(stop.fix!)}>{stop.fixWords}</Link></>}
      {run.status === 'paused' && <p className="text-muted" data-testid="run-resume-note">
        Nothing runs until you resume: it picks up at {stoppedAt}.</p>}
      {run.status === 'paused' && view.envelope?.state === 'granted' && <p className="text-muted" data-testid="run-paused-expiry">
        Unattended approval expires {when(view.envelope.expires_at ?? view.monitor.budgets.expires_at)}</p>}
    </div>}

    {going && (pausedAt || card || view.monitor.waiting) && <div data-testid="run-waiting">
      <div className="font-medium">Waiting for you</div>
      <ul className="list-disc pl-5">
        {pausedAt && <li>
          <button type="button" disabled={disabled} className="text-accent disabled:opacity-40" data-testid="run-continue"
            onClick={() => useChatStore.getState().sendRequest(`Continue to ${stageWords(pausedAt)}`, {
              source: 'run-monitor', label: `Continue to ${stageWords(pausedAt)}`,
              harness: { continue_task_id: taskId } })}>
            Continue to {stageWords(pausedAt)}
          </button>
        </li>}
        {card && <li data-testid="run-waiting-card">A step needs your approval: see the card below</li>}
        {!card && view.monitor.waiting && !pausedAt && <li data-testid="run-waiting-step">A step waits for your decision; its card opens here</li>}
      </ul>
    </div>}

    {view.brief_stale && !stop && <div data-testid="run-failure">
      Inputs out of date: <Link onClick={() => fix('new_brief')}>Review a new brief</Link>
    </div>}

    {cancelled && <p data-testid="run-stopped">
      Stopped at {stoppedAt}: the edits applied so far and the results so far are kept.
      A stopped run cannot be restarted; start a new one from the composer.
    </p>}
    {finished && <p data-testid="run-finished">Finished: the report is in Reports.</p>}

    {failed && <p className="text-amber-400">{failed}</p>}
    {!cancelled && !finished && <div className="mt-1 flex flex-wrap items-baseline gap-2">
      {going && <button type="button" disabled={busy} className="text-accent disabled:opacity-40" data-testid="run-pause"
        onClick={() => void act(() => pauseRun(taskId))}>Pause</button>}
      {!stopping && <button type="button" disabled={busy} className="text-muted hover:text-text disabled:opacity-40" data-testid="run-stop"
        onClick={() => setStopping(true)}>Stop</button>}
    </div>}
    {stopping && !cancelled && !finished && <div role="group" aria-label="Stop the run?" data-testid="run-stop-confirm">
      <div className="flex flex-wrap items-baseline gap-1">
        <span className="font-medium">Stop the run?</span>
        <button type="button" disabled={busy} className="text-amber-400 hover:underline disabled:opacity-40" data-testid="run-stop-yes"
          onClick={() => { setStopping(false); void act(() => stopRun(taskId)) }}>Stop</button>
        <span className="text-muted">·</span>
        <button type="button" disabled={busy} className="text-accent hover:underline disabled:opacity-40" data-testid="run-stop-keep"
          onClick={() => setStopping(false)}>Keep running</button>
      </div>
      <p className="text-muted" data-testid="run-stop-note">
        The edits applied so far and the results so far are kept. A stopped run cannot be restarted.</p>
    </div>}
    {!guided && <details className="mt-1 text-muted"><summary>Details</summary>
      <div>Task {taskId}</div>
      <div>Stage {run.stage}</div>
    </details>}
  </div>
}

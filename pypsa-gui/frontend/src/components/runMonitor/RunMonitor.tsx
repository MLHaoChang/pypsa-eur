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
import { openPanel } from '../../pages/results/openPanel'
import { PANEL_SECTION } from '../../layout/flowSections'
import { manualNavigation } from '../../utils/navigationEpoch'
import {
  FACT_WORDS, STAGE_STATUS_WORDS, factUnit, stageWords, stopWords, when, type RunFix,
} from './runWords'
import { runUnderReview } from './planReview'
import { fmtCurrency } from '../../utils/format'

const POLL_MS = 4000
const PRESENT_EVERY_MS = 60_000
const OVER = new Set(['completed', 'cancelled'])

/** Open a panel on the user's click, through the shared mode-aware opener
 *  (in place over the cards in Guided, the panel in Expert), with "Back to <origin>". */
function open(panel: SlidePanel): void {
  manualNavigation()
  openPanel(PANEL_SECTION[panel] ?? 'results', panel)
}

/** The `error_kind` of a refused run call, or null (network error, other shape). */
function errorKind(err: unknown): string | null {
  return isAxiosError(err) ? (err.response?.data as { detail?: { error_kind?: string } })?.detail?.error_kind ?? null : null
}

/** A gate rule in words; a failing counted rule adds its count:
 *  "Every load is connected · 1 unconnected load group". A passing rule's
 *  name already says its count ("The model has no errors"). */
function ruleWords(r: GateRule): string {
  const counted = r.verdict !== 'pass' && typeof r.value === 'number' && Number.isFinite(r.value)
  const unit = counted ? r.unit ?? factUnit(r.fact, r.value as number) : undefined
  const value = counted ? ` · ${r.value}${unit ? ` ${unit}` : ''}` : ''
  return `${FACT_WORDS[r.fact] ?? 'A check'}${value}`
}

function Budgets({ view }: { view: RunMonitorView }) {
  // A paused run says its expiry once, as "Unattended approval expires …".
  const paused = view.run?.status === 'paused' && view.envelope?.state === 'granted'
  const b = view.monitor.budgets
  const parts = [b.cost_eur.unmetered ? 'Cost could not be counted'
    : b.cost_eur.limit > 0 ? `Cost ${fmtCurrency(b.cost_eur.used)} of ${fmtCurrency(b.cost_eur.limit)}`
    : `Cost ${fmtCurrency(b.cost_eur.used)} · no cost cap`]
  if (b.solves) parts.push(`Solves ${b.solves.used} of ${b.solves.of}`)
  if (b.expires_at && !paused) parts.push(`Expires ${when(b.expires_at)}`)
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

/** A link that goes somewhere (a panel, the Envelope section, the assistant): "›". */
function Link({ children, onClick, disabled, testId }: {
  children: string; onClick: () => void; disabled?: boolean; testId?: string
}) {
  return <button type="button" disabled={disabled} onClick={onClick} data-testid={testId} data-run-link=""
    className="text-accent hover:underline disabled:opacity-40">{children} ›</button>
}

/** An action in place (resume, retry): no "›". */
function Action({ children, onClick, disabled }: { children: string; onClick: () => void; disabled?: boolean }) {
  return <button type="button" disabled={disabled} onClick={onClick}
    className="text-accent hover:underline disabled:opacity-40">{children}</button>
}

export default function RunMonitor({ task, disabled, disabledReason, onRequest }: {
  /** The run's view as the tool result carried it; refreshed from the server. */
  task: Record<string, unknown>; disabled: boolean; onRequest: (text: string) => void
  /** Why a chat-sent click (Continue) cannot work now, in words; shown beside it. */
  disabledReason?: string
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
      const kind = errorKind(err)
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
      const kind = errorKind(err)
      if (kind === 'task_not_found') setGone(true)
      else setFailed(kind === 'no_chat_session' ? 'Open the chat once, then try again.' : 'That did not go through; try again.')
    } finally {
      setBusy(false)
    }
  }
  const sessionId = useChatStore((s) => s.sessionId)
  const fix = (which: RunFix) => {
    if (which === 'resume') void act(() => resumeRun(taskId, sessionId))
    else if (which === 'issues') open('issues')
    else if (which === 'results') open('results')
    else if (which === 'solve_log') open('solveQueue')
    else if (which === 'budget') changeBudget()
    else if (which === 'new_brief') onRequest('The inputs changed outside the run. Draft a new brief for me to review.')
    else ask('Review the step', `Use get_task with task_id ${taskId}. Inspect the uncertain step's effects and explain what needs verification before reconciliation.`)
  }
  /** A request to the assistant whose transcript line is `label`: no tool name or id shows. */
  const ask = (label: string, text: string) =>
    useChatStore.getState().sendRequest(text, { source: 'run-monitor', label })
  /** "Change the budget ›": this run's Envelope section, in a local plan review,
   *  when its envelope can be granted again (used up, expired, withdrawn);
   *  otherwise (the cost cap of a run whose envelope still holds) the assistant. */
  const changeBudget = () => {
    const envelope = view?.envelope
    if (!envelope || envelope.state === 'granted') {
      ask('Change the budget', `Use get_task with task_id ${taskId}. The run used up its cost budget: explain what I can change and how.`)
      return
    }
    const chat = useChatStore.getState()
    if (runUnderReview(chat.messages)?.taskId !== taskId) {
      chat.appendMessage({ role: 'tool', content: '', tool_use_id: `run-change-${taskId}`, tool_name: 'get_task',
        tool_result: { task_id: taskId, title: view.title, run: {}, envelope } })
    }
    chat.setChoice({ tool_use_id: `run-change-${taskId}`, intent: 'plan_review', title: view.title,
      question: 'Change the budget, then approve again', options: [], allow_free_text: false })
  }

  if (gone) return <p data-testid="run-gone">This run is no longer available to you.</p>
  const run = view?.run
  const title = view?.title ?? String(task.title ?? 'Run')
  if (!view || !run) return <div data-testid="run-monitor">
    <div className="font-medium">{title}</div>
    <p className="text-muted">{failed ?? 'Reading the run…'}</p>
  </div>

  const stages = run.stages
  // The run buttons that work now, as the server says (`monitor.actions`).
  const can = new Set(view.monitor.actions ?? [])
  // The Reports panel has no per-report selection yet: the link lands on the panel.
  const hasReport = stages.some(s => s.stage === 'report' && s.status === 'passed')
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
  // The current stage row once the run is held, paused or over: where it stands, not "now".
  const currentWords = cancelled ? 'stopped' : run.status === 'paused' ? 'paused'
    : pausedAt || run.status === 'needs_user' ? 'waits for you' : 'now'
  const continueReason = disabled && disabledReason ? disabledReason : null

  return <div data-testid="run-monitor" data-run-task={taskId} data-run-status={run.status} data-task-status={view.status}>
    <div className="font-medium">{title}</div>
    <div data-testid="run-state">{state}</div>
    <ol aria-label="Run stages" className="my-1 space-y-0.5">
      {stages.map(s => {
        const now = s.stage === run.stage && !finished
        return <li key={s.stage} data-testid="run-stage" data-stage={s.stage} data-status={s.status}
          aria-current={now ? 'step' : undefined} className={now ? 'font-medium' : s.status === 'skipped' ? 'text-muted' : ''}>
          <span data-testid="run-stage-words">{stageWords(s.stage)} · {now && ['pending', 'active'].includes(s.status)
            ? currentWords : STAGE_STATUS_WORDS[s.status] ?? 'to do'}</span>
          {now && !cancelled && <CurrentStage stage={s} view={view} />}
        </li>
      })}
      {hasReport && <li data-testid="run-report-row">
        <Link testId="run-report-link" onClick={() => open('reports')}>Open the report</Link>
      </li>}
    </ol>

    {gateFailed && <div data-testid="run-failure">
      {stoppedAt} failed: {failedRules} {failedRules === 1 ? 'rule' : 'rules'} did not pass.{' '}
      <Link onClick={() => fix(run.stage === 'result_gate' ? 'results' : 'issues')}>
        {run.stage === 'result_gate' ? 'Open results' : 'See the issues'}</Link>
      {can.has('resume') && <><span className="text-muted">, then</span>{' '}
        <Action disabled={busy} onClick={() => fix('resume')}>Check again</Action></>}
    </div>}
    {stop && !cancelled && <div data-testid="run-failure">
      {stop.words}{stop.fix && (stop.fix !== 'resume' || can.has('resume')) && <>: {stop.fix === 'resume'
        ? <Action disabled={busy} onClick={() => fix('resume')}>{stop.fixWords}</Action>
        : <Link disabled={busy} onClick={() => fix(stop.fix!)}>{stop.fixWords}</Link>}</>}
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
            aria-describedby={continueReason ? `run-continue-reason-${taskId}` : undefined}
            onClick={() => useChatStore.getState().sendRequest(`Continue to ${stageWords(pausedAt)}`, {
              source: 'run-monitor', label: `Continue to ${stageWords(pausedAt)}`,
              harness: { continue_task_id: taskId } })}>
            Continue to {stageWords(pausedAt)}
          </button>
          {continueReason && <div id={`run-continue-reason-${taskId}`} className="text-muted" data-testid="run-continue-reason">
            {continueReason}</div>}
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
      A stopped run cannot be restarted: start a new one from the composer.
    </p>}
    {finished && hasReport && <p data-testid="run-finished">The report is in Reports.</p>}

    {failed && <p className="text-amber-400">{failed}</p>}
    {(can.has('pause') || can.has('stop')) && <div className="mt-1 flex flex-wrap items-baseline gap-2">
      {can.has('pause') && <button type="button" disabled={busy} className="text-accent disabled:opacity-40" data-testid="run-pause"
        onClick={() => void act(() => pauseRun(taskId))}>Pause</button>}
      {can.has('stop') && !stopping && <button type="button" disabled={busy} className="text-muted hover:text-text disabled:opacity-40" data-testid="run-stop"
        onClick={() => setStopping(true)}>Stop</button>}
    </div>}
    {stopping && can.has('stop') && <div role="group" aria-label="Stop the run?" data-testid="run-stop-confirm">
      <div className="flex flex-wrap items-baseline gap-1">
        <span className="font-medium">Stop the run?</span>
        <button type="button" disabled={busy} className="text-amber-400 hover:underline disabled:opacity-40" data-testid="run-stop-yes"
          onClick={() => { setStopping(false); void act(() => stopRun(taskId)) }}>Stop</button>
        <span className="text-muted">·</span>
        <button type="button" disabled={busy} className="text-muted hover:text-text disabled:opacity-40" data-testid="run-stop-keep"
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

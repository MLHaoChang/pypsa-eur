// Study harness issue 08: the plan-review Choice card's Envelope section
// (design §7 "Grant", §10 "Approve"; owner decisions SH-ENVELOPE-1, COP-1).
// In words, with units (UX condition 2); exactly three verdicts, the
// unattended one disabled with its reason in visible text (condition 3).
// The user may narrow it (untick an edit or run, lower a budget), never
// widen it: the server checks again (`run_envelope.narrow`).
import { useEffect, useState } from 'react'
import { getRun, type Envelope, type RunMonitorView } from '../../api/runMonitor'
import { UNATTENDED_BLOCK_WORDS, editLimits, editSubject, runWords } from './runWords'
import { fmtCurrency } from '../../utils/format'

export type EnvelopeVerdict =
  | { verdict: 'unattended'; envelope: Record<string, unknown>; notify_email: boolean }
  | { verdict: 'stepwise' }
  | { verdict: 'change' }

const VERDICT = 'w-full text-left px-2.5 py-1.5 text-[12px] rounded border transition-colors disabled:opacity-50'

function Stepper({ label, name, value, max, unit, onChange, testId }: {
  /** `name` is the input's accessible name ("Solves, at most"). */
  label: string; name: string; value: number; max: number; unit?: string; onChange: (v: number) => void; testId: string
}) {
  return <label className="inline-flex items-center gap-1">
    {label}
    <input type="number" min={1} max={max} step={1} value={value} data-testid={testId} aria-label={name}
      onChange={(e) => {
        const v = Math.floor(Number(e.target.value))
        if (Number.isFinite(v)) onChange(Math.min(max, Math.max(1, v)))
      }}
      className="w-12 rounded border border-border bg-bg-2 px-1 py-0.5 text-[11px]" />
    {unit}
  </label>
}

export default function EnvelopeSection({ taskId, proposed, disabled, onVerdict }: {
  taskId: string
  /** The envelope as the run's start carried it; refreshed from the server. */
  proposed: Envelope
  disabled: boolean
  onVerdict: (v: EnvelopeVerdict) => void
}) {
  const [view, setView] = useState<RunMonitorView | null>(null)
  useEffect(() => {
    let live = true
    getRun(taskId).then(v => { if (live) setView(v) }).catch(() => undefined)
    return () => { live = false }
  }, [taskId])
  const envelope = view?.envelope ?? proposed
  const [edits, setEdits] = useState<boolean[]>(() => proposed.edits.map(() => true))
  const [runs, setRuns] = useState<boolean[]>(() => proposed.runs.map(() => true))
  const [solves, setSolves] = useState(proposed.budget.solves)
  const [hours, setHours] = useState(proposed.budget.wall_time_h)
  const [email, setEmail] = useState(false)

  const unattended = view?.monitor.unattended
  const block = !view ? 'Checking whether this server can run it unattended…'
    : unattended && !unattended.available ? UNATTENDED_BLOCK_WORDS[unattended.reason ?? ''] ?? 'Not available here' : null
  const emailOk = view?.monitor.email.available === true
  const costCap = view?.monitor.budgets.cost_eur.limit

  const grant = () => onVerdict({
    verdict: 'unattended', notify_email: emailOk && email,
    envelope: {
      edits: envelope.edits.filter((_, i) => edits[i]), runs: envelope.runs.filter((_, i) => runs[i]),
      budget: { solves, rounds: envelope.budget.rounds, wall_time_h: hours }, research: envelope.research,
    },
  })

  return <div data-testid="envelope-section" className="mb-2 rounded border border-border bg-bg-2/40 px-2.5 py-1.5 text-[12px]">
    <div className="text-[10px] uppercase tracking-wide text-accent">Envelope</div>
    <p className="text-muted">What the run may do without asking you, if you approve it to run unattended:</p>
    <ul className="my-1 space-y-0.5" aria-label="Edits it may make">
      {envelope.edits.map((e, i) => <li key={i} data-testid="envelope-edit">
        <label className="inline-flex items-start gap-1.5">
          <input type="checkbox" checked={edits[i] ?? false} disabled={disabled}
            onChange={() => setEdits(cur => cur.map((v, j) => (j === i ? !v : v)))} />
          <span><span className={edits[i] ? undefined : 'text-muted line-through'}>{editSubject(e)}: {editLimits(e)}</span>
            {!edits[i] && <span className="text-muted" data-testid="envelope-removed">{' · removed'}</span>}</span>
        </label>
      </li>)}
      {envelope.runs.map((r, i) => <li key={`r${i}`} data-testid="envelope-run">
        <label className="inline-flex items-start gap-1.5">
          <input type="checkbox" checked={runs[i] ?? false} disabled={disabled}
            onChange={() => setRuns(cur => cur.map((v, j) => (j === i ? !v : v)))} />
          <span><span className={runs[i] ? undefined : 'text-muted line-through'}>{runWords(r)}</span>
            {!runs[i] && <span className="text-muted" data-testid="envelope-removed">{' · removed'}</span>}</span>
        </label>
      </li>)}
    </ul>
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1" data-testid="envelope-budgets">
      <Stepper label="Solves up to" name="Solves, at most" value={solves} max={proposed.budget.solves} onChange={setSolves} testId="envelope-solves" />
      <span>·</span>
      <span>Rounds up to {envelope.budget.rounds}</span>
      <span>·</span>
      <Stepper label="Expires after" name="Hours until it expires, at most" value={hours} max={proposed.budget.wall_time_h} unit="h" onChange={setHours} testId="envelope-hours" />
      <span>·</span>
      <span>{costCap !== undefined && costCap > 0 ? `Cost cap ${fmtCurrency(costCap)}` : 'no cost cap'}</span>
    </div>
    <div data-testid="envelope-research">
      {envelope.research.allowed ? `Research cost cap ${fmtCurrency(envelope.research.cost_cap_eur)}` : 'No research'}
    </div>
    <p className="text-muted">Always asks you first: deletes, publishing a grid code, outside tools, researched values,
      and anything not listed here.</p>
    <label className="inline-flex items-center gap-1.5" data-testid="envelope-email">
      <input type="checkbox" checked={emailOk && email} disabled={disabled || !emailOk} onChange={() => setEmail(v => !v)} />
      Email me when it needs me or ends
      {view && !emailOk && <span className="text-muted" data-testid="envelope-email-reason">: email is not set up on this server</span>}
    </label>
    <div className="mt-2 flex flex-col gap-1.5" role="group" aria-label="Your verdict">
      <button type="button" data-testid="envelope-verdict" data-verdict="unattended"
        disabled={disabled || block !== null} onClick={grant}
        className={`${VERDICT} border-accent/70 bg-accent/10 hover:bg-accent/20`}>
        <span className="font-medium text-text">Approve and run unattended</span>
      </button>
      {block && <div className="text-[11px] text-muted" data-testid="envelope-unattended-reason">{block}</div>}
      <button type="button" data-testid="envelope-verdict" data-verdict="stepwise" disabled={disabled}
        onClick={() => onVerdict({ verdict: 'stepwise' })}
        className={`${VERDICT} border-border bg-bg-2/60 hover:bg-bg-3/50`}>
        <span className="font-medium text-text">Approve, step by step</span>
        <div className="text-[11px] text-muted mt-0.5">Every change and run asks you first.</div>
      </button>
      <button type="button" data-testid="envelope-verdict" data-verdict="change" disabled={disabled}
        onClick={() => onVerdict({ verdict: 'change' })}
        className={`${VERDICT} border-border bg-bg-2/60 hover:bg-bg-3/50`}>
        <span className="font-medium text-text">Change</span>
      </button>
    </div>
  </div>
}

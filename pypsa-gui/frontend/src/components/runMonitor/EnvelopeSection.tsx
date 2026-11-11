// Study harness issue 08: the plan-review Choice card's Envelope section
// (design §7 "Grant", §10 "Approve"; owner decisions SH-ENVELOPE-1, COP-1).
// In words, with units (UX condition 2); exactly three verdicts, the
// unattended one disabled with its reason in visible text (condition 3).
// The user may narrow it (untick an edit or run, lower a budget), never
// widen it: the server checks again (`run_envelope.narrow`).
import { useEffect, useState } from 'react'
import { getRun, type Envelope, type EnvelopeGoal, type IntakeRow, type RunIntake, type RunMonitorView } from '../../api/runMonitor'
import { UNATTENDED_BLOCK_WORDS, editLimits, editSubject, runWords } from './runWords'
import { fmtCurrency } from '../../utils/format'
import { useUIStore } from '../../store/uiStore'
import { RELIABILITY_GOAL_LABEL } from '../../hooks/useReliabilityGoal'
import { openReliabilityGoal } from '../../layout/flowNavigation'

// P5a: the goal the LOLE rule judges against, with its unit and where it comes from.
// No engine id in the words (#1194): the template is "the site template".
const GOAL_SOURCE: Record<string, string> = { stored: "from the project's solver settings", template: "from the site template's default" }
// UX (13): a missing goal names the input and opens its editor (mode-aware, with Back to this card).
function GoalRow({ goal }: { goal: EnvelopeGoal }) {
  if (goal.value === null) return <div data-testid="envelope-goal">No reliability goal is set. Missing:{' '}
    <button type="button" data-testid="envelope-goal-fix" onClick={openReliabilityGoal}
      className="text-accent hover:underline whitespace-nowrap">{RELIABILITY_GOAL_LABEL} ›</button></div>
  return <div data-testid="envelope-goal">
    {`${RELIABILITY_GOAL_LABEL}: ${goal.value} h/yr, ${GOAL_SOURCE[goal.source] ?? goal.source}`}</div>
}
/** The grant refuses `goal_missing`, so unattended waits for a goal; step by step does not need one. */
const GOAL_BLOCK = 'Set the reliability goal first: see above.'

export type EnvelopeVerdict =
  | { verdict: 'unattended'; envelope: Record<string, unknown>; notify_email: boolean; goal?: EnvelopeGoal; intake_digest?: string }
  | { verdict: 'stepwise'; intake_digest?: string }
  | { verdict: 'change' }

// SH-26 (UX): what a plan-state tariff gap says about the Library tariffs held now, as the outcome of approving.
const HELD_WORDS: Record<string, string> = {
  none: 'No Library tariff is held, so approving leaves it empty.',
  unreadable: 'Library tariffs could not be read now: they are read again when you approve.',
  unnamed: 'A Library tariff without an id is held, so approving fills none.',
}
const heldOutcome = (n: number) => n === 1
  ? 'Approving fills it with this one if it is still the only one.'
  : `${n} are held now, so approving fills none: keep one in the Library to fill it.`

// Issue 37 (UX): the template in words, never its engine id (#1194).
const TEMPLATE_WORDS: Record<string, string> = { eh_site: 'site' }
/** "Creates project <name> from the site template", or the generic words for a template with none. */
export const setupWords = (name: string, template: string) =>
  TEMPLATE_WORDS[template] ? `Creates project ${name} from the ${TEMPLATE_WORDS[template]} template`
    : `Creates project ${name} from the chosen template`
/** A preset default opens with the field's own label, never its key (#1199, #1190). The labels are
 *  those of `run_profiles.EH_SITE_FIELDS` (a parity test reads them); a group opens with its group's name. */
export const DEFAULT_WORDS: Record<string, string> = {
  site_type: 'Site type', stages: 'Growth stages', connection_mw: 'Grid connection', energization: 'Connection energization',
  critical_share: 'Critical load share', load_profile: 'Load shape', existing: 'Existing equipment',
  candidates: 'New equipment considered', ride_through: 'Ride-through required', fault_level_mva: 'Grid fault level',
  horizon_end: 'Study horizon end',
}
const DEFAULT_UNITS: Record<string, string> = { connection_mw: 'MW', fault_level_mva: 'MVA' }
const showValue = (v: unknown, unit: string | null) => {
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  if (Array.isArray(v)) return `${v.length} entries`
  if (typeof v === 'object' && v !== null) return 'several values'
  if (unit === 'EUR' && typeof v === 'number') return fmtCurrency(v)
  return unit ? `${v} ${unit}` : String(v)
}
const showDefault = (d: { param: string; value: unknown }) =>
  d.param === 'critical_share' && typeof d.value === 'number' ? `${+(d.value * 100).toFixed(1)} %`
    : showValue(d.value, DEFAULT_UNITS[d.param] ?? null)

/** Issue 21 "Inputs from the chat": each row with its value, unit and source; read-only here. */
function IntakeRows({ intake }: { intake: RunIntake }) {
  const rows = intake.card.rows
  const rival = (r: IntakeRow) => rows.find(o => o.index !== r.index && o.param === r.param && o.status === 'conflict')
  const defaults = intake.card.defaults
  return <div data-testid="envelope-intake" className="my-1">
    <div className="text-[10px] uppercase tracking-wide text-accent">Inputs from the chat</div>
    <ul aria-label="Inputs from the chat" className="space-y-0.5">
      {rows.map(r => {
        const struck = r.status === 'struck'
        const other = r.status === 'conflict' ? rival(r) : undefined
        return <li key={r.index} data-testid="envelope-intake-row" data-status={r.status}>
          <span className={struck ? 'text-muted line-through' : undefined}>{r.label}: {showValue(r.value, r.unit)}</span>
          {struck && <>{' '}<span className="text-muted" data-testid="envelope-intake-struck">· Struck</span></>}
          {r.status === 'conflict' && <>{' '}<span className="text-warn" data-testid="envelope-intake-conflict">
            · Not used: {other ? `conflicts with ${other.source}` : 'two sources disagree'}</span></>}
          {r.from_document && <>{' '}<span className="text-muted" data-testid="envelope-intake-document">· read from a document</span></>}
          <div className="text-[11px] text-muted" data-testid="envelope-intake-source">{r.source}</div>
        </li>
      })}
    </ul>
    {intake.missing.length > 0 && <div data-testid="envelope-intake-missing">Still missing: {intake.request ?? `${intake.missing.length} inputs`}</div>}
    {defaults.length > 0 && <>
      <div className="text-muted">Not given, so the preset default is used:</div>
      <ul aria-label="Preset defaults" className="space-y-0.5">
        {defaults.map(d => <li key={d.param} data-testid="envelope-intake-default">
          {DEFAULT_WORDS[d.param] ?? 'Another setting'}: {showDefault(d)} <span className="text-muted">· preset default</span></li>)}
      </ul></>}
  </div>
}

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
  const expert = useUIStore(s => s.uiMode === 'expert')
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

  // A plan that creates a project binds its grant to the intake digest the card shows: echo it, never compute it.
  const setup = envelope.setup ?? proposed.setup
  const intake = view?.run.intake
  const digest = setup && intake ? { intake_digest: intake.digest } : {}
  const unattended = view?.monitor.unattended
  const block = !view ? 'Checking whether this server can run it unattended…'
    : setup && intake && intake.missing.length > 0 ? 'Inputs are still missing: see the list above.'
    : setup && !intake ? 'The inputs this plan creates the project from are not shown yet.'
    : unattended && !unattended.available ? UNATTENDED_BLOCK_WORDS[unattended.reason ?? ''] ?? 'Not available here'
      : envelope.goal?.value === null ? GOAL_BLOCK : null
  const stepwiseBlock = setup && intake && intake.missing.length > 0 ? 'Inputs are still missing: see the list above.' : null
  const emailOk = view?.monitor.email.available === true
  const costCap = view?.monitor.budgets.cost_eur.limit

  const grant = () => onVerdict({
    verdict: 'unattended', notify_email: emailOk && email, ...digest,
    envelope: {
      edits: envelope.edits.filter((_, i) => edits[i]), runs: envelope.runs.filter((_, i) => runs[i]),
      budget: { solves, rounds: envelope.budget.rounds, wall_time_h: hours }, research: envelope.research,
    },
    // GK B (34) F1: echo the goal this card shows, so the grant freezes exactly it.
    ...(envelope.goal ? { goal: envelope.goal } : {}),
  })

  return <div data-testid="envelope-section" className="mb-2 rounded border border-border bg-bg-2/40 px-2.5 py-1.5 text-[12px]">
    {setup && <div data-testid="envelope-setup" className="mb-1 font-medium text-text">
      {setupWords(setup.name, setup.template)}</div>}
    {intake && <IntakeRows intake={intake} />}
    <div className="text-[10px] uppercase tracking-wide text-accent">Envelope</div>
    <p className="text-muted">What the run may do without asking you, if you approve it to run unattended:</p>
    <ul className="my-1 space-y-0.5" aria-label="Edits it may make">
      {envelope.edits.map((e, i) => <li key={i} data-testid="envelope-edit">
        <label className="inline-flex items-start gap-1.5">
          <input type="checkbox" checked={edits[i] ?? false} disabled={disabled}
            onChange={() => setEdits(cur => cur.map((v, j) => (j === i ? !v : v)))} />
          <span><span className={edits[i] ? undefined : 'text-muted line-through'}>{editSubject(e)}: {editLimits(e)}</span>
            {!edits[i] && <>{' '}<span className="inline-block text-muted" data-testid="envelope-removed">· removed</span></>}</span>
        </label>
      </li>)}
      {envelope.runs.map((r, i) => <li key={`r${i}`} data-testid="envelope-run">
        <label className="inline-flex items-start gap-1.5">
          <input type="checkbox" checked={runs[i] ?? false} disabled={disabled}
            onChange={() => setRuns(cur => cur.map((v, j) => (j === i ? !v : v)))} />
          <span><span className={runs[i] ? undefined : 'text-muted line-through'}>{runWords(r)}</span>
            {!runs[i] && <>{' '}<span className="inline-block text-muted" data-testid="envelope-removed">· removed</span></>}</span>
        </label>
      </li>)}
    </ul>
    {envelope.gaps?.filter(g => g.source.kind === 'library_tariff' && g.held_state).map((g, i) =>
      <div key={`g${i}`} data-testid="envelope-held" className="my-1">
        <div>Fills {g.target.name} tariff from the Library, only where none is set.</div>
        {g.held_state === 'held' && g.held
          ? <>
            <div className="text-muted">Held now (read again when you approve):</div>
            <ul aria-label="Library tariffs held now" className="space-y-0.5">
              {g.held.map(t => <li key={t.hash} data-testid="envelope-held-tariff">
                {t.name}{t.version != null && ` v${t.version}`}
                {expert && <> · <span className="font-mono">{t.hash.slice(0, 8)}</span></>}
              </li>)}
            </ul>
            <div className="text-muted" data-testid="envelope-held-note">{heldOutcome(g.held.length)}</div>
          </>
          : <div className="text-muted" data-testid="envelope-held-note">{HELD_WORDS[g.held_state!]}</div>}
      </div>)}
    {/* Two short rows, so a wrap never leaves a "·" at a line's end. */}
    <div className="space-y-1" data-testid="envelope-budgets">
      <div className="flex flex-wrap items-center gap-x-2" data-testid="envelope-budget-row">
        <Stepper label="Solves up to" name="Solves, at most" value={solves} max={proposed.budget.solves} onChange={setSolves} testId="envelope-solves" />
        <span>·</span>
        <span>Rounds up to {envelope.budget.rounds}</span>
      </div>
      <div className="flex flex-wrap items-center gap-x-2" data-testid="envelope-budget-row">
        <Stepper label="Expires after" name="Hours until it expires, at most" value={hours} max={proposed.budget.wall_time_h} unit="h" onChange={setHours} testId="envelope-hours" />
        <span>·</span>
        <span>{costCap !== undefined && costCap > 0 ? `Cost cap ${fmtCurrency(costCap)}` : 'no cost cap'}</span>
      </div>
    </div>
    {envelope.goal && <GoalRow goal={envelope.goal} />}
    <div data-testid="envelope-research">
      {envelope.research.allowed ? `Research cost cap ${fmtCurrency(envelope.research.cost_cap_eur)}` : 'No research'}
    </div>
    <p className="text-muted">Always asks you first: deletes, publishing a grid code, outside tools, researched values,
      and anything not listed here.</p>
    <label className="inline-flex items-center gap-1.5" data-testid="envelope-email">
      <input type="checkbox" checked={emailOk && email} disabled={disabled || !emailOk} onChange={() => setEmail(v => !v)} />
      <span>Email me when it needs me or ends{view && !emailOk
        && <span className="text-muted" data-testid="envelope-email-reason">: email is not set up on this server</span>}</span>
    </label>
    <div className="mt-2 flex flex-col gap-1.5" role="group" aria-label="Your verdict">
      <button type="button" data-testid="envelope-verdict" data-verdict="unattended"
        disabled={disabled || block !== null} onClick={grant}
        className={`${VERDICT} border-accent/70 bg-accent/10 hover:bg-accent/20`}>
        <span className="font-medium text-text">Approve and run unattended</span>
      </button>
      {block && <div className="text-[11px] text-muted" data-testid="envelope-unattended-reason">{block}</div>}
      <button type="button" data-testid="envelope-verdict" data-verdict="stepwise" disabled={disabled || (setup != null && !intake) || stepwiseBlock !== null}
        onClick={() => onVerdict({ verdict: 'stepwise', ...digest })}
        className={`${VERDICT} border-border bg-bg-2/60 hover:bg-bg-3/50`}>
        <span className="font-medium text-text">Approve, step by step</span>
        <div className="text-[11px] text-muted mt-0.5">{setup ? `Creates project ${setup.name} now; every other change and run asks you first.` : 'Every change and run asks you first.'}</div>
      </button>
      {stepwiseBlock && <div className="text-[11px] text-muted" data-testid="envelope-stepwise-reason">{stepwiseBlock}</div>}
      <button type="button" data-testid="envelope-verdict" data-verdict="change" disabled={disabled}
        onClick={() => onVerdict({ verdict: 'change' })}
        className={`${VERDICT} border-border bg-bg-2/60 hover:bg-bg-3/50`}>
        <span className="font-medium text-text">Change</span>
      </button>
    </div>
  </div>
}

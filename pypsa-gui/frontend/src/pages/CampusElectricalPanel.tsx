// The campus electrical study of a hub project (plan C6).
//
// A capacity-expansion (hub) project, solved and saved, is taken here to its
// electrical design:
// - transformer ratings;
// - reactive compensation at the PCC;
// - short circuit against the switchgear;
// - PCC grid-code compliance, at the critical hours, by AC load flow;
// - what to buy from the asset library to meet it, at least cost (plan C9);
// - optionally, the joint optimisation (a MILP, plan C11), run as a background
//   job with progress and cancel (plan C12), its result beside the least-cost one.
//
// Thin like GridspinePanel. Everything shown is READ from
// `GET /api/campus-electrical/{name}`, and every action is one request:
// draft, save, run (and the library's save and reset). Refusals come back as the backend's message and render
// inline: another project kind (409), not saved or solved (422), a campus
// file that does not build (422, naming the field).
//
// A run is a solve-queue job of kind `campus` (E2E demo plan 2026-10-14, D1): the
// request queues it and answers at once; the job runs for minutes. The panel reads
// the job and the stage it reached from the state's `run_job`, polls while it is
// queued or running, says plainly that the user should wait, and offers Abort
// (the queue's). The header's status pill shows the same job.
// When the queueing request gets no answer (a timeout, a dropped connection) the
// backend may still have queued it, so the panel never treats the results it
// shows as the new run's: it re-reads the state, warns while the backend says a
// run is going on, and says once it ends whether the results it saved are the
// run asked for.
import { assetLabel } from './results/investment/financeModel'
import { SiteTypeSelect } from '../components/reliabilityGoal/SiteTypeSelect'
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Play, RefreshCw, Save, Square, Wand2, Workflow } from 'lucide-react'
import {
  campusApi, errorText, isActiveRun, isNoAnswer, isOtherKind, NO_LIMIT_DETAIL,
  type CampusRunJob, type CampusSettings, type CampusState, type CheckStatus, type ComplianceRow,
  type InvestedComplianceRow, type InvestmentRow, type MilpJobRecord, type TransformerSizing,
} from '../api/campusElectrical'
import { solveQueueApi } from '../api/solveQueue'
import { useUIStore } from '../store/uiStore'
import { openPanel } from './results/openPanel'
import { settingsFromDraft, useCampusDraft, useCampusRunNotice, type LostRun } from '../store/campusSettingsDraft'
import { isNotSolvedRefusal, NotSolvedRefusal } from './results/NotSolved'
import { Btn, Field, PageBody, PageSection, Tag } from '../components/PageKit'
import { ConnectionLimits } from '../components/gridReadiness/ConnectionLimits'
import { SiteConnectionEditor } from './economics/SiteConnectionLink'
import CampusGridCodeSection, { GRID_CODES_KEY } from './CampusGridCodeSection'
import CampusInvestmentSection, { TableScroll, formatEur, iterationsText } from './CampusInvestmentSection'
import CampusHarmonicScanSection from './CampusHarmonicScanSection'
import { GRID_CODE_CHANGED } from '../components/gridReadiness/GridReadinessScorecard'
import CampusIslandSection, { ISLAND_REPORT_KEY } from './CampusIslandSection'
import CampusLibrarySection from './CampusLibrarySection'
import { useCampusMilpJob } from './useCampusMilpJob'
import { elapsed, failureLine, stageLabel, stepText, useTick } from './campusRunProgress'

export const CAMPUS_KEY = (name: string) => ['campusElectrical', 'state', name] as const
/** How often the state is re-read while a run goes on without an answer. */
export const CAMPUS_RUN_POLL_MS = 1500

function sameSettings(a: CampusSettings | null, b: CampusSettings): boolean {
  if (a == null) return false
  return (Object.keys(b) as (keyof CampusSettings)[]).every(k => a[k] === b[k])
}

const INPUT = 'px-2.5 py-1.5 text-sm border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20'
export const DEFAULT_SETTINGS: CampusSettings = {
  k: 3, pf: null, profile: 'eu_rfg_dcc_ce', margin: 0.2, n_minus_1: true, invest: true, pcc_switchgear_by_operator: false,
  pcc_tap_control: 'campus',
}

const CHECK_LABEL: Record<ComplianceRow['check'], string> = {
  pcc_reactive: 'PCC reactive power',
  pcc_voltage: 'PCC voltage',
  campus_voltage: 'Voltages inside the campus',
  transformer_loading: 'Transformer loading',
  switchgear: 'Switchgear short circuit',
  cable_loading: 'Cable loading',
  pcc_q_band: 'Reactive power band against active power at the PCC',
  pcc_power_factor: 'Minimum power factor at the PCC',
}
const STATUS_TEXT: Record<CheckStatus, string> = {
  pass: 'pass', fail: 'fail', not_rated: 'not rated', not_rechecked: 'not re-checked', unknown: 'not checked',
}
const STATUS_TONE: Record<CheckStatus, 'ok' | 'err' | 'neutral' | 'warn'> = {
  pass: 'ok', fail: 'err', not_rated: 'neutral', not_rechecked: 'warn', unknown: 'neutral',
}

function num(v: number | null | undefined, digits = 2): string {
  return v == null || !isFinite(v) ? '—' : v.toFixed(digits).replace(/\.?0+$/, m => (m.startsWith('.') ? '' : m))
}

/** A figure and its unit; a missing figure is "—" alone, with no unit after it. */
function numU(v: number | null | undefined, unit: string, digits = 2): string {
  return v == null || !isFinite(v) ? '—' : `${num(v, digits)}\u00A0${unit}`
}

function where(period: number | null | undefined, hour: number | null | undefined, kase?: string | null): string {
  if (period == null) return ''
  return [`${period}`, hour == null ? null : `h${hour}`, kase || null].filter(Boolean).join(' · ')
}

/** Which part of the study a panel shows: its inputs (section 3), its settings,
 *  run and island (section 8), or its results (section 10). */
type CampusPart = 'inputs' | 'study' | 'results'
const PART_TITLE: Record<CampusPart, string> = {
  inputs: 'Grid connection', study: 'Grid study', results: 'Grid study results',
}

/** The one link to the next part: 3 → 8 → 10. */
function NextPart({ part }: { part: CampusPart }) {
  const setSlidePanel = useUIStore(s => s.setSlidePanel)
  if (part === 'results') return null
  const next = part === 'inputs'
    ? { panel: 'campusStudy' as const, label: 'Next: study settings and run' }
    : { panel: 'campusResults' as const, label: 'Next: results' }
  return (
    <div className="flex justify-end">
      <Btn onClick={() => setSlidePanel(next.panel)}>
        <span data-testid={`campus-next-${part}`}>{next.label}</span>
      </Btn>
    </div>
  )
}

function CampusPanel({ part }: { part: CampusPart }) {
  const currentProject = useUIStore(s => s.currentProject)
  if (!currentProject) {
    return (
      <PageBody>
        <PageSection title={PART_TITLE[part]}>
          <p className="text-[12px] text-muted">
            Open a solved hub project to take it to its electrical design.
          </p>
        </PageSection>
      </PageBody>
    )
  }
  return <CampusView name={currentProject} part={part} />
}

export default function CampusElectricalPanel() { return <CampusPanel part="inputs" /> }
export function CampusStudyPanel() { return <CampusPanel part="study" /> }
const bandEdge = (value: number) => num(value, 3).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '').replace('-', '\u2212')

export function CampusResultsPanel() { return <CampusPanel part="results" /> }

function CampusView({ name, part }: { name: string; part: CampusPart }) {
  const qc = useQueryClient()
  // The site connection is the project's, not the campus study's: it shows even when the campus read fails
  // or the project is not a hub, because Economics and Investment send the user here to set it.
  // The site type (W3b-R) heads the panel: the reliability goal's, which every
  // reliability study runs; Guided sets it on the Site card.
  const lead = part === 'inputs' ? (<>
    <PageSection title="Site type" hint="The grid connection every reliability study assumes">
      <SiteTypeSelect mode="expert" testId="campus-site-type" label="Grid connection" />
    </PageSection>
    <PageSection title="Site connection" hint="The point of connection the bill and the investment case read">
      <SiteConnectionEditor />
    </PageSection>
  </>) : null
  const notice = useCampusRunNotice(s => s.notices[name])
  const setNotice = useCampusRunNotice(s => s.set)
  const lost: LostRun | null = notice?.lost ?? null
  const outcome: string | null = notice?.outcome ?? null
  const setLost = (l: LostRun | null) => setNotice(name, { lost: l })
  const setOutcome = (o: string | null) => setNotice(name, { outcome: o })
  const state = useQuery({
    queryKey: CAMPUS_KEY(name), queryFn: () => campusApi.state(name), retry: false,
    refetchInterval: q => (lost != null || q.state.data?.run_in_progress || isActiveRun(q.state.data?.run_job)
      ? CAMPUS_RUN_POLL_MS : false),
  })
  // When the run's job ends, its results are new: the island report reads them too.
  const jobRef = useRef<CampusRunJob | null | undefined>(undefined)
  useEffect(() => {
    const job = state.data?.run_job
    const was = jobRef.current
    jobRef.current = job
    if (was != null && isActiveRun(was) && job != null && job.id === was.id && !isActiveRun(job)) {
      void qc.invalidateQueries({ queryKey: ISLAND_REPORT_KEY(name) })
    }
  }, [state.data?.run_job, qc, name])
  // Once a read made after the lost request (the effect runs on new data only) finds no run
  // going on, the backend has ended: say whether what it saved is the run asked for.
  const lostRef = useRef(lost)
  lostRef.current = lost
  useEffect(() => {
    const l = lostRef.current
    if (l == null || !state.data || state.data.run_in_progress) return
    setOutcome(sameSettings(state.data.settings, l.settings)
      ? 'The backend finished the run after the request went unanswered: the results below are that run\'s.'
      : 'The backend saved no results for the run asked for: the results below, if any, are from an earlier run. '
        + 'Run the study again.')
    setLost(null)
  }, [state.data, state.dataUpdatedAt])

  if (state.isError && isOtherKind(state.error)) {
    return (
      <PageBody>
        {lead}
        <PageSection title={PART_TITLE[part]}>
          <p className="text-[12px] text-muted">
            The campus electrical study is for capacity-expansion (hub) projects. {errorText(state.error)}
          </p>
        </PageSection>
      </PageBody>
    )
  }
  if (!state.data) {
    return (
      <PageBody>
        {lead}
        <PageSection title={PART_TITLE[part]}>
          <p className="text-[12px] text-muted">{state.isError ? errorText(state.error) : 'Loading…'}</p>
        </PageSection>
      </PageBody>
    )
  }
  const refresh = (data?: CampusState) => {
    // A run writes the island-hours results the island report reads (I9b).
    void qc.invalidateQueries({ queryKey: ISLAND_REPORT_KEY(name) })
    return data ? qc.setQueryData(CAMPUS_KEY(name), data) : qc.invalidateQueries({ queryKey: CAMPUS_KEY(name) })
  }
  const onRunStart = () => { setOutcome(null); setLost(null) }
  const onRunLost = (settings: CampusSettings, why: string) => {
    setLost({ settings, why })
    void qc.invalidateQueries({ queryKey: CAMPUS_KEY(name) })
  }
  const pending = lost != null || state.data.run_in_progress === true || isActiveRun(state.data.run_job)

  return (
    <PageBody>
      {part === 'inputs' && (
        <div data-testid="campus-intro">
          <PageSection title={PART_TITLE[part]} hint="From the solved hub to its electrical design">
            <p className="text-[12px] text-muted">
              A steady-state AC load-flow study at the hub's critical hours. It sizes the transformers and the
              reactive compensation, checks short circuit against the switchgear, and checks the PCC against
              the chosen grid code. It is not a dynamic study, and not a compliance certificate.
            </p>
          </PageSection>
        </div>
      )}
      {part === 'inputs' && (<>
        {lead}
        <CampusFileSection name={name} state={state.data} onChange={refresh} />
        <GridConnectionSection name={name} state={state.data} />
        <ConnectionLimits />
        <CampusGridCodeSection
          name={name}
          onProfilesChanged={() => qc.invalidateQueries({ queryKey: CAMPUS_KEY(name) })}
        />
        <CampusLibrarySection name={name} onChanged={() => qc.invalidateQueries({ queryKey: CAMPUS_KEY(name) })} />
      </>)}
      {part === 'study' && (<>
        {state.data.campus_yaml ? (
          <RunSection name={name} state={state.data} onChange={refresh} lost={lost} outcome={outcome} pending={pending}
                      onRunStart={onRunStart} onRunLost={onRunLost} />
        ) : (
          <PageSection title="Study settings">
            <p className="text-[12px] text-muted">
              Draft the campus file in the grid connection first; the study runs on it.
            </p>
          </PageSection>
        )}
        <CampusIslandSection name={name} />
      </>)}
      {part === 'results' && (state.data.results
        ? <ResultsSection state={state.data} pending={pending} />
        : (
          <PageSection title="Results">
            <p className="text-[12px] text-muted" data-testid="campus-no-results">
              No grid study results yet. Run the study from its settings.
            </p>
          </PageSection>
        ))}
      <NextPart part={part} />
    </PageBody>
  )
}

/** A refusal in words; an unsolved project gets the state and the action instead of a red sentence. */
function Refusal({ text, className }: { text: string; className: string }) {
  if (isNotSolvedRefusal(text)) {
    return <div className={className} data-testid="campus-not-solved"><NotSolvedRefusal what="The grid checks" /></div>
  }
  return <p className={`text-[12px] text-danger ${className}`}>{text}</p>
}

function CampusFileSection({ name, state, onChange }: {
  name: string; state: CampusState; onChange: (d?: CampusState) => unknown
}) {
  const [text, setText] = useState(state.campus_yaml ?? '')
  const [refusal, setRefusal] = useState<string | null>(null)
  useEffect(() => { setText(state.campus_yaml ?? '') }, [state.campus_yaml])

  const draft = useMutation({
    mutationFn: (overwrite: boolean) => campusApi.draft(name, overwrite),
    onMutate: () => setRefusal(null),
    onSuccess: () => onChange(),
    onError: e => setRefusal(errorText(e)),
  })
  const save = useMutation({
    mutationFn: () => campusApi.save(name, text),
    onMutate: () => setRefusal(null),
    onSuccess: () => onChange(),
    onError: e => setRefusal(errorText(e)),
  })
  const has = state.campus_yaml != null
  const dirty = has && text !== state.campus_yaml

  return (
    <PageSection
      title="Campus file"
      hint={has ? 'Edit, then save; every value carries its source tag' : 'Draft it from the project, or paste your own'}
      right={has ? (
        <Btn
          onClick={() => { if (window.confirm('Draft again from the project? This replaces your edits.')) draft.mutate(true) }}
          disabled={draft.isPending}
        >
          <RefreshCw size={13} /> Draft again
        </Btn>
      ) : (
        <Btn variant="primary" onClick={() => draft.mutate(false)} disabled={draft.isPending}>
          <Wand2 size={13} /> Draft from project
        </Btn>
      )}
    >
      {refusal && <Refusal text={refusal} className="mb-2" />}
      {state.skipped.length > 0 && (
        <div className="text-[11.5px] text-muted mb-2">
          Left out of the draft: <ul className="list-disc ml-5">{state.skipped.map(s => <li key={s}>{s}</li>)}</ul>
        </div>
      )}
      <textarea
        aria-label="Campus file"
        className={`${INPUT} w-full font-mono text-[11.5px] h-[260px]`}
        spellCheck={false}
        value={text}
        onChange={e => setText(e.target.value)}
        placeholder="campus:\n  pcc: …"
      />
      <div className="flex justify-end mt-2">
        <Btn onClick={() => save.mutate()} disabled={save.isPending || !text.trim() || (has && !dirty)}>
          <Save size={13} /> Save campus file
        </Btn>
      </div>
    </PageSection>
  )
}

/** The grid connection of the study (section 3): the grid code the PCC is held to,
 *  the connection-agreement power factor, and who owns the PCC switchgear and
 *  moves its taps. The study settings in section 8 read the same draft. */
function GridConnectionSection({ name, state }: { name: string; state: CampusState }) {
  const { draft, patch } = useCampusDraft(name, state.settings ?? DEFAULT_SETTINGS)
  // The grid-codes listing says which published profiles still carry
  // unconfirmed limits; the picker names them so a run is never held to one unawares.
  const codes = useQuery({ queryKey: GRID_CODES_KEY(name), queryFn: () => campusApi.gridCodes(name), retry: false })
  const unconfirmed = new Set((codes.data?.published ?? []).filter(p => p.unconfirmed.length > 0).map(p => p.id))
  return (
    <PageSection title="Grid code and PCC" hint="Applies to the next run">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Grid code">
          <select aria-label="Grid code" className={`${INPUT} w-[320px]`} value={draft.profile}
                  onChange={e => patch({ profile: e.target.value })}>
            {Object.entries(state.profiles).map(([key, title]) => (
              <option key={key} value={key}>{unconfirmed.has(key) ? `${title} (unconfirmed limits)` : title}</option>
            ))}
          </select>
        </Field>
        <Field label="Connection-agreement power factor" hint="empty: the grid code's widest range">
          <input aria-label="Connection-agreement power factor" className={`${INPUT} w-[90px]`} inputMode="decimal" value={draft.pf}
                 placeholder="code" onChange={e => patch({ pf: e.target.value })} />
        </Field>
        <Field label="PCC switchgear owned by the grid operator (not costed)" row>
          <input type="checkbox" aria-label="PCC switchgear owned by the grid operator (not costed)" checked={draft.byOperator}
                 disabled={!draft.invest} onChange={e => patch({ byOperator: e.target.checked })} />
        </Field>
        <Field label="PCC transformer taps held by the grid operator (at the campus file's position)" row>
          <input type="checkbox" aria-label="PCC transformer taps held by the grid operator (at the campus file's position)"
                 checked={draft.tapsByOperator} disabled={!draft.invest}
                 onChange={e => patch({ tapsByOperator: e.target.checked })} />
        </Field>
      </div>
      {!draft.invest && (
        <p className="text-[11.5px] text-muted mt-2">
          Who owns the PCC switchgear and moves its taps only matters when the study buys assets from the
          library; that is set in the study settings.
        </p>
      )}
    </PageSection>
  )
}

function RunSection({ name, state, onChange, lost, outcome, pending, onRunStart, onRunLost }: {
  name: string; state: CampusState; onChange: (d?: CampusState) => unknown
  /** The run whose request got no answer, while the panel waits for the backend. */
  lost: LostRun | null
  /** Once it ended: whether the backend saved the run asked for. */
  outcome: string | null
  /** A run is going on in the backend: no second one is asked for. */
  pending: boolean
  onRunStart: () => void
  onRunLost: (settings: CampusSettings, why: string) => void
}) {
  const { draft, patch } = useCampusDraft(name, state.settings ?? DEFAULT_SETTINGS)
  const { k, margin, n1, invest } = draft
  const [refusal, setRefusal] = useState<string | null>(null)
  // The grid connection (profile, power factor, PCC ownership) is edited in section 3.
  const settings = (): CampusSettings => settingsFromDraft(draft)
  const run = useMutation({
    mutationFn: (s: CampusSettings) => campusApi.run(name, s),
    onMutate: () => { setRefusal(null); onRunStart() },
    // The answer is the queued job: re-read the state, whose `run_job` the panel follows.
    onSuccess: () => onChange(),
    onError: (e, s) => (isNoAnswer(e) ? onRunLost(s, errorText(e)) : setRefusal(errorText(e))),
  })
  const abortRun = useMutation({
    mutationFn: (jobId: string) => solveQueueApi.abort(jobId),
    onSuccess: () => onChange(),
    onError: e => setRefusal(errorText(e)),
  })
  const job = state.run_job ?? null
  // The joint optimisation: a background job, polled while it runs; when it
  // ends the state is re-read so its results appear beside the least-cost ones.
  const milp = useCampusMilpJob(name, { onStarted: () => onChange(), onFinished: () => onChange() })
  const hasLeastCost = state.results?.investment != null
  const startMilp = () => {
    setRefusal(null)
    milp.start(settings()).catch(e => setRefusal(isNoAnswer(e)
      ? `No answer from the backend to the start of the joint optimisation (${errorText(e)}). It may have started: `
        + 'its progress is read again.'
      : errorText(e)))
  }
  const cancelMilp = () => { milp.cancel().catch(e => setRefusal(errorText(e))) }
  const busy = run.isPending || milp.isStarting || milp.isRunning || pending

  return (
    <PageSection title="Study settings" hint="Applies to the next run">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Hours per criterion">
          <input aria-label="Hours per criterion" className={`${INPUT} w-[70px]`} inputMode="numeric" value={k} onChange={e => patch({ k: e.target.value })} />
        </Field>
        <Field label="Design margin (%)">
          <input aria-label="Design margin (%)" className={`${INPUT} w-[70px]`} inputMode="numeric" value={margin} onChange={e => patch({ margin: e.target.value })} />
        </Field>
        <Field label="Transformer N-1" row>
          <input type="checkbox" aria-label="Transformer N-1" checked={n1} onChange={e => patch({ n1: e.target.checked })} />
        </Field>
        <Field label="Buy assets from the library (least cost, AC-checked)" row>
          <input type="checkbox" aria-label="Buy assets from the library (least cost, AC-checked)" checked={invest}
                 onChange={e => patch({ invest: e.target.checked })} />
        </Field>
        <Btn variant="primary" onClick={() => run.mutate(settings())} disabled={busy}>
          <Play size={13} /> {run.isPending ? 'Queueing…' : isActiveRun(job) ? 'Running…' : 'Run study'}
        </Btn>
        <Btn
          onClick={startMilp}
          disabled={!hasLeastCost || busy}
          title={hasLeastCost ? undefined : 'Run the study with assets bought from the library first'}
        >
          <Workflow size={13} /> {milp.isStarting ? 'Starting…' : 'Joint optimisation (MILP, slow — minutes)'}
        </Btn>
      </div>
      {job != null && <RunJobStatus job={job} onAbort={() => abortRun.mutate(job.id)} aborting={abortRun.isPending} />}
      <p className="text-[11.5px] text-muted mt-2">
        The joint optimisation chooses every asset together instead of one need at a time, starting from the
        least-cost choice and re-checking each step by AC load flow. It takes minutes on a real site and runs in the
        background; you can cancel it and keep the best choice found so far.
        {!hasLeastCost && ' It needs a least-cost run first: run the study with assets bought from the library.'}
      </p>
      {milp.record && (
        <MilpProgress record={milp.record} cancelling={milp.cancelling} onCancel={cancelMilp}
                      stale={state.milp?.stale === true} />
      )}
      {refusal && <Refusal text={refusal} className="mt-2" />}
      {lost && (
        <p data-testid="campus-run-lost" role="alert" className="text-[12px] text-warn mt-2">
          No answer from the backend to the run ({lost.why}). It may still be running the study, and saves its
          results when it ends; the panel reads them again until then.
        </p>
      )}
      {outcome && <p data-testid="campus-run-outcome" className="text-[12px] mt-2">{outcome}</p>}
    </PageSection>
  )
}

/** The job's line: while it runs, its iteration and best cost against the
 *  least-cost one, with Cancel; once ended, how it ended, or that its result is
 *  stale. Iteration 0 is the warm start (the least-cost choice re-solved), so it
 *  is named, not counted: the counts agree with the summary and the history. */
function MilpProgress({ record, cancelling, onCancel, stale }: {
  record: MilpJobRecord; cancelling: boolean; onCancel: () => void
  /** The least-cost run the finished result came from has changed since. */
  stale: boolean
}) {
  if (record.state === 'running') {
    const started = record.iteration != null && record.max_iter != null
    const best = `best ${formatEur(record.best_cost)}/a vs least cost ${formatEur(record.c8_cost)}/a`
    return (
      <div data-testid="milp-progress" className="flex items-center gap-3 mt-2 text-[12px]">
        <span className="tabular-nums">
          {!started
            ? `Joint optimisation running: ${record.message}`
            : record.iteration === 0
              ? `Warm start solved, 0 of ${record.max_iter} iterations · ${best}`
              : `Iteration ${record.iteration} of ${record.max_iter} · ${best}`}
          {cancelling && <span className="text-muted"> · cancelling…</span>}
        </span>
        <Btn onClick={onCancel} disabled={cancelling}><Square size={12} /> Cancel</Btn>
      </div>
    )
  }
  if (record.state === 'failed') {
    return (
      <p data-testid="milp-progress" role="alert" className="text-[12px] text-danger mt-2">
        The joint optimisation failed: {record.error ?? 'no reason given'}.
      </p>
    )
  }
  const ended = record.state === 'cancelled' ? 'The joint optimisation was cancelled' : 'The joint optimisation has finished'
  return (
    <p data-testid="milp-progress" className={`text-[12px] mt-2 ${stale ? 'text-warn' : 'text-muted'}`}>
      {stale
        ? `${ended}, but the least-cost run it started from has changed since: its result is stale. Run it again `
          + 'before using it.'
        : record.state === 'cancelled'
          ? `${ended}; the best choice it had found is shown under Joint optimisation.`
          : `${ended}; its choice is shown under Joint optimisation.`}
    </p>
  )
}

/** The run's job, in words: queued or running (please wait, the stage, Abort),
 *  or how the last one ended. A finished run says nothing: its results show. */
function RunJobStatus({ job, onAbort, aborting }: { job: CampusRunJob; onAbort: () => void; aborting: boolean }) {
  useTick(isActiveRun(job))
  if (job.status === 'queued') {
    return (
      <p className="mt-2 text-[12px]" data-testid="campus-run-status" role="status">
        <Tag tone="accent">queued</Tag>{' '}
        The study is waiting for the solve queue{job.position != null ? ` (#${job.position})` : ''}. Please wait; it starts on its own.{' '}
        <Btn onClick={onAbort} disabled={aborting}><Square size={12} /> Abort</Btn>
      </p>
    )
  }
  // A GFM-settings job (GFM-b) is a campus job but not a run: the campus results stay current.
  const gfm = job.stage === 'gfm_settings'
  if (job.status === 'running' && gfm) {
    return (
      <p className="mt-2 text-[12px]" data-testid="campus-run-status" role="status">
        <Tag tone="accent">running</Tag>{' '}
        Recommending grid-forming settings from the island hours · {elapsed(job.started_at)}. The campus results
        below are current.{' '}
        <Btn onClick={onAbort} disabled={aborting}><Square size={12} /> Abort</Btn>
      </p>
    )
  }
  if (gfm && (job.status === 'failed' || job.status === 'interrupted' || job.status === 'aborted')) {
    const how = job.status === 'aborted' ? 'was stopped'
      : job.status === 'failed' ? `failed${job.error ? `: ${job.error}` : ''}` : 'was interrupted (the backend restarted)'
    return (
      <p className={`mt-2 text-[12px] ${job.status === 'aborted' ? 'text-muted' : 'text-danger'}`}
         data-testid="campus-run-status" role={job.status === 'aborted' ? undefined : 'alert'}>
        The grid-forming settings search {how}. The campus run's results are unchanged.
      </p>
    )
  }
  if (job.status === 'running') {
    const stage = job.stage ?? 'prepare'
    const step = stepText(job) ? ` (${stepText(job)})` : ''
    return (
      <p className="mt-2 text-[12px]" data-testid="campus-run-status" role="status">
        <Tag tone="accent">running</Tag>{' '}
        Running: {stageLabel(stage)}{step} · {elapsed(job.started_at)}. The study takes several minutes; please wait.
        The results below are the previous run's until it finishes.{' '}
        <Btn onClick={onAbort} disabled={aborting}><Square size={12} /> Abort</Btn>
      </p>
    )
  }
  if (job.status === 'failed' || job.status === 'interrupted') {
    return (
      <p className="mt-2 text-[12px] text-danger" data-testid="campus-run-status" role="alert">
        {failureLine(job)} The results below, if any, are from an earlier run.
      </p>
    )
  }
  if (job.status === 'aborted') {
    return (
      <p className="mt-2 text-[12px] text-muted" data-testid="campus-run-status">
        The last run was aborted{job.stage ? ` during ${stageLabel(job.stage)}` : ''}. The results below, if any,
        are from an earlier run.
      </p>
    )
  }
  return null
}

/** The first purchase the investment step chose for a transformer group (its
 *  need is `transformer <group>`), or undefined when the run bought nothing. */
function boughtFor(rows: InvestmentRow[] | null, group: string): InvestmentRow | undefined {
  return rows?.find(i => i.need === `transformer ${group}` && i.status === 'chosen' && !i.existing
                         && (i.purchase ?? 1) === 1)
}

/** The sizing verdict describes the campus file's drafted rating. When the
 *  investment buys the group anyway (for N-1, or because the drafted rating is
 *  a placeholder), the group is planned, and the verdict is shown as context. */
function TrafoVerdict({ t, bought }: { t: TransformerSizing; bought: InvestmentRow | undefined }) {
  const verdict = t.adequate ? 'adequate' : 'undersized'
  if (!bought) return <Tag tone={t.adequate ? 'ok' : 'err'}>{verdict}</Tag>
  return (
    <span className="whitespace-nowrap">
      <Tag tone="accent">planned</Tag>{' '}
      <span className="text-muted">
        (drafted rating {verdict}; bought as {bought.units} × <span title={bought.library_id ?? undefined}>{bought.library_id ? assetLabel(bought.library_id) : '—'}</span>{t.n_minus_1 ? ' for N-1' : ''})
      </span>
    </span>
  )
}

function StatusTag({ s, detail }: { s: CheckStatus; detail?: string | null }) {
  // unknown is "no limit" only when the profile sets none; a missing input is "not checked", its detail says which
  const text = s === 'unknown' && detail === NO_LIMIT_DETAIL ? 'no limit' : STATUS_TEXT[s]
  return <Tag tone={STATUS_TONE[s]}>{text}</Tag>
}

/** `show` picks the part of the results a Results tab mounts: the grid-code compliance
 *  (Grid code & connection), or the electrical design (investment, transformers,
 *  compensation, short circuit, critical hours); the Grid study results panel shows both. */
export type CampusResultsPart = 'all' | 'grid_code' | 'electrical_design'

function ResultsSection({ state, pending, show = 'all' }: { state: CampusState; pending: boolean; show?: CampusResultsPart }) {
  const grid = show !== 'electrical_design'
  const design = show !== 'grid_code'
  const r = state.results!
  const req = r.requirement
  // The investment's table re-solves every check with the assets in place; without it
  // the "with measures" column is part one's recommendation, and some checks are not re-checked.
  const invested = r.compliance_invested != null
  const rows: ComplianceRow[] = r.compliance_invested ?? r.compliance
  // What the grid code asks that no steady-state check here can show, each named once.
  const rms = [...new Set((r.reported_requirements ?? []).map(q => q.requirement))]
  return (
    <>
      {pending && (
        <p data-testid="campus-run-pending" role="status"
           className="text-[12px] text-warn border border-warn/40 rounded px-3 py-2">
          A study run is going on in the backend: the results below are from the previous run, not the one being
          run. They are replaced when it ends.
        </p>
      )}
      {(state.stale || state.grid_code_changed) && (
        <p data-testid="campus-results-stale" className="text-[12px] text-warn border border-warn/40 rounded px-3 py-2">
          {state.stale && 'The campus file or the project has changed since this run. Run the study again before using these numbers.'}
          {state.stale && state.grid_code_changed && ' '}
          {state.grid_code_changed && GRID_CODE_CHANGED}
        </p>
      )}
      {design && <CampusInvestmentSection state={state} />}

      {grid && <PageSection
        title="PCC compliance"
        hint={invested ? 'as the campus is, and with the purchased assets, re-solved' : 'as the campus is, and with the recommended measures'}
      >
        <TableScroll>
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-left text-muted">
                <th className="font-normal pr-3">Check</th><th className="font-normal pr-3">As is</th>
                <th className="font-normal pr-3">{invested ? 'With the assets (AC re-solved)' : 'With measures'}</th>
                <th className="font-normal pr-3">Worst</th>
                <th className="font-normal pr-3">Limit</th><th className="font-normal">Detail · rule</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(c => {
                // With the investment, the "with" side is an AC result of the campus as bought, read at its
                // own worst hour and case (a run from before they were kept has none: no hour is shown
                // rather than the as-is one).
                const w = c as InvestedComplianceRow
                const value = invested ? w.value_with_measures : c.value
                const detail = invested ? w.detail_with_measures : c.detail
                const at = invested
                  ? where(w.worst_period_with_measures, w.worst_hour_with_measures, w.worst_case_with_measures)
                  : where(c.worst_period, c.worst_hour, c.worst_case)
                return (
                  <tr key={c.check} data-testid={`compliance-${c.check}`} className="border-t border-border">
                    <td className="py-1 pr-3 whitespace-nowrap align-top">{CHECK_LABEL[c.check] ?? c.check}</td>
                    <td className="pr-3 align-top"><StatusTag s={c.status_as_is} detail={c.detail} /></td>
                    <td className="pr-3 align-top"><StatusTag s={c.status_with_measures} detail={detail} /></td>
                    <td data-testid={`compliance-worst-${c.check}`} className="pr-3 whitespace-nowrap align-top tabular-nums">
                      {num(value, 3)} {c.unit} <span className="block text-muted">{at}</span>
                    </td>
                    <td data-testid={`compliance-limit-${c.check}`} className="pr-3 whitespace-nowrap align-top tabular-nums text-muted">
                    {/* a unitless row (the power factor) reads back from the CSV with a null unit */}
                    {/* a band with a negative edge reads "−a to b": an en dash beside a minus is hard to read */}
                    {c.limit_min != null && c.limit_max != null
                      ? (c.limit_min < 0
                        ? `${bandEdge(c.limit_min)} to ${bandEdge(c.limit_max)} ${c.unit ?? ''}`
                        : `${num(c.limit_min, 3)}–${num(c.limit_max, 3)} ${c.unit ?? ''}`).trim()
                      : `${num(c.limit, 3)} ${c.unit ?? ''}`.trim()}
                  </td>
                    <td className="align-top min-w-[16rem]">
                      <div>{detail}</div>
                      {/* a row without a limit has no clause, and so no source to tag */}
                      {c.clause && (
                        <div className="text-[11px] text-muted flex items-start gap-1.5 mt-0.5">
                          <Tag tone={c.source === 'code' ? 'accent' : 'warn'}>{c.source}</Tag><span>{c.clause}</span>
                        </div>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </TableScroll>
        <p className="text-[11px] text-muted mt-2">
          PCC reactive band {req.q_limit_mvar == null ? '—' : `±${numU(req.q_limit_mvar, 'Mvar')}`} ({req.clause}); P_ref {numU(req.p_ref_mw, 'MW')}, {req.p_ref_from}.
        </p>
        {rms.length > 0 && (
          <p data-testid="compliance-rms" className="text-[11px] text-muted mt-1">
            Needs an RMS study (not checked here): {rms.join(' · ')}
          </p>
        )}
      </PageSection>}

      {grid && <CampusHarmonicScanSection scan={r.harmonic_scan} />}

      {design && <>
      <PageSection title="Transformers" hint="worst selected hour, intact and N-1, with the design margin">
        <TableScroll>
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-left text-muted">
                <th className="font-normal pr-3">Group</th><th className="font-normal pr-3">Now</th>
                <th className="font-normal pr-3">Peak S intact</th><th className="font-normal pr-3">Peak S N-1</th>
                <th className="font-normal pr-3">Required per unit</th><th className="font-normal pr-3">Recommended</th>
                <th className="font-normal">Verdict</th>
              </tr>
            </thead>
            <tbody>
              {r.transformers.map(t => (
                <tr key={t.group} data-testid={`trafo-${t.group}`} className="border-t border-border">
                  <td className="py-1 pr-3 whitespace-nowrap">{t.group}</td>
                  <td className="pr-3 whitespace-nowrap tabular-nums">{t.units} × {num(t.unit_rating_mva)} MVA</td>
                  <td className="pr-3 tabular-nums">{num(t.max_s_intact_mva)} MVA</td>
                  <td className="pr-3 tabular-nums">{t.n_minus_1 ? `${num(t.max_s_n1_mva)} MVA` : '—'}</td>
                  <td className="pr-3 tabular-nums">{num(t.required_unit_mva)} MVA</td>
                  <td className="pr-3 whitespace-nowrap tabular-nums">{t.units} × {num(t.recommended_unit_mva)} MVA</td>
                  <td data-testid={`trafo-verdict-${t.group}`}><TrafoVerdict t={t} bought={boughtFor(r.investment, t.group)} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      </PageSection>

      <PageSection title="Reactive compensation" hint="after the inverters' own headroom, sized to the worst hour">
        <TableScroll>
          <table className="w-full text-[12px]">
            <tbody>
              {r.compensation.map(c => (
                <tr key={c.direction} data-testid={`comp-${c.direction}`} className="border-t border-border first:border-0">
                  <td className="py-1 pr-3 capitalize">{c.direction}</td>
                  <td className="pr-3 tabular-nums">required {numU(c.required_mvar, 'Mvar')}</td>
                  <td className="pr-3 tabular-nums font-semibold">recommended {numU(c.recommended_mvar, 'Mvar')}</td>
                  <td className="text-muted">{where(c.worst_period, c.worst_hour)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      </PageSection>

      <PageSection title="Short circuit" hint="IEC 60909, every installed unit energised, per period">
        <TableScroll>
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-left text-muted">
                <th className="font-normal pr-3">Period</th><th className="font-normal pr-3">Bus</th>
                <th className="font-normal pr-3">kV</th><th className="font-normal pr-3">Ik'' max</th>
                <th className="font-normal pr-3">ip</th><th className="font-normal pr-3">Ik'' min</th>
                <th className="font-normal pr-3">Rating</th><th className="font-normal">Verdict</th>
              </tr>
            </thead>
            <tbody>
              {r.short_circuit.map(f => (
                <tr key={`${f.bus}-${f.period}`} data-testid={`fault-${f.bus}-${f.period}`} className="border-t border-border">
                  <td className="py-1 pr-3">{f.period}</td><td className="pr-3">{f.bus}</td>
                  <td className="pr-3 tabular-nums">{num(f.vn_kv)}</td>
                  <td className="pr-3 tabular-nums">{num(f.ikss_max_ka)} kA</td>
                  <td className="pr-3 tabular-nums">{num(f.ip_max_ka)} kA</td>
                  <td className="pr-3 tabular-nums">{num(f.ikss_min_ka)} kA</td>
                  <td className="pr-3 tabular-nums">{f.rated_ka == null ? '—' : `${num(f.rated_ka)} kA`}</td>
                  <td>{f.adequate == null ? <Tag tone="neutral">not rated</Tag>
                    : <Tag tone={f.adequate ? 'ok' : 'err'}>{f.adequate ? 'within rating' : 'over rating'}</Tag>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      </PageSection>

      <PageSection title="Critical hours" count={r.selection.length} hint="selected per investment period">
        <ul className="text-[12px] space-y-0.5">
          {r.selection.map(s => (
            <li key={`${s.period}-${s.hour}`} className="tabular-nums">
              {s.period} · h{s.hour} <span className="text-muted">— {s.reasons.join(', ')}</span>
            </li>
          ))}
        </ul>
      </PageSection>
      </>}
    </>
  )
}

/** The campus study's results as a Results tab (W10 R2): the same state the Grid study
 *  results panel reads, one part of it. A project the campus study is not for, or one
 *  without a run, says so; it never shows an empty table. */
export function CampusResultsTab({ name, part }: { name: string; part: Exclude<CampusResultsPart, 'all'> }) {
  const state = useQuery({
    queryKey: CAMPUS_KEY(name), queryFn: () => campusApi.state(name), retry: false,
    refetchInterval: q => (q.state.data?.run_in_progress || isActiveRun(q.state.data?.run_job)
      ? CAMPUS_RUN_POLL_MS : false),
  })
  if (state.isError) {
    return <p className="text-[12px] text-muted" data-testid="campus-tab-state">
      {isOtherKind(state.error)
        ? 'The campus electrical study is for capacity-expansion (hub) projects.'
        : errorText(state.error)}
    </p>
  }
  if (!state.data) return <p className="text-[12px] text-muted" data-testid="campus-tab-state">Loading…</p>
  if (!state.data.results) {
    return (
      <div data-testid="campus-tab-state" className="text-[12px] text-muted space-y-2">
        <p>Not run yet: the grid study has not produced results for this project.</p>
        <Btn onClick={() => openPanel('scenarios', 'campusStudy')}>Run the grid checks in Grid study ›</Btn>
      </div>
    )
  }
  const pending = state.data.run_in_progress === true || isActiveRun(state.data.run_job)
  return (
    <div className="space-y-3">
      <p className="text-[11px] text-muted" data-testid="campus-tab-from">From the Grid study (latest run).</p>
      <ResultsSection state={state.data} pending={pending} show={part} />
    </div>
  )
}

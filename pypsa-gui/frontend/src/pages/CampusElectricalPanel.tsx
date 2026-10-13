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
// A run is one synchronous request (seconds to minutes; `CAMPUS_RUN_TIMEOUT_MS`).
// When it gets no answer (a timeout, a dropped connection) the backend may still
// be running it and will save its results, so the panel never treats the results
// it shows as the new run's: it re-reads the state, warns while the backend says
// a run is going on (`run_in_progress`), and says once it ends whether the
// results it saved are the run asked for.
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Play, RefreshCw, Save, Square, Wand2, Workflow } from 'lucide-react'
import {
  campusApi, errorText, isNoAnswer, isOtherKind,
  type CampusSettings, type CampusState, type CheckStatus, type ComplianceRow, type InvestedComplianceRow,
  type MilpJobRecord,
} from '../api/campusElectrical'
import { useUIStore } from '../store/uiStore'
import { Btn, Field, PageBody, PageSection, Tag } from '../components/PageKit'
import CampusGridCodeSection, { GRID_CODES_KEY } from './CampusGridCodeSection'
import CampusInvestmentSection, { TableScroll, formatEur, iterationsText } from './CampusInvestmentSection'
import CampusLibrarySection from './CampusLibrarySection'
import { useCampusMilpJob } from './useCampusMilpJob'

export const CAMPUS_KEY = (name: string) => ['campusElectrical', 'state', name] as const
/** How often the state is re-read while a run goes on without an answer. */
export const CAMPUS_RUN_POLL_MS = 1500

/** A run whose request got no answer: what was asked, and why. */
interface LostRun { settings: CampusSettings; why: string }

function sameSettings(a: CampusSettings | null, b: CampusSettings): boolean {
  if (a == null) return false
  return (Object.keys(b) as (keyof CampusSettings)[]).every(k => a[k] === b[k])
}

const INPUT = 'px-2.5 py-1.5 text-sm border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20'
const DEFAULT_SETTINGS: CampusSettings = {
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
}
const STATUS_TEXT: Record<CheckStatus, string> = {
  pass: 'pass', fail: 'fail', not_rated: 'not rated', not_rechecked: 'not re-checked',
}
const STATUS_TONE: Record<CheckStatus, 'ok' | 'err' | 'neutral' | 'warn'> = {
  pass: 'ok', fail: 'err', not_rated: 'neutral', not_rechecked: 'warn',
}

function num(v: number | null | undefined, digits = 2): string {
  return v == null || !isFinite(v) ? '—' : v.toFixed(digits).replace(/\.?0+$/, m => (m.startsWith('.') ? '' : m))
}

function where(period: number | null | undefined, hour: number | null | undefined, kase?: string | null): string {
  if (period == null) return ''
  return [`${period}`, hour == null ? null : `h${hour}`, kase || null].filter(Boolean).join(' · ')
}

export default function CampusElectricalPanel() {
  const currentProject = useUIStore(s => s.currentProject)
  if (!currentProject) {
    return (
      <PageBody>
        <PageSection title="Campus electrical">
          <p className="text-[12px] text-muted">
            Open a solved hub project to take it to its electrical design.
          </p>
        </PageSection>
      </PageBody>
    )
  }
  return <CampusView name={currentProject} />
}

function CampusView({ name }: { name: string }) {
  const qc = useQueryClient()
  const [lost, setLost] = useState<LostRun | null>(null)
  const [outcome, setOutcome] = useState<string | null>(null)
  const state = useQuery({
    queryKey: CAMPUS_KEY(name), queryFn: () => campusApi.state(name), retry: false,
    refetchInterval: q => (lost != null || q.state.data?.run_in_progress ? CAMPUS_RUN_POLL_MS : false),
  })
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
        <PageSection title="Campus electrical">
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
        <PageSection title="Campus electrical">
          <p className="text-[12px] text-muted">{state.isError ? errorText(state.error) : 'Loading…'}</p>
        </PageSection>
      </PageBody>
    )
  }
  const refresh = (data?: CampusState) =>
    data ? qc.setQueryData(CAMPUS_KEY(name), data) : qc.invalidateQueries({ queryKey: CAMPUS_KEY(name) })
  const onRunStart = () => { setOutcome(null); setLost(null) }
  const onRunLost = (settings: CampusSettings, why: string) => {
    setLost({ settings, why })
    void qc.invalidateQueries({ queryKey: CAMPUS_KEY(name) })
  }
  const pending = lost != null || state.data.run_in_progress === true

  return (
    <PageBody>
      <div data-testid="campus-intro">
        <PageSection title="Campus electrical" hint="From the solved hub to its electrical design">
          <p className="text-[12px] text-muted">
            A steady-state AC load-flow study at the hub's critical hours. It sizes the transformers and the
            reactive compensation, checks short circuit against the switchgear, and checks the PCC against
            the chosen grid code. It is not a dynamic study, and not a compliance certificate.
          </p>
        </PageSection>
      </div>
      <CampusFileSection name={name} state={state.data} onChange={refresh} />
      <CampusGridCodeSection
        name={name}
        onProfilesChanged={() => qc.invalidateQueries({ queryKey: CAMPUS_KEY(name) })}
      />
      <CampusLibrarySection name={name} onChanged={() => qc.invalidateQueries({ queryKey: CAMPUS_KEY(name) })} />
      {state.data.campus_yaml && (
        <RunSection name={name} state={state.data} onChange={refresh} lost={lost} outcome={outcome} pending={pending}
                    onRunStart={onRunStart} onRunLost={onRunLost} />
      )}
      {state.data.results && <ResultsSection state={state.data} pending={pending} />}
    </PageBody>
  )
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
      {refusal && <p className="text-[12px] text-danger mb-2">{refusal}</p>}
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
  const initial = state.settings ?? DEFAULT_SETTINGS
  const [k, setK] = useState(String(initial.k))
  const [pf, setPf] = useState(initial.pf == null ? '' : String(initial.pf))
  const [profile, setProfile] = useState(initial.profile)
  const [margin, setMargin] = useState(String(Math.round(initial.margin * 100)))
  const [n1, setN1] = useState(initial.n_minus_1)
  // A run saved before these settings existed has neither: the defaults.
  const [invest, setInvest] = useState(initial.invest ?? DEFAULT_SETTINGS.invest)
  const [byOperator, setByOperator] = useState(initial.pcc_switchgear_by_operator ?? DEFAULT_SETTINGS.pcc_switchgear_by_operator)
  const [tapsByOperator, setTapsByOperator] = useState(
    (initial.pcc_tap_control ?? DEFAULT_SETTINGS.pcc_tap_control) === 'grid_operator')
  const [refusal, setRefusal] = useState<string | null>(null)
  // The grid-codes listing says which published profiles still carry
  // unconfirmed limits; the picker names them so a run is never held to one unawares.
  const codes = useQuery({ queryKey: GRID_CODES_KEY(name), queryFn: () => campusApi.gridCodes(name), retry: false })
  const unconfirmed = new Set((codes.data?.published ?? []).filter(p => p.unconfirmed.length > 0).map(p => p.id))

  const settings = (): CampusSettings => ({
    k: Number(k), pf: pf.trim() === '' ? null : Number(pf), profile, margin: Number(margin) / 100, n_minus_1: n1,
    invest, pcc_switchgear_by_operator: byOperator, pcc_tap_control: tapsByOperator ? 'grid_operator' : 'campus',
  })
  const run = useMutation({
    mutationFn: (s: CampusSettings) => campusApi.run(name, s),
    onMutate: () => { setRefusal(null); onRunStart() },
    onSuccess: data => onChange(data),
    onError: (e, s) => (isNoAnswer(e) ? onRunLost(s, errorText(e)) : setRefusal(errorText(e))),
  })
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
          <input aria-label="Hours per criterion" className={`${INPUT} w-[70px]`} inputMode="numeric" value={k} onChange={e => setK(e.target.value)} />
        </Field>
        <Field label="Connection-agreement power factor" hint="empty: the grid code's widest range">
          <input aria-label="Connection-agreement power factor" className={`${INPUT} w-[90px]`} inputMode="decimal" value={pf} placeholder="code"
                 onChange={e => setPf(e.target.value)} />
        </Field>
        <Field label="Grid code">
          <select aria-label="Grid code" className={`${INPUT} w-[320px]`} value={profile}
                  onChange={e => setProfile(e.target.value)}>
            {Object.entries(state.profiles).map(([key, title]) => (
              <option key={key} value={key}>{unconfirmed.has(key) ? `${title} (unconfirmed limits)` : title}</option>
            ))}
          </select>
        </Field>
        <Field label="Design margin (%)">
          <input aria-label="Design margin (%)" className={`${INPUT} w-[70px]`} inputMode="numeric" value={margin} onChange={e => setMargin(e.target.value)} />
        </Field>
        <Field label="Transformer N-1" row>
          <input type="checkbox" aria-label="Transformer N-1" checked={n1} onChange={e => setN1(e.target.checked)} />
        </Field>
        <Field label="Buy assets from the library (least cost, AC-checked)" row>
          <input type="checkbox" aria-label="Buy assets from the library (least cost, AC-checked)" checked={invest}
                 onChange={e => setInvest(e.target.checked)} />
        </Field>
        <Field label="PCC switchgear owned by the grid operator (not costed)" row>
          <input type="checkbox" aria-label="PCC switchgear owned by the grid operator (not costed)" checked={byOperator}
                 disabled={!invest} onChange={e => setByOperator(e.target.checked)} />
        </Field>
        <Field label="PCC transformer taps held by the grid operator (at the campus file's position)" row>
          <input type="checkbox" aria-label="PCC transformer taps held by the grid operator (at the campus file's position)"
                 checked={tapsByOperator} disabled={!invest} onChange={e => setTapsByOperator(e.target.checked)} />
        </Field>
        <Btn variant="primary" onClick={() => run.mutate(settings())} disabled={busy}>
          <Play size={13} /> {run.isPending ? 'Running…' : 'Run study'}
        </Btn>
        <Btn
          onClick={startMilp}
          disabled={!hasLeastCost || busy}
          title={hasLeastCost ? undefined : 'Run the study with assets bought from the library first'}
        >
          <Workflow size={13} /> {milp.isStarting ? 'Starting…' : 'Joint optimisation (MILP, slow — minutes)'}
        </Btn>
      </div>
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
      {refusal && <p className="text-[12px] text-danger mt-2">{refusal}</p>}
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

function StatusTag({ s }: { s: CheckStatus }) {
  return <Tag tone={STATUS_TONE[s]}>{STATUS_TEXT[s]}</Tag>
}

function ResultsSection({ state, pending }: { state: CampusState; pending: boolean }) {
  const r = state.results!
  const req = r.requirement
  // The investment's table re-solves every check with the assets in place; without it
  // the "with measures" column is part one's recommendation, and some checks are not re-checked.
  const invested = r.compliance_invested != null
  const rows: ComplianceRow[] = r.compliance_invested ?? r.compliance
  return (
    <>
      {pending && (
        <p data-testid="campus-run-pending" role="status"
           className="text-[12px] text-warn border border-warn/40 rounded px-3 py-2">
          A study run is going on in the backend: the results below are from the previous run, not the one being
          run. They are replaced when it ends.
        </p>
      )}
      {state.stale && (
        <p className="text-[12px] text-warn border border-warn/40 rounded px-3 py-2">
          The campus file or the project has changed since this run. Run the study again before using these numbers.
        </p>
      )}
      <CampusInvestmentSection state={state} />

      <PageSection
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
                    <td className="pr-3 align-top"><StatusTag s={c.status_as_is} /></td>
                    <td className="pr-3 align-top"><StatusTag s={c.status_with_measures} /></td>
                    <td data-testid={`compliance-worst-${c.check}`} className="pr-3 whitespace-nowrap align-top tabular-nums">
                      {num(value, 3)} {c.unit} <span className="block text-muted">{at}</span>
                    </td>
                    <td data-testid={`compliance-limit-${c.check}`} className="pr-3 whitespace-nowrap align-top tabular-nums text-muted">
                    {c.limit_min != null && c.limit_max != null
                      ? `${num(c.limit_min, 3)}–${num(c.limit_max, 3)} ${c.unit}`
                      : `${num(c.limit, 3)} ${c.unit}`}
                  </td>
                    <td className="align-top min-w-[16rem]">
                      <div>{detail}</div>
                      <div className="text-[11px] text-muted flex items-start gap-1.5 mt-0.5">
                        <Tag tone={c.source === 'code' ? 'accent' : 'warn'}>{c.source}</Tag><span>{c.clause}</span>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </TableScroll>
        <p className="text-[11px] text-muted mt-2">
          PCC reactive band ±{num(req.q_limit_mvar)} Mvar ({req.clause}); P_ref {num(req.p_ref_mw)} MW, {req.p_ref_from}.
        </p>
      </PageSection>

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
                  <td><Tag tone={t.adequate ? 'ok' : 'err'}>{t.adequate ? 'adequate' : 'undersized'}</Tag></td>
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
                  <td className="pr-3 tabular-nums">required {num(c.required_mvar)} Mvar</td>
                  <td className="pr-3 tabular-nums font-semibold">recommended {num(c.recommended_mvar)} Mvar</td>
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
    </>
  )
}

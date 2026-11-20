// The preference comparison (AS-10): one sizing loop per design intent, one table row each, run as a
// background job the section polls. Thin: every number is the backend's. A loop that is not converged,
// unresolved, failed or stopped is never shown as a pass, and a converged row says how many checks did not run.
import { useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { errorText } from '../api/campusElectrical'
import { networkApi } from '../api/network'
import { nk } from '../utils/queryKeys'
import { intentDefaults } from './intentDefaults'
import { SOURCE_KEY, sourceWord, usedWord } from './intentSources'
import { SOURCE_CHIP } from './DcArchitectureSection'
import { money } from '../utils/money'
import { projectsApi } from '../api/projects'
import type { ProjectInfo } from '../api/types'
import {
  DESIGN_INTENTS, intentComparisonApi, type CheckState, type DesignIntent, type IntentJob, type IntentRow, type Outcome,
  type IntentInputDefaults, type PqField,
} from '../api/intentComparison'
import { Btn, Field, PageSection, Tag } from '../components/PageKit'
import { withReturnFrom, openIslandLimits } from '../layout/flowNavigation'
import { useUIStore } from '../store/uiStore'

type Tone = 'ok' | 'warn' | 'err' | 'neutral'
export const OUTCOME_WORDS: Record<Outcome, { text: string; tone: Tone }> = {
  pass: { text: 'Converged', tone: 'ok' },
  fail: { text: 'Converged · checks fail', tone: 'err' },
  not_converged: { text: 'Not converged', tone: 'warn' },
  unresolved: { text: 'Unresolved', tone: 'err' },
  failed: { text: 'Failed', tone: 'err' },
  aborted: { text: 'Stopped', tone: 'warn' },
}
export const CHECK_WORDS: Record<CheckState, { text: string; tone: Tone }> = {
  ok: { text: 'Pass', tone: 'ok' }, fail: { text: 'Fail', tone: 'err' },
  unresolved: { text: 'Unresolved', tone: 'err' }, not_run: { text: 'Not run', tone: 'neutral' },
}
// Keys of `sizing_loop_runner._sizes` and the check names of `forward_checks`; the backend owns them.
const ASSET_LABELS: Record<string, string> = {
  q_ss: 'STATCOM steady-state', q_dyn: 'STATCOM dynamic', p_sm: 'Smoothing power', e_sm: 'Smoothing energy',
}
/** Carrier names that are acronyms, shown upper case in an asset label. */
const ACRONYMS = /\b(bess|ups|pv|dc|ac)\b/gi
const ASSET_PREFIX: Record<string, string> = { store: 'Store', charger: 'Charger', s_x: 'Converter rating' }
export const CHECK_LABELS: Record<string, string> = {
  transformer: 'Transformer', pq: 'PQ capability', escr: 'ESCR', smoothing: 'Smoothing', pst: 'Flicker (Pst)',
  harmonics: 'Harmonics', campus_flow: 'Campus load flow', campus_reactive: 'Campus reactive', grid_readiness: 'Grid readiness',
  gfm_tuning: 'Grid-forming tuning',
}
const humanise = (s: string) => {
  const t = s.replace(/[_:]+/g, ' ').trim()
  return t ? t.charAt(0).toUpperCase() + t.slice(1) : s
}
/** A plain label for an asset key or check name; an unmapped one is made readable, never shown raw. */
export const assetLabel = (key: string) => {
  if (ASSET_LABELS[key]) return ASSET_LABELS[key]
  const [kind, rest] = key.split(':', 2)
  return rest !== undefined && ASSET_PREFIX[kind] ? `${ASSET_PREFIX[kind]} ${rest.replace(ACRONYMS, m => m.toUpperCase())}` : humanise(key)
}
export const checkLabel = (name: string) => CHECK_LABELS[name] ?? humanise(name)
const byIntent = <T,>(items: T[], id: (t: T) => string) => [...items].sort((a, b) => id(a).localeCompare(id(b)))
const intentLabel = (id: string) => DESIGN_INTENTS.find(i => i.id === id)?.label ?? ''
const NEED_TWO = 'Pick at least two intents.'
/** The backend's plain-words island refusal (routers/compare.py), whose setting lives in Solver settings. */
const ISLAND_REFUSAL = /^Island limits in the optimisation/
const NO_CODE_LINK = 'Set grid code ›'
const INPUT = 'px-2.5 py-1.5 text-sm border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20'
const POLL_MS = 2000
/** The point-of-connection values, in the form's words, with the decimals each field shows. */
const PQ_FIELDS: [PqField, string, number][] = [
  ['x_pu', 'Transformer reactance (p.u.)', 3], ['pf', 'Load power factor', 3], ['pf_req', 'Required power factor', 3],
  ['s_n_mva', 'Transformer rating (MVA)', 2], ['p_cap_mw', 'Import cap (MW)', 2],
  ['statcom_capital_cost', 'STATCOM cost (€/Mvar/yr)', 0],
]
/** A prefill as the field shows it: rounded to the field's precision, never a long float. */
export const shown = (v: number | null | undefined, d: number) => (v == null ? '' : String(Number(v.toFixed(d))))
/** The field on this form a refusal opens with (review-ux.md: a refusal opens with the field's label, and the fix
 *  action sits on its own line): the label to name in the link and the field's test id. Null when the refusal
 *  names no field here. */
export function refusalField(text: string): { label: string; testId: string } | null {
  const FORM: [string, string, string][] = [
    ['Campus link:', 'Campus link', 'intent-in-link'],
    ['Grid ramp limit:', 'Grid ramp limit', 'intent-in-ramp'],
    ['Load swing, oscillation amplitude and period:', 'Load swing', 'intent-in-swing'],
  ]
  const hit = FORM.find(([prefix]) => text.startsWith(prefix))
  if (hit) return { label: hit[1], testId: hit[2] }
  const poc = 'Point of connection: '
  if (!text.startsWith(poc)) return null
  const rest = text.slice(poc.length).toLowerCase()
  const field = PQ_FIELDS.find(([, label]) => rest.startsWith(label.toLowerCase()))
  return field ? { label: field[1], testId: `intent-pq-${field[0]}` } : { label: 'Point of connection', testId: 'intent-pq-x_pu' }
}
type Typed = 'critical' | 'link' | 'sk' | 'pst' | 'ramp' | 'swing' | 'amp' | 'period'

const AUTONOMY_DEFAULT_H = 4
const AUTONOMY_MAX_H = 720 // the backend's ceiling (0 < h <= 720)
const autonomyOk = (s: string) => { const v = Number(s); return s.trim() !== '' && v > 0 && v <= AUTONOMY_MAX_H }
const num = (s: string) => (s.trim() === '' ? NaN : Number(s))
const fmt = (v: number | null | undefined, d = 1) => (v == null ? '—' : v.toLocaleString(undefined, { maximumFractionDigits: d }))
/** The loop's last round is not a final answer unless it converged: its sizes are shown muted, marked as such. */
const isFinal = (r: IntentRow) => r.status === 'converged'
const notRun = (r: IntentRow) => r.checks.filter(c => c.state === 'not_run').length

export default function IntentComparisonSection({ name, gridCode: savedCode }: { name: string; gridCode: string | null }) {
  const qc = useQueryClient()
  const setSlidePanel = useUIStore(s => s.setSlidePanel)
  const requestGridCodeScroll = useUIStore(s => s.requestGridCodeScroll)
  const [chosen, setChosen] = useState<DesignIntent[]>([])
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => projectsApi.list(), staleTime: 30_000 })
  const storedAutonomy = (projects.data as ProjectInfo[] | undefined)?.find(p => p.name === name)?.settings?.backup_autonomy_h
  // The stored value is only the default a run starts from; an edit is this run's own (AS10-AUTONOMY-2).
  const [edited, setAutonomy] = useState<string | null>(null)
  const autonomy = edited ?? String(storedAutonomy ?? AUTONOMY_DEFAULT_H)
  // Fields with a network or grid-code source hold only what the user typed (undefined = the prefill shows).
  const [typed, setF] = useState<Partial<Record<Typed, string>>>({})
  const [pqTyped, setPq] = useState<Partial<Record<PqField, string>>>({})
  const [g, setG] = useState({ library: '' })
  const q = (root: string, queryFn: () => Promise<unknown>) => ({ queryKey: nk(name, root), queryFn, staleTime: 30_000, retry: false })
  // What a run takes for each input the request leaves out (one ladder with the run: intent_inputs.resolve).
  const held = useQuery({ queryKey: ['intentInputDefaults', name], queryFn: () => intentComparisonApi.defaults(name),
    staleTime: 30_000, retry: false })
  // The grid code the campus study applies: the saved one, else the backend's default (never a second copy here).
  const gridCode = savedCode ?? held.data?.grid_code.value ?? null
  const codeKnown = savedCode != null || held.isSuccess || held.isError
  const env = held.data?.envelope.value?.max
  const buses = useQuery(q('buses', networkApi.getBuses))
  const loads = useQuery(q('loads', networkApi.getLoads))
  const links = useQuery(q('links', networkApi.getLinks))
  const defaults = intentDefaults({
    buses: (buses.data ?? []) as never, loads: (loads.data ?? []) as never, links: (links.data ?? []) as never,
    // The applied code's Pst limit from the backend, which reads shipped and the project's own codes alike.
    pstLimit: held.data?.pst_limit.value ?? null, gridCode: codeKnown ? gridCode : '',
    status: { buses: buses.status === 'pending' ? 'loading' : buses.status === 'error' ? 'error' : 'ok',
      loads: loads.status === 'pending' ? 'loading' : loads.status === 'error' ? 'error' : 'ok',
      links: links.status === 'pending' ? 'loading' : links.status === 'error' ? 'error' : 'ok',
      grid: held.status === 'pending' ? 'loading' : held.status === 'error' ? 'error' : 'ok' },
  })
  const f = {
    critical: typed.critical ?? defaults.critical.value, link: typed.link ?? defaults.link.value,
    sk: typed.sk ?? defaults.sk.value, pst: typed.pst ?? defaults.pst.value,
    ramp: typed.ramp ?? shown(held.data?.ramp_mw_per_min.value, 2),
    swing: typed.swing ?? shown(env?.swing_mw, 2), amp: typed.amp ?? shown(env?.oscillation_amplitude_mw, 2),
    period: typed.period ?? shown(env?.oscillation_period_s, 2), ...g,
  }
  // GK B3: on another link than the grid import, the PoC values a run takes are that link's own; show those.
  const otherLink = held.isSuccess && f.link !== '' && f.link !== (held.data?.link.value ?? '')
  const heldForLink = useQuery({ queryKey: ['intentInputDefaults', name, f.link],
    queryFn: () => intentComparisonApi.defaults(name, f.link), enabled: otherLink, staleTime: 30_000, retry: false })
  const heldPq: IntentInputDefaults['pq'] = (otherLink ? heldForLink.data?.pq : held.data?.pq) ?? {}
  const pqValue = (k: PqField, d: number) => pqTyped[k] ?? shown(heldPq[k]?.value, d)
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const v = e.target.value
    if (k === 'library') setG(p => ({ ...p, [k]: v }))
    else setF(p => ({ ...p, [k]: v }))
  }
  /** Why an empty field has no prefill (GK B4: every field the backend gives a reason for). */
  const why = (k: Typed | 'library'): string | null | undefined => {
    if (k === 'critical' || k === 'link' || k === 'sk' || k === 'pst') return defaults[k].reason
    if (k === 'ramp') return held.data?.ramp_mw_per_min.reason
    if (k === 'swing' || k === 'amp' || k === 'period') return held.data?.envelope.reason
    return null
  }
  /** The word after a field for where its value comes from. */
  const fieldSource = (k: Typed | 'library'): string | null => {
    if (k === 'library') return null
    const isTyped = typed[k] !== undefined
    if (f[k] === '' && !isTyped) return null
    if (k === 'critical' || k === 'sk' || k === 'link') return sourceWord('project', isTyped)
    if (k === 'pst') return sourceWord(held.data?.pst_limit.kind ?? 'grid_code', isTyped)
    if (k === 'ramp') return sourceWord(held.data?.ramp_mw_per_min.kind, isTyped)
    return sourceWord(held.data?.envelope.kind, isTyped)
  }
  const key = ['intentComparison', name]
  const job = useQuery({
    queryKey: key, queryFn: () => intentComparisonApi.get(name), retry: false,
    refetchInterval: q => (q.state.data?.status === 'running' ? POLL_MS : false),
  })
  const running = job.data?.status === 'running'
  const start = useMutation({
    mutationFn: () => {
      const swing = num(f.swing), amp = num(f.amp), period = num(f.period)
      // A value the project holds and the user left alone is left out: the backend takes it from the project
      // (the same ladder the prefill shows) and records its source. `pq` is always sent, holding only the typed
      // values; the backend fills the others from the same ladder, so the run takes what the form shows.
      const envTyped = typed.swing !== undefined || typed.amp !== undefined || typed.period !== undefined
      const fromProject = (k: Typed, v?: unknown) => typed[k] === undefined && v != null
      return intentComparisonApi.start(name, {
        intents: chosen.map(intent => (intent === 'A' ? { intent, backup_autonomy_h: num(autonomy) } : { intent })),
        critical_mw: num(f.critical), grid_code: gridCode,
        inputs: {
          ...(fromProject('link', held.data?.link.value) && f.link === held.data?.link.value ? {} : { link: f.link.trim() }),
          ...(fromProject('ramp', held.data?.ramp_mw_per_min.value) ? {} : { ramp_mw_per_min: num(f.ramp) }),
          sk_mva: num(f.sk), pst_limit: num(f.pst),
          transformer_library_mva: f.library.split(',').map(s => Number(s.trim())).filter(Number.isFinite),
          ...(!envTyped && held.data?.envelope.value != null ? {} : {
            envelope: { phases: [{ swing_mw: swing, oscillation: { amplitude_mw: amp, period_s: period } }],
              max: { swing_mw: swing, oscillation_amplitude_mw: amp, oscillation_period_s: period } } }),
          pq: Object.fromEntries(Object.entries(pqTyped).map(([k, v]) => [k, num(v as string)])),
        },
      })
    },
    onSuccess: data => qc.setQueryData(key, data),
    // 409: a comparison already runs for this project; show that job, never start a second one.
    onError: e => { if ((e as { response?: { status?: number } })?.response?.status === 409) void qc.invalidateQueries({ queryKey: key }) },
  })
  const abort = useMutation({ mutationFn: () => intentComparisonApi.abort(name), onSuccess: () => qc.invalidateQueries({ queryKey: key }) })
  const toggle = (id: DesignIntent) => setChosen(c => (c.includes(id) ? c.filter(x => x !== id) : [...c, id]))
  const autonomyValid = autonomyOk(autonomy)
  const autonomyBad = chosen.includes('A') && !autonomyValid
  const saveAutonomy = useMutation({
    mutationFn: () => projectsApi.updateSettings(name, { backup_autonomy_h: Number(autonomy) }),
    onSuccess: updated => {
      qc.setQueryData<ProjectInfo[]>(['projects'], prev =>
        prev?.map(p => (p.name === updated.name ? { ...p, settings: updated.settings } : p)) ?? prev)
      void qc.invalidateQueries({ queryKey: ['projects'] })
    },
  })
  const complete = [f.critical, f.ramp, f.sk, f.pst, f.swing, f.amp, f.period].every(v => Number(v) > 0) &&
    f.link.trim() !== '' && f.library.trim() !== '' &&
    PQ_FIELDS.every(([k, , d]) => Number(pqValue(k, d)) > 0 || (k === 'x_pu' && Number(pqValue(k, d)) === 0))
  const blocked = chosen.length < 2 ? NEED_TWO : autonomyBad ? null : !complete ? 'Fill in every input below, all greater than zero.' : null
  const rows = job.data?.rows ?? []

  return (
    <PageSection title="Intent comparison" hint="one sizing loop per design intent" bodyClassName="p-4 space-y-3"
      right={running
        ? <Btn disabled={abort.isPending} onClick={() => abort.mutate()} data-testid="intent-abort"
            title="Stops the running loops. A stopped run cannot be resumed.">Abort</Btn>
        : <Btn variant="primary" disabled={blocked != null || autonomyBad || start.isPending} onClick={() => start.mutate()}
            data-testid="intent-run">Compare intents</Btn>}>
      <div className="space-y-1" role="group" aria-label="Design intents">
        {DESIGN_INTENTS.map(i => (
          <div key={i.id}>
            <label className="flex items-center gap-1.5 text-[12px]">
              <input type="checkbox" checked={chosen.includes(i.id)} disabled={running} onChange={() => toggle(i.id)} />
              {i.id}. {i.label}
            </label>
            {i.id === 'D' && chosen.includes('D') && codeKnown && gridCode == null && (
              <p className="ml-5 text-[11px] text-warn" data-testid="intent-d-nocode">
                No grid code set: whether transfer to BESS counts as ride-through is unknown.{' '}
                <button type="button" className="underline" data-testid="intent-set-grid-code"
                  onClick={() => withReturnFrom(() => { setSlidePanel('campusElectrical'); requestGridCodeScroll() })}>{NO_CODE_LINK}</button>
              </p>
            )}
          </div>
        ))}
      </div>
      {blocked && <p className="text-[11px] text-muted" data-testid="intent-blocked">{blocked}</p>}
      {chosen.includes('A') && (
        <Field label="Backup autonomy (h)" hint={`Intent A: backup energy = critical load × hours. Each run uses the value shown and records it. ${storedAutonomy != null ? `The project default is ${fmt(storedAutonomy, 4)} h.` : `No project default is saved, so ${AUTONOMY_DEFAULT_H} h applies.`}`}>
          <input className={INPUT} inputMode="decimal" value={autonomy} onChange={e => setAutonomy(e.target.value)}
            disabled={running} data-testid="intent-autonomy" />
          {autonomyBad && <p className="text-[11px] text-err" data-testid="intent-autonomy-error">Enter a number of hours above zero, up to 720.</p>}
          <div className="mt-1 flex items-center gap-2">
            <Btn disabled={!autonomyValid || saveAutonomy.isPending || Number(autonomy) === storedAutonomy}
              onClick={() => saveAutonomy.mutate()} data-testid="intent-autonomy-save"
              title="Stores this value in the project as the default for later runs. Results already shown keep the value they used.">
              Save as project default</Btn>
          </div>
          {saveAutonomy.isError && <p className="text-[11px] text-err" role="alert" data-testid="intent-autonomy-save-error">{errorText(saveAutonomy.error)}</p>}
        </Field>
      )}
      <p className="text-[11px] text-muted" data-testid="intent-source-key">
        Where each value comes from:{' '}
        {SOURCE_KEY.map(([word, meaning], i) => (
          <span key={word}>{i > 0 && '; '}<span className={SOURCE_CHIP}>{word}</span> {meaning}</span>
        ))}.
      </p>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
        {([['critical', 'Critical load (MW)'], ['link', 'Campus link'], ['ramp', 'Grid ramp limit (MW/min)'],
          ['sk', 'Short-circuit power (MVA)'], ['pst', 'Pst limit (unitless)'], ['library', 'Transformer sizes (MVA, comma list)'],
          ['swing', 'Load swing (MW)'], ['amp', 'Oscillation amplitude (MW)'], ['period', 'Oscillation period (s)']] as const)
          .map(([k, label]) => (
            <Field key={k} label={label}>
              {k === 'link' ? (
                <select className={INPUT} value={f.link} onChange={set('link')} disabled={running} data-testid="intent-in-link">
                  <option value="">Choose a link</option>
                  {defaults.links.map(n => <option key={n} value={n}>{n === defaults.link.value ? 'Grid import (point of connection)' : n}</option>)}
                </select>
              ) : (
                <input className={INPUT} inputMode="decimal" value={f[k]} onChange={set(k)} disabled={running} data-testid={`intent-in-${k}`} />
              )}
              {fieldSource(k) && <span className={`${SOURCE_CHIP} ml-1`} data-testid={`intent-src-${k}`}>{fieldSource(k)}</span>}
              {f[k] === '' && why(k) && (
                <p className="text-[11px] text-muted" data-testid={`intent-why-${k}`}>{why(k)}</p>
              )}
            </Field>
          ))}
      </div>
      <div className="space-y-1.5" role="group" aria-label="Point of connection" data-testid="intent-poc">
        <p className="text-[12px] font-medium">Point of connection</p>
        <p className="text-[11px] text-muted" data-testid="intent-poc-scope">
          These values apply to this comparison only; the saved network is not changed. The PQ and flicker checks
          read them at {f.link === defaults.link.value ? 'the point of connection' : (f.link || 'the campus link')}.
          The ramp limit, load swing and oscillation are the project's own: change them for the project in the
          connection limits under Grid connection, and in the DC load.
        </p>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
          {PQ_FIELDS.map(([k, label, d]) => (
            <Field key={k} label={label}>
              <input className={INPUT} inputMode="decimal" value={pqValue(k, d)} disabled={running}
                onChange={e => { const v = e.target.value; setPq(p => ({ ...p, [k]: v })) }} data-testid={`intent-pq-${k}`} />
              {(pqTyped[k] !== undefined || heldPq[k]?.value != null) && (
                <span className={`${SOURCE_CHIP} ml-1`} data-testid={`intent-pq-src-${k}`}
                  title={pqTyped[k] === undefined ? heldPq[k]?.source ?? undefined : undefined}>
                  {sourceWord(heldPq[k]?.kind, pqTyped[k] !== undefined)}</span>
              )}
              {pqValue(k, d) === '' && heldPq[k]?.reason && (
                <p className="text-[11px] text-muted" data-testid={`intent-why-pq-${k}`}>{heldPq[k]?.reason}</p>
              )}
              {k === 'statcom_capital_cost' && Number(pqValue(k, d)) > 0 && (
                <p className="text-[11px] text-muted" data-testid="intent-pq-cost-money">
                  {money(Number(pqValue(k, d)), 'EUR')} per Mvar per year</p>
              )}
            </Field>
          ))}
        </div>
        {held.isError && <p className="text-[11px] text-err" data-testid="intent-held-error">The project's values could not be read: {errorText(held.error)}</p>}
      </div>
      {(start.isError || job.isError) && !(start.isError && start.error && (start.error as { response?: { status?: number } }).response?.status === 409) && (
        <RefusalLine text={errorText(start.error ?? job.error)} />
      )}
      {job.data?.status === 'failed' && job.data.error && (
        <p className="text-[12px] text-err" role="alert" data-testid="intent-job-failed">Failed: {job.data.error}</p>
      )}
      {job.data && <Progress progress={job.data.progress} />}
      {job.data?.inputs && <InputsUsed inputs={job.data.inputs} />}
      {rows.length > 0 && <ResultTable rows={rows} />}
    </PageSection>
  )
}

function Progress({ progress }: { progress: Record<string, string> }) {
  const words: Record<string, string> = { queued: 'queued', running: 'running', done: 'done' }
  return (
    <ul className="flex flex-wrap gap-x-4 text-[11px] text-muted" data-testid="intent-progress">
      {byIntent(Object.entries(progress), e => e[0]).map(([intent, st]) => (
        <li key={intent} data-testid={`intent-progress-${intent}`}>{intent}: {words[st] ?? st}</li>
      ))}
    </ul>
  )
}

function ResultTable({ rows }: { rows: IntentRow[] }) {
  return (
    <div className="space-y-2" data-testid="intent-table">
      {byIntent(rows, r => r.intent).filter(r => r.warnings.length > 0).map(r => (
        <p key={r.intent} className="text-[11px] text-warn" role="status" data-testid={`intent-warning-${r.intent}`}>
          {r.intent}. {r.warnings.join(' ')}
        </p>
      ))}
      <div className="overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead><tr className="text-left text-muted">
            <th className="pr-2">Intent</th><th className="pr-2">Loop status</th>
            <th className="pr-2" title="LP total: capex plus yearly opex, not capex alone">Total cost (€ per year)</th>
            <th className="pr-2">Assets (MW, MWh, Mvar)</th><th className="pr-2">Transformer (MVA)</th>
            <th className="pr-2">STATCOM (Mvar)</th><th>Checks</th>
          </tr></thead>
          <tbody>
            {byIntent(rows, r => r.intent).map(r => {
              const w = OUTCOME_WORDS[r.outcome]
              const skipped = notRun(r)
              const dim = isFinal(r) ? '' : 'text-muted'
              return (
                <tr key={r.intent} className="align-top border-t border-border" data-testid={`intent-row-${r.intent}`}>
                  <td className="py-1 pr-2"><span className="font-semibold">{r.intent}.</span>{' '}
                    <span className="text-muted">{intentLabel(r.intent)}</span></td>
                  <td className="py-1 pr-2">
                    <Tag tone={w.tone}>{w.text}</Tag>
                    {r.outcome === 'pass' && skipped > 0 && (
                      <span className="text-muted" data-testid={`intent-notrun-${r.intent}`}>
                        {' · '}{skipped} {skipped === 1 ? 'check' : 'checks'} not run
                      </span>)}
                    {r.warnings.length > 0 && <div className="text-warn">Grid-code warning</div>}
                    {!isFinal(r) && <div className="text-muted">not final</div>}
                    {r.verdict && !isFinal(r) && <div className="text-muted">{r.verdict}</div>}
                    {r.backup_autonomy_h != null && (
                      <div className="text-muted">{fmt(r.backup_autonomy_h)} h autonomy · {fmt(r.backup_energy_mwh)} MWh</div>)}
                  </td>
                  <td className={`py-1 pr-2 font-mono ${dim}`}>{fmt(r.cost_eur, 0)}</td>
                  <td className={`py-1 pr-2 font-mono ${dim}`}>{Object.entries(r.assets).map(([k, a]) => (
                    <div key={k}>{assetLabel(k)}: {fmt(a.value)} {a.unit}</div>))}</td>
                  <td className={`py-1 pr-2 font-mono ${dim}`}>{fmt(r.transformer_mva)}</td>
                  <td className={`py-1 pr-2 font-mono ${dim}`}>{fmt(r.statcom_mvar)}</td>
                  <td className="py-1">{r.checks.map(c => (
                    <div key={c.name}>{checkLabel(c.name)}: {!isFinal(r) && c.state === 'ok'
                      ? <Tag tone="neutral">OK in last round</Tag>
                      : <Tag tone={CHECK_WORDS[c.state].tone}>{CHECK_WORDS[c.state].text}</Tag>}</div>))}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {rows[0]?.basis_note && (
        <p className="text-[11px] text-muted" data-testid="intent-basis">
          Basis: {rows[0].basis_note}. Total cost is the LP total (capex plus yearly opex), not capex alone. Assumed values
          are marked as such in the note.
        </p>)}
    </div>
  )
}

const USED_LABELS: Record<string, string> = {
  link: 'Campus link', ramp_mw_per_min: 'Grid ramp limit', envelope: 'Load swing and oscillation', xr: 'X/R',
  x_pu: 'transformer reactance', pf: 'load power factor', pf_req: 'required power factor', s_n_mva: 'transformer rating',
  p_cap_mw: 'import cap', statcom_capital_cost: 'STATCOM cost',
}
/** What the run took for each project-held input, and from where (a run on assumed values says so). */
function InputsUsed({ inputs }: { inputs: NonNullable<IntentJob['inputs']> }) {
  const { pq, xr: _xr, ...rest } = inputs.sources
  const item = ([k, v]: [string, unknown]) => (
    <span key={k}>{USED_LABELS[k] ?? k} <span className={SOURCE_CHIP}>{usedWord(v as string)}</span></span>)
  const join = (xs: ReactNode[]) => xs.flatMap((x, i) => (i ? [', ', x] : [x]))
  const poc = Object.entries((pq ?? {}) as Record<string, string>)
  return (
    <p className="text-[11px] text-muted" data-testid="intent-inputs-used">
      Inputs used: {join(Object.entries(rest).map(item))}
      {poc.length > 0 && <>; point of connection: {join(poc.map(item))}</>}.
    </p>
  )
}

/** A refusal, with a link on its own line that focuses the field on this form it names (review-ux.md). */
function RefusalLine({ text }: { text: string }) {
  const field = refusalField(text)
  const focus = () => {
    const el = field && document.querySelector<HTMLElement>(`[data-testid="${field.testId}"]`)
    if (!el) return
    el.scrollIntoView({ block: 'center' })
    el.focus()
  }
  return (
    <p className="text-[12px] text-err" role="alert" data-testid="intent-error">
      {text}
      {field && <>{' '}<button type="button" className="underline" data-testid="intent-error-field" onClick={focus}>{field.label} ›</button></>}
      {!field && ISLAND_REFUSAL.test(text) && <>{' '}<button type="button" className="underline" data-testid="intent-error-island" onClick={openIslandLimits}>Island limits ›</button></>}
    </p>
  )
}

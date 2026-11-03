// The preference comparison (AS-10): one sizing loop per design intent, one table row each, run as a
// background job the section polls. Thin: every number is the backend's. A loop that is not converged,
// unresolved, failed or stopped is never shown as a pass, and a converged row says how many checks did not run.
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { campusApi, errorText } from '../api/campusElectrical'
import { networkApi } from '../api/network'
import { nk } from '../utils/queryKeys'
import { intentDefaults } from './intentDefaults'
import { projectsApi } from '../api/projects'
import type { ProjectInfo } from '../api/types'
import {
  DESIGN_INTENTS, intentComparisonApi, type CheckState, type DesignIntent, type IntentRow, type Outcome,
} from '../api/intentComparison'
import { Btn, Field, PageSection, Tag } from '../components/PageKit'
import { withReturnFrom } from '../layout/flowNavigation'
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
const NO_CODE_LINK = 'Set grid code ›'
const INPUT = 'px-2.5 py-1.5 text-sm border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20'
const POLL_MS = 2000

const AUTONOMY_DEFAULT_H = 4
const AUTONOMY_MAX_H = 720 // the backend's ceiling (0 < h <= 720)
const autonomyOk = (s: string) => { const v = Number(s); return s.trim() !== '' && v > 0 && v <= AUTONOMY_MAX_H }
const num = (s: string) => (s.trim() === '' ? NaN : Number(s))
const fmt = (v: number | null | undefined, d = 1) => (v == null ? '–' : v.toLocaleString(undefined, { maximumFractionDigits: d }))
/** The loop's last round is not a final answer unless it converged: its sizes are shown muted, marked as such. */
const isFinal = (r: IntentRow) => r.status === 'converged'
const notRun = (r: IntentRow) => r.checks.filter(c => c.state === 'not_run').length

export default function IntentComparisonSection({ name, gridCode }: { name: string; gridCode: string | null }) {
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
  const [typed, setF] = useState<Partial<Record<'critical' | 'link' | 'sk' | 'pst', string>>>({})
  const [g, setG] = useState({ ramp: '', library: '', swing: '', amp: '', period: '' })
  const q = (root: string, queryFn: () => Promise<unknown>) => ({ queryKey: nk(name, root), queryFn, staleTime: 30_000, retry: false })
  const buses = useQuery(q('buses', networkApi.getBuses))
  const loads = useQuery(q('loads', networkApi.getLoads))
  const links = useQuery(q('links', networkApi.getLinks))
  const profile = useQuery({
    queryKey: ['intentGridCode', name, gridCode], enabled: gridCode != null, retry: false, staleTime: 30_000,
    queryFn: () => campusApi.getPublishedGridCode(name, gridCode as string),
  })
  const defaults = intentDefaults({
    buses: (buses.data ?? []) as never, loads: (loads.data ?? []) as never, links: (links.data ?? []) as never,
    pstLimit: profile.data?.profile.pst_limit?.value ?? null, gridCode,
    status: { buses: buses.status === 'pending' ? 'loading' : buses.status === 'error' ? 'error' : 'ok',
      loads: loads.status === 'pending' ? 'loading' : loads.status === 'error' ? 'error' : 'ok',
      links: links.status === 'pending' ? 'loading' : links.status === 'error' ? 'error' : 'ok',
      grid: gridCode == null || profile.status === 'success' ? 'ok' : profile.status === 'error' ? 'error' : 'loading' },
  })
  const f = {
    critical: typed.critical ?? defaults.critical.value, link: typed.link ?? defaults.link.value,
    sk: typed.sk ?? defaults.sk.value, pst: typed.pst ?? defaults.pst.value, ...g,
  }
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const v = e.target.value
    if (k === 'critical' || k === 'link' || k === 'sk' || k === 'pst') setF(p => ({ ...p, [k]: v }))
    else setG(p => ({ ...p, [k]: v }))
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
      return intentComparisonApi.start(name, {
        intents: chosen.map(intent => (intent === 'A' ? { intent, backup_autonomy_h: num(autonomy) } : { intent })),
        critical_mw: num(f.critical), grid_code: gridCode,
        inputs: {
          link: f.link.trim(), ramp_mw_per_min: num(f.ramp), sk_mva: num(f.sk), pst_limit: num(f.pst),
          transformer_library_mva: f.library.split(',').map(s => Number(s.trim())).filter(Number.isFinite),
          envelope: { phases: [{ swing_mw: swing, oscillation: { amplitude_mw: amp, period_s: period } }],
            max: { swing_mw: swing, oscillation_amplitude_mw: amp, oscillation_period_s: period } },
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
    f.link.trim() !== '' && f.library.trim() !== ''
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
            {i.id === 'D' && chosen.includes('D') && gridCode == null && (
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
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
        {([['critical', 'Critical load (MW)'], ['link', 'Campus link'], ['ramp', 'Grid ramp limit (MW/min)'],
          ['sk', 'Short-circuit power (MVA)'], ['pst', 'Pst limit'], ['library', 'Transformer sizes (MVA, comma list)'],
          ['swing', 'Load swing (MW)'], ['amp', 'Oscillation amplitude (MW)'], ['period', 'Oscillation period (s)']] as const)
          .map(([k, label]) => (
            <Field key={k} label={label}>
              {k === 'link' ? (
                <select className={INPUT} value={f.link} onChange={set('link')} disabled={running} data-testid="intent-in-link">
                  <option value="">Choose a link</option>
                  {defaults.links.map(n => <option key={n} value={n}>{n}</option>)}
                </select>
              ) : (
                <input className={INPUT} value={f[k]} onChange={set(k)} disabled={running} data-testid={`intent-in-${k}`} />
              )}
              {(k === 'critical' || k === 'link' || k === 'sk' || k === 'pst') && f[k] === '' && defaults[k].reason && (
                <p className="text-[11px] text-muted" data-testid={`intent-why-${k}`}>{defaults[k].reason}</p>
              )}
            </Field>
          ))}
      </div>
      {(start.isError || job.isError) && !(start.isError && start.error && (start.error as { response?: { status?: number } }).response?.status === 409) && (
        <p className="text-[12px] text-err" role="alert" data-testid="intent-error">{errorText(start.error ?? job.error)}</p>
      )}
      {job.data?.status === 'failed' && job.data.error && (
        <p className="text-[12px] text-err" role="alert" data-testid="intent-job-failed">Failed: {job.data.error}</p>
      )}
      {job.data && <Progress progress={job.data.progress} />}
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

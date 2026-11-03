// The preference comparison (AS-10): one sizing loop per design intent, one table row each, run as a
// background job the section polls. Thin: every number is the backend's. A loop that is not converged,
// unresolved, failed or stopped is never shown as a pass, and a converged row says how many checks did not run.
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { errorText } from '../api/campusElectrical'
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
  return rest !== undefined && ASSET_PREFIX[kind] ? `${ASSET_PREFIX[kind]} ${rest}` : humanise(key)
}
export const checkLabel = (name: string) => CHECK_LABELS[name] ?? humanise(name)
const byIntent = <T,>(items: T[], id: (t: T) => string) => [...items].sort((a, b) => id(a).localeCompare(id(b)))
const intentLabel = (id: string) => DESIGN_INTENTS.find(i => i.id === id)?.label ?? ''
const NEED_TWO = 'Pick at least two intents.'
const NO_CODE_LINK = 'Set grid code ›'
const INPUT = 'px-2.5 py-1.5 text-sm border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20'
const POLL_MS = 2000

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
  const [autonomy, setAutonomy] = useState('4')
  const [f, setF] = useState({ critical: '', link: '', ramp: '', sk: '', pst: '1', library: '', swing: '', amp: '', period: '' })
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF(p => ({ ...p, [k]: e.target.value }))
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
  const autonomyBad = chosen.includes('A') && !(Number(autonomy) > 0)
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
        <Field label="Backup autonomy (h)" hint="Intent A: backup energy = critical load × hours. Used for this run only; default 4 h.">
          <input className={INPUT} inputMode="decimal" value={autonomy} onChange={e => setAutonomy(e.target.value)}
            disabled={running} data-testid="intent-autonomy" />
          {autonomyBad && <p className="text-[11px] text-err" data-testid="intent-autonomy-error">Enter a number of hours above zero.</p>}
        </Field>
      )}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
        {([['critical', 'Critical load (MW)'], ['link', 'Campus link'], ['ramp', 'Grid ramp limit (MW/min)'],
          ['sk', 'Short-circuit power (MVA)'], ['pst', 'Pst limit'], ['library', 'Transformer sizes (MVA, comma list)'],
          ['swing', 'Load swing (MW)'], ['amp', 'Oscillation amplitude (MW)'], ['period', 'Oscillation period (s)']] as const)
          .map(([k, label]) => (
            <Field key={k} label={label}>
              <input className={INPUT} value={f[k]} onChange={set(k)} disabled={running} data-testid={`intent-in-${k}`} />
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

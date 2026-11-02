// The preference comparison (AS-10): one sizing loop per design intent, one table row each.
// Thin: every number is the backend's. A loop that is not converged, unresolved, failed or stopped is
// never shown as a pass.
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { errorText } from '../api/campusElectrical'
import {
  DESIGN_INTENTS, intentComparisonApi, type CheckState, type DesignIntent, type IntentRow, type Outcome,
} from '../api/intentComparison'
import { Btn, Field, PageSection, Tag } from '../components/PageKit'

export const OUTCOME_WORDS: Record<Outcome, { text: string; tone: 'ok' | 'warn' | 'err' }> = {
  pass: { text: 'Pass', tone: 'ok' },
  fail: { text: 'Checks fail', tone: 'err' },
  not_converged: { text: 'Not converged', tone: 'warn' },
  unresolved: { text: 'Unresolved: needs grid reinforcement or EMT study', tone: 'err' },
  failed: { text: 'Run failed', tone: 'err' },
  aborted: { text: 'Stopped', tone: 'warn' },
}
const CHECK_WORDS: Record<CheckState, { text: string; tone: 'ok' | 'warn' | 'err' | 'neutral' }> = {
  ok: { text: 'ok', tone: 'ok' }, fail: { text: 'fails', tone: 'err' },
  unresolved: { text: 'unresolved', tone: 'err' }, not_run: { text: 'not run', tone: 'neutral' },
}
const NEED_TWO = 'Choose at least two intents to compare.'
const INPUT = 'px-2.5 py-1.5 text-sm border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20'

const num = (s: string) => (s.trim() === '' ? NaN : Number(s))

export default function IntentComparisonSection({ name }: { name: string }) {
  const [chosen, setChosen] = useState<DesignIntent[]>([])
  const [autonomy, setAutonomy] = useState('4')
  const [f, setF] = useState({ critical: '', link: '', ramp: '', sk: '', pst: '1', library: '', swing: '', amp: '', period: '' })
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF(p => ({ ...p, [k]: e.target.value }))
  const run = useMutation({
    mutationFn: () => {
      const swing = num(f.swing), amp = num(f.amp), period = num(f.period)
      const osc = { amplitude_mw: amp, period_s: period }
      return intentComparisonApi.run(name, {
        intents: chosen.map(intent => (intent === 'A' ? { intent, backup_autonomy_h: num(autonomy) } : { intent })),
        critical_mw: num(f.critical),
        inputs: {
          link: f.link.trim(), ramp_mw_per_min: num(f.ramp), sk_mva: num(f.sk), pst_limit: num(f.pst),
          transformer_library_mva: f.library.split(',').map(s => Number(s.trim())).filter(Number.isFinite),
          envelope: { phases: [{ swing_mw: swing, oscillation: osc }],
            max: { swing_mw: swing, oscillation_amplitude_mw: amp, oscillation_period_s: period } },
        },
      })
    },
  })
  const toggle = (id: DesignIntent) =>
    setChosen(c => (c.includes(id) ? c.filter(x => x !== id) : [...c, id]))
  const complete = [f.critical, f.ramp, f.sk, f.pst, f.swing, f.amp, f.period].every(v => Number(v) > 0) &&
    f.link.trim() !== '' && f.library.trim() !== '' && (!chosen.includes('A') || Number(autonomy) > 0)
  const blocked = chosen.length < 2 ? NEED_TWO : !complete ? 'Fill in every input below, all greater than zero.' : null
  const rows = run.data?.rows

  return (
    <PageSection title="Intent comparison" hint="one sizing loop per design intent" bodyClassName="p-4 space-y-3"
      right={<Btn variant="primary" disabled={blocked != null || run.isPending} onClick={() => run.mutate()}
        data-testid="intent-run">{run.isPending ? 'Running…' : 'Compare intents'}</Btn>}>
      <div className="flex flex-wrap gap-x-4 gap-y-1" role="group" aria-label="Design intents">
        {DESIGN_INTENTS.map(i => (
          <label key={i.id} className="flex items-center gap-1.5 text-[12px]">
            <input type="checkbox" checked={chosen.includes(i.id)} onChange={() => toggle(i.id)} />
            {i.id} · {i.label}
          </label>
        ))}
      </div>
      {blocked && <p className="text-[11px] text-muted" data-testid="intent-blocked">{blocked}</p>}
      {chosen.includes('A') && (
        <Field label="Intent A backup autonomy (h)" hint="Backup energy = critical load × hours; default 4 h.">
          <input className={INPUT} inputMode="decimal" value={autonomy} onChange={e => setAutonomy(e.target.value)}
            data-testid="intent-autonomy" />
        </Field>
      )}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
        {([['critical', 'Critical load (MW)'], ['link', 'Campus link'], ['ramp', 'Grid ramp limit (MW/min)'],
          ['sk', 'Short-circuit power (MVA)'], ['pst', 'Pst limit'], ['library', 'Transformer sizes (MVA, comma list)'],
          ['swing', 'Load swing (MW)'], ['amp', 'Oscillation amplitude (MW)'], ['period', 'Oscillation period (s)']] as const)
          .map(([k, label]) => (
            <Field key={k} label={label}><input className={INPUT} value={f[k]} onChange={set(k)} data-testid={`intent-in-${k}`} /></Field>
          ))}
      </div>
      {run.isError && <p className="text-[12px] text-err" role="alert" data-testid="intent-error">{errorText(run.error)}</p>}
      {rows && <ResultTable rows={rows} />}
    </PageSection>
  )
}

const fmt = (v: number | null | undefined, d = 1) => (v == null ? '–' : v.toLocaleString(undefined, { maximumFractionDigits: d }))

function ResultTable({ rows }: { rows: IntentRow[] }) {
  return (
    <div className="space-y-2" data-testid="intent-table">
      {rows.some(r => r.warnings.length > 0) && rows.filter(r => r.warnings.length > 0).map(r => (
        <p key={r.intent} className="text-[11px] text-warn" role="status" data-testid={`intent-warning-${r.intent}`}>
          Intent {r.intent}: {r.warnings.join(' ')}
        </p>
      ))}
      <div className="overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead><tr className="text-left text-muted">
            <th className="pr-2">Intent</th><th className="pr-2">Loop status</th><th className="pr-2">Cost (€ per year, LP total)</th>
            <th className="pr-2">Assets</th><th className="pr-2">Transformer (MVA)</th><th className="pr-2">STATCOM (Mvar)</th>
            <th>Checks</th>
          </tr></thead>
          <tbody>
            {rows.map(r => {
              const w = OUTCOME_WORDS[r.outcome]
              return (
                <tr key={r.intent} className="align-top border-t border-border" data-testid={`intent-row-${r.intent}`}>
                  <td className="py-1 pr-2 font-semibold">{r.intent}</td>
                  <td className="py-1 pr-2"><Tag tone={w.tone}>{w.text}</Tag>
                    {r.backup_autonomy_h != null && <div className="text-muted">{fmt(r.backup_autonomy_h)} h autonomy · {fmt(r.backup_energy_mwh)} MWh</div>}</td>
                  <td className="py-1 pr-2 font-mono">{fmt(r.cost_eur, 0)}</td>
                  <td className="py-1 pr-2 font-mono">{Object.entries(r.assets).map(([k, a]) => (
                    <div key={k}>{k}: {fmt(a.value)} {a.unit}</div>))}</td>
                  <td className="py-1 pr-2 font-mono">{fmt(r.transformer_mva)}</td>
                  <td className="py-1 pr-2 font-mono">{fmt(r.statcom_mvar)}</td>
                  <td className="py-1">{r.checks.map(c => (
                    <div key={c.name}>{c.name}: <Tag tone={CHECK_WORDS[c.state].tone}>{CHECK_WORDS[c.state].text}</Tag></div>))}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {rows[0]?.basis_note && <p className="text-[11px] text-muted" data-testid="intent-basis">Basis: {rows[0].basis_note}</p>}
    </div>
  )
}

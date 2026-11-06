/** The data-centre backup (DC 03): gensets, batteries or both for a grid
 *  outage. The mode is stored in `eh_template.json`; the assessment sizes the
 *  strings, proves both legs over the outage window and prices the delta
 *  against the gensets. Shown only on a project created with a `dc_load`. */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  campusApi, errorText,
  type DcBackupAssessment, type DcBackupMode, type DcBackupProof, type GridReadinessVerdict,
} from '../api/campusElectrical'
import { resultsApi } from '../api/simulation'
import { Btn, PageSection, Seg } from '../components/PageKit'
import { StatePill } from '../components/ResultState'
import { VerdictChip } from '../components/gridReadiness/GridReadinessScorecard'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'

const NBSP = ' '
const MODE_LABEL: Record<DcBackupMode, string> = { genset: 'Gensets', bess: 'Batteries', hybrid: 'Hybrid' }
const CHIP: Record<DcBackupProof['verdict'], GridReadinessVerdict> = {
  pass: 'pass', fail: 'fail', not_shown: 'unknown',
}
/** The redundancy tier in words (UX: no bare "N+1"). */
const SPARE: Record<string, string> = { N: 'no spare string', 'N+1': 'one spare string', '2N': 'a full second set' }
const SYMBOL: Record<string, string> = { EUR: '€', USD: '$', GBP: '£' }
/** Cost-line words (UX: labels from a word map, never raw keys). */
const LINE_LABEL: Record<string, string> = {
  capex_and_repurchase: 'Capex and repurchase', fom: 'Fixed O&M', fuel: 'Fuel',
  residual_value: 'Residual value (credit)', power_capex_and_repurchase: 'Power capex and repurchase',
  power_fom: 'Power fixed O&M', energy_capex_and_repurchase: 'Energy capex and repurchase',
  energy_fom: 'Energy fixed O&M', augmentation: 'Augmentation',
}
const lineLabel = (k: string) => LINE_LABEL[k] ?? k.replace(/_/g, ' ')

const num = (v: number | null | undefined, digits = 3) =>
  v == null || !Number.isFinite(v) ? '–' : v.toLocaleString('en', { maximumSignificantDigits: digits })
const qty = (v: number | null | undefined, unit: string, digits = 3) => `${num(v, digits)}${NBSP}${unit}`
const plural = (n: number, one: string, many: string) => `${n}${NBSP}${n === 1 ? one : many}`

/** Money in short form, unsigned: "€ 1.2 m", "€ 350 k", "€ 900". */
export function money(v: number, currency?: string | null): string {
  const cur = currency ? (SYMBOL[currency] ?? currency) : ''
  const a = Math.abs(v)
  const body = a >= 1e6 ? `${num(a / 1e6, 2)}${NBSP}m` : a >= 1e3 ? `${num(a / 1e3, 3)}${NBSP}k` : num(a, 3)
  return cur ? `${cur}${NBSP}${body}` : body
}

/** The stored backup mode of a data-centre project, or null when it has no `dc_load`. */
function storedMode(meta: unknown): DcBackupMode | null {
  const dc = (meta as { dc_load?: { spec?: { backup?: { mode?: DcBackupMode } } } } | null)?.dc_load
  if (!dc?.spec) return null
  return dc.spec.backup?.mode ?? 'genset'
}

export default function DcBackupSection({ name }: { name: string }) {
  const qc = useQueryClient()
  const key = nk(name, 'adequacy', 'eh_template')
  const { data: meta } = useQuery({
    queryKey: key,
    queryFn: () => resultsApi.getEhTemplate(name, { quiet: true }),
    staleTime: Infinity,
  })
  const mode = storedMode(meta)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [result, setResult] = useState<DcBackupAssessment | null>(null)
  const [assessError, setAssessError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: (next: DcBackupMode) => campusApi.setDcBackup(name, { mode: next }),
    onMutate: () => { setRefusal(null) },
    onSuccess: () => { void qc.invalidateQueries({ queryKey: key }) },
    // The control shows the stored mode, so a refused one never looks selected.
    onError: e => setRefusal(errorText(e)),
  })
  const assess = useMutation({
    mutationFn: () => campusApi.assessDcBackup(name),
    onMutate: () => { setAssessError(null); setRefusal(null) },
    onSuccess: setResult,
    onError: e => setAssessError(errorText(e)),
  })

  if (mode == null) return null
  return (
    <div data-testid="dc-backup">
      <PageSection title="Backup" hint="Gensets, batteries or both for a grid outage">
        <div className="flex items-center gap-2" role="group" aria-label="Backup mode"
             data-testid="dc-backup-mode" data-mode={mode}>
          <Seg<DcBackupMode>
            value={mode}
            onChange={v => { if (v !== mode) save.mutate(v) }}
            options={(['genset', 'bess', 'hybrid'] as const).map(v => ({
              value: v, label: MODE_LABEL[v], disabled: save.isPending,
            }))}
            title="Backup mode"
          />
          <Btn onClick={() => assess.mutate()} disabled={assess.isPending || save.isPending}>
            <span data-testid="dc-backup-assess">{assess.isPending ? 'Assessing…' : 'Assess'}</span>
          </Btn>
        </div>
        {refusal && <p className="mt-1 text-[12px] text-danger" data-testid="dc-backup-refusal">{refusal}</p>}
        {assessError && <p className="mt-1 text-[12px] text-danger" data-testid="dc-backup-error">{assessError}</p>}
        {result && <Assessment result={result} stale={result.mode !== mode} />}
      </PageSection>
    </div>
  )
}

function Assessment({ result, stale }: { result: DcBackupAssessment; stale: boolean }) {
  const guided = useUIStore(s => s.uiMode) === 'guided'
  const { window: w, sizing: s, cost: c } = result
  const legs = (['genset', result.mode] as DcBackupMode[]).filter((m, i, a) => a.indexOf(m) === i)
  const assumed = c.inputs.filter(i => i.assumed)
  return (
    <div className="mt-2 space-y-2 text-[12px]" data-testid="dc-backup-result" data-stale={stale}>
      {stale && (
        <p className="flex items-center gap-2" data-testid="dc-backup-stale">
          <StatePill state="stale" />
          <span className="text-muted">This assessment is for {MODE_LABEL[result.mode]}. Assess again for the stored mode.</span>
        </p>
      )}
      <div className={stale ? 'opacity-50 space-y-2' : 'space-y-2'}>
        <p>
          {qty(w.hours, 'h')} outage window from {w.first}, {qty(w.energy_mwh, 'MWh')} critical energy.
        </p>
        <table className="w-full text-[11px]">
          <thead className="text-muted">
            <tr><th className="text-left font-normal">Leg</th><th className="text-left font-normal">Verdict</th>
              <th className="text-right font-normal">Unserved in window, MWh</th>
              <th className="text-right font-normal">Unserved outside, MWh</th></tr>
          </thead>
          <tbody>
            {legs.map(m => {
              const p = result.proofs[m]
              if (!p) return null
              return (
                <tr key={m} data-testid={`dc-backup-proof-${m}`}>
                  <td>{MODE_LABEL[m]}</td>
                  <td>
                    <VerdictChip verdict={CHIP[p.verdict]} />
                    {p.verdict === 'not_shown' && p.reason && <span className="text-muted">: {p.reason}</span>}
                  </td>
                  <td className="text-right tabular-nums">{num(p.window_unserved_mwh)}</td>
                  <td className="text-right tabular-nums">{num(p.on_grid_unserved_mwh)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {result.mode !== 'genset' && (
          <p data-testid="dc-backup-sizing">
            {plural(s.strings_n, 'string', 'strings')} of {qty(s.string_mw, 'MW')} ({SPARE[s.redundancy] ?? s.redundancy}),{' '}
            {qty(s.strings_n * s.e_usable_mwh_per_string, 'MWh')} usable; {qty(s.shortfall_mw, 'MW')} above the firm gensets.
          </p>
        )}
        <CostLine result={result} />
        {result.mode !== 'genset' && assumed.length > 0 && (
          <p className="text-[11px] text-muted" data-testid="dc-backup-assumed">
            {assumed.map((i, k) => (
              <span key={i.key}>{k > 0 && '; '}{i.key.replace(/_/g, ' ')} {String(i.value)}{i.unit ? `${NBSP}${i.unit}` : ''}{' '}
                <span className="rounded border border-border px-1 text-[10px]">assumed</span></span>
            ))}
          </p>
        )}
        {!guided && result.mode !== 'genset' && (
          <details>
            <summary className="cursor-pointer text-muted">Cost lines</summary>
            <table className="w-full text-[11px]" data-testid="dc-backup-lines">
              <thead className="text-muted">
                <tr><th className="text-left font-normal">Leg</th><th className="text-left font-normal">Line</th>
                  <th className="text-right font-normal">NPV</th></tr>
              </thead>
              <tbody>
                {c.lines.map(l => (
                  <tr key={`${l.leg}-${l.line}`}>
                    <td>{l.leg === 'genset' ? 'Gensets' : 'Batteries'}</td><td>{lineLabel(l.line)}</td>
                    <td className="text-right tabular-nums">{l.npv < 0 ? '−' : ''}{money(l.npv, l.unit)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        )}
        <ul className="text-[11px] text-muted list-disc pl-4">
          <li>The batteries' charge at the start of the outage assumes perfect foresight.</li>
          <li>The strings are unpriced in the solve; their cost is in this comparison only.</li>
          {result.basis && <>
            <li data-testid="dc-backup-basis">Read the network saved at {result.basis.network_saved_at.slice(0, 16).replace('T', ' ')} UTC.</li>
            {result.basis.not_reproduced.map(t => <li key={t}>Not reproduced from a normal solve: {t}.</li>)}
          </>}
        </ul>
      </div>
    </div>
  )
}

function CostLine({ result }: { result: DcBackupAssessment }) {
  const c = result.cost
  if (result.mode === 'genset') return null
  if (c.delta == null) {
    return <p data-testid="dc-backup-cost">The cost against the gensets cannot be priced.</p>
  }
  const amount = money(c.delta, c.currency)
  return (
    <p data-testid="dc-backup-cost">
      Cost over {plural(c.years, 'year', 'years')} vs gensets:{' '}
      <span className="font-medium">
        {c.delta > 0 ? `costs ${amount} more` : c.delta < 0 ? `saves ${amount}` : 'costs the same'}
      </span>{' '}
      (NPV, {num(c.discount_rate * 100)}{NBSP}% real{c.money_year ? `, ${c.money_year} money` : ''}).
    </p>
  )
}

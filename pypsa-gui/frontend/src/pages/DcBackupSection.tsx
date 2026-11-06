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
import { VerdictChip } from '../components/gridReadiness/GridReadinessScorecard'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'

const MODE_LABEL: Record<DcBackupMode, string> = { genset: 'Gensets', bess: 'Batteries', hybrid: 'Hybrid' }
const CHIP: Record<DcBackupProof['verdict'], GridReadinessVerdict> = {
  pass: 'pass', fail: 'fail', not_shown: 'unknown',
}

const num = (v: number | null | undefined, digits = 3) =>
  v == null || !Number.isFinite(v) ? '–' : v.toLocaleString(undefined, { maximumSignificantDigits: digits })

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
    onSuccess: () => { setResult(null); void qc.invalidateQueries({ queryKey: key }) },
    onError: e => setRefusal(errorText(e)),
  })
  const assess = useMutation({
    mutationFn: () => campusApi.assessDcBackup(name),
    onMutate: () => { setAssessError(null) },
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
        {result && result.mode === mode && <Assessment result={result} />}
      </PageSection>
    </div>
  )
}

function Assessment({ result }: { result: DcBackupAssessment }) {
  const guided = useUIStore(s => s.uiMode) === 'guided'
  const { window: w, sizing: s, cost: c } = result
  const legs = (['genset', result.mode] as DcBackupMode[]).filter((m, i, a) => a.indexOf(m) === i)
  return (
    <div className="mt-2 space-y-2 text-[12px]" data-testid="dc-backup-result">
      <p>
        {num(w.hours)} h outage window from {w.first}, {num(w.energy_mwh)} MWh critical energy.
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
                  {p.verdict === 'not_shown' && p.reason && <span className="ml-1 text-muted">{p.reason}</span>}
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
          {s.strings_n} strings × {num(s.string_mw)} MW ({s.redundancy}), {num(s.e_mwh)} MWh usable;{' '}
          {num(s.shortfall_mw)} MW above firm gensets.
        </p>
      )}
      <CostLine result={result} />
      {!guided && result.mode !== 'genset' && (
        <details>
          <summary className="cursor-pointer text-muted">Cost lines</summary>
          <table className="w-full text-[11px]" data-testid="dc-backup-lines">
            <thead className="text-muted">
              <tr><th className="text-left font-normal">Leg</th><th className="text-left font-normal">Line</th>
                <th className="text-left font-normal">Unit</th><th className="text-right font-normal">NPV</th></tr>
            </thead>
            <tbody>
              {c.lines.map(l => (
                <tr key={`${l.leg}-${l.line}`}>
                  <td>{l.leg}</td><td>{l.line.replace(/_/g, ' ')}</td><td>{l.unit}</td>
                  <td className="text-right tabular-nums">{num(l.npv, 4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-1 text-[11px] text-muted">
            Assumed: {c.inputs.filter(i => i.assumed).map(i => `${i.key.replace(/_/g, ' ')} ${String(i.value)}${i.unit ? ` ${i.unit}` : ''}`).join('; ') || 'none'}.
          </p>
        </details>
      )}
      <ul className="text-[11px] text-muted list-disc pl-4">
        <li>The batteries' charge at the start of the outage assumes perfect foresight.</li>
        <li>The strings are unpriced in the solve; their cost is in this delta only.</li>
      </ul>
    </div>
  )
}

function CostLine({ result }: { result: DcBackupAssessment }) {
  const c = result.cost
  if (result.mode === 'genset') return null
  if (c.delta == null) {
    return <p data-testid="dc-backup-cost">The cost difference against the gensets cannot be priced.</p>
  }
  const cur = c.currency ?? ''
  const word = c.delta > 0 ? 'costs more' : 'saves'
  return (
    <p data-testid="dc-backup-cost">
      NPV against gensets: {MODE_LABEL[result.mode]} {word},{' '}
      <span className="tabular-nums font-medium">{c.delta > 0 ? '+' : '−'}{cur} {num(Math.abs(c.delta), 4)}</span>{' '}
      over {c.years} y at {num(c.discount_rate * 100)} % real{c.money_year ? ` (${c.money_year} money)` : ''}.
    </p>
  )
}

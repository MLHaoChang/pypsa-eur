/** The data-centre power architecture (DC 05): AC (LV UPS), MV UPS, 800 VDC or
 *  a solid-state transformer. The architecture is set when the project is
 *  created; Compare screens all four against it on the saved network (no
 *  solve, nothing written): energy, effective PUE, peak within the import
 *  limit, capex, the cost over the horizon at the range ends, and the grid
 *  services each can offer with the scorecard's verdicts. Shown only on a
 *  project created with a `dc_load`. */
import { Fragment, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  campusApi, errorText,
  type DcArchitecture, type DcArchitectureCompare, type DcArchitectureVariant,
} from '../api/campusElectrical'
import { resultsApi } from '../api/simulation'
import { Btn, PageSection } from '../components/PageKit'
import { StatePill } from '../components/ResultState'
import { VerdictChip } from '../components/gridReadiness/GridReadinessScorecard'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { money } from './DcBackupSection'

const NBSP = ' '
export const ARCH_LABEL: Record<DcArchitecture, string> = {
  ac: 'AC (LV UPS)', mv_ups: 'MV UPS', dc_800v: '800 VDC', sst: 'Solid-state transformer',
}
/** Grid-service chips: a short label and the full name for the tooltip (UX 2). */
export const SERVICE_CHIP: Record<string, { short: string; full: string }> = {
  fast_frequency_response: { short: 'FFR', full: 'Fast frequency response' },
  frequency_containment: { short: 'FCR', full: 'Frequency containment' },
  voltage_control: { short: 'V-ctrl', full: 'Voltage control' },
  grid_forming: { short: 'GFM', full: 'Grid-forming' },
  synthetic_inertia: { short: 'Inertia', full: 'Synthetic inertia' },
}
const DELTA_WORDS: Record<DcArchitectureVariant['npv_delta']['verdict'], string> = {
  cheaper: 'cheaper', dearer: 'dearer', not_distinguishable: 'not distinguishable',
}

const num = (v: number | null | undefined, digits = 3) =>
  v == null || !Number.isFinite(v) ? '–' : v.toLocaleString('en', { maximumSignificantDigits: digits })
const gwh = (mwh: number) => num(mwh / 1000, 3)
const signedM = (v: number) => `${v < 0 ? '−' : v > 0 ? '+' : ''}${num(Math.abs(v) / 1e6, 3)}`

interface DcMeta { dc_load?: { spec?: { architecture?: DcArchitecture; backup?: unknown } } }

/** The project's own architecture (unset reads "ac", assumed), or null without a `dc_load`. */
function ownArchitecture(meta: unknown): { arch: DcArchitecture; set: boolean } | null {
  const spec = (meta as DcMeta | null)?.dc_load?.spec
  if (!spec) return null
  return { arch: spec.architecture ?? 'ac', set: spec.architecture != null }
}

/** What the compare reads from the stored spec; a change makes a result out of date (UX 5). */
const specKey = (meta: unknown) => JSON.stringify((meta as DcMeta | null)?.dc_load?.spec ?? null)

function Assumed() {
  return <span className="rounded border border-border px-1 text-[10px] text-muted">assumed</span>
}

export default function DcArchitectureSection({ name }: { name: string }) {
  const guided = useUIStore(s => s.uiMode) === 'guided'
  const { data: meta } = useQuery({
    queryKey: nk(name, 'adequacy', 'eh_template'),
    queryFn: () => resultsApi.getEhTemplate(name, { quiet: true }),
    staleTime: Infinity,
  })
  const own = ownArchitecture(meta)
  const [price, setPrice] = useState('')
  const [result, setResult] = useState<{ data: DcArchitectureCompare; key: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const compare = useMutation({
    mutationFn: () => {
      const p = price.trim() === '' ? null : Number(price)
      return campusApi.compareDcArchitecture(name, p != null && Number.isFinite(p) ? p : null)
    },
    onMutate: () => setError(null),
    onSuccess: data => setResult({ data, key: specKey(meta) }),
    onError: e => setError(errorText(e)),
  })

  if (own == null) return null
  const stale = result != null && result.key !== specKey(meta)
  const complete = result != null && !stale
  return (
    <div data-testid="dc-architecture" data-complete={complete}>
      <PageSection
        title="Power architecture"
        hint={<span data-testid="dc-architecture-own">
          {ARCH_LABEL[own.arch]} (set when the project is created){own.set ? '' : ', assumed'}
        </span>}
        right={<Btn onClick={() => compare.mutate()} disabled={compare.isPending}>
          <span data-testid="dc-architecture-compare">{compare.isPending ? 'Comparing…' : 'Compare'}</span>
        </Btn>}
      >
        {!guided && (
          <label className="flex items-center gap-2 text-[11px]">
            <span className="text-muted">Energy price</span>
            <input
              type="number" min={0} step="any" value={price} placeholder="the tariff's energy rate"
              onChange={e => setPrice(e.target.value)} aria-label="Energy price per MWh"
              data-testid="dc-architecture-price"
              className="w-40 rounded border border-border bg-bg px-1 py-0.5 tabular-nums"
            />
            <span className="text-muted">per MWh; empty reads the project&apos;s tariff</span>
          </label>
        )}
        {error && <p className="mt-1 text-[12px] text-danger" data-testid="dc-architecture-error">{error}</p>}
        {result && <Result data={result.data} stale={stale} guided={guided} />}
      </PageSection>
    </div>
  )
}

function Result({ data, stale, guided }: { data: DcArchitectureCompare; stale: boolean; guided: boolean }) {
  const [open, setOpen] = useState<DcArchitecture | null>(null)
  const cur = data.currency
  const p = data.price
  return (
    <div className="mt-2 space-y-2 text-[12px]" data-testid="dc-architecture-result" data-stale={stale}>
      {stale && (
        <p className="flex items-center gap-2" data-testid="dc-architecture-stale">
          <StatePill state="stale" />
          <span className="text-muted">Out of date: the project changed since this comparison. Compare again.</span>
        </p>
      )}
      <div className={stale ? 'opacity-50 space-y-2' : 'space-y-2'}>
        <p data-testid="dc-architecture-summary" className="font-medium">
          {data.summary.cheapest_text} · {data.summary.most_services_text}
        </p>
        <p data-testid="dc-architecture-price-used" className="text-[11px]">
          Energy priced at {p.weighted_mean_per_mwh == null ? '–' : money(p.weighted_mean_per_mwh, cur)} per MWh
          {' '}({p.source}){p.assumed && <>{' '}<Assumed /></>}; held constant in real terms.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]" data-testid="dc-architecture-table">
            <thead className="text-muted">
              <tr>
                <th className="text-left font-normal">Architecture</th>
                {!guided && <th className="text-right font-normal">Efficiency to rack</th>}
                <th className="text-right font-normal">Effective PUE</th>
                <th className="text-right font-normal">Facility energy (GWh/yr)</th>
                {!guided && <th className="text-right font-normal">Loss energy (GWh/yr)</th>}
                {!guided && <th className="text-left font-normal">Peak within import limit</th>}
                {!guided && <th className="text-right font-normal">Capex (M {cur})</th>}
                <th className="text-right font-normal">NPV vs this project (M {cur}, {data.money_year})</th>
                {!guided && <th className="text-left font-normal">Grid services</th>}
                <th className="text-left font-normal">Frequency support</th>
                <th className="text-left font-normal">Voltage control offered</th>
              </tr>
            </thead>
            <tbody>
              {data.variants.map(v => (
                <Fragment key={v.architecture}>
                  <tr data-testid={`dc-architecture-row-${v.architecture}`} className={v.own ? 'font-medium' : ''}>
                    <td>
                      {!guided && (
                        <button type="button" className="mr-1 text-muted" aria-expanded={open === v.architecture}
                                aria-label={`Loss chain of ${v.label}`}
                                onClick={() => setOpen(open === v.architecture ? null : v.architecture)}>
                          {open === v.architecture ? '▾' : '▸'}
                        </button>
                      )}
                      {v.label}{v.own && <span className="ml-1 text-muted">(this project)</span>}
                    </td>
                    {!guided && <td className="text-right tabular-nums">{num(v.eta_to_rack * 100, 3)}{NBSP}%</td>}
                    <td className="text-right tabular-nums">{num(v.effective_pue, 3)}</td>
                    <td className="text-right tabular-nums">{gwh(v.facility_mwh)}</td>
                    {!guided && <td className="text-right tabular-nums">{gwh(v.loss_mwh)}</td>}
                    {!guided && <td><VerdictChip verdict={v.peak.verdict} /></td>}
                    {!guided && <td className="text-right tabular-nums">{num(v.capex.total / 1e6, 3)}</td>}
                    <td className="text-right tabular-nums" data-testid={`dc-architecture-npv-${v.architecture}`}>
                      {v.own ? '–' : <>
                        {signedM(v.npv_delta.central)}
                        <span className="text-muted"> ({signedM(v.npv_delta.low)} to {signedM(v.npv_delta.high)}; {DELTA_WORDS[v.npv_delta.verdict]})</span>
                      </>}
                    </td>
                    {!guided && (
                      <td>
                        {v.services.counted.map(s => (
                          <span key={s} title={SERVICE_CHIP[s]?.full ?? s}
                                className="mr-1 rounded border border-border px-1 text-[10px]">{SERVICE_CHIP[s]?.short ?? s}</span>
                        ))}
                        {v.services.conditional.map(s => (
                          <span key={s} title={`${SERVICE_CHIP[s]?.full ?? s}: needs storage on the DC bus`}
                                className="mr-1 rounded border border-dashed border-border px-1 text-[10px] text-muted">{SERVICE_CHIP[s]?.short ?? s}</span>
                        ))}
                        {v.services.counted.length + v.services.conditional.length === 0 && <span className="text-muted">none</span>}
                      </td>
                    )}
                    <td><VerdictChip verdict={v.verdicts.frequency_support.verdict} /></td>
                    <td><VerdictChip verdict={v.verdicts.voltage_control.verdict} /></td>
                  </tr>
                  {!guided && open === v.architecture && (
                    <tr data-testid={`dc-architecture-chain-${v.architecture}`}>
                      <td colSpan={11} className="bg-bg-2 px-2 py-1">
                        <table className="w-full text-[11px]">
                          <thead className="text-muted"><tr>
                            <th className="text-left font-normal">Stage</th><th className="text-right font-normal">Efficiency</th>
                            <th className="text-right font-normal">Range</th><th className="text-left font-normal">Source</th>
                          </tr></thead>
                          <tbody>
                            {v.stages.map(s => (
                              <tr key={s.stage}>
                                <td>{s.label}{s.stage === 'rack' && <span className="text-muted"> (beyond the PUE boundary)</span>}</td>
                                <td className="text-right tabular-nums">{num(s.efficiency * 100, 4)}{NBSP}%</td>
                                <td className="text-right tabular-nums">{s.range ? `${num(s.range[0] * 100, 4)}–${num(s.range[1] * 100, 4)}${NBSP}%` : '–'}</td>
                                <td className="text-muted">{s.source}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        <p className="mt-1">
                          Capex {money(v.capex.overnight_per_mw, cur)} per MW of IT ({money(v.capex.range_per_mw[0], cur)}–{money(v.capex.range_per_mw[1], cur)}) <Assumed />;
                          {' '}lifetime {num(v.capex.lifetime)} years; fixed O&amp;M {num(v.capex.fom_share * 100)}{NBSP}% a year.
                          {' '}NPV: capex {money(v.npv.value.capex, cur)}, fixed O&amp;M {money(v.npv.value.fom, cur)}, energy {money(v.npv.value.energy, cur)}.
                          {' '}Services: {v.services_why}.
                        </p>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
        <ul className="text-[11px] text-muted list-disc pl-4">
          <li data-testid="dc-architecture-footnote">A screening estimate, with no solve. Outage proof is in Backup.</li>
          <li>{data.boundary}; the stated PUE is this project&apos;s architecture&apos;s.</li>
          <li>Effects not modelled: {data.second_order.replace(/^assumed: /, '')} (assumed).</li>
          <li>{data.rms_caveat}.</li>
          <li>{data.capex_note}; {data.years}{NBSP}years at {num(data.discount_rate * 100)}{NBSP}% real ({data.discount_rate_source}).</li>
          <li>{p.not_valued}.</li>
          <li data-testid="dc-architecture-source">Source: defaults pack {data.pack_version}.</li>
        </ul>
      </div>
    </div>
  )
}

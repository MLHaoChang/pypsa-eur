// The other series (TS-03): a time-of-use price on a generator's or link's
// marginal cost, a storage unit's inflow, a heat pump's COP on its link's
// efficiency. The host picks the kind (it knows the asset); the editor
// previews before Apply and refines under Advanced (TS-04), like the load and
// availability editors, and mounts the same way in Properties and the Time
// Series drawer.
//
// Guided shows the key parameters: a peak and off-peak price, an annual
// inflow and its shape, a heat source and sink temperature. Period lists,
// monthly shares and the Carnot parameters sit under Advanced, open by
// default in Expert.
import { useState, type ReactNode } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Play, Plus, Trash2 } from 'lucide-react'
import { Btn, Seg } from '../PageKit'
import { errorText, seriesApi, type PendingSeries } from '../../api/profiles'
import {
  CurrentSeriesLine, DisabledReason, INPUT, MeasuredFileTab, PendingCard, RefinePanel, type EditorMode,
} from './SeriesKit'

export type SeriesKind = 'tou' | 'inflow' | 'cop'

export interface SeriesEditorProps {
  project: string
  component: string
  kind: SeriesKind
  /** For a price: whose marginal cost (a generator's or a link's). */
  componentType?: 'generators' | 'links'
  mode: EditorMode
}

interface Period { name: string; rate: string; months: string; weekdays: string; start: string; end: string }

const SHAPES = [
  { value: 'snowmelt', label: 'Snowmelt' },
  { value: 'rain_fed', label: 'Rain-fed' },
  { value: 'flat', label: 'Flat' },
] as const
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

const ints = (s: string) => s.split(/[,\s]+/).filter(Boolean).map(Number)
const opt = (s: string) => (s === '' ? undefined : Number(s))

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-28 text-muted">{label}</span>
      {children}
    </div>
  )
}

function periodBody(p: Period): Record<string, unknown> {
  const body: Record<string, unknown> = { name: p.name, rate: Number(p.rate) }
  if (p.months.trim()) body.months = ints(p.months)
  if (p.weekdays.trim()) body.weekdays = ints(p.weekdays)
  if (p.start !== '' || p.end !== '') { body.start_hour = Number(p.start); body.end_hour = Number(p.end) }
  return body
}

export default function SeriesEditor({ project, component, kind, componentType = 'generators', mode }: SeriesEditorProps) {
  const [advanced, setAdvanced] = useState(mode === 'expert')
  const [pending, setPending] = useState<PendingSeries | null>(null)
  // Generate a series, or read a measured file (a price or a COP; an inflow
  // has no row template to fill).
  const [from, setFrom] = useState<'generate' | 'measured'>('generate')
  const measurable = kind !== 'inflow'
  // tou
  const [currency, setCurrency] = useState('EUR')
  const [basis, setBasis] = useState<'real' | 'nominal'>('real')
  const [moneyYear, setMoneyYear] = useState(String(new Date().getFullYear()))
  const [peak, setPeak] = useState('')
  const [offPeak, setOffPeak] = useState('')
  const [peakFrom, setPeakFrom] = useState('8')
  const [peakTo, setPeakTo] = useState('20')
  const [periods, setPeriods] = useState<Period[]>([
    { name: 'peak', rate: '', months: '', weekdays: '0,1,2,3,4', start: '8', end: '20' },
    { name: 'off-peak', rate: '', months: '', weekdays: '', start: '', end: '' },
  ])
  // inflow
  const [annual, setAnnual] = useState('')
  const [shape, setShape] = useState<string>('snowmelt')
  const [byMonth, setByMonth] = useState(false)
  const [monthly, setMonthly] = useState<string[]>(Array(12).fill('1'))
  // cop
  const [source, setSource] = useState<'air' | 'ground'>('air')
  const [sink, setSink] = useState('45')
  const [ground, setGround] = useState('10')
  const [eta, setEta] = useState('')
  const [copMax, setCopMax] = useState('')
  // An air source's ambient: empty uses the project's data-centre ambient
  // (the backend refuses when the project has none).
  const [ambMean, setAmbMean] = useState('')
  const [ambSeason, setAmbSeason] = useState('')
  const [ambDay, setAmbDay] = useState('')

  function params(): Record<string, unknown> {
    if (kind === 'tou') {
      const list = advanced
        ? periods.map(periodBody)
        : [{ name: 'peak', rate: Number(peak), weekdays: [0, 1, 2, 3, 4],
             start_hour: Number(peakFrom), end_hour: Number(peakTo) },
           { name: 'off-peak', rate: Number(offPeak) }]
      const p: Record<string, unknown> = { periods: list, currency, price_basis: basis,
        currency_year: Math.trunc(Number(moneyYear)) }
      if (componentType === 'links') p.component_type = 'links'
      return p
    }
    if (kind === 'inflow') {
      return byMonth ? { annual_mwh: Number(annual), monthly: monthly.map(Number) }
        : { annual_mwh: Number(annual), shape }
    }
    const p: Record<string, unknown> = { source, sink_c: Number(sink) }
    if (source === 'ground') p.source_c = Number(ground)
    if (source === 'air' && [ambMean, ambSeason, ambDay].every(v => v.trim() !== '')) {
      p.ambient_annual_mean_c = Number(ambMean)
      p.ambient_seasonal_amplitude_k = Number(ambSeason)
      p.ambient_diurnal_amplitude_k = Number(ambDay)
    }
    if (opt(eta) != null) p.eta = opt(eta)
    if (opt(copMax) != null) p.cop_max = opt(copMax)
    return p
  }

  const missing = kind === 'tou'
    ? (!currency || !moneyYear ? 'Enter the currency and its money year to preview.'
      : advanced ? (periods.every(p => p.name && p.rate !== '') ? null : 'Name each period and give it a rate to preview.')
        : peak && offPeak ? null : 'Enter the peak and off-peak prices to preview.')
    : kind === 'inflow' ? (annual ? null : 'Enter the annual inflow to preview.')
      : sink ? null : 'Enter the supply temperature to preview.'
  const ready = missing == null

  const generate = useMutation({
    mutationFn: () => seriesApi.generateSeries(project, { kind, components: [component], params: params() }),
    onSuccess: setPending,
  })

  const setPeriod = (i: number, patch: Partial<Period>) =>
    setPeriods(periods.map((p, j) => (j === i ? { ...p, ...patch } : p)))

  return (
    <div className="text-xs space-y-1" data-testid="series-editor">
      <CurrentSeriesLine project={project}
        componentType={kind === 'inflow' ? 'storage_units' : kind === 'cop' ? 'links' : componentType}
        attribute={kind === 'inflow' ? 'inflow' : kind === 'cop' ? 'efficiency' : 'marginal_cost'}
        component={component} onPending={p => { setPending(p); setAdvanced(true) }} />
      {measurable && (
        <Seg value={from} onChange={v => { setFrom(v); setPending(null) }}
          options={[{ value: 'generate', label: 'Generate' }, { value: 'measured', label: 'Measured file' }]} />
      )}
      {from === 'measured' && measurable ? (
        <MeasuredFileTab project={project} componentType={kind === 'cop' ? 'links' : componentType}
          attribute={kind === 'cop' ? 'efficiency' : 'marginal_cost'} component={component} onPending={setPending} />
      ) : (
        <>
          {kind === 'tou' && (
            <>
              {!advanced && (
                <>
                  <Row label="Peak price">
                    <input aria-label="Peak price" className={`${INPUT} w-20`} value={peak} onChange={e => setPeak(e.target.value)} />
                    <span className="text-muted">{currency}/MWh, weekdays</span>
                    <input aria-label="Peak from hour" className={`${INPUT} w-12`} value={peakFrom} onChange={e => setPeakFrom(e.target.value)} />
                    <span className="text-muted">to</span>
                    <input aria-label="Peak to hour" className={`${INPUT} w-12`} value={peakTo} onChange={e => setPeakTo(e.target.value)} />
                  </Row>
                  <Row label="Off-peak price">
                    <input aria-label="Off-peak price" className={`${INPUT} w-20`} value={offPeak} onChange={e => setOffPeak(e.target.value)} />
                    <span className="text-muted">{currency}/MWh</span>
                  </Row>
                </>
              )}
              <Row label="Money">
                <input aria-label="Currency" className={`${INPUT} w-14`} value={currency} onChange={e => setCurrency(e.target.value.toUpperCase())} />
                <Seg value={basis} onChange={setBasis} options={[{ value: 'real', label: 'Real' }, { value: 'nominal', label: 'Nominal' }]} />
                <input aria-label="Currency year" className={`${INPUT} w-16`} value={moneyYear} onChange={e => setMoneyYear(e.target.value)} />
              </Row>
            </>
          )}

          {kind === 'inflow' && (
            <>
              <Row label="Annual inflow">
                <input aria-label="Annual inflow MWh" className={`${INPUT} w-24`} value={annual} onChange={e => setAnnual(e.target.value)} />
                <span className="text-muted">MWh</span>
              </Row>
              {!byMonth && (
                <Row label="Shape">
                  <Seg value={shape} onChange={setShape} options={SHAPES.map(s => ({ value: s.value, label: s.label }))} />
                </Row>
              )}
            </>
          )}

          {kind === 'cop' && (
            <>
              <Row label="Heat source">
                <Seg value={source} onChange={setSource} options={[{ value: 'air', label: 'Air' }, { value: 'ground', label: 'Ground' }]} />
              </Row>
              <Row label="Supply at °C">
                <input aria-label="Supply at °C" className={`${INPUT} w-16`} value={sink} onChange={e => setSink(e.target.value)} />
              </Row>
              {source === 'ground' && (
                <Row label="Ground at °C">
                  <input aria-label="Ground at °C" className={`${INPUT} w-16`} value={ground} onChange={e => setGround(e.target.value)} />
                </Row>
              )}
              {source === 'air' && (
                <>
                  <p className="text-[11px] text-muted" data-testid="air-ambient-note">
                    Outdoor air follows the project's site temperature. Leave these empty to use it, or state all three.
                  </p>
                  <Row label="Mean outdoor °C">
                    <input aria-label="Mean outdoor °C" className={`${INPUT} w-16`} value={ambMean} onChange={e => setAmbMean(e.target.value)} />
                  </Row>
                  <Row label="Seasonal swing K">
                    <input aria-label="Seasonal swing K" className={`${INPUT} w-16`} value={ambSeason} onChange={e => setAmbSeason(e.target.value)} />
                  </Row>
                  <Row label="Daily swing K">
                    <input aria-label="Daily swing K" className={`${INPUT} w-16`} value={ambDay} onChange={e => setAmbDay(e.target.value)} />
                  </Row>
                </>
              )}
            </>
          )}

          <button type="button" className="block text-[11px] text-accent" onClick={() => setAdvanced(!advanced)}>
            {advanced ? 'Hide advanced' : 'Advanced'}
          </button>
          {advanced && (
            <div className="space-y-1" data-testid="series-advanced">
              {kind === 'tou' && (
                <>
                  <p className="text-[11px] text-muted">
                    Periods in order; the first match prices the hour, so the catch-all goes last.
                    Months 1–12, weekdays 0 = Monday, hours [from, to).
                  </p>
                  {periods.map((p, i) => (
                    <div key={i} className="flex flex-wrap items-center gap-1" data-testid="tou-period">
                      <input aria-label={`Period ${i + 1} name`} className={`${INPUT} w-20`} value={p.name} onChange={e => setPeriod(i, { name: e.target.value })} />
                      <input aria-label={`Period ${i + 1} price`} className={`${INPUT} w-16`} placeholder="price" value={p.rate} onChange={e => setPeriod(i, { rate: e.target.value })} />
                      <input aria-label={`Period ${i + 1} months`} className={`${INPUT} w-20`} placeholder="months" value={p.months} onChange={e => setPeriod(i, { months: e.target.value })} />
                      <input aria-label={`Period ${i + 1} weekdays`} className={`${INPUT} w-20`} placeholder="weekdays" value={p.weekdays} onChange={e => setPeriod(i, { weekdays: e.target.value })} />
                      <input aria-label={`Period ${i + 1} from hour`} className={`${INPUT} w-12`} placeholder="from" value={p.start} onChange={e => setPeriod(i, { start: e.target.value })} />
                      <input aria-label={`Period ${i + 1} to hour`} className={`${INPUT} w-12`} placeholder="to" value={p.end} onChange={e => setPeriod(i, { end: e.target.value })} />
                      {periods.length > 1 && (
                        <button type="button" aria-label={`Remove period ${i + 1}`} className="text-muted hover:text-danger"
                          onClick={() => setPeriods(periods.filter((_, j) => j !== i))}><Trash2 size={11} /></button>
                      )}
                    </div>
                  ))}
                  <Btn onClick={() => setPeriods([...periods, { name: '', rate: '', months: '', weekdays: '', start: '', end: '' }])}>
                    <Plus size={12} /> Period
                  </Btn>
                </>
              )}
              {kind === 'inflow' && (
                <>
                  <label className="flex items-center gap-2">
                    <input type="checkbox" checked={byMonth} onChange={e => setByMonth(e.target.checked)} />
                    Monthly shares instead of a shape
                  </label>
                  {byMonth && (
                    <div className="grid grid-cols-[repeat(auto-fill,minmax(90px,1fr))] gap-1">
                      {MONTHS.map((m, i) => (
                        <label key={m} className="flex items-center gap-1">
                          <span className="text-muted w-8">{m}</span>
                          <input aria-label={`${m} share`} className={`${INPUT} w-14`} value={monthly[i]}
                            onChange={e => setMonthly(monthly.map((v, j) => (j === i ? e.target.value : v)))} />
                        </label>
                      ))}
                    </div>
                  )}
                </>
              )}
              {kind === 'cop' && (
                <>
                  <Row label="Carnot share">
                    <input aria-label="Carnot share" className={`${INPUT} w-16`} placeholder="0.45" value={eta} onChange={e => setEta(e.target.value)} />
                  </Row>
                  <Row label="COP cap">
                    <input aria-label="COP cap" className={`${INPUT} w-16`} placeholder="7" value={copMax} onChange={e => setCopMax(e.target.value)} />
                  </Row>
                </>
              )}
            </div>
          )}

          <div className="flex items-center gap-2">
            <Btn variant="primary" onClick={() => generate.mutate()} disabled={!ready || generate.isPending}>
              <Play size={12} /> Preview
            </Btn>
            <DisabledReason reason={missing} />
          </div>
        </>
      )}

      {generate.error && <p className="text-[11px] text-red-600 mt-1" data-testid="editor-error">{errorText(generate.error)}</p>}

      {pending && (
        <>
          <PendingCard project={project} component={component} pending={pending} power={false}
            unit={kind === 'inflow' ? 'MW' : kind === 'tou' ? (typeof pending.unit === 'string' ? pending.unit : undefined) : undefined}
            attribute={kind === 'inflow' ? 'inflow' : kind === 'cop' ? 'efficiency' : 'marginal_cost'} />
          {advanced && <RefinePanel project={project} pending={pending} power={false} onRefined={setPending} />}
        </>
      )}
    </div>
  )
}

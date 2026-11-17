import type { CostBreakdown } from '../../api/simulation'
import { fmtCurrency } from './shared'
import { openSectionInExpert, withReturnFrom } from '../../layout/flowNavigation'

type Fuel = NonNullable<CostBreakdown['fuel']>[number]
type Hours = NonNullable<CostBreakdown['run_hours']>[number]

const NOT_SOLVED = 'not solved'
const num = (v: number | null | undefined) =>
  v == null ? NOT_SOLVED : v.toLocaleString(undefined, { maximumFractionDigits: 0 })
const eur = (v: number | null | undefined) => (v == null ? NOT_SOLVED : fmtCurrency(v))

/** Fuel burnt and run hours per generator, next to the costs they drive (homer-parity 02h).
 *  Read-only; hidden when the run has no fuel-curve and no run-hour generator. */
export default function FuelRunHours({ cost }: { cost: Pick<CostBreakdown, 'fuel' | 'run_hours'> | null | undefined }) {
  const fuel = cost?.fuel ?? []
  const hours = cost?.run_hours ?? []
  if (fuel.length === 0 && hours.length === 0) return null
  const byGen = new Map<string, { carrier: string; fuel?: Fuel; hours?: Hours }>()
  for (const f of fuel) byGen.set(f.generator, { carrier: f.carrier, fuel: f })
  for (const h of hours) byGen.set(h.generator, { ...(byGen.get(h.generator) ?? { carrier: h.carrier }), hours: h })
  const rows = [...byGen.entries()].sort(([a], [b]) => a.localeCompare(b))
  const td = 'px-2 py-1 text-[11px] text-right font-mono'
  const th = 'px-2 py-1.5 text-[10px] font-semibold text-muted uppercase tracking-wide whitespace-nowrap text-right'
  return (
    <section data-testid="fuel-run-hours" className="mb-4">
      <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted mb-1">Fuel &amp; run hours</h3>
      <p className="text-[11px] text-muted mb-2">
        Fuel burnt and hours run per generator over the solved horizon. Their costs are already inside OPEX.
        The fuel curve and run-hour inputs are in the generator&apos;s form under Assets.{' '}
        <button type="button" data-testid="fuel-run-hours-inputs"
          onClick={() => withReturnFrom(() => openSectionInExpert('assets'))}
          className="text-accent hover:underline">Generator inputs ›</button>
      </p>
      <div className="overflow-auto border border-border rounded">
        <table className="w-full text-xs border-collapse" style={{ minWidth: 640 }}>
          <thead>
            <tr>
              <th className={`${th} text-left`}>Generator</th>
              <th className={th}>Fuel burnt (MWh)</th>
              <th className={th}>Fuel cost (EUR)</th>
              <th className={th}>Run hours (h)</th>
              <th className={th}>Run-hour O&amp;M (EUR)</th>
              <th className={th}>No-load fuel (MWh)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([name, r], i) => (
              <tr key={name} data-testid={`fuel-run-hours-row-${name}`} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                <td className="px-2 py-1 text-[11px]">{name} <span className="text-muted">{r.carrier}</span></td>
                <td className={td}>{r.fuel ? num(r.fuel.fuel_mwh) : '–'}</td>
                <td className={td}>{r.fuel ? eur(r.fuel.fuel_cost_eur) : '–'}</td>
                <td className={td}
                  title={r.hours ? (r.hours.counted_from === 'status' ? 'Hours counted while the unit was committed on' : 'Hours counted while the unit was producing') : undefined}>
                  {r.hours ? num(r.hours.hours_run) : '–'}
                </td>
                <td className={td}>{r.hours ? eur(r.hours.om_cost_eur) : '–'}</td>
                <td className={td}>{r.fuel ? num(r.fuel.no_load_fuel_mwh) : '–'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-muted mt-1">
        – means this generator has no fuel curve (fuel columns) or no run-hour inputs (hour columns).
      </p>
    </section>
  )
}

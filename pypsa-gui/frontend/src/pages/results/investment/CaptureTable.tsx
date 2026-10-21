// Results → Investment → Investment case: what each renewable plant and each
// battery captured from the market (IPP ticket 05). The figures are the asset
// results' own price metrics (capture price and rate per Generator; charge
// price, discharge capture price and spread per StorageUnit), read as served.
// A plant that produced nothing has no capture price: a dash with "no
// output", never 0 % or NaN.
import { useQueries, useQuery } from '@tanstack/react-query'
import { networkApi } from '../../../api/network'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { isRenewableCarrier } from '../../../utils/carriers'
import { assetResultsApi } from '../asset/api'
import type { AssetResultsResponse } from '../asset/types'

const GEN_METRICS = ['capture_price', 'capture_rate']
const SU_METRICS = ['charge_price', 'capture_price', 'captured_spread']

type Scalar = AssetResultsResponse['scalars'][string] | undefined

const fmtPrice = (v: number) => v.toFixed(2)
const fmtRate = (v: number) => `${(v * 100).toFixed(1)} %`

/** One metric cell: the value, per period when the backend splits it; a dash
 *  with why when there is none. */
function MetricCell({ res, id, fmt, failed }: {
  res: AssetResultsResponse | undefined; id: string; fmt: (v: number) => string; failed: boolean
}) {
  const v: Scalar = res?.scalars?.[id]
  const row = res?.metrics?.find(m => m.id === id)
  const shown = typeof v === 'number' && Number.isFinite(v) ? fmt(v)
    : v && typeof v === 'object'
      ? Object.entries(v).filter(([, x]) => typeof x === 'number' && Number.isFinite(x))
        .map(([p, x]) => `${p}: ${fmt(x as number)}`).join('; ')
      : ''
  if (shown) return <td className="text-right" data-established="true">{shown}</td>
  const why = failed ? 'not loaded'
    : row && row.status !== 'ok' ? (row.reason ?? 'not available') : 'no output'
  return <td className="text-right text-muted" data-established="false">— <span>{why}</span></td>
}

export default function CaptureTable() {
  const project = useUIStore(s => s.currentProject)
  const gens = useQuery({ queryKey: nk(project, 'generators'), queryFn: networkApi.getGenerators })
  const sus = useQuery({ queryKey: nk(project, 'storage_units'), queryFn: networkApi.getStorageUnits })
  const plants = (gens.data ?? []).filter(g => isRenewableCarrier(g.carrier)).map(g => g.name)
  const batteries = (sus.data ?? []).map(s => s.name)
  const asset = (componentClass: string, name: string, metrics: string[]) => ({
    queryKey: nk(project, 'results', 'asset', componentClass, name, 'prices', metrics.join(',')),
    queryFn: () => assetResultsApi.get({ componentClass, name, category: 'prices', metrics,
      source: 'lopf', fromIso: null, toIso: null, period: null, mode: 'chronological' }),
  })
  const plantRes = useQueries({ queries: plants.map(n => asset('Generator', n, GEN_METRICS)) })
  const batteryRes = useQueries({ queries: batteries.map(n => asset('StorageUnit', n, SU_METRICS)) })

  return (
    <section className="space-y-2" data-testid="ic-capture">
      <h3 className="text-[11px] font-semibold">Capture</h3>
      {gens.isSuccess && sus.isSuccess && !plants.length && !batteries.length && (
        <p className="text-muted">No renewable plant or battery in this network.</p>
      )}
      {plants.length > 0 && (
        <div className="overflow-x-auto">
          <table className="text-[11px] w-full" data-testid="ic-capture-plants">
            <thead><tr>
              <th scope="col" className="text-left">Plant</th>
              <th scope="col" className="text-right">Capture price (EUR/MWh)</th>
              <th scope="col" className="text-right">Capture rate (% of the average market price)</th>
            </tr></thead>
            <tbody>{plants.map((n, i) => (
              <tr key={n} data-testid={`ic-capture-plant-${n}`}>
                <th scope="row" className="text-left font-normal">{n}</th>
                <MetricCell res={plantRes[i]?.data} id="capture_price" fmt={fmtPrice}
                            failed={!!plantRes[i]?.isError} />
                <MetricCell res={plantRes[i]?.data} id="capture_rate" fmt={fmtRate}
                            failed={!!plantRes[i]?.isError} />
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {batteries.length > 0 && (
        <div className="overflow-x-auto">
          <table className="text-[11px] w-full" data-testid="ic-capture-batteries">
            <caption className="text-left text-muted">EUR/MWh</caption>
            <thead><tr>
              <th scope="col" className="text-left">Battery</th>
              <th scope="col" className="text-right">Average charge price</th>
              <th scope="col" className="text-right">Average discharge price</th>
              <th scope="col" className="text-right">Spread</th>
            </tr></thead>
            <tbody>{batteries.map((n, i) => (
              <tr key={n} data-testid={`ic-capture-battery-${n}`}>
                <th scope="row" className="text-left font-normal">{n}</th>
                {SU_METRICS.map(id => (
                  <MetricCell key={id} res={batteryRes[i]?.data} id={id} fmt={fmtPrice}
                              failed={!!batteryRes[i]?.isError} />
                ))}
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </section>
  )
}

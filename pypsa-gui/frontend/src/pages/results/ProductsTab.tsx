// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
// Results › Capacity expansion › Costs & prices › Products (guided-results 09, R8): what the site makes, in the
// product's own unit, how long its process units ran and, for hydrogen, what a kg cost. Every figure is READ from
// `GET /results/process_view` and `GET /results/lcoh` (the report's source); nothing here prices a product.
// Ammonia and other products show their output only: no cost per tonne is calculated anywhere yet.
import { useQuery } from '@tanstack/react-query'
import Kpi from '../../components/Kpi'
import { PageBody, PageSection } from '../../components/PageKit'
import { resultsApi } from '../../api/simulation'
import type { ProcessProduct, ProcessUnit } from '../../api/types'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { productionTiles } from './ProductionTiles'

const isHydrogen = (p: ProcessProduct) => /^(h2|hydrogen)$/i.test((p.carrier || p.bus).trim())

/** The average output over the modelled hours, in the unit the product is sold in: kg/h for hydrogen, t/d otherwise.
 *  A unit change of two read figures (tonnes made, hours modelled), not a new result. */
export function averageOutput(p: ProcessProduct, hours: number | null | undefined): { value: string; unit: string } | null {
  const t = p.production.t
  if (t == null || !Number.isFinite(t) || hours == null || !Number.isFinite(hours) || hours <= 0) return null
  return isHydrogen(p)
    ? { value: ((t * 1000) / hours).toFixed(1), unit: 'kg/h' }
    : { value: ((t * 24) / hours).toFixed(1), unit: 't/d' }
}

/** Share of the modelled hours the unit ran (read: running_hours, modelled_hours). */
export function runningShare(u: ProcessUnit): number | null {
  const { running_hours: r, modelled_hours: h } = u
  if (r == null || h == null || !Number.isFinite(r) || !Number.isFinite(h) || h <= 0) return null
  return (100 * r) / h
}

/** The hydrogen cost to two decimals, as the report and the Economics tab print it (a Kpi tile would round it again). */
function CostTile({ testId, kg }: { testId: string; kg: number }) {
  return (
    <div data-testid={testId} className="flex flex-col gap-0.5 rounded border border-border px-3 py-2">
      <span className="text-[11px] text-muted">Hydrogen cost</span>
      <span className="text-sm font-semibold text-text whitespace-nowrap">{kg.toFixed(2)} €/kg</span>
      <span className="text-[10px] text-muted">electrolysers only: annualised fixed cost + variable cost + electricity, per kg H₂ made; as in Economics and the report</span>
    </div>
  )
}

export default function ProductsTab() {
  const project = useUIStore(s => s.currentProject)
  const view = useQuery({
    queryKey: nk(project, 'results', 'process_view'), queryFn: resultsApi.getProcessView, retry: false,
  })
  const lcoh = useQuery({
    queryKey: nk(project, 'results', 'lcoh'), queryFn: resultsApi.getLcoh, retry: false,
  })
  const products = view.data?.products ?? []
  const units = view.data?.process_units ?? []
  const h2Kg = lcoh.data?.total?.lcoh_eur_per_kg_h2
  const hasH2Product = products.some(isHydrogen)

  if (view.isError) {
    return <PageBody><p data-testid="products-unavailable" className="text-xs text-muted">
      Products are not available: the process view could not be read for this solve.</p></PageBody>
  }
  if (view.isLoading) return <PageBody><p className="text-xs text-muted">Reading the products…</p></PageBody>
  if (!products.length && !units.length) {
    return <PageBody><p data-testid="products-empty" className="text-xs text-muted">This solve has no product the site makes.</p></PageBody>
  }
  return (
    <PageBody>
      <div data-testid="products-tab" className="flex flex-col gap-5">
        {products.map(p => {
          const mine = units.filter(u => u.product_bus === p.bus)
          const out = averageOutput(p, mine.find(u => u.modelled_hours != null)?.modelled_hours)
          const tiles = productionTiles(p)
          const made = tiles.find(t => t.id === 'made')
          return (
            <PageSection key={p.bus} title={p.bus} hint={p.carrier || 'product'}>
              <div className="grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(150px,1fr))]">
                {made && <Kpi testId={`products-${p.bus}-made`} label="Made" value={made.value} unit={made.unit}
                  state={made.state} reason={made.reason}
                  basis={p.production.value != null ? `${made.basis} · ${p.production.value.toLocaleString('en-US', { maximumFractionDigits: 0 })} ${p.production.unit}` : made.basis} />}
                {out
                  ? <Kpi testId={`products-${p.bus}-rate`} label="Average output" value={out.value} unit={out.unit} state="ready" basis="whole horizon" />
                  : <Kpi testId={`products-${p.bus}-rate`} label="Average output" value="" state="not_modelled"
                      reason="The solve has no process unit hours for this product." />}
                {mine.map(u => {
                  const share = runningShare(u)
                  return share != null
                    ? <Kpi key={u.name} testId={`products-${u.name}-running`} label={`${u.name} running`} value={share.toFixed(0)} unit="%"
                        state="ready" basis="of the modelled hours" />
                    : <Kpi key={u.name} testId={`products-${u.name}-running`} label={`${u.name} running`} value="" state="not_modelled"
                        reason="The solve recorded no finite running hours for this unit." />
                })}
                {isHydrogen(p) && (h2Kg != null && Number.isFinite(h2Kg)
                  ? <CostTile testId={`products-${p.bus}-cost`} kg={h2Kg} />
                  : <Kpi testId={`products-${p.bus}-cost`} label="Hydrogen cost" value="" state="not_modelled"
                      reason={lcoh.isError
                        ? 'Not available: the hydrogen cost could not be read for this solve.'
                        : 'The solve has no hydrogen produced by an electrolyser, so no cost per kg.'} />)}
              </div>
              {!isHydrogen(p) && (
                <p data-testid={`products-${p.bus}-no-cost`} className="mt-2 text-[11px] text-muted">
                  No cost per tonne is calculated for this product yet; the output above is what the solve made.
                </p>
              )}
            </PageSection>
          )
        })}
        {!hasH2Product && h2Kg != null && Number.isFinite(h2Kg) && (
          <PageSection title="Hydrogen cost" hint="Electrolyser fleet">
            <div className="grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(150px,1fr))]">
              <CostTile testId="products-fleet-cost" kg={h2Kg} />
            </div>
          </PageSection>
        )}
      </div>
    </PageBody>
  )
}

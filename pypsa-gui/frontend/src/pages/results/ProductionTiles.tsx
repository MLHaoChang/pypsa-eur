// The Guided "Production" tile group (grid-to-process ticket 07), fed by `GET /results/process_view`
// (ticket 04): per product bus, tonnes made and delivered, lost tonnes and EUR, and the closing inventory.
// A network with no product bus has no group: the component renders nothing, never a row of zeros.
// A figure the view could not compute shows a state tile with its reason, never 0.
import Kpi from '../../components/Kpi'
import type { ResultStateKind } from '../../components/ResultState'
import type { ProcessAmount, ProcessProduct, ProcessView } from '../../api/types'
import { fmtCurrency, fmtTonnage } from '../../utils/format'
import { money } from '../../utils/money'
import { openComponentField } from './openPanel'

const REASON: Record<string, string> = {
  tonne_factor_missing: 'The product bus has no “Energy per tonne”, so its amounts cannot be shown in tonnes.',
  non_finite_values: 'The solve gave no finite value for this figure.',
  no_shortfall_slack: 'No shortfall is modelled for this product.',
  lost_production_value_missing: 'The shortfall has no price, so lost EUR is not known.',
  buy_in_dispatch_missing: 'The solve recorded no flow for a buy-in generator, so the amount bought in is not known.',
}
const reasonFor = (flag: string | null | undefined) => (flag && REASON[flag]) || 'Not available for this solve.'

export interface ProductionTile {
  id: string
  label: string
  value: string
  unit?: string
  state: ResultStateKind
  reason?: string
  basis?: string
  /** The product bus to open to set the missing factor. */
  fixBus?: string
}

function tonnes(id: string, label: string, a: ProcessAmount, basis: string, bus: string): ProductionTile {
  if (a.t == null) {
    const flag = a.t_flag ?? a.flag
    return { id, label, value: '', state: 'not_modelled', reason: reasonFor(flag), fixBus: flag === 'tonne_factor_missing' ? bus : undefined }
  }
  return { id, label, value: fmtTonnage(a.t), state: 'ready', basis }
}

/** The closing inventory (last snapshot, summed over the bus's stores) in t. */
function closingInventory(p: ProcessProduct): number | null {
  if (!p.inventory.length || p.t_per_unit == null) return null
  let sum = 0
  for (const s of p.inventory) {
    const last = s.e.length ? s.e[s.e.length - 1] : null
    if (last == null) return null
    sum += last
  }
  return sum * p.t_per_unit
}

export function productionTiles(p: ProcessProduct): ProductionTile[] {
  const lostEur: ProductionTile = p.lost.eur != null
    ? { id: 'lost-eur', label: 'Lost production value', value: fmtCurrency(p.lost.eur), state: 'ready', basis: 'at the shortfall price' }
    : { id: 'lost-eur', label: 'Lost production value', value: '', state: 'not_modelled', reason: reasonFor(p.lost.eur_flag) }
  const inv = closingInventory(p)
  const inventory: ProductionTile = inv != null
    ? { id: 'inventory', label: 'Inventory at the end', value: fmtTonnage(inv), state: 'ready', basis: 'closing level' }
    : { id: 'inventory', label: 'Inventory at the end', value: '',
        state: p.inventory.length ? 'not_modelled' : 'not_applicable',
        reason: p.inventory.length ? reasonFor(p.t_per_unit == null ? 'tonne_factor_missing' : 'non_finite_values')
          : 'No store sits on this product bus.',
        fixBus: p.inventory.length && p.t_per_unit == null ? p.bus : undefined }
  // Control-strategy walkthrough issue 10: the product bought in, beside Made and Delivered. Hidden only when
  // the bus has no buy-in generator (`carriers` empty); a real zero shows as one. No balance is claimed (GK A B5).
  // A buy-in recognised only by its generator's name says so (role_from_name).
  const buyBasis = ['whole horizon',
    p.buy_in.eur != null && Number.isFinite(p.buy_in.eur) ? `${money(p.buy_in.eur, 'EUR')} at the buy-in price` : null,
    p.buy_in.role_flag === 'role_from_name' ? 'buy-in assumed from its name' : null,
  ].filter(Boolean).join(' · ')
  const boughtIn: ProductionTile[] = p.buy_in.carriers?.length === 0 ? [] : [{
    ...tonnes('bought-in', 'Bought in', p.buy_in, buyBasis, p.bus),
  }]
  return [
    tonnes('made', 'Made', p.production, 'whole horizon', p.bus),
    ...boughtIn,
    tonnes('delivered', 'Delivered', p.delivery, 'whole horizon', p.bus),
    tonnes('lost', 'Lost', p.lost, 'shortfall, whole horizon', p.bus),
    lostEur,
    inventory,
  ]
}

/** "NH3" → "NH₃": a product name with its digits set as subscripts, as the templates write it. */
function productName(carrier: string): string {
  const SUB = '₀₁₂₃₄₅₆₇₈₉'
  return carrier.replace(/([A-Za-z)])(\d+)/g, (_, a: string, d: string) => a + [...d].map(c => SUB[Number(c)]).join(''))
}

/** The Key-capacities names for the buy-in and shortfall generators' carriers, in the tiles' words
 *  (control-strategy walkthrough issue 10): "NH₃ bought in", "NH₃ lost (shortfall)". */
export function processCarrierLabels(view: ProcessView | null | undefined): Map<string, string> {
  const out = new Map<string, string>()
  for (const p of view?.products ?? []) {
    const name = productName(p.carrier || p.bus)
    for (const c of p.buy_in.carriers ?? []) out.set(c, `${name} bought in`)
    for (const c of p.lost.carriers ?? []) out.set(c, `${name} lost (shortfall)`)
  }
  return out
}

export default function ProductionTiles({ view }: { view: ProcessView | null | undefined }) {
  if (!view || !view.products.length) return null
  return (
    <section data-testid="summary-production" className="flex flex-col gap-2">
      <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted">Production</h3>
      {view.products.map(p => (
        <div key={p.bus} data-testid={`production-${p.bus}`} className="flex flex-col gap-1.5">
          {view.products.length > 1 && <div className="text-[11px] text-muted">{p.bus}</div>}
          <div className="grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(150px,1fr))]">
            {productionTiles(p).map(t => (
              // A missing tonne factor is said once for the product, under the tiles it affects.
              <Kpi key={t.id} testId={`production-${p.bus}-${t.id}`} label={t.label} value={t.value} unit={t.unit}
                state={t.state} reason={t.fixBus ? undefined : t.reason} basis={t.basis} />
            ))}
          </div>
          {productionTiles(p).some(t => t.id === 'bought-in') && (
            // The shortfall is supplied to the bus too, so Lost is part of Delivered, not a loss beside it
            // (control-strategy 10, UX review).
            <p data-testid={`production-${p.bus}-sum`} className="text-[11px] text-muted">
              Delivered counts what fell short (Lost) too: the shortfall is supplied to the product at its price.
            </p>
          )}
          {productionTiles(p).some(t => t.fixBus) && (
            <p data-testid={`production-${p.bus}-fix`} className="flex flex-wrap items-center gap-x-2 text-[11.5px] text-muted">
              {REASON.tonne_factor_missing}
              <button type="button" onClick={() => openComponentField({ section: 'carriers', panel: null, component: 'Bus', entity: p.bus, field: 'mwh_per_t' })}
                className="text-accent hover:underline">Set it on {p.bus} ›</button>
            </p>
          )}
        </div>
      ))}
    </section>
  )
}

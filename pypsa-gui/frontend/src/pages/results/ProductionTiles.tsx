// The Guided "Production" tile group (grid-to-process ticket 07), fed by `GET /results/process_view`
// (ticket 04): per product bus, tonnes made and delivered, lost tonnes and EUR, and the closing inventory.
// A network with no product bus has no group: the component renders nothing, never a row of zeros.
// A figure the view could not compute shows a state tile with its reason, never 0.
import Kpi from '../../components/Kpi'
import type { ResultStateKind } from '../../components/ResultState'
import type { ProcessAmount, ProcessProduct, ProcessView } from '../../api/types'
import { fmtCurrency, fmtTonnage } from '../../utils/format'
import { useUIStore } from '../../store/uiStore'

const REASON: Record<string, string> = {
  tonne_factor_missing: 'The product bus has no “Energy per tonne”, so its amounts cannot be shown in tonnes.',
  non_finite_values: 'The solve gave no finite value for this figure.',
  no_shortfall_slack: 'No shortfall is modelled for this product.',
  lost_production_value_missing: 'The shortfall has no price, so lost EUR is not known.',
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
  return [
    tonnes('made', 'Made', p.production, 'whole horizon', p.bus),
    tonnes('delivered', 'Delivered', p.delivery, 'whole horizon', p.bus),
    tonnes('lost', 'Lost', p.lost, 'shortfall, whole horizon', p.bus),
    lostEur,
    inventory,
  ]
}

export default function ProductionTiles({ view }: { view: ProcessView | null | undefined }) {
  const setSelectedComponent = useUIStore(s => s.setSelectedComponent)
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
          {productionTiles(p).some(t => t.fixBus) && (
            <p data-testid={`production-${p.bus}-fix`} className="flex flex-wrap items-center gap-x-2 text-[11.5px] text-muted">
              {REASON.tonne_factor_missing}
              <button type="button" onClick={() => setSelectedComponent({ type: 'Bus', name: p.bus })}
                className="text-accent hover:underline">Set it on {p.bus} ›</button>
            </p>
          )}
        </div>
      ))}
    </section>
  )
}

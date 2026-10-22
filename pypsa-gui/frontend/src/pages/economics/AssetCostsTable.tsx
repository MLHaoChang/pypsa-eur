// Economics in Expert, Asset costs tab (plan 2026-10-14-economics-in-expert.md):
// what every investable asset costs, from `GET /network/asset_costs` (the
// same `upfront_parts` the LP and the finance engine read). Read-only: a row
// opens the asset's card, where its investment is edited, so each field has
// one editor. Money per MW or MWh is shown per kW or kWh, as on the cards;
// branch money stays per MVA (the units catalogues publish).
import { useQuery } from '@tanstack/react-query'
import { assetSchemaApi, type AssetCostRow } from '../../api/assetSchema'
import { ProvenanceChip } from '../../components/ProvenanceChip'
import { useGlobalDefaultLifetime } from '../../layout/properties/cardKit'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'

const DISPLAY: Record<AssetCostRow['basis'], { unit: string; scale: number }> = {
  'EUR/MW': { unit: '€/kW', scale: 1e-3 },
  'EUR/MWh': { unit: '€/kWh', scale: 1e-3 },
  'EUR/MVA': { unit: '€/MVA', scale: 1 },
}
const OVERNIGHT_FIELDS = ['overnight_cost', 'inv_power_overnight', 'inv_energy_overnight', 'inv_per_km_overnight']

function money(v: number | null, scale: number): string {
  return v == null ? '—' : (v * scale).toLocaleString(undefined, { maximumFractionDigits: v * scale >= 100 ? 0 : 2 })
}

function Row({ a, onOpen, defaultLife }: { a: AssetCostRow; onOpen: () => void; defaultLife?: number }) {
  const d = DISPLAY[a.basis]
  const priced = a.parts.length > 0
  // A part with no lifetime of its own inherits the project default.
  const life = [...new Set(a.parts.map(p => p.lifetime != null ? `${p.lifetime} y`
    : defaultLife != null ? `${defaultLife} y (default)` : 'default'))].join(' / ')
  const stamp = OVERNIGHT_FIELDS.map(k => a.provenance[k]).find(Boolean)
  return (
    <tr className="border-b border-border/50 hover:bg-panel cursor-pointer" onClick={onOpen}
      data-testid={`asset-cost-${a.component}-${a.name}`}>
      <td className="px-2 py-1 text-xs">
        <span className="text-muted">{a.component} · </span>{a.name}
        {a.carrier && <span className="text-muted"> ({a.carrier})</span>}
      </td>
      <td className="px-2 py-1 text-xs font-mono">
        {!priced ? <span className="text-muted">not priced</span> : a.parts.length === 1 && a.parts[0].name === 'investment'
          ? `${money(a.parts[0].upfront_per_unit, d.scale)} ${d.unit}`
          : a.parts.map(p => `${p.name} ${money(p.upfront_per_unit, d.scale)}`).join(' + ') + ` ${d.unit}`}
        {a.parts.some(p => p.derived_from_capital_cost) && (
          <span className="ml-1 text-[9px] text-warning" title="Back-calculated from a typed annual capital cost at the global discount rate">
            from capital cost
          </span>
        )}
        <ProvenanceChip entry={stamp} testId={`asset-cost-prov-${a.component}-${a.name}`} />
      </td>
      <td className="px-2 py-1 text-xs font-mono">{priced ? life : '—'}</td>
      <td className="px-2 py-1 text-xs font-mono">{priced ? `${money(a.capital_cost, d.scale)} ${d.unit}/yr` : '—'}</td>
      <td className="px-2 py-1 text-xs font-mono">{a.fom_cost ? `${money(a.fom_cost, d.scale)} ${d.unit}/yr` : '—'}</td>
    </tr>
  )
}

export default function AssetCostsTable() {
  const project = useUIStore(s => s.currentProject)
  const setSlidePanel = useUIStore(s => s.setSlidePanel)
  const setSelectedComponent = useUIStore(s => s.setSelectedComponent)
  const defaultLife = useGlobalDefaultLifetime()
  const { data, isLoading, isError } = useQuery({
    queryKey: nk(project, 'assetCosts'), queryFn: assetSchemaApi.costs, enabled: !!project, staleTime: 0,
  })
  if (isLoading) return <p className="p-3 text-xs text-muted">Loading…</p>
  if (isError || !data) return <p className="p-3 text-xs text-danger">Could not load the asset costs.</p>
  if (!data.assets.length) return <p className="p-3 text-xs text-muted">No generators, links, storage, stores, lines or transformers yet.</p>
  const unpriced = data.assets.filter(a => !a.parts.length).length
  return (
    <div className="p-3 flex flex-col gap-2">
      <p className="text-[10px] text-muted">
        Overnight cost per unit of capacity and the annualised capital cost the model charges. Open a row to edit
        the asset on its card. {unpriced > 0 && `${unpriced} not priced: the model builds them for free.`}
      </p>
      <table className="w-full border border-border rounded">
        <thead>
          <tr className="text-[10px] uppercase tracking-wide text-muted border-b border-border bg-panel">
            <th className="px-2 py-1 text-left">Asset</th>
            <th className="px-2 py-1 text-left">Overnight cost</th>
            <th className="px-2 py-1 text-left">Lifetime</th>
            <th className="px-2 py-1 text-left">Capital cost (annualised)</th>
            <th className="px-2 py-1 text-left">Fixed O&amp;M</th>
          </tr>
        </thead>
        <tbody>
          {data.assets.map(a => (
            <Row key={`${a.component}:${a.name}`} a={a} defaultLife={defaultLife} onOpen={() => {
              setSlidePanel(null)
              setSelectedComponent({ type: a.component, name: a.name })
            }} />
          ))}
        </tbody>
      </table>
    </div>
  )
}

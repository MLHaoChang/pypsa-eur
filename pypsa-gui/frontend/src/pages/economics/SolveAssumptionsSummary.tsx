// Economics in Expert, Basis tab: the two solve assumptions that price an asset
// or a tonne of CO₂ and are edited in Solver Settings (Economic assumptions).
// Read-only here, so each value has one editor; the links open that block.
import { useQuery } from '@tanstack/react-query'
import { simulationApi } from '../../api/simulation'
import { openEconomicAssumptions } from '../../layout/flowNavigation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'

function Line({ label, value, note, testId }: { label: string; value: string; note?: string; testId: string }) {
  return (
    <div className="flex flex-col gap-0.5" data-testid={testId}>
      <span className="text-[10px] text-muted">{label}</span>
      <span className="text-xs font-mono text-text">{value}</span>
      {note && <span className="text-[10px] text-muted">{note}</span>}
      <button type="button" className="text-[10px] underline text-left" onClick={openEconomicAssumptions}
        title="Opens Solver Settings in Expert">
        Edit in Scenarios &amp; optimisation ›
      </button>
    </div>
  )
}

export default function SolveAssumptionsSummary() {
  const project = useUIStore(s => s.currentProject)
  const { data: cfg } = useQuery({
    queryKey: nk(project, 'solverConfig'), queryFn: simulationApi.getSolverConfig, enabled: !!project,
  })
  if (!cfg) return null
  const overrides = Object.keys(cfg.co2_price_per_period ?? {}).length
  return (
    <section className="border border-border rounded" data-testid="economics-solve-assumptions">
      <div className="px-3 py-1.5 border-b border-border bg-panel text-[10px] font-semibold uppercase tracking-wide text-muted">
        Solve assumptions
      </div>
      <div className="grid grid-cols-2 gap-3 p-3 max-w-md">
        <Line testId="economics-default-lifetime" label="Default lifetime"
          value={`${cfg.default_lifetime ?? 25} years`}
          note="Used by every asset with no lifetime of its own." />
        <Line testId="economics-co2-price" label="CO₂ price"
          value={`${cfg.co2_price ?? 0} €/tCO₂`}
          note={`Per-period overrides: ${overrides}`} />
      </div>
    </section>
  )
}

// Economics in Expert, Basis tab (plan 2026-10-14-economics-in-expert.md):
// the discount and inflation rates. They live in the solver config, which
// Solver Settings also edits; this form reads and writes the same query
// (`nk(project, 'solverConfig')`), so both always show the same value, and
// it sends only these two fields (the route merges a partial config).
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { simulationApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'

function pct(v: number | null | undefined): string {
  return v == null || Number.isNaN(v) ? '' : String(Number((v * 100).toFixed(4)))
}

export default function RatesForm() {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const { data: cfg } = useQuery({ queryKey: nk(project, 'solverConfig'), queryFn: simulationApi.getSolverConfig })
  const [rate, setRate] = useState('')
  const [inflation, setInflation] = useState('')
  useEffect(() => {
    setRate(pct(cfg?.discount_rate))
    setInflation(pct(cfg?.inflation_rate))
  }, [cfg?.discount_rate, cfg?.inflation_rate])

  const save = useMutation({
    mutationFn: () => simulationApi.updateSolverConfig({
      discount_rate: parseFloat(rate) / 100, inflation_rate: inflation === '' ? 0 : parseFloat(inflation) / 100,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: nk(project, 'solverConfig') })
      qc.invalidateQueries({ queryKey: nk(project, 'assetCosts') })
    },
  })
  const invalid = rate === '' || !Number.isFinite(parseFloat(rate)) || (inflation !== '' && !Number.isFinite(parseFloat(inflation)))
  const dirty = rate !== pct(cfg?.discount_rate) || inflation !== pct(cfg?.inflation_rate)

  return (
    <section className="border border-border rounded" data-testid="economics-rates">
      <div className="px-3 py-1.5 border-b border-border bg-panel text-[10px] font-semibold uppercase tracking-wide text-muted">
        Rates
      </div>
      <div className="grid grid-cols-2 gap-3 p-3 max-w-md">
        <label className="flex flex-col gap-0.5 text-[10px] text-muted">
          Discount rate (%)
          <input type="number" step="0.1" value={rate} onChange={e => setRate(e.target.value)} aria-label="Discount rate (%)"
            className="bg-bg border border-border rounded px-1.5 py-0.5 text-xs font-mono text-text" />
          <span>Annualises each overnight cost over its lifetime with the annuity factor r(1+r)^L / ((1+r)^L−1); an asset's own rate overrides it. Typical: 5–10 %. A nominal rate needs the inflation rate beside it.</span>
        </label>
        <label className="flex flex-col gap-0.5 text-[10px] text-muted">
          Inflation rate (%)
          <input type="number" step="0.1" value={inflation} onChange={e => setInflation(e.target.value)} aria-label="Inflation rate (%)"
            className="bg-bg border border-border rounded px-1.5 py-0.5 text-xs font-mono text-text" />
          <span>With a nominal discount rate, the real rate is (1+nominal)/(1+inflation)−1 (the Fisher relation), used for the cross-period present value. Leave 0 when costs are already in real euros. Typical: 1.5–3 %.</span>
        </label>
      </div>
      <div className="px-3 pb-3 flex items-center gap-2">
        <button type="button" onClick={() => save.mutate()} disabled={!dirty || invalid || save.isPending}
          className="text-xs px-2 py-1 rounded bg-accent text-white disabled:opacity-40">Save rates</button>
        {save.isError && <span className="text-[10px] text-danger">Could not save the rates.</span>}
      </div>
    </section>
  )
}

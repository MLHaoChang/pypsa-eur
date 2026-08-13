// The "Investment years" step: the year chips, the add-year input, and the
// no-years warning. No Advanced slot — nothing here needs hiding.
//
// Presentational only: `applyPeriods` (the mutation) stays owned by
// ModelHorizon.tsx. This file owns the ADD button's range validation
// (1900–2200) and the immediate, not-gated-on-success reset of the
// `newPeriod` text field — exactly what the original inline `addPeriod`
// function did, just split so the "what year, validated" half lives here and
// the "compute the next full periods array and mutate" half (`onAddPeriod`)
// stays with the shell, same division StepWeights.tsx uses for its bulk-apply
// control. `onRemovePeriod` needs no validation, so it's passed straight
// through unchanged.
import toast from 'react-hot-toast'
import { Plus, X } from 'lucide-react'

export interface StepYearsProps {
  periods: number[]
  onRemovePeriod: (year: number) => void
  newPeriod: string
  onNewPeriodChange: (value: string) => void
  /** Called with the validated year once the user commits (button click or
   * Enter) — the shell computes the deduped/sorted next array and mutates. */
  onAddPeriod: (year: number) => void
  addPeriodPending: boolean
}

export function StepYears({
  periods, onRemovePeriod, newPeriod, onNewPeriodChange, onAddPeriod, addPeriodPending,
}: StepYearsProps) {
  const commitAdd = () => {
    const y = parseInt(newPeriod, 10)
    if (!Number.isFinite(y) || y < 1900 || y > 2200) {
      toast.error('Year must be between 1900 and 2200')
      return
    }
    onAddPeriod(y)
    onNewPeriodChange('')
  }

  return (
    <section>
      <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2.5">Investment years</h3>
      <div className="border border-border rounded mb-3">
        <div className="p-2.5">
          <p className="text-[11px] text-muted mb-2 leading-relaxed">
            Each year becomes one optimisation period. Capacity decisions are
            made independently per period; assets must have <code>build_year</code>
            ≤ period to be available, and are retired at
            <code> build_year + lifetime</code>.
          </p>
          <div className="flex flex-wrap items-center gap-1">
            {periods.map(y => (
              <span
                key={y}
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-border bg-bg text-[11px] font-mono"
              >
                {y}
                <button
                  onClick={() => onRemovePeriod(y)}
                  className="text-muted hover:text-danger"
                  title={`Remove ${y}`}
                ><X size={10} /></button>
              </span>
            ))}
            <input
              type="number"
              value={newPeriod}
              onChange={e => onNewPeriodChange(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') commitAdd() }}
              placeholder="add year"
              className="w-20 px-1.5 py-0.5 border border-border rounded text-[11px] font-mono bg-bg"
            />
            <button
              onClick={commitAdd}
              disabled={!newPeriod.trim() || addPeriodPending}
              className="px-1.5 py-0.5 border border-border rounded text-[11px] text-accent hover:border-accent disabled:opacity-40"
            ><Plus size={10} /></button>
          </div>
          {periods.length === 0 && (
            <p className="text-[10px] text-warn mt-2">
              No years yet — add at least one before solving.
            </p>
          )}
        </div>
      </div>
    </section>
  )
}

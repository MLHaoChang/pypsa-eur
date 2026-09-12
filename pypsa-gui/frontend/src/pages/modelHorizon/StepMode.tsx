// The "Mode" step: the single multi-investment-periods decision point and
// its explanatory copy. No Advanced slot — there's nothing here to hide.
//
// Presentational only: `toggleMultiPeriod` (the mutation, including its
// snapshot-index promote/demote cascade) stays owned by ModelHorizon.tsx.
// `disabled` folds together the two conditions the original inline checkbox
// used (`!cfg || toggleMultiPeriod.isPending` — solver config not loaded yet,
// or a toggle already in flight) so this file doesn't need to know about the
// `SolverConfig` shape at all.

export interface StepModeProps {
  isMultiPeriod: boolean
  disabled: boolean
  onToggle: (enabled: boolean) => void
}

export function StepMode({ isMultiPeriod, disabled, onToggle }: StepModeProps) {
  return (
    <section className="border border-border rounded-[10px] bg-bg p-3.5 shadow-[0_1px_0_rgba(10,14,20,0.04)]">
      <label className="flex items-start gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={isMultiPeriod}
          disabled={disabled}
          onChange={e => onToggle(e.target.checked)}
          className="accent-accent mt-0.5"
        />
        <div>
          <div className="font-semibold text-text">Multi-investment periods</div>
          <p className="text-[11px] text-muted mt-0.5 leading-relaxed">
            Enables PyPSA's multi-horizon LP. The optimiser sizes new capacity
            for several investment years (e.g. <code>2030/2040/2050</code>),
            each with its own operational profile. Leave OFF for a single
            operational range with capacity decided once.
          </p>
        </div>
      </label>
    </section>
  )
}

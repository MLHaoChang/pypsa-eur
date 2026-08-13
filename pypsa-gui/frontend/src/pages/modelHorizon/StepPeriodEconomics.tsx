// The "Economics" step: the period table's default columns (period, years,
// objective, PV preview) plus, behind Advanced, the per-carrier load-scaler
// columns and the CAPEX budget column.
//
// This is a COLUMN split of one table, not a section split. The naive
// version of "put the rest behind StepShell's `advanced` slot" — like
// StepWeightsAdvanced does — would render the extra columns in a SECOND
// `<table>` physically relocated below a `<details>`, independent of the
// first: two scrollbars, two header rows, and nothing keeping a period's row
// in table A vertically aligned with its row in table B once either scrolls
// or a row's height differs (long carrier list wraps a header, a validation
// message changes a row's height, etc.). That's worse than today's single
// wide table, not better, so this file does NOT use StepShell's `advanced`
// prop — ModelHorizon.tsx passes nothing into it for the 'economics' step,
// same as it already does for 'mode', 'years', and now 'window'.
//
// Instead there is exactly one `<table>`. A local `advancedOpen` boolean
// (owned here, not by StepShell) conditionally renders the extra `<th>`/`<td>`
// INSIDE the same header row and each same body row, so the columns that
// exist are always the same columns in the same order in both thead and
// tbody — coherence is structural, not just visual. A `<details>` styled
// exactly like StepShell's own Advanced disclosure sits under the table,
// reusing the same "Advanced" affordance/interaction language the rest of
// this page already uses; its content is the (unmodified, verbatim) note
// explaining every column, default and advanced alike, and its `open` state
// IS `advancedOpen` — opening the note and revealing the columns are the same
// action, not two things that happen to be wired together.
//
// The extra cells are conditionally RENDERED (not present in the DOM at all
// when collapsed), not conditionally CSS-hidden while staying mounted — i.e.
// this deliberately does not take the "always mounted, native-hidden" shape
// StepShell's default (`unmountAdvancedWhenCollapsed=false`) uses for
// StepWindow's per-period table. That default exists because a closed native
// `<details>` is still reachable by Chrome's find-in-page (it searches inside
// and auto-expands on a match) — an unmounted subtree isn't. That benefit
// doesn't transfer to individual hidden `<td>`/`<th>` inside a live `<tr>`:
// there's no way to wrap a `<details>` around table cells without breaking
// the table's content model (the browser foster-parents it out), and the only
// attribute-level alternative (a `hidden` attribute per cell) is excluded
// from find-in-page exactly like an unmounted node is, so it buys none of
// the reachability the "stay mounted" convention is for. With no benefit to
// preserve, plain conditional rendering is the simpler and more honest
// choice — the DOM says exactly what's visible, which is also what this
// step's RED test (asserting the Budget column's absence, not just its
// invisibility) needs to be able to check directly.
//
// Presentational only: `applyBulkPeriodYears`, `applyBulkPeriodObjective`,
// `updateAutoDiscount`, `updateOnePeriodCol`, `updateLoadScalersByCarrier`,
// `updateCapexBudget` (all mutations) stay owned by ModelHorizon.tsx. Per-cell
// validation and the revert-on-invalid-blur behaviour stay here, same split
// StepWeightsAdvanced's per-row objective editor already uses. Building the
// wholesale PUT body for the per-carrier scaler map and the CAPEX budget map
// (cloning the existing map, deciding when an entry is deleted vs written)
// stays with the shell, which owns those maps' shape — this file only reads
// them for display and hands back semantic (carrier, period, value) / (period,
// value) callbacks, mirroring how `updateOneWeight` is composed in
// ModelHorizon.tsx today.
import { useState, type ReactNode } from 'react'
import toast from 'react-hot-toast'
import { pvFactor } from '../modelHorizonModel'

export interface StepPeriodEconomicsProps {
  periods: number[]
  periodWeightings: Array<Record<string, number | string>>
  /** Shown instead of the table when there are zero investment years yet. */
  noPeriodsFallback: ReactNode
  refPeriod: number
  /** Whether Auto-discount will ACTUALLY write anything at solve time — the
   * PV column's colour/tooltip gate, already resolved by the shell. */
  autoDiscountOn: boolean
  discountRate: number
  inflationRate: number

  bulkYears: string
  onBulkYearsChange: (value: string) => void
  onApplyBulkYears: (value: number) => void
  applyBulkYearsPending: boolean

  bulkObjective: string
  onBulkObjectiveChange: (value: string) => void
  onApplyBulkObjective: (value: number) => void
  applyBulkObjectivePending: boolean

  /** The Auto-discount checkbox's own checked state (`cfg.auto_discount_periods`
   * — distinct from `autoDiscountOn`, which also requires multi-period mode,
   * MultiIndex snapshots, and at least one period). */
  autoDiscountChecked: boolean
  onAutoDiscountChange: (enabled: boolean) => void

  onPeriodColChange: (args: { period: number; col: 'years' | 'objective'; value: number }) => void
  updatePeriodColPending: boolean

  /** Carriers present in (or pre-configured for) the network, already
   * canonicalised, sorted, and labelled by the shell. */
  loadCarriers: Array<{ key: string; label: string }>
  loadScalersByCarrier: Record<string, Record<string, number>>
  /** Legacy per-period scaler applied to every carrier when there's no
   * per-carrier entry (`cfg.load_scalers`). */
  legacyLoadScalers: Record<string, number>
  onLoadScalerChange: (carrier: string, period: number, value: number) => void
  loadScalerPending: boolean

  /** `cfg.capex_budget_per_period`, in EUR. */
  capexBudgetPerPeriod: Record<string, number>
  /** `null` means "clear this period's budget (unconstrained)". */
  onCapexBudgetChange: (period: number, valueEur: number | null) => void
  capexBudgetPending: boolean
}

export function StepPeriodEconomics({
  periods, periodWeightings, noPeriodsFallback, refPeriod, autoDiscountOn, discountRate, inflationRate,
  bulkYears, onBulkYearsChange, onApplyBulkYears, applyBulkYearsPending,
  bulkObjective, onBulkObjectiveChange, onApplyBulkObjective, applyBulkObjectivePending,
  autoDiscountChecked, onAutoDiscountChange,
  onPeriodColChange, updatePeriodColPending,
  loadCarriers, loadScalersByCarrier, legacyLoadScalers, onLoadScalerChange, loadScalerPending,
  capexBudgetPerPeriod, onCapexBudgetChange, capexBudgetPending,
}: StepPeriodEconomicsProps) {
  const [advancedOpen, setAdvancedOpen] = useState(false)

  if (periods.length === 0) {
    return (
      <section>
        <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2.5">Economics</h3>
        {noPeriodsFallback}
      </section>
    )
  }

  return (
    <section>
      <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2.5">Economics</h3>
      <div className="border border-border rounded mb-3">
        <div className="px-2.5 py-1.5 border-b border-border bg-bg-2 text-[9px] font-bold uppercase tracking-[0.14em] text-muted flex items-center justify-between">
          <span>Period weightings</span>
          <span className="text-[10px] text-muted/70 normal-case">years × objective per period</span>
        </div>
        <div className="p-2.5 flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <input
              type="number"
              step="0.1"
              min={0}
              placeholder="Apply years to all (e.g. 10)"
              value={bulkYears}
              onChange={e => onBulkYearsChange(e.target.value)}
              className="flex-1 px-2 py-1 border border-border rounded text-[11px] bg-bg font-mono"
            />
            <button
              disabled={!bulkYears || applyBulkYearsPending}
              onClick={() => {
                const v = parseFloat(bulkYears)
                if (!Number.isFinite(v) || v < 0) {
                  toast.error('Enter a non-negative number')
                  return
                }
                onApplyBulkYears(v)
                onBulkYearsChange('')
              }}
              className="px-2 py-1 bg-accent/80 text-white rounded text-[11px] font-medium hover:bg-accent disabled:opacity-40"
            >Apply years</button>
          </div>
          <div className="flex items-center gap-2">
            <input
              type="number"
              step="0.01"
              min={0}
              placeholder="Apply objective weight to all (e.g. 0.6)"
              value={bulkObjective}
              onChange={e => onBulkObjectiveChange(e.target.value)}
              className="flex-1 px-2 py-1 border border-border rounded text-[11px] bg-bg font-mono"
            />
            <button
              disabled={!bulkObjective || applyBulkObjectivePending}
              onClick={() => {
                const v = parseFloat(bulkObjective)
                if (!Number.isFinite(v) || v < 0) {
                  toast.error('Enter a non-negative number')
                  return
                }
                onApplyBulkObjective(v)
                onBulkObjectiveChange('')
              }}
              className="px-2 py-1 bg-accent/80 text-white rounded text-[11px] font-medium hover:bg-accent disabled:opacity-40"
            >Apply objective</button>
          </div>

          {/* Auto-discount: the ONLY automated path to ipw.objective. Writes
              PV × years per period at LP build time using the solver
              settings' discount_rate and inflation_rate, and reverts in
              restore() so the on-disk network keeps whatever the user typed.
              The PV column in the table below previews exactly what it will
              write. Manual edits to the objective cell still work and are
              what to use for one-off factors. */}
          <label className="flex items-center gap-2 mt-1 pt-2 border-t border-border/60 text-[10px] text-muted">
            <input
              type="checkbox"
              checked={autoDiscountChecked}
              onChange={e => onAutoDiscountChange(e.target.checked)}
              className="cursor-pointer"
            />
            <span className="select-none">
              Auto-discount: overwrite <code>objective</code> per period at solve time using
              <code> discount_rate</code> from solver settings. Stops the LP from front-loading
              all CAPEX into period 1.
            </span>
          </label>

          {/* Per-period inline editor. Default view: period / years /
              objective / PV preview. Advanced adds one column per load
              carrier plus CAPEX budget — see the file header for why that's
              a conditional-render column split on this ONE table rather than
              a second table behind StepShell's `advanced` slot. */}
          <div className="border border-border rounded overflow-auto max-h-64 mt-1">
            <table
              className="w-full text-[11px] border-collapse"
              style={{ minWidth: advancedOpen ? 380 + (loadCarriers.length + 1) * 100 : 380 }}
            >
              <thead className="sticky top-0 bg-bg-2 z-10">
                <tr className="border-b border-border">
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Period</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Years</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Objective</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase"
                      title="What Auto-discount will write into `objective` at solve time: (1+real rate)^-(period-first period) x years. Preview only — nothing is written until you solve.">
                    PV ×<br />preview
                  </th>
                  {advancedOpen && loadCarriers.map(({ key, label }) => (
                    <th key={`hdr-load-${key}`}
                        className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase"
                        title={`Per-period load multiplier for carrier "${key}". 1.00 = unchanged, 1.10 = +10% growth. Applied to every load whose carrier canonicalises to "${key}".`}>
                      Load ×<br />{label}
                    </th>
                  ))}
                  {advancedOpen && (
                    <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase"
                        title="Per-period upper bound on overnight CAPEX (€). LP enforces Σ overnight_cost × Δp_nom ≤ budget for all extendable assets with build_year=P. Leave 0 (or empty) for unconstrained.">Budget M€</th>
                  )}
                </tr>
              </thead>
              <tbody>
                {periodWeightings.map((row, i) => {
                  const period = Number(row.period ?? row.name ?? periods[i] ?? 0)
                  const years = Number(row.years ?? 1)
                  const objective = Number(row.objective ?? 1)
                  // Resolve scaler per carrier: prefer the new
                  // `load_scalers_by_carrier[carrier][period]`, fall back to
                  // legacy `load_scalers[period]` (applied to every carrier
                  // when there's no per-carrier entry).
                  const legacyScaler = Number(legacyLoadScalers[String(period)] ?? 1)
                  const resolvedScaler = (carrierKey: string): number => {
                    const v = loadScalersByCarrier[carrierKey]?.[String(period)]
                    return v != null && Number.isFinite(v) ? Number(v) : legacyScaler
                  }
                  const budgetEur = Number(capexBudgetPerPeriod[String(period)] ?? 0)
                  const budgetMeur = budgetEur > 0 ? budgetEur / 1e6 : 0
                  return (
                    <tr key={period} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                      <td className="px-2 py-1 font-mono text-[11px]">{period}</td>
                      <td className="px-2 py-1 text-right">
                        <input
                          key={`y-${period}-${years}`}
                          type="number"
                          step="0.1"
                          min={0}
                          defaultValue={years.toFixed(2)}
                          // Disable while a sibling mutation is in flight —
                          // without this, a fast double-blur (user clicks
                          // away, immediately clicks back in & blurs again)
                          // fires two PUTs racing each other; the second
                          // one's response can overwrite the first's success
                          // state.
                          disabled={updatePeriodColPending}
                          onBlur={e => {
                            const v = parseFloat(e.target.value)
                            if (!Number.isFinite(v) || v < 0) {
                              e.target.value = String(years)
                              return
                            }
                            if (v !== years) {
                              onPeriodColChange({ period, col: 'years', value: v })
                            }
                          }}
                          className="w-20 px-1 py-0.5 border border-border rounded text-[11px] font-mono bg-bg focus:outline-none focus:border-accent text-right disabled:opacity-50 disabled:cursor-wait"
                        />
                      </td>
                      <td className="px-2 py-1 text-right">
                        <input
                          key={`o-${period}-${objective}`}
                          type="number"
                          step="0.01"
                          min={0}
                          defaultValue={objective.toFixed(4)}
                          disabled={updatePeriodColPending}
                          onBlur={e => {
                            const v = parseFloat(e.target.value)
                            if (!Number.isFinite(v) || v < 0) {
                              e.target.value = String(objective)
                              return
                            }
                            if (v !== objective) {
                              onPeriodColChange({ period, col: 'objective', value: v })
                            }
                          }}
                          className="w-24 px-1 py-0.5 border border-border rounded text-[11px] font-mono bg-bg focus:outline-none focus:border-accent text-right disabled:opacity-50 disabled:cursor-wait"
                        />
                      </td>
                      <td className={`px-2 py-1 text-right font-mono text-[11px] ${autoDiscountOn ? 'text-text' : 'text-muted/40'}`}
                          title={autoDiscountOn
                            ? `Auto-discount will set objective = ${pvFactor({ period, refPeriod, years, discountRate, inflationRate }).toFixed(4)} at solve time, overriding the value on the left.`
                            : 'Auto-discount is off — the objective value on the left is what the LP uses.'}>
                        {pvFactor({ period, refPeriod, years, discountRate, inflationRate }).toFixed(4)}
                      </td>
                      {advancedOpen && loadCarriers.map(({ key: carrier }) => {
                        const v = resolvedScaler(carrier)
                        return (
                          <td key={`ls-${period}-${carrier}`} className="px-2 py-1 text-right">
                            <input
                              key={`ls-${period}-${carrier}-${v}`}
                              type="number"
                              step="0.01"
                              min={0.01}
                              defaultValue={v.toFixed(2)}
                              title={`Load multiplier for "${carrier}" in period ${period} (1.00 = unchanged, 1.10 = +10% growth)`}
                              disabled={loadScalerPending}
                              onBlur={e => {
                                const nv = parseFloat(e.target.value)
                                if (!Number.isFinite(nv) || nv <= 0) {
                                  e.target.value = v.toFixed(2)
                                  return
                                }
                                if (nv === v) return
                                onLoadScalerChange(carrier, period, nv)
                              }}
                              className="w-20 px-1 py-0.5 border border-border rounded text-[11px] font-mono bg-bg focus:outline-none focus:border-accent text-right disabled:opacity-50 disabled:cursor-wait"
                            />
                          </td>
                        )
                      })}
                      {advancedOpen && (
                        <td className="px-2 py-1 text-right">
                          <input
                            key={`b-${period}-${budgetMeur}`}
                            type="number"
                            step="10"
                            min={0}
                            defaultValue={budgetMeur > 0 ? budgetMeur.toFixed(0) : ''}
                            placeholder="—"
                            title="CAPEX budget for this period in millions of EUR. 0 / empty = unconstrained"
                            disabled={capexBudgetPending}
                            onBlur={e => {
                              const raw = e.target.value.trim()
                              let nextEur: number | null
                              if (!raw) {
                                nextEur = null
                              } else {
                                const v = parseFloat(raw)
                                if (!Number.isFinite(v) || v < 0) {
                                  e.target.value = budgetMeur > 0 ? budgetMeur.toFixed(0) : ''
                                  return
                                }
                                nextEur = v === 0 ? null : v * 1e6  // M€ → €
                              }
                              // Only send the PUT if the stored value actually changed.
                              if ((nextEur ?? 0) !== budgetEur) {
                                onCapexBudgetChange(period, nextEur)
                              }
                            }}
                            className="w-20 px-1 py-0.5 border border-border rounded text-[11px] font-mono bg-bg focus:outline-none focus:border-accent text-right disabled:opacity-50 disabled:cursor-wait"
                          />
                        </td>
                      )}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Advanced: reveals the per-carrier load-scaler and CAPEX budget
              columns in the table above (same `advancedOpen` state) plus the
              full column-by-column explanation. Styled identically to
              StepShell's own Advanced disclosure for a consistent
              affordance, even though — being a column toggle for a live
              table rather than a block of content — it can't literally nest
              the table inside itself; see the file header. */}
          <details
            className="mt-1 border border-border rounded text-[11px]"
            open={advancedOpen}
            onToggle={e => setAdvancedOpen((e.target as HTMLDetailsElement).open)}
          >
            <summary className="px-3 py-2 cursor-pointer text-muted hover:text-text select-none">Advanced</summary>
            <div className="px-3 py-2">
              <p className="text-[10px] text-muted leading-relaxed">
                <code>years</code> = calendar years the period stands in for
                (period 2030 with <code>years=10</code> → 2030–2039).
                <code> objective</code> = LP-objective weight. When
                Auto-discount is ON the <code>PV × preview</code> column is
                what actually reaches the LP; the value you type here is
                overridden at solve time and restored afterwards. With
                Auto-discount OFF, what you type is what the LP uses.
                <code>Load × · {'{carrier}'}</code> =
                per-carrier load growth multiplier — loads of that carrier
                are scaled independently per period at solve time
                (<code>1.00</code> = unchanged, <code>1.10</code> = +10 %).
                Each carrier present in the network gets its own column;
                set Hydrogen ×1.5 in 2030 without affecting Electrical.
                <code> Budget M€</code> = upper bound on total NEW overnight
                CAPEX for assets built in this period; blank = unconstrained.
                Defaults: 1.
              </p>
            </div>
          </details>
        </div>
      </div>
    </section>
  )
}

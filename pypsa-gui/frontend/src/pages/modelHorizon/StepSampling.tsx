// The "Representative weeks" step: samples N random ISO weeks per month from
// an uploaded full-year hourly profile. No advanced slot — the results table
// is at most 5 weeks/month × 12 months, nowhere near the per-row weightings
// table's 8,760-row scale, so there's nothing here that needs hiding.
//
// Presentational only: `sampleWeeks` (the mutation) and its scratch state
// (`sampleNWeeks`, `sampleSeed`, `sampledWeeks`) stay owned by
// ModelHorizon.tsx, which passes down current values, change callbacks, and
// the composed `onSampleWeeks` handler (validation + confirm dialog +
// `.mutate`) unchanged from its original inline definition.

export interface StepSamplingProps {
  canSampleWeeks: boolean
  sampleNWeeks: string
  onSampleNWeeksChange: (value: string) => void
  sampleSeed: string
  onSampleSeedChange: (value: string) => void
  onSampleWeeks: () => void
  sampleWeeksPending: boolean
  sampledWeeks: Array<{ month: number; iso_week: number; start: string; end: string; weight: number }>
}

export function StepSampling({
  canSampleWeeks, sampleNWeeks, onSampleNWeeksChange, sampleSeed, onSampleSeedChange,
  onSampleWeeks, sampleWeeksPending, sampledWeeks,
}: StepSamplingProps) {
  return (
    <section>
      <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2.5">Representative weeks</h3>
      <p className="text-[11px] text-muted mb-2 leading-relaxed">
        Sample <code>N</code> random ISO calendar weeks (Mon–Sun) per month
        from an uploaded full-year hourly profile — e.g. 1 week/month →
        12 × 168 = <span className="font-mono">2016</span> snapshots instead
        of 8760. Each sampled hour is weighted by{' '}
        <code>days-in-month / (weeks × 7)</code> so dispatch, cost and
        emissions still aggregate to a full year.{' '}
        {!canSampleWeeks && (
          <span className="text-warn">
            Disabled — upload a full-year hourly profile on the Time Series
            page first.
          </span>
        )}
      </p>
      <div className="flex items-end gap-2">
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-muted">Weeks / month</span>
          <input
            type="number" min={1} max={5} step={1} value={sampleNWeeks}
            onChange={e => onSampleNWeeksChange(e.target.value)}
            className="w-24 px-2 py-1 border border-border rounded text-xs bg-bg font-mono"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-muted">Seed (optional)</span>
          <input
            type="number" placeholder="random" value={sampleSeed}
            onChange={e => onSampleSeedChange(e.target.value)}
            title="Fix the RNG seed for reproducible sampling — leave blank for a fresh random draw"
            className="w-28 px-2 py-1 border border-border rounded text-xs bg-bg font-mono"
          />
        </label>
        <button
          onClick={onSampleWeeks}
          disabled={!canSampleWeeks || sampleWeeksPending}
          className="px-3 py-1.5 bg-accent text-white rounded text-xs font-medium hover:bg-accent/90 disabled:opacity-40"
        >
          {sampleWeeksPending
            ? 'Sampling…'
            : sampledWeeks.length > 0 ? 'Re-sample' : 'Sample weeks'}
        </button>
      </div>
      {sampledWeeks.length > 0 && (
        <div className="border border-border rounded overflow-auto max-h-48 mt-2">
          <table className="w-full text-[11px] border-collapse" style={{ minWidth: 340 }}>
            <thead className="sticky top-0 bg-bg-2 z-10">
              <tr className="border-b border-border">
                <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Month</th>
                <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">ISO week</th>
                <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Range</th>
                <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Weight</th>
              </tr>
            </thead>
            <tbody>
              {sampledWeeks.map((w, i) => (
                <tr key={`${w.month}-${w.iso_week}-${i}`} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                  <td className="px-2 py-1 font-mono">
                    {new Date(2000, w.month - 1, 1).toLocaleString('en-US', { month: 'short' })}
                  </td>
                  <td className="px-2 py-1 font-mono">W{w.iso_week}</td>
                  <td className="px-2 py-1 font-mono text-muted">
                    {w.start.slice(0, 10)} → {w.end.slice(0, 10)}
                  </td>
                  <td className="px-2 py-1 font-mono text-right">{w.weight.toFixed(2)}×</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

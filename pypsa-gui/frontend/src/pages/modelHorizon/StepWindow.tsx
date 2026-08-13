// The "Snapshot window" step: both operational-window forms. Single-period
// mode shows one start/end/resolution range; multi-period mode shows the
// MultiIndex (period × timestep) constructor. Exactly one renders, gated by
// `isMultiPeriod` — same branch the page itself used before this move.
//
// Presentational only: every mutation (`applySnapshots`,
// `applyMultiPeriodSnapshots`) and every scratch-state hook (start/end/freq,
// the mp* fields) stays owned by ModelHorizon.tsx, which passes down current
// values and change callbacks. This file doesn't call useMutation or own any
// of that state itself.
//
// `noPeriodsFallback` is a fully-built ReactNode (NoPeriodsFallback + its
// message + onGoToYears), constructed by ModelHorizon.tsx and handed down —
// NoPeriodsFallback itself stays private to ModelHorizon.tsx per the Task 4
// brief ("leave alone"); this file never imports it.
//
// The "Different year per period" table (`PerPeriodRangeTable`, below)
// renders INLINE in this step's own body, right below the mode radios —
// NOT behind StepShell's `advanced` slot. It used to live there (final
// whole-branch review, Blocker 1): that disclosure is collapsed by default
// AND reset to collapsed on every step entry, with the "Build MultiIndex
// snapshots" button sitting ABOVE it — so selecting "Different year per
// period" showed only an explanatory paragraph, and the natural path was
// pick the mode → click Build → get a "Fill start + end for period …" toast
// with no field anywhere on screen. The table is at most a handful of rows
// (one per investment period), the same size argument that already justifies
// StepShell's default always-mounted disclosure behaviour for it — so there
// was never a performance reason to hide it behind a click in the first
// place. Because of this, `window` contributes no StepShell advanced content
// at all (see ModelHorizon.tsx's `advancedContent` derivation).
import type { ReactNode } from 'react'
import { FREQ_OPTIONS } from '../modelHorizonModel'

export interface StepWindowProps {
  isMultiPeriod: boolean
  periods: number[]
  /** Shown instead of the multi-period constructor when there are zero
   * investment years yet. */
  noPeriodsFallback: ReactNode

  // ── Single-period snapshot range (rendered when !isMultiPeriod) ────────
  start: string
  end: string
  freq: string
  onStartChange: (value: string) => void
  onEndChange: (value: string) => void
  onFreqChange: (value: string) => void
  /** Pre-formatted "YYYY-MM-DD → YYYY-MM-DD" label for the uploaded
   * time-series extent, or null when there's no upload yet (hides the reset
   * button entirely, same as the original `snap?.ts_start && snap?.ts_end`
   * guard). */
  resetRangeLabel: string | null
  onResetToUploadedRange: () => void
  onApplySnapshots: () => void
  applySnapshotsPending: boolean

  // ── Multi-period MultiIndex constructor (rendered when isMultiPeriod) ──
  mpMode: 'same' | 'per_period'
  onMpModeChange: (mode: 'same' | 'per_period') => void
  mpStart: string
  mpEnd: string
  mpFreq: string
  onMpStartChange: (value: string) => void
  onMpEndChange: (value: string) => void
  onMpFreqChange: (value: string) => void
  onApplyMultiPeriod: () => void
  applyMultiPeriodPending: boolean

  // ── Per-period range table (rendered inline when mpMode === 'per_period') ─
  mpPerPeriod: Array<{ start: string; end: string; freq: string }>
  onMpPerPeriodChange: (index: number, patch: Partial<{ start: string; end: string; freq: string }>) => void
}

export function StepWindow({
  isMultiPeriod, periods, noPeriodsFallback,
  start, end, freq, onStartChange, onEndChange, onFreqChange,
  resetRangeLabel, onResetToUploadedRange, onApplySnapshots, applySnapshotsPending,
  mpMode, onMpModeChange, mpStart, mpEnd, mpFreq,
  onMpStartChange, onMpEndChange, onMpFreqChange,
  onApplyMultiPeriod, applyMultiPeriodPending,
  mpPerPeriod, onMpPerPeriodChange,
}: StepWindowProps) {
  if (isMultiPeriod) {
    return (
      <section>
        <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2.5">Snapshot window</h3>
        {periods.length === 0 ? noPeriodsFallback : (
          <div className="border border-border rounded">
            <div className="px-2.5 py-1.5 border-b border-border bg-bg-2 text-[9px] font-bold uppercase tracking-[0.14em] text-muted flex items-center justify-between">
              <span>Snapshot constructor (MultiIndex)</span>
              <span className="text-[10px] text-muted/70 normal-case">builds (period × timestep) snapshots</span>
            </div>
            <div className="p-2.5 flex flex-col gap-2.5">
              <div className="flex items-center gap-3 text-[11px]">
                <label className="flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="radio"
                    name="mp-mode"
                    checked={mpMode === 'same'}
                    onChange={() => onMpModeChange('same')}
                    className="accent-accent"
                  />
                  Same year per period
                </label>
                <label className="flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="radio"
                    name="mp-mode"
                    checked={mpMode === 'per_period'}
                    onChange={() => onMpModeChange('per_period')}
                    className="accent-accent"
                  />
                  Different year per period
                </label>
              </div>

              {mpMode === 'same' ? (
                <div className="flex flex-col gap-2">
                  <p className="text-[10px] text-muted leading-relaxed">
                    One operational (start, end, freq) range replicated under
                    every investment period. Canonical workflow when you have
                    one year of weather/load data and want to use it as a
                    representative profile for every decade.
                  </p>
                  <div className="grid grid-cols-3 gap-2">
                    <label className="flex flex-col gap-0.5">
                      <span className="text-[10px] text-muted">Start</span>
                      <input
                        type="datetime-local" lang="en-US"
                        value={mpStart}
                        onChange={e => onMpStartChange(e.target.value)}
                        className="px-2 py-1 border border-border rounded text-[11px] bg-bg"
                      />
                    </label>
                    <label className="flex flex-col gap-0.5">
                      <span className="text-[10px] text-muted">End</span>
                      <input
                        type="datetime-local" lang="en-US"
                        value={mpEnd}
                        onChange={e => onMpEndChange(e.target.value)}
                        className="px-2 py-1 border border-border rounded text-[11px] bg-bg"
                      />
                    </label>
                    <label className="flex flex-col gap-0.5">
                      <span className="text-[10px] text-muted">Resolution</span>
                      <select
                        value={mpFreq}
                        onChange={e => onMpFreqChange(e.target.value)}
                        className="px-2 py-1 border border-border rounded text-[11px] bg-bg"
                      >
                        {FREQ_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                      </select>
                    </label>
                  </div>
                </div>
              ) : (
                <div className="flex flex-col gap-2">
                  <p className="text-[10px] text-muted leading-relaxed">
                    One (start, end, freq) per investment period. Use when you
                    have multi-year weather data and want each decade to see a
                    different operational year.
                  </p>
                  <PerPeriodRangeTable
                    periods={periods}
                    mpPerPeriod={mpPerPeriod}
                    onMpPerPeriodChange={onMpPerPeriodChange}
                  />
                </div>
              )}

              <button
                onClick={onApplyMultiPeriod}
                disabled={applyMultiPeriodPending}
                className="w-full px-3 py-1.5 bg-accent text-white rounded text-xs font-medium hover:bg-accent/90 disabled:opacity-40"
              >
                {applyMultiPeriodPending ? 'Building…' : 'Build MultiIndex snapshots'}
              </button>
              <p className="text-[10px] text-muted leading-relaxed">
                This replaces the snapshot index with a 2-level
                (period, timestep) MultiIndex. Uploaded time-series profiles
                are re-aligned by their operational timestamp — a 1-year upload
                becomes the profile under every period.
              </p>
            </div>
          </div>
        )}
      </section>
    )
  }

  return (
    <section>
      <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2.5">Snapshot range</h3>
      <p className="text-[11px] text-muted mb-2 leading-relaxed">
        Defines the single operational window the LP spans. The index is
        built as <code>pd.date_range(start, end, freq)</code>. Time-series
        uploads are re-aligned by datetime intersection.
      </p>
      <div className="flex flex-col gap-2">
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-muted">Start</span>
          <input
            type="datetime-local" lang="en-US" value={start}
            onChange={e => onStartChange(e.target.value)}
            className="px-2 py-1 border border-border rounded text-xs bg-bg"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-muted">End</span>
          <input
            type="datetime-local" lang="en-US" value={end}
            onChange={e => onEndChange(e.target.value)}
            className="px-2 py-1 border border-border rounded text-xs bg-bg"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-muted">Resolution</span>
          <select
            value={freq} onChange={e => onFreqChange(e.target.value)}
            className="px-2 py-1 border border-border rounded text-xs bg-bg"
          >
            {FREQ_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
      </div>
      {/* Snap back to the uploaded time-series extent — the data range the
          user actually uploaded. Only shown when flat profiles exist. */}
      {resetRangeLabel && (
        <button
          onClick={onResetToUploadedRange}
          className="mt-2 text-[10.5px] text-accent hover:underline"
        >↻ Reset to uploaded data range ({resetRangeLabel})</button>
      )}
      <button
        onClick={onApplySnapshots}
        disabled={applySnapshotsPending}
        className="mt-3 w-full px-3 py-1.5 bg-accent text-white rounded text-xs font-medium hover:bg-accent/90 disabled:opacity-40"
      >{applySnapshotsPending ? 'Applying…' : 'Apply snapshots'}</button>
    </section>
  )
}

// ── The per-period range table ("Different year per period") ──────────────
// Rendered inline by StepWindow itself, directly below the explanatory
// paragraph, only while mpMode === 'per_period' — at that point periods.length
// is always > 0 (zero periods short-circuits to noPeriodsFallback above this
// branch entirely), so no extra guard is needed here. NOT exported: nothing
// outside this file renders it — see the file header for why this moved out
// of StepShell's `advanced` slot.

interface PerPeriodRangeTableProps {
  periods: number[]
  mpPerPeriod: Array<{ start: string; end: string; freq: string }>
  onMpPerPeriodChange: (index: number, patch: Partial<{ start: string; end: string; freq: string }>) => void
}

function PerPeriodRangeTable({ periods, mpPerPeriod, onMpPerPeriodChange }: PerPeriodRangeTableProps) {
  return (
    <div className="border border-border rounded overflow-auto max-h-64">
      <table className="w-full text-[11px] border-collapse" style={{ minWidth: 480 }}>
        <thead className="sticky top-0 bg-bg-2 z-10">
          <tr className="border-b border-border">
            <th className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Period</th>
            <th className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Start</th>
            <th className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">End</th>
            <th className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Freq</th>
          </tr>
        </thead>
        <tbody>
          {periods.map((p, i) => {
            const row = mpPerPeriod[i] ?? { start: '', end: '', freq: 'h' }
            const updateRow = (patch: Partial<typeof row>) => onMpPerPeriodChange(i, patch)
            return (
              <tr key={p} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                <td className="px-2 py-1 font-mono text-[11px]">{p}</td>
                <td className="px-2 py-1">
                  <input
                    type="datetime-local" lang="en-US"
                    value={row.start}
                    onChange={e => updateRow({ start: e.target.value })}
                    className="w-full px-1 py-0.5 border border-border rounded text-[11px] bg-bg"
                  />
                </td>
                <td className="px-2 py-1">
                  <input
                    type="datetime-local" lang="en-US"
                    value={row.end}
                    onChange={e => updateRow({ end: e.target.value })}
                    className="w-full px-1 py-0.5 border border-border rounded text-[11px] bg-bg"
                  />
                </td>
                <td className="px-2 py-1">
                  <select
                    value={row.freq}
                    onChange={e => updateRow({ freq: e.target.value })}
                    className="w-full px-1 py-0.5 border border-border rounded text-[11px] bg-bg"
                  >
                    {FREQ_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.value}</option>)}
                  </select>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

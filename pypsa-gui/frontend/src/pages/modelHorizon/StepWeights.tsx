// The "Snapshot weightings" step: bulk apply-to-all control in the main
// body; CSV download/upload and the paginated per-row table behind
// StepShell's `advanced` disclosure (StepWeightsAdvanced, below) — the
// per-row table can be 8,760 rows on an hourly model, unusable as a
// default-visible element, so Task 4 moved it behind Advanced rather than
// just relocating it verbatim.
//
// Presentational only: `applyBulkWeight` / `updateOneWeight` (the
// mutations) stay owned by ModelHorizon.tsx. This file receives their
// `.mutate` / `.isPending` as plain callback/boolean props, not the mutation
// objects themselves — same "pure UI, mutations passed in via callbacks"
// convention as ScenariosPanel.tsx's ScenarioNodeRow.
import { useEffect, useMemo, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import { Download, Upload } from 'lucide-react'
import { confirmToast } from '../../utils/toasts'
import { buildWeightingRows, type WeightingRow } from '../modelHorizonModel'

export interface StepWeightsProps {
  bulkWeight: string
  onBulkWeightChange: (value: string) => void
  onApplyBulkWeight: (value: number) => void
  applyBulkWeightPending: boolean
}

export function StepWeights({
  bulkWeight, onBulkWeightChange, onApplyBulkWeight, applyBulkWeightPending,
}: StepWeightsProps) {
  return (
    <section>
      <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2.5">Snapshot weightings</h3>
      <p className="text-[11px] text-muted mb-2 leading-relaxed">
        Per-snapshot weight in the LP objective and storage SoC equations.
        Default <code>1</code>. For representative-day workflows set
        <code> all</code> to the number of days each snapshot represents
        (e.g. <code>30</code> for one typical day × 30 days). Use CSV for
        hourly horizons where row-by-row editing isn't practical.
      </p>

      {/* Apply-to-all row */}
      <div className="flex items-center gap-2 mb-2">
        <input
          type="number"
          step="0.1"
          min={0}
          placeholder="Apply to all (e.g. 30)"
          value={bulkWeight}
          onChange={e => onBulkWeightChange(e.target.value)}
          className="flex-1 px-2 py-1 border border-border rounded text-xs bg-bg font-mono"
        />
        <button
          disabled={!bulkWeight || applyBulkWeightPending}
          onClick={() => {
            const v = parseFloat(bulkWeight)
            if (!Number.isFinite(v) || v < 0) {
              toast.error('Enter a non-negative number')
              return
            }
            confirmToast(
              `Set every snapshot weight to ${v}? This overwrites all per-row edits.`,
              () => onApplyBulkWeight(v),
              { confirmLabel: 'Apply' },
            )
          }}
          className="px-2 py-1 bg-accent text-white rounded text-xs font-medium hover:bg-accent/90 disabled:opacity-40"
        >
          {applyBulkWeightPending ? 'Applying…' : 'Apply to all'}
        </button>
      </div>
    </section>
  )
}

// ── Advanced: CSV import/export + the paginated per-row table ─────────────
// Pagination (`page`) and the CSV upload `<input>`'s ref are local UI state —
// nothing outside this table needs either, so both moved here rather than
// staying lifted in ModelHorizon.tsx. `weightingRows` is still derived via
// `buildWeightingRows` from modelHorizonModel.ts (not re-derived).

const PAGE_SIZE = 100

export interface StepWeightsAdvancedProps {
  /** `snap.weightings` — the full set, this component slices its own page. */
  weightings: WeightingRow[]
  /** `snap.snapshots` — positional fallback for rows with no timestamp of their own. */
  snapshots: string[]
  snapshotsAreMulti: boolean
  onWeightChange: (key: string, objective: number) => void
  updateOneWeightPending: boolean
  onDownloadCsv: () => void
  onUploadCsv: (file: File | undefined) => void
}

export function StepWeightsAdvanced({
  weightings, snapshots, snapshotsAreMulti, onWeightChange, updateOneWeightPending,
  onDownloadCsv, onUploadCsv,
}: StepWeightsAdvancedProps) {
  const totalWeightings = weightings.length
  const totalPages = Math.max(1, Math.ceil(totalWeightings / PAGE_SIZE))
  const [page, setPage] = useState(0)
  useEffect(() => {
    // Clamp when the underlying snapshot set shrinks (e.g. after re-indexing).
    if (page > 0 && page >= totalPages) setPage(0)
  }, [totalPages, page])
  const pageStart = page * PAGE_SIZE
  const pageEnd = Math.min(pageStart + PAGE_SIZE, totalWeightings)
  const pageRows = useMemo(
    () => weightings.slice(pageStart, pageEnd),
    [weightings, pageStart, pageEnd],
  )
  const weightingRows = useMemo(
    () => buildWeightingRows(pageRows, snapshots, snapshotsAreMulti, pageStart),
    [pageRows, snapshots, snapshotsAreMulti, pageStart],
  )

  const csvUploadRef = useRef<HTMLInputElement>(null)

  return (
    <>
      {/* CSV import/export row */}
      <div className="flex items-center gap-2 mb-2">
        <button
          onClick={onDownloadCsv}
          className="flex items-center gap-1 px-2 py-1 border border-border rounded text-[11px] hover:border-accent hover:text-accent"
          title="Download all snapshot weightings as CSV"
        ><Download size={11} /> Download CSV</button>
        <button
          onClick={() => csvUploadRef.current?.click()}
          className="flex items-center gap-1 px-2 py-1 border border-border rounded text-[11px] hover:border-accent hover:text-accent"
          title="Upload edited CSV to overwrite weights"
        ><Upload size={11} /> Upload CSV</button>
        <input
          ref={csvUploadRef}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          onChange={e => {
            onUploadCsv(e.target.files?.[0])
            if (csvUploadRef.current) csvUploadRef.current.value = ''
          }}
        />
        <span className="text-[10px] text-muted">
          Columns: <code>snapshot</code>, <code>objective</code>, <code>generators</code>, <code>stores</code>
        </span>
      </div>

      {/* Per-row table — paginated so 8760-hour horizons are reachable */}
      <div className="border border-border rounded overflow-auto max-h-64">
        <table className="w-full text-xs border-collapse" style={{ minWidth: snapshotsAreMulti ? 560 : 480 }}>
          <thead className="sticky top-0 bg-bg-2 z-10">
            <tr className="border-b border-border">
              {snapshotsAreMulti && (
                <th className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Period</th>
              )}
              <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Snapshot</th>
              <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Objective</th>
              <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Generators</th>
              <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Stores</th>
            </tr>
          </thead>
          <tbody>
            {weightingRows.map((row, i) => (
              <tr key={row.key} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                {snapshotsAreMulti && (
                  <td className="px-2 py-1 font-mono text-[11px]">{row.period}</td>
                )}
                <td className="px-2 py-1 font-mono text-[11px] whitespace-nowrap">{row.iso}</td>
                <td className="px-2 py-1 text-right">
                  <input
                    key={`sw-${row.key}-${row.objective}`}
                    type="number"
                    step="0.1"
                    min={0}
                    defaultValue={row.objective.toFixed(2)}
                    // Disabled while the shared per-snapshot mutation is
                    // in flight — same double-blur race protection as
                    // the per-period table on the Economics step.
                    disabled={updateOneWeightPending}
                    onBlur={e => {
                      const v = parseFloat(e.target.value)
                      if (!Number.isFinite(v) || v < 0) {
                        e.target.value = row.objective.toFixed(2)
                        return
                      }
                      if (v !== row.objective) {
                        onWeightChange(row.key, v)
                      }
                    }}
                    className="w-20 px-1 py-0.5 border border-border rounded text-[11px] font-mono bg-bg focus:outline-none focus:border-accent text-right disabled:opacity-50 disabled:cursor-wait"
                  />
                </td>
                <td className="px-2 py-1 font-mono text-[11px] text-right text-muted">
                  {row.generators.toFixed(2)}
                </td>
                <td className="px-2 py-1 font-mono text-[11px] text-right text-muted">
                  {row.stores.toFixed(2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination footer — only shown when there's more than one page */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-1.5 text-[10px] text-muted">
          <span>
            Rows {pageStart + 1}–{pageEnd} of {totalWeightings}
          </span>
          <div className="flex items-center gap-2">
            <button
              disabled={page === 0}
              onClick={() => setPage(p => Math.max(0, p - 1))}
              className="px-1.5 py-0.5 border border-border rounded hover:border-accent hover:text-accent disabled:opacity-30"
            >← Prev</button>
            <span>Page {page + 1} / {totalPages}</span>
            <button
              disabled={page >= totalPages - 1}
              onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
              className="px-1.5 py-0.5 border border-border rounded hover:border-accent hover:text-accent disabled:opacity-30"
            >Next →</button>
          </div>
        </div>
      )}
    </>
  )
}

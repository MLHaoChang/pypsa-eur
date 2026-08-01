import { useRef } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import type { AssetResultsResponse } from './types'

const head = (label: string, unit: string) => unit ? `${label} (${unit})` : label

/**
 * The exact rows the table renders — also the rows the CSV export writes.
 * Pure, so the file and the screen cannot drift.
 */
export function tableRows(data: AssetResultsResponse): {
  header: string[]; rows: unknown[][]
} {
  const cols = data.columns
  if (data.mode === 'duration') {
    return {
      header: ['rank', 'pct_of_hours', ...cols.map(c => head(c.label, c.unit))],
      rows: data.index.map((rank, i) => [
        rank, data.pct_of_hours?.[i] ?? null,
        ...cols.map(c => data.series[c.id]?.[i] ?? null),
      ]),
    }
  }
  const first = data.mode === 'monthly' ? 'month' : 'snapshot'
  const withPeriod = data.mode === 'chronological' && !!data.periods
  return {
    header: [first, ...(withPeriod ? ['period'] : []),
             ...cols.map(c => head(c.label, c.unit))],
    rows: data.index.map((stamp, i) => [
      stamp,
      ...(withPeriod ? [data.periods![i]] : []),
      ...cols.map(c => data.series[c.id]?.[i] ?? null),
    ]),
  }
}

const fmt = (v: unknown) => {
  if (v === null || v === undefined) return ''
  if (typeof v === 'number') {
    // Non-finite must never reach the DOM as the text "NaN"/"Infinity". The
    // backend already nulls these, but the guard costs nothing and this is the
    // component users read actual numbers from.
    if (!Number.isFinite(v)) return ''
    return Number.isInteger(v) ? String(v) : v.toFixed(3)
  }
  return String(v)
}

export default function AssetTable({ data }: { data: AssetResultsResponse }) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const { header, rows } = tableRows(data)
  const virt = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 22,
    overscan: 20,
  })

  // Key the empty state off the METRIC columns, not the header length. The
  // number of fixed leading columns varies by mode — duration always has two
  // (`rank`, `pct_of_hours`) and chronological-with-periods has two — so a
  // `header.length <= 1` test would never fire in either, and a user with
  // nothing ticked would get thousands of bare rank rows instead of a prompt.
  if (data.columns.length === 0) {
    return (
      <p className="p-4 text-[11px] text-muted">
        Tick a time series on the left to populate the table.
      </p>
    )
  }

  // Div + CSS grid, NOT a real <table>. Virtualised rows are absolutely
  // positioned, which removes them from a table's layout grid: the browser
  // would then size columns from <thead> alone while each `display:table` row
  // computed its own widths independently, so header and body would not line
  // up — and an explicit height on a `table-row-group` is not honoured, so the
  // scroll container would never match getTotalSize(). One shared grid
  // template applied to the header and every row sidesteps both problems.
  // AssetPicker.tsx virtualises the same way for the same reason.
  const gridTemplate =
    `minmax(9rem, 1.2fr) repeat(${header.length - 1}, minmax(6rem, 1fr))`

  return (
    <div ref={scrollRef} className="flex-1 min-h-0 overflow-auto">
      <div role="table" className="text-[11px] font-mono min-w-max">
        <div
          role="row"
          style={{ display: 'grid', gridTemplateColumns: gridTemplate }}
          className="sticky top-0 z-10 bg-panel border-b border-border"
        >
          {header.map(h => (
            <div key={h} role="columnheader"
              className="px-2 py-1 text-left font-medium whitespace-nowrap">
              {h}
            </div>
          ))}
        </div>
        <div style={{ height: virt.getTotalSize(), position: 'relative' }}>
          {virt.getVirtualItems().map(v => (
            <div
              key={v.key}
              role="row"
              style={{
                position: 'absolute', top: 0, left: 0, width: '100%',
                height: v.size, transform: `translateY(${v.start}px)`,
                display: 'grid', gridTemplateColumns: gridTemplate,
              }}
              className="border-b border-border/40"
            >
              {rows[v.index].map((cell, ci) => (
                <div key={ci} role="cell"
                  className="px-2 py-0.5 tabular-nums whitespace-nowrap overflow-hidden text-ellipsis">
                  {fmt(cell)}
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

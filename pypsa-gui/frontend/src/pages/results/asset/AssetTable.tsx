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

const fmt = (v: unknown) =>
  v === null || v === undefined ? '' :
  typeof v === 'number' ? (Number.isInteger(v) ? String(v) : v.toFixed(3)) : String(v)

export default function AssetTable({ data }: { data: AssetResultsResponse }) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const { header, rows } = tableRows(data)
  const virt = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 22,
    overscan: 20,
  })

  if (header.length <= 1) {
    return (
      <p className="p-4 text-[11px] text-muted">
        Tick a time series on the left to populate the table.
      </p>
    )
  }

  return (
    <div ref={scrollRef} className="flex-1 min-h-0 overflow-auto">
      <table className="w-full text-[11px] font-mono border-collapse">
        <thead className="sticky top-0 bg-panel z-10">
          <tr>
            {header.map(h => (
              <th key={h}
                className="text-left px-2 py-1 border-b border-border font-medium whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody style={{ height: virt.getTotalSize(), position: 'relative' }}>
          {virt.getVirtualItems().map(v => (
            <tr key={v.key}
              style={{ position: 'absolute', top: 0, left: 0, width: '100%',
                       height: v.size, transform: `translateY(${v.start}px)`,
                       display: 'table', tableLayout: 'fixed' }}
              className="border-b border-border/40">
              {rows[v.index].map((cell, ci) => (
                <td key={ci} className="px-2 py-0.5 tabular-nums whitespace-nowrap">
                  {fmt(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

import { Ban, CircleAlert, Sigma } from 'lucide-react'
import { fmtScalar } from './format'

/**
 * The one way a scalar result is rendered anywhere in this tab.
 *
 * Replaces the row of KPI cards. Cards wrapped unpredictably, gave a
 * ten-character number the same footprint as a two-character one, and had
 * nowhere to put a unit, a formula or a "why is this unavailable" reason —
 * so the Summary tab, which is almost entirely scalars, looked like a
 * jumble. A four-column table (metric / value / unit / source) scans down,
 * aligns the digits, and has room for the rest.
 *
 * Used by Summary for its headline KPIs and by every other category for its
 * own scalars, so all eight tabs read the same way.
 */

export interface ScalarRow {
  id: string
  label: string
  unit: string
  /** Present when the value computed; absent when blocked or n/a. */
  value?: unknown
  status: 'ok' | 'blocked' | 'na'
  reason?: string
  formula?: string
  /** Which tab this came from. Set on Summary's headline rows only. */
  categoryLabel?: string
}

function StatusCell({ row }: { row: ScalarRow }) {
  if (row.status === 'ok') return <>{fmtScalar(row.value, { blank: '—' })}</>
  return (
    <span className="inline-flex items-center gap-1 text-muted italic">
      {row.status === 'blocked'
        ? <CircleAlert size={10} className="text-warn shrink-0" />
        : <Ban size={10} className="shrink-0" />}
      <span className="truncate">{row.reason || 'unavailable'}</span>
    </span>
  )
}

export default function ScalarTable(
  { rows, showSource = false, caption }: {
    rows: ScalarRow[]
    /** Render the "Source tab" column — Summary's headline KPIs only. */
    showSource?: boolean
    caption?: string
  },
) {
  if (rows.length === 0) return null
  return (
    <div className="mb-3">
      {caption && (
        <div className="text-[9px] uppercase tracking-wider text-muted px-2 py-1">
          {caption}
        </div>
      )}
      <table className="w-full text-[11px] border-collapse">
        <thead>
          <tr className="text-left text-muted border-b border-border">
            <th className="font-medium px-2 py-1">Metric</th>
            <th className="font-medium px-2 py-1 text-right">Value</th>
            <th className="font-medium px-2 py-1">Unit</th>
            {showSource && <th className="font-medium px-2 py-1">Source tab</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.id} className="border-b border-border/40 align-top">
              <td className="px-2 py-1">
                <span className="inline-flex items-center gap-1">
                  {row.label}
                  {row.formula && (
                    <span title={row.formula} className="text-muted">
                      <Sigma size={9} />
                    </span>
                  )}
                </span>
              </td>
              <td className="px-2 py-1 text-right font-mono tabular-nums
                whitespace-nowrap max-w-0 overflow-hidden text-ellipsis">
                <StatusCell row={row} />
              </td>
              <td className="px-2 py-1 font-mono text-muted whitespace-nowrap">
                {row.status === 'ok' ? row.unit : ''}
              </td>
              {showSource && (
                <td className="px-2 py-1 text-muted whitespace-nowrap">
                  {row.categoryLabel ?? ''}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

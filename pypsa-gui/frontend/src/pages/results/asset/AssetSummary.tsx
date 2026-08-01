import ScalarTable, { type ScalarRow } from './ScalarTable'
import { fmtScalar } from './format'
import type { AssetResultsResponse } from './types'

/**
 * The Summary tab.
 *
 * Three tables, not a wall of KPI cards: what this asset IS (identity), what
 * it was configured with (parameters), and the headline results lifted out
 * of the other seven tabs (key results). The third is the point — a user
 * opening an asset wants the numbers that characterise it before deciding
 * which tab to drill into, and previously Summary showed only identity and
 * parameters, rendered as `key: value` strings jammed into card faces.
 *
 * The headline rows come from the backend registry's HEADLINE map, already
 * resolved through the same applicability path as any other metric — so a
 * blocked KPI appears WITH its reason rather than silently vanishing.
 */

/** Identity/params dicts render as plain two-column key/value tables. */
function KeyValueTable(
  { caption, entries }: { caption: string; entries: Array<[string, unknown]> },
) {
  if (entries.length === 0) return null
  return (
    <div className="mb-3">
      <div className="text-[9px] uppercase tracking-wider text-muted px-2 py-1">
        {caption}
      </div>
      <table className="w-full text-[11px] border-collapse">
        <tbody>
          {entries.map(([k, v]) => (
            <tr key={k} className="border-b border-border/40">
              <td className="px-2 py-1 text-muted w-2/5">{k}</td>
              <td className="px-2 py-1 font-mono tabular-nums break-all">
                {fmtScalar(v, { blank: '—' })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function AssetSummary({ data }: { data: AssetResultsResponse }) {
  const { asset } = data

  const identity: Array<[string, unknown]> = [
    ['Name', asset.name],
    ['Class', asset.class],
    ...(asset.carrier ? [['Carrier', asset.carrier] as [string, unknown]] : []),
    ...(asset.bus ? [['Bus', asset.bus] as [string, unknown]] : []),
  ]
  const params = Object.entries(asset.params ?? {})

  const headline: ScalarRow[] = (data.headline ?? []).map(h => ({
    id: h.id,
    label: h.label,
    unit: h.unit,
    value: h.value,
    status: h.status,
    reason: h.reason,
    formula: h.formula,
    categoryLabel: h.category_label,
  }))

  return (
    <div className="flex-1 min-h-0 overflow-auto px-2 py-2">
      <ScalarTable rows={headline} showSource caption="Key results" />
      {headline.length === 0 && (
        <p className="px-2 py-1 text-[11px] text-muted">
          No headline results are defined for {asset.class}.
        </p>
      )}
      <div className="flex flex-wrap gap-x-6">
        <div className="flex-1 min-w-[16rem]">
          <KeyValueTable caption="Identity" entries={identity} />
        </div>
        <div className="flex-1 min-w-[16rem]">
          <KeyValueTable caption="Parameters" entries={params} />
        </div>
      </div>
    </div>
  )
}

import { useMemo, useState } from 'react'
import { Download } from 'lucide-react'
import toast from 'react-hot-toast'
import { type TSPayload, downloadCSV } from './shared'

// ── Storage charge/discharge daily heatmap ─────────────────────────────────
// Each cell = aggregate storage power at (day d, hour h):
//   • positive (discharge → red shades, screenshot's left half)
//     mapped to red intensity
//   • negative (charge → blue shades, screenshot's right half) mapped to
//     blue intensity
//
// One row per day in the selected period (rows D1 / D2 / ...), 24 columns
// (hours 0..23). Caps the day count to keep the canvas small — long horizons
// can be paginated in a follow-up.

interface StorageHeatmapProps {
  storPowerTS: TSPayload | null
  range: { from: number; to: number }
  // Optional overrides — when omitted the component renders as the original
  // storage charge/discharge heatmap. Set them to repurpose the same grid
  // for other power flows (electrolyser consumption, generator dispatch, …)
  // without duplicating the (day × hour) layout / CSV-export plumbing.
  columnFilter?: Set<string>
  title?: string
  subtitle?: string
  csvName?: string
  // Colour mode: 'diverging' is the storage default (blue=charge,
  // red=discharge). 'unsigned-blue' uses a single blue gradient where
  // intensity = |value| — useful for one-directional flows like
  // electrolyser consumption (always ≥ 0 at bus0). 'unsigned-red' is the
  // mirror image for one-directional production.
  mode?: 'diverging' | 'unsigned-blue' | 'unsigned-red'
  legendLabels?: { negative?: string; positive?: string }
}

const MAX_DAYS = 31  // hard cap so a year of hourly data doesn't render 365 rows

function colourForValue(
  v: number,
  maxAbs: number,
  mode: 'diverging' | 'unsigned-blue' | 'unsigned-red',
): string {
  if (!Number.isFinite(v) || maxAbs <= 0) return '#f9fafb'  // off-white blank
  const intensity = Math.min(1, Math.abs(v) / maxAbs)
  const alpha = (0.05 + 0.75 * intensity).toFixed(3)
  if (mode === 'unsigned-blue') return `rgba(59, 130, 246, ${alpha})`
  if (mode === 'unsigned-red')  return `rgba(220, 38, 38, ${alpha})`
  // Diverging (storage default).
  if (v < 0) return `rgba(59, 130, 246, ${alpha})`  // charge → blue
  return `rgba(220, 38, 38, ${alpha})`                // discharge → red
}

export default function StorageHeatmap({
  storPowerTS, range, columnFilter, title, subtitle, csvName, mode, legendLabels,
}: StorageHeatmapProps) {
  const effMode: 'diverging' | 'unsigned-blue' | 'unsigned-red' = mode ?? 'diverging'
  const [hover, setHover] = useState<{ day: number; hour: number; mw: number } | null>(null)

  // Build (day, hour) → aggregate signed power across all storage units.
  // Days indexed from 1 within the selected slice. PyPSA's snapshots are
  // expected to be hourly here; non-hourly snapshots would alias the heatmap
  // but the chart still renders something sensible.
  const grid = useMemo(() => {
    if (!storPowerTS) return null
    const rFrom = Math.max(0, range.from)
    const rTo = Math.min(storPowerTS.data.length - 1, range.to)
    if (rFrom > rTo) return null
    // Walk all rows to count total days first (so we can tell the user how
    // many we're hiding), THEN truncate the rendered grid to MAX_DAYS.
    let totalDays = 0
    let lastSeen = ''
    for (let row = rFrom; row <= rTo; row++) {
      const k = storPowerTS.index[row].slice(0, 10)
      if (k !== lastSeen) { totalDays += 1; lastSeen = k }
    }
    const days: number[][] = []
    const dayLabels: string[] = []
    const dayDates: string[] = []   // ISO date per rendered row, for tooltip
    let maxAbs = 0
    let currentDay = -1
    let lastDateKey = ''
    for (let row = rFrom; row <= rTo && days.length < MAX_DAYS; row++) {
      const iso = storPowerTS.index[row]
      const dateKey = iso.slice(0, 10)
      const hourMatch = iso.match(/T(\d{2}):/)
      const hour = hourMatch ? parseInt(hourMatch[1], 10) : 0
      if (dateKey !== lastDateKey) {
        currentDay += 1
        days.push(new Array(24).fill(NaN))
        dayLabels.push(`D${currentDay + 1}`)
        dayDates.push(dateKey)
        lastDateKey = dateKey
      }
      let totalMW = 0
      for (let i = 0; i < storPowerTS.columns.length; i++) {
        if (columnFilter && !columnFilter.has(storPowerTS.columns[i])) continue
        const v = storPowerTS.data[row][i]
        if (Number.isFinite(v)) totalMW += v
      }
      days[currentDay][hour] = totalMW
      const a = Math.abs(totalMW)
      if (a > maxAbs) maxAbs = a
    }
    return { days, dayLabels, dayDates, maxAbs, totalDays, truncated: totalDays > MAX_DAYS }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storPowerTS, range.from, range.to, columnFilter])

  if (!grid || grid.days.length === 0) {
    return (
      <div className="border border-border rounded p-4 text-center text-xs text-muted">
        No power data for the selected period.
      </div>
    )
  }

  // Cell sizing — fit the heatmap inside the section without scrolling.
  // 24 hours × ~28px = ~672px wide.
  const cellW = 24
  const cellH = 20

  const exportCSV = () => {
    // Long CSV: one row per (day, hour) cell.
    const rows: Array<[string, string, string, string]> = []
    grid.days.forEach((dayRow, d) => {
      const dateKey = grid.dayDates[d]
      dayRow.forEach((mw, h) => {
        rows.push([
          dateKey,
          String(h).padStart(2, '0'),
          Number.isFinite(mw) ? mw.toFixed(3) : '',
          // Direction column makes the export readable without a formula —
          // user can pivot on it directly.
          Number.isFinite(mw)
            ? (mw > 0 ? 'discharge' : mw < 0 ? 'charge' : 'idle')
            : '',
        ])
      })
    })
    downloadCSV(csvName ?? 'storage_heatmap.csv',
      ['date', 'hour', 'power_MW', 'direction'], rows)
    toast.success('Exported')
  }

  const headerTitle = title ?? 'Power heatmap — daily charge / discharge pattern'
  const headerSubtitle = subtitle ?? '1 row per day, 24 cells per row · red = discharge, blue = charge'
  const negLabel = legendLabels?.negative ?? (effMode === 'unsigned-blue' ? 'Idle' : 'Charge')
  const posLabel = legendLabels?.positive ?? (effMode === 'unsigned-blue' ? 'Consume' : effMode === 'unsigned-red' ? 'Produce' : 'Discharge')

  return (
    <section className="border border-border rounded">
      <header className="flex items-center gap-2 px-3 py-2 border-b border-border bg-panel flex-wrap">
        <span className="text-xs font-semibold text-text">{headerTitle}</span>
        <span className="text-[10px] text-muted">{headerSubtitle}</span>
        {grid.truncated && (
          <span className="text-[10px] text-warn font-mono">
            Showing first {MAX_DAYS} of {grid.totalDays} days — pick a shorter period to see the rest
          </span>
        )}
        <span className="ml-auto text-[10px] font-mono text-muted">
          max | {grid.maxAbs.toFixed(1)} MW |
        </span>
        {/* HTML-rendered heatmap — SVG export doesn't apply (no <svg> element).
            CSV-only. */}
        <button onClick={exportCSV}
          className="flex items-center gap-1 text-[11px] text-muted hover:text-accent"
          title="Export the (day × hour) grid as a long CSV (date, hour, power_MW, direction)">
          <Download size={11} /> CSV
        </button>
      </header>
      <div className="px-3 py-3 overflow-auto" style={{ position: 'relative' }}>
        {/* Hour-axis header */}
        <div className="flex items-center text-[9px] text-muted font-mono mb-1"
             style={{ paddingLeft: '3.25rem' /* matches w-12 day label + gap */ }}>
          {[0, 4, 8, 12, 16, 20].map(h => (
            <div key={h} style={{ width: 4 * cellW, textAlign: 'left' }}>{h}h</div>
          ))}
          <div style={{ marginLeft: -10 }}>23h</div>
        </div>
        {grid.days.map((row, d) => (
          <div key={d} className="flex items-center gap-1 mb-0.5">
            <div className="w-12 text-[9px] font-mono text-muted text-right pr-1"
                 title={grid.dayDates[d]}>
              {grid.dayLabels[d]}
            </div>
            <div className="flex">
              {row.map((mw, h) => (
                <div key={h}
                  onMouseEnter={() => setHover({ day: d + 1, hour: h, mw })}
                  onMouseLeave={() => setHover(null)}
                  style={{
                    width: cellW, height: cellH,
                    background: colourForValue(mw, grid.maxAbs, effMode),
                    border: '1px solid rgba(0,0,0,0.04)',
                  }}
                  title={`${grid.dayDates[d]} ${String(h).padStart(2, '0')}:00 — ${
                    Number.isFinite(mw)
                      ? (mw > 0 ? `${posLabel}: ${mw.toFixed(1)} MW` : mw < 0 ? `${negLabel}: ${(-mw).toFixed(1)} MW` : '0 MW')
                      : 'no data'
                  }`}
                />
              ))}
            </div>
          </div>
        ))}
        {/* Colour legend — switches between diverging (blue ↔ red) and
            single-hue gradients depending on the heatmap mode. */}
        <div className="flex items-center gap-2 mt-3 text-[10px] text-muted">
          <span>{negLabel}</span>
          {effMode === 'diverging' && (
            <>
              <div className="flex">
                {[1, 0.75, 0.5, 0.25, 0].map((a, i) => (
                  <div key={`b${i}`} style={{ width: 18, height: 8, background: `rgba(59, 130, 246, ${a})` }} />
                ))}
              </div>
              <div className="flex">
                {[0, 0.25, 0.5, 0.75, 1].map((a, i) => (
                  <div key={`r${i}`} style={{ width: 18, height: 8, background: `rgba(220, 38, 38, ${a})` }} />
                ))}
              </div>
            </>
          )}
          {effMode === 'unsigned-blue' && (
            <div className="flex">
              {[0.05, 0.25, 0.5, 0.75, 1].map((a, i) => (
                <div key={`u${i}`} style={{ width: 18, height: 8, background: `rgba(59, 130, 246, ${a})` }} />
              ))}
            </div>
          )}
          {effMode === 'unsigned-red' && (
            <div className="flex">
              {[0.05, 0.25, 0.5, 0.75, 1].map((a, i) => (
                <div key={`u${i}`} style={{ width: 18, height: 8, background: `rgba(220, 38, 38, ${a})` }} />
              ))}
            </div>
          )}
          <span>{posLabel}</span>
          {hover && (
            <span className="ml-auto font-mono text-text">
              D{hover.day} {String(hover.hour).padStart(2, '0')}:00 — {
                Number.isFinite(hover.mw)
                  ? (hover.mw > 0 ? `${posLabel}: ${hover.mw.toFixed(1)} MW` : hover.mw < 0 ? `${negLabel}: ${(-hover.mw).toFixed(1)} MW` : '0 MW')
                  : 'no data'
              }
            </span>
          )}
        </div>
      </div>
    </section>
  )
}

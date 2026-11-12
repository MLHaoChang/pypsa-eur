import { useMemo, useState } from 'react'
import toast from 'react-hot-toast'
import { type TSPayload, downloadCSV, ChartFrame, ChartActions, type SerializedSVG } from './shared'

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

// Theme tokens, so the map follows light and dark (ui-modern 02 slice 6).
const BLUE = 'var(--color-info)'
const RED = 'var(--color-danger)'
const shade = (colour: string, share: number) => `color-mix(in srgb, ${colour} ${(share * 100).toFixed(1)}%, transparent)`

function colourForValue(
  v: number,
  maxAbs: number,
  mode: 'diverging' | 'unsigned-blue' | 'unsigned-red',
): string {
  if (!Number.isFinite(v) || maxAbs <= 0) return 'var(--color-bg-2)'  // blank
  const intensity = Math.min(1, Math.abs(v) / maxAbs)
  const share = 0.05 + 0.75 * intensity
  if (mode === 'unsigned-blue') return shade(BLUE, share)
  if (mode === 'unsigned-red')  return shade(RED, share)
  // Diverging (storage default).
  return shade(v < 0 ? BLUE : RED, share)  // charge → blue, discharge → red
}

const xmlEsc = (t: string) => t.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string))

/** The grid as one <svg>, for the report: opacity (not color-mix) so every renderer agrees. */
function gridPicture(
  grid: { days: number[][]; dayLabels: string[] }, maxAbs: number, mode: 'diverging' | 'unsigned-blue' | 'unsigned-red',
  cellW: number, cellH: number,
): SerializedSVG {
  const css = getComputedStyle(document.documentElement)
  const tok = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback
  const blue = tok('--color-info', '#2563eb'), red = tok('--color-danger', '#dc2626')
  const bg = tok('--color-bg', '#ffffff'), text = tok('--color-muted', '#64748b')
  const left = 56, top = 16
  const width = left + 24 * cellW, height = top + grid.days.length * (cellH + 2)
  const parts = [`<?xml version="1.0" standalone="no"?>\r\n<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}">`,
    `<rect width="100%" height="100%" fill="${bg}"/>`]
  for (const h of [0, 4, 8, 12, 16, 20, 23]) {
    parts.push(`<text x="${left + h * cellW}" y="11" font-size="9" fill="${text}">${h}h</text>`)
  }
  grid.days.forEach((row, d) => {
    const y = top + d * (cellH + 2)
    parts.push(`<text x="${left - 4}" y="${y + cellH * 0.7}" font-size="9" text-anchor="end" fill="${text}">${xmlEsc(grid.dayLabels[d])}</text>`)
    row.forEach((mw, h) => {
      if (!Number.isFinite(mw) || maxAbs <= 0) return
      const share = 0.05 + 0.75 * Math.min(1, Math.abs(mw) / maxAbs)
      const colour = mode === 'unsigned-blue' ? blue : mode === 'unsigned-red' ? red : (mw < 0 ? blue : red)
      parts.push(`<rect x="${left + h * cellW}" y="${y}" width="${cellW - 1}" height="${cellH}" fill="${colour}" fill-opacity="${share.toFixed(3)}"/>`)
    })
  })
  parts.push('</svg>')
  return { source: parts.join(''), width, height }
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

  const headerTitle = title ?? 'Power heatmap · daily charge and discharge pattern'
  const headerSubtitle = subtitle ?? '1 row per day, 24 cells per row · red = discharge, blue = charge'
  const negLabel = legendLabels?.negative ?? (effMode === 'unsigned-blue' ? 'Idle' : 'Charge')
  const posLabel = legendLabels?.positive ?? (effMode === 'unsigned-blue' ? 'Consume' : effMode === 'unsigned-red' ? 'Produce' : 'Discharge')

  return (
    <ChartFrame
      title={headerTitle}
      figure={`${grid.maxAbs.toFixed(1)} MW highest`}
      hint={headerSubtitle}
      // An HTML grid, not an <svg>: CSV only.
      right={(() => {
        const filename = (csvName ?? 'storage_heatmap.csv').replace(/\.csv$/, '')
        const rows = grid.days.flatMap((dayRow, d) => dayRow.map((mw, h) => [grid.dayDates[d], h, Number.isFinite(mw) ? mw : null]))
        return (
          <ChartActions onExportCSV={exportCSV} filename={filename}
            report={{
              // The carrier is in the title and file name, the window in the days.
              view: 'storage-heatmap', figure: `${filename}_${grid.dayDates[0]}_${grid.dayDates[grid.dayDates.length - 1]}`, unit: 'MW',
              columns: ['date', 'hour', 'power_MW'], rows,
              rowsTotal: rows.length,
              note: grid.truncated ? `first ${MAX_DAYS} of ${grid.totalDays} days` : undefined,
              picture: () => gridPicture(grid, grid.maxAbs, effMode, cellW, cellH),
            }} />
        )
      })()}
      bodyClassName="p-0"
    >
      {grid.truncated && (
        <p className="px-3 pt-2 text-[10.5px] text-warn font-mono">
          Showing the first {MAX_DAYS} of {grid.totalDays} days. Pick a shorter period to see the rest.
        </p>
      )}
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
                  title={`${grid.dayDates[d]} ${String(h).padStart(2, '0')}:00 · ${
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
          <span>{negLabel}{effMode === 'diverging' ? ` \u2212${grid.maxAbs.toFixed(1)} MW` : ' 0 MW'}</span>
          {effMode === 'diverging' && (
            <>
              <div className="flex">
                {[1, 0.75, 0.5, 0.25, 0].map((a, i) => (
                  <div key={`b${i}`} style={{ width: 18, height: 8, background: shade(BLUE, a) }} />
                ))}
              </div>
              <div className="flex">
                {[0, 0.25, 0.5, 0.75, 1].map((a, i) => (
                  <div key={`r${i}`} style={{ width: 18, height: 8, background: shade(RED, a) }} />
                ))}
              </div>
            </>
          )}
          {effMode === 'unsigned-blue' && (
            <div className="flex">
              {[0.05, 0.25, 0.5, 0.75, 1].map((a, i) => (
                <div key={`u${i}`} style={{ width: 18, height: 8, background: shade(BLUE, a) }} />
              ))}
            </div>
          )}
          {effMode === 'unsigned-red' && (
            <div className="flex">
              {[0.05, 0.25, 0.5, 0.75, 1].map((a, i) => (
                <div key={`u${i}`} style={{ width: 18, height: 8, background: shade(RED, a) }} />
              ))}
            </div>
          )}
          <span>{posLabel} {grid.maxAbs.toFixed(1)} MW · stronger colour is higher</span>
          {hover && (
            <span className="ml-auto font-mono text-text">
              D{hover.day} {String(hover.hour).padStart(2, '0')}:00 · {
                Number.isFinite(hover.mw)
                  ? (hover.mw > 0 ? `${posLabel}: ${hover.mw.toFixed(1)} MW` : hover.mw < 0 ? `${negLabel}: ${(-hover.mw).toFixed(1)} MW` : '0 MW')
                  : 'no data'
              }
            </span>
          )}
        </div>
      </div>
    </ChartFrame>
  )
}

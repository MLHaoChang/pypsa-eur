// The tornado as a chart type of the energy chart kit (ui-modern 02, slice 5;
// VISUALS-1). One row per driver: the result at the driver's low and high
// input, drawn on ONE value axis and split at the base case, so base and
// sensitivity cases share axis, scale, colours and order. Below the base case
// is the danger token, above it the success token; every bar end is also
// written out (result, the −/+ change against the base case, the input that
// gave it), so nothing is read from colour or length alone.
//
// A case with no result is "not established" in words and draws no bar on
// that side; it is never a bar at 0 (ADR-0001).
import { useRef, useState, type ReactNode } from 'react'
import toast from 'react-hot-toast'
import { useElementWidth } from '../../hooks/useElementWidth'
import { money, SYMBOL } from '../../utils/money'
import { downloadCSV, ChartActions, ChartFrame, ChartZero, CHART_TICK } from './shared'

export interface TornadoBar {
  key: string
  label: string
  unit: string | null
  low_value: number
  high_value: number
  /** The result at the low input; `null` is "not established". */
  npv_low: number | null
  npv_high: number | null
  swing: number | null
}

const MINUS = '\u2212'
const NBSP = '\u00a0'

/** Money with its sign written out: "€ 1.2 m", "−€ 350 k". */
export function signedMoney(v: number, currency: string): string {
  return `${v < 0 ? MINUS : ''}${money(v, currency)}`
}

/** A change against the base case, always signed: "+€ 810 k", "−€ 600 k". */
export function deltaMoney(v: number, base: number, currency: string): string {
  const d = v - base
  return `${d < 0 ? MINUS : '+'}${money(d, currency)}`
}

/** The value axis: every known result and the base case, padded; `[lo, hi]`. */
export function tornadoDomain(rows: readonly TornadoBar[], base: number | null): [number, number] | null {
  const vals = [...rows.flatMap(r => [r.npv_low, r.npv_high]), base]
    .filter((v): v is number => v != null && Number.isFinite(v))
  if (!vals.length) return null
  const lo = Math.min(...vals), hi = Math.max(...vals)
  const pad = hi > lo ? (hi - lo) * 0.04 : Math.max(Math.abs(hi) * 0.05, 1)
  return [lo - pad, hi + pad]
}

/** Round tick values inside `[lo, hi]`, about `count` of them. */
export function niceTicks(lo: number, hi: number, count: number): { ticks: number[]; step: number } {
  const raw = (hi - lo) / Math.max(1, count)
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 5, 10].map(m => m * mag).find(s => s >= raw) ?? raw
  const ticks: number[] = []
  for (let t = Math.ceil(lo / step) * step; t <= hi + step * 1e-9; t += step) ticks.push(Number(t.toPrecision(12)))
  return { ticks, step }
}

/** An axis tick in the money notation without the currency (it is on the axis
 *  once), with enough decimals to tell neighbouring ticks apart: "3.52 m". */
export function tickText(v: number, step: number, span: number): string {
  const [scale, suffix] = span >= 1e6 ? [1e6, `${NBSP}m`] : span >= 1e3 ? [1e3, `${NBSP}k`] : [1, '']
  const digits = Math.min(3, Math.max(0, Math.ceil(-Math.log10(step / scale) - 1e-9)))
  const body = (Math.abs(v) / scale).toFixed(digits)
  return `${v < 0 && Number(body) !== 0 ? MINUS : ''}${body}${suffix}`
}

interface End { which: 'Low' | 'High'; input: number; npv: number | null }

/** The two ends of a row, left then right: the lower result on the left. A
 *  case that is not established takes the side its known partner leaves free. */
export function rowEnds(r: TornadoBar, base: number | null): [End, End] {
  const low: End = { which: 'Low', input: r.low_value, npv: r.npv_low }
  const high: End = { which: 'High', input: r.high_value, npv: r.npv_high }
  if (low.npv != null && high.npv != null) return low.npv <= high.npv ? [low, high] : [high, low]
  if (low.npv == null && high.npv == null) return [low, high]
  const [known, unknown] = low.npv != null ? [low, high] : [high, low]
  return base != null && (known.npv as number) > base ? [unknown, known] : [known, unknown]
}

/** Break a label into lines of at most `max` characters, at spaces. */
export function wrapLabel(text: string, max: number): string[] {
  const lines: string[] = []
  let line = ''
  for (const word of text.split(/\s+/)) {
    if (line && line.length + 1 + word.length > max) { lines.push(line); line = word }
    else line = line ? `${line} ${word}` : word
  }
  if (line) lines.push(line)
  return lines
}

export interface TornadoChartProps {
  title: string
  /** What the bars measure, for the axis and the base line: "Battery NPV". */
  measure: string
  rows: readonly TornadoBar[]
  /** The base case every row departs from; `null` when it is not established. */
  base: number | null
  currency?: string
  /** A driver's input value with its unit, as the page writes it elsewhere. */
  formatInput: (value: number, unit: string | null) => string
  /** Fix the value axis, so two options side by side share one scale. */
  domain?: [number, number]
  /** A sentence above the plot. */
  note?: ReactNode
  /** Shown under the plot (per-driver notes). */
  children?: ReactNode
  /** File name for the SVG and CSV exports, without the extension. */
  filename: string
  testId?: string
}

const PAD = { left: 8, right: 8, top: 30, bottom: 30 }
const LINE = 13      // a line of label or end text
const BAR = 12
const GAP = 12
const FALLBACK_WIDTH = 720   // jsdom, and the first paint before the width is measured
const TEXT = { fontSize: 11, fill: 'var(--color-text)' }
const SMALL = { fontSize: 10.5, fill: 'var(--color-muted)', fontFamily: 'var(--font-family-mono)' }

export default function TornadoChart({
  title, measure, rows, base, currency = 'EUR', formatInput, domain, note, children, filename, testId,
}: TornadoChartProps) {
  const chartRef = useRef<HTMLDivElement | null>(null)
  const [box, setBox] = useState<HTMLDivElement | null>(null)
  const width = Math.max(280, useElementWidth(box) ?? FALLBACK_WIDTH)
  const dom = domain ?? tornadoDomain(rows, base)
  const top = rows.reduce<TornadoBar | null>((m, r) => (r.swing != null && (m?.swing == null || r.swing > m.swing) ? r : m), null)

  if (!rows.length || !dom) {
    return (
      <ChartFrame testId={testId} title={title} bodyClassName="p-3">
        {note && <p className="text-[11px] text-muted mb-2">{note}</p>}
        {rows.length > 0 && (
          <ul className="flex flex-col gap-1 mb-2">
            {rows.map(r => (
              <li key={r.key} data-testid="tornado-bar" data-key={r.key}>
                {r.label}: <span data-testid="tornado-values">Low case: not established · High case: not established</span>
              </li>
            ))}
          </ul>
        )}
        <ChartZero>{rows.length ? 'No case has a result yet.' : 'No driver has been checked yet.'}</ChartZero>
        {children}
      </ChartFrame>
    )
  }

  const [lo, hi] = dom
  const plotW = width - PAD.left - PAD.right
  const x = (v: number) => PAD.left + ((Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo || 1)) * plotW
  const narrow = width < 560
  const { ticks, step } = niceTicks(lo, hi, narrow ? 3 : 5)
  const span = Math.max(Math.abs(lo), Math.abs(hi))
  const zeroIn = lo <= 0 && hi >= 0
  const labelChars = Math.max(16, Math.floor((plotW - 110) / 6.2))

  // Lay the rows out top to bottom; a long driver label wraps.
  let y = PAD.top
  const laid = rows.map(r => {
    const lines = wrapLabel(r.label, labelChars)
    const at = y
    const barY = at + lines.length * LINE + 3
    const endsY = barY + BAR + LINE
    y = endsY + (narrow ? LINE : 0) + GAP
    return { r, lines, at, barY, endsY }
  })
  const axisY = y
  const height = axisY + PAD.bottom
  const anyMissing = rows.some(r => r.npv_low == null || r.npv_high == null)

  const endText = (e: End, unit: string | null) => (e.npv == null
    ? `${e.which} case: not established`
    : `${e.which} ${formatInput(e.input, unit)}: ${signedMoney(e.npv, currency)}`
      + (base != null ? ` (${deltaMoney(e.npv, base, currency)})` : ''))
  // Keep a line's label inside the plot.
  const clampX = (px: number, half: number) => Math.min(width - PAD.right - half, Math.max(PAD.left + half, px))
  const baseLabel = base != null ? `Base case ${signedMoney(base, currency)}` : ''

  return (
    <ChartFrame
      testId={testId}
      title={title}
      figure={top?.swing != null ? `${money(top.swing, currency)} largest swing` : undefined}
      hint={top ? `· ${top.label}` : undefined}
      right={
        <ChartActions
          chartRef={chartRef}
          onExportCSV={() => {
            downloadCSV(`${filename}.csv`,
              ['driver', 'input_low', 'input_high', 'unit', `npv_low_${currency}`, `npv_high_${currency}`, `swing_${currency}`],
              rows.map(r => [r.label, r.low_value, r.high_value, r.unit ?? '', r.npv_low ?? '', r.npv_high ?? '', r.swing ?? '']))
            toast.success('Exported')
          }}
          filename={filename} />
      }
      bodyClassName="p-3"
    >
      {note && <p className="text-[11px] text-muted mb-2">{note}</p>}
      <div ref={el => { chartRef.current = el; setBox(el) }}>
        <svg width={width} height={height} role="list" aria-label={title} data-testid="tornado-plot">
          {/* The value axis: grid, ticks, and the unit once. */}
          <g aria-hidden="true">
            {ticks.map(t => (
              <g key={t}>
                <line x1={x(t)} x2={x(t)} y1={PAD.top} y2={axisY} stroke="var(--color-border)" strokeWidth={1} />
                <text x={clampX(x(t), 22)} y={axisY + 12} textAnchor="middle" {...CHART_TICK}>{tickText(t, step, span)}</text>
              </g>
            ))}
            <line x1={PAD.left} x2={width - PAD.right} y1={axisY} y2={axisY} stroke="var(--color-axis)" strokeWidth={1} />
            <text x={PAD.left} y={axisY + 25} textAnchor="start" fontSize={10.5} fill="var(--color-axis)">
              {`${measure}, ${SYMBOL[currency] ?? currency}`}
            </text>
          </g>
          {/* Zero and the base case sit on separate lines of text, so they never collide. */}
          {zeroIn && (
            <g data-testid="tornado-zero">
              <line x1={x(0)} x2={x(0)} y1={PAD.top - 4} y2={axisY} stroke="var(--color-axis)" strokeWidth={1} strokeDasharray="3 3" />
              <text x={clampX(x(0), 34)} y={PAD.top - 7} textAnchor="middle" {...SMALL}>{`Zero ${measure}`}</text>
            </g>
          )}
          {base != null && (
            <g data-testid="tornado-base">
              <line x1={x(base)} x2={x(base)} y1={PAD.top - 16} y2={axisY} stroke="var(--color-text)" strokeWidth={1.2} />
              <text x={clampX(x(base), baseLabel.length * 3.3)} y={10} textAnchor="middle" fontSize={10.5}
                fontWeight={600} fill="var(--color-text)" fontFamily="var(--font-family-mono)">{baseLabel}</text>
            </g>
          )}
          {laid.map(({ r, lines, at, barY, endsY }) => {
            const [left, right] = rowEnds(r, base)
            const known = [r.npv_low, r.npv_high].filter((v): v is number => v != null)
            const a = known.length ? Math.min(...known, ...(known.length === 1 && base != null ? [base] : [])) : null
            const b = known.length ? Math.max(...known, ...(known.length === 1 && base != null ? [base] : [])) : null
            const seg = (from: number, to: number, fill: string, side: string) => (
              <rect key={side} data-side={side} x={Math.min(x(from), x(to) - 2)} y={barY}
                width={Math.max(2, x(to) - x(from))} height={BAR} rx={1.5} fill={fill} />
            )
            return (
              <g key={r.key} role="listitem" data-testid="tornado-bar" data-key={r.key}>
                <text x={PAD.left} y={at + 10} {...TEXT} fontWeight={500}>
                  {lines.map((l, i) => <tspan key={i} x={PAD.left} dy={i ? LINE : 0}>{l}</tspan>)}
                </text>
                <text x={width - PAD.right} y={at + 10} textAnchor="end" {...SMALL}>
                  {r.swing != null ? `swing ${money(r.swing, currency)}` : 'swing not established'}
                </text>
                {a != null && b != null && (base == null
                  ? seg(a, b, 'var(--color-series-1)', 'range')
                  : [
                    a < base && seg(a, Math.min(b, base), 'var(--color-danger)', 'below'),
                    b > base && seg(Math.max(a, base), b, 'var(--color-success)', 'above'),
                  ])}
                <g data-testid="tornado-values">
                  <text x={PAD.left} y={endsY} textAnchor="start" {...SMALL} fill="var(--color-text)">
                    {endText(left, r.unit)}
                  </text>
                  <text x={width - PAD.right} y={endsY + (narrow ? LINE : 0)} textAnchor="end" {...SMALL} fill="var(--color-text)">
                    {endText(right, r.unit)}
                  </text>
                </g>
              </g>
            )
          })}
        </svg>
      </div>
      {anyMissing && (
        <p className="text-[11px] text-muted mt-1" data-testid="tornado-missing-note">
          A driver with a case that is not established has no swing and is listed last; that side draws no bar.
        </p>
      )}
      {children}
    </ChartFrame>
  )
}

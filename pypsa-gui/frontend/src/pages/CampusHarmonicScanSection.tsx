// The campus run's harmonic impedance scan from the PCC (DC-SURFACE-1, DC grid readiness 12 part 3): the
// |Z|(h) envelope over the short-circuit cases and capacitor states, the characteristic orders marked, and each
// parallel resonance against the grid code's clearance. Screening only: the run computes it after sizing; a
// run from before the scan stage, or a scan that could not run, says so instead of drawing an empty chart.
import {
  CartesianGrid, Line, LineChart, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import type { HarmonicResonance, HarmonicScan } from '../api/campusElectrical'
import { PageSection, Tag } from '../components/PageKit'
import { VerdictChip } from '../components/gridReadiness/GridReadinessScorecard'
import { CHART_AXIS, CHART_GRID, CHART_TOOLTIP, yAxisLabel } from './results/shared'
import { TableScroll } from './CampusInvestmentSection'

const CASE_LABEL: Record<string, string> = {
  max: 'Maximum short circuit', min: 'Minimum short circuit', normal: 'Normal short circuit',
}

function fig(v: number | null | undefined, digits = 3): string {
  return v == null || !isFinite(v) ? '—' : `${Number(v.toPrecision(digits))}`
}

/** A figure and its unit, kept together; a missing figure is "—" alone, with no unit after it. */
function figUnit(v: number | null | undefined, unit: string, digits = 3): string {
  const f = fig(v, digits)
  return f === '—' ? f : `${f}\u00A0${unit}`
}

/** 6k±1 up to `hMax`: 5, 7, 11, 13 … */
export function characteristicOrders(hMax: number): number[] {
  const out: number[] = []
  for (let k = 1; 6 * k - 1 <= hMax; k++) {
    out.push(6 * k - 1)
    if (6 * k + 1 <= hMax) out.push(6 * k + 1)
  }
  return out
}

function ResonanceRow({ r }: { r: HarmonicResonance }) {
  return (
    <tr data-testid="scan-resonance" className="border-t border-border">
      <td className="py-1 pr-3 align-top whitespace-nowrap">
        {CASE_LABEL[r.case] ?? r.case}
        {r.cap_state && <span className="block text-muted">{r.cap_state}</span>}
      </td>
      <td className="pr-3 align-top whitespace-nowrap tabular-nums">
        h {fig(r.h_r)} · {figUnit(r.f_hz, 'Hz', 4)} · |Z| {figUnit(r.z_peak_ohm, 'Ω')}
      </td>
      <td className="pr-3 align-top whitespace-nowrap tabular-nums">
        h {r.nearest_order} <span className="text-muted">({r.order_set})</span>
      </td>
      <td data-testid="scan-clearance" className="pr-3 align-top whitespace-nowrap tabular-nums">
        {figUnit(r.margin_h, 'h', 2)} · limit ±{figUnit(r.limit_h, 'h')}
      </td>
      <td className="align-top">
        <span className="inline-flex items-center gap-1.5">
          <VerdictChip verdict={r.verdict === 'flag' ? 'fail' : 'pass'} />
          {r.assumed && <Tag tone="warn">assumed</Tag>}
        </span>
      </td>
    </tr>
  )
}

export default function CampusHarmonicScanSection({ scan }: { scan: HarmonicScan | null | undefined }) {
  const title = 'Harmonic impedance scan from the PCC (screening)'
  if (scan == null) {
    return (
      <PageSection title={title}>
        <p data-testid="scan-not-computed" className="px-4 py-3 text-[12px] text-muted">
          Not computed for this run. Rerun the study to add the scan.
        </p>
      </PageSection>
    )
  }
  if (scan.error !== undefined) {
    return (
      <PageSection title={title}>
        <p data-testid="scan-error" className="px-4 py-3 text-[12px] text-warn">
          The scan could not run for this campus: {scan.error}. The sizing and checks above still stand.
        </p>
      </PageSection>
    )
  }
  const hMax = scan.envelope.length ? scan.envelope[scan.envelope.length - 1].h : 50
  const orders = characteristicOrders(hMax)
  const fails = scan.resonance.filter(r => r.verdict === 'flag').length
  return (
    <PageSection title={title}
      hint={scan.resonance.length
        ? `${scan.resonance.length} ${scan.resonance.length === 1 ? 'resonance' : 'resonances'} · ${fails} inside the clearance`
        : 'no resonance found'}>
      <div className="px-4 py-3 flex flex-col gap-2">
        <div data-testid="scan-chart" className="h-[220px]">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={scan.envelope} margin={{ top: 18, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid {...CHART_GRID} />
              <XAxis dataKey="h" type="number" domain={[1, hMax]} ticks={orders} {...CHART_AXIS}
                tickFormatter={(h: number) => `${h}`} />
              <YAxis {...CHART_AXIS} label={yAxisLabel('|Z| max over the cases (Ω)')} />
              <Tooltip {...CHART_TOOLTIP} labelFormatter={(h: number) => `h ${h}`}
                formatter={(v: number) => [`${fig(v)} Ω`, '|Z| max']} />
              {orders.map(h => (
                <ReferenceLine key={h} x={h} stroke="var(--color-axis)" strokeDasharray="1 3" strokeWidth={1} />
              ))}
              <Line type="monotone" dataKey="z_max_ohm" dot={false} isAnimationActive={false}
                stroke="var(--color-text)" strokeWidth={1.25} />
              {scan.resonance.map((r, i) => (
                <ReferenceDot key={i} x={r.h_r} y={r.z_peak_ohm} r={3.5}
                  fill={r.verdict === 'flag' ? 'var(--color-danger)' : 'var(--color-muted)'} stroke="none" />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
        <p data-testid="scan-caption" className="text-[11px] text-muted">
          Dotted lines: the characteristic orders 6k±1 (5, 7, 11, 13 …). Dots: the resonances, red inside the
          clearance.
        </p>
        {scan.resonance.length > 0 ? (
          <TableScroll>
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-left text-muted">
                  <th className="font-normal pr-3">Case</th><th className="font-normal pr-3">Resonance</th>
                  <th className="font-normal pr-3">Nearest order</th><th className="font-normal pr-3">Clearance</th>
                  <th className="font-normal">Verdict</th>
                </tr>
              </thead>
              <tbody>
                {scan.resonance.map((r, i) => <ResonanceRow key={i} r={r} />)}
              </tbody>
            </table>
          </TableScroll>
        ) : (
          <p data-testid="scan-none" className="text-[12px] text-muted">
            No parallel resonance up to h {fig(hMax)} in any case.
          </p>
        )}
        <div data-testid="scan-limit" className="text-[11px] text-muted flex items-start gap-1.5">
          {scan.clause
            ? <><Tag tone={scan.limit_source === 'code' ? 'accent' : 'warn'}>{scan.limit_source}</Tag>
              <span>Clearance ±{fig(scan.limit_h)}&nbsp;h: {scan.clause}</span></>
            : <><Tag tone="warn">assumed</Tag>
              <span>The grid code sets no resonance clearance, so the screen takes ±{fig(scan.limit_h)}&nbsp;h.</span></>}
        </div>
        {scan.notes.length > 0 && (
          <p data-testid="scan-notes" className="text-[11px] text-muted">{scan.notes.join('. ')}.</p>
        )}
        <details className="text-[11px] text-muted">
          <summary className="cursor-pointer">Basis: {scan.label}</summary>
          <ul className="list-disc pl-4 mt-1">
            {scan.basis.map(b => <li key={b}>{b}</li>)}
          </ul>
        </details>
      </div>
    </PageSection>
  )
}

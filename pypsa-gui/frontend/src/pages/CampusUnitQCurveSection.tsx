// The campus run's per-unit reactive capability check (DC 15): each IBR unit's inverter circle against the grid
// code's per-unit curve, at the curve's breakpoints and at rated P. The data keeps gridspine's pass / fail /
// reported words; "reported" (no rating, or a point the curve does not state) shows as not checked, never pass.
// Absent when the grid code has no curve; a run from before the table was kept says so.
import type { IbrQCurveRow } from '../api/campusElectrical'
import { PageSection, Tag } from '../components/PageKit'
import { VerdictChip } from '../components/gridReadiness/GridReadinessScorecard'
import { TableScroll } from './CampusInvestmentSection'

const TITLE = 'Unit reactive capability'
const BASIS = "screening at unit terminals: √(S² − P²) per inverter against the grid code's per-unit curve × rated P, "
  + 'at each checked active power; collector and transformer reactive losses are not included.'

function fig(v: number | null | undefined, digits = 3): string {
  return v == null || !isFinite(v) ? '—' : `${Number(v.toPrecision(digits))}`
}

/** A signed figure with a true minus: "−0.33", "+0.33", "0". */
function signed(v: number): string {
  const f = fig(Math.abs(v))
  return v > 0 ? `+${f}` : v < 0 ? `−${f}` : f
}

/** Two decimals, as the Available column and the engine's detail line. */
function mvar(v: number): string {
  return v.toFixed(2)
}

/** A real shortfall that rounds to 0.00 is "under 0.01", never "0.00 Mvar short". */
function shortMvar(v: number): string {
  return mvar(v) === '0.00' ? 'under 0.01' : mvar(v)
}

function signed2(v: number): string {
  return v > 0 ? `+${mvar(v)}` : v < 0 ? `−${mvar(-v)}` : mvar(v)
}

/** A side counts as short only above this many Mvar, as in gridspine's campus_reactive: a band edge equal to the
 * circle up to float noise is met, never "0.00 Mvar short". */
const SHORT_EPS = 1e-9

/** A fail row's detail in the table's own number style; the other rows keep the engine's words. */
function detailText(r: IbrQCurveRow): string {
  if (r.status !== 'fail' || r.available_mvar == null || r.shortfall_mvar == null
    || r.q_min_req_mvar == null || r.q_max_req_mvar == null) return r.detail
  const deliver = Math.max(0, r.q_max_req_mvar - r.available_mvar)
  const absorb = Math.max(0, -r.q_min_req_mvar - r.available_mvar)
  const short = shortMvar(deliver) === shortMvar(absorb) && deliver > SHORT_EPS && absorb > SHORT_EPS ? `${shortMvar(deliver)} Mvar short each way`
    : [deliver > SHORT_EPS && `${shortMvar(deliver)} Mvar short to deliver`,
      absorb > SHORT_EPS && `${shortMvar(absorb)} Mvar short to absorb`]
      .filter(Boolean).join(' and ') || `${shortMvar(r.shortfall_mvar)} Mvar short`
  return `±${mvar(r.available_mvar)} Mvar available for ${signed2(r.q_min_req_mvar)} to ${signed2(r.q_max_req_mvar)} Mvar: ${short}`
}

function power(r: IbrQCurveRow): string {
  return r.p_pu == null ? '—' : `${fig(r.p_pu)} pu · ${fig(r.p_mw)} MW`
}

function required(r: IbrQCurveRow): string {
  return r.q_min_req_mvar == null || r.q_max_req_mvar == null
    ? '—' : `${signed(r.q_min_req_mvar)} to ${signed(r.q_max_req_mvar)} Mvar`
}

function available(r: IbrQCurveRow): string {
  return r.available_mvar == null ? '—' : `±${fig(r.available_mvar)} Mvar`
}

function shortfall(r: IbrQCurveRow): string {
  return r.shortfall_mvar == null || r.shortfall_mvar <= 0 ? '—' : `${fig(r.shortfall_mvar)} Mvar`
}

/** By unit, then active power ascending; a unit's no-rating row (no P) first. */
export function orderRows(rows: IbrQCurveRow[]): IbrQCurveRow[] {
  return [...rows].sort((a, b) => String(a.unit).localeCompare(String(b.unit))
    || (a.p_pu ?? -Infinity) - (b.p_pu ?? -Infinity))
}

function Row({ r }: { r: IbrQCurveRow }) {
  const shown = r.status !== 'pass'      // (only 'pass' shows Pass; an unknown status fails closed)
  // the detail of a fail or a not-checked row is text, not a tooltip
  return (
    <>
      <tr data-testid={`qcurve-${r.unit}-${r.p_pu ?? 'none'}`} data-status={r.status}
        title={shown ? undefined : r.detail} className="border-t border-border">
        <td className="py-1 pr-3 align-top">{r.unit}</td>
        <td className="pr-3 align-top whitespace-nowrap tabular-nums">{power(r)}</td>
        <td className="pr-3 align-top whitespace-nowrap tabular-nums">{required(r)}</td>
        <td className="pr-3 align-top whitespace-nowrap tabular-nums">{available(r)}</td>
        <td className="pr-3 align-top whitespace-nowrap tabular-nums">{shortfall(r)}</td>
        <td className="align-top">
          <span className="inline-flex items-center gap-1.5">
            {r.status === 'reported'
              ? <Tag tone="neutral">Not checked · reported only</Tag>
              : <VerdictChip verdict={r.status === 'pass' ? 'pass' : 'fail'} />}
            {r.source !== 'code' && <Tag tone="warn">{r.source}</Tag>}
          </span>
        </td>
      </tr>
      {shown && (
        <tr data-testid={`qcurve-detail-${r.unit}-${r.p_pu ?? 'none'}`}>
          <td colSpan={6} className="pb-1 text-[11px] text-muted">{detailText(r)}</td>
        </tr>
      )}
    </>
  )
}

export default function CampusUnitQCurveSection({ rows, notComputed }: {
  rows: IbrQCurveRow[] | null | undefined
  notComputed?: boolean
}) {
  if (rows == null || rows.length === 0) {
    if (!notComputed) return null
    return (
      <PageSection title={TITLE}>
        <p data-testid="qcurve-not-computed" className="px-4 py-3 text-[12px] text-muted">
          Not computed for this run. Rerun the study to add the check.
        </p>
      </PageSection>
    )
  }
  const short = rows.filter(r => r.status === 'fail').length
  const first = rows[0]
  return (
    <PageSection title={TITLE}
      hint={`${rows.length} ${rows.length === 1 ? 'check' : 'checks'} · ${short} short`}>
      <div className="px-4 py-3 flex flex-col gap-2">
        <TableScroll>
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-left text-muted">
                <th className="font-normal pr-3">Unit</th><th className="font-normal pr-3">Active power</th>
                <th className="font-normal pr-3">Required</th><th className="font-normal pr-3">Available</th>
                <th className="font-normal pr-3">Shortfall</th><th className="font-normal">Verdict</th>
              </tr>
            </thead>
            <tbody>
              {orderRows(rows).map(r => <Row key={`${r.unit}-${r.p_pu ?? 'none'}`} r={r} />)}
            </tbody>
          </table>
        </TableScroll>
        <div data-testid="qcurve-clause" className="text-[11px] text-muted flex items-start gap-1.5">
          <Tag tone={first.source === 'code' ? 'accent' : 'warn'}>{first.source}</Tag>
          <span>Curve: {first.clause}</span>
        </div>
        <p className="text-[11px] text-muted">Basis: {BASIS}</p>
      </div>
    </PageSection>
  )
}

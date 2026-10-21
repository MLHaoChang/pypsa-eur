// Results → Investment → Investment case: the owner's operating cash by
// stream and year (IPP ticket 05), from the project section's
// `revenue_streams`. Read as served: owner-signed (income +, costs −), a
// null cell is a dash, never 0; the view computes nothing.
import type { InvestmentCaseReportPayload, RevenueStreamRow, RevenueStreams } from '../../../api/types'
import { useUIStore } from '../../../store/uiStore'
import { downloadCSV } from '../shared'
import { sectionOf } from './financeModel'
import { fmtAmount } from './valueFlows'

const DASH = '—'

/** The report's revenue streams, or null when it does not establish them. */
export function revenueStreams(report: InvestmentCaseReportPayload): RevenueStreams | null {
  const v = sectionOf(report, 'project')?.payload?.revenue_streams
  return v && typeof v === 'object' && Array.isArray((v as RevenueStreams).years)
    ? v as RevenueStreams : null
}

const cell = (v: number | null | undefined) => (v == null ? DASH : fmtAmount(v))

export default function RevenueByStream({ report }: { report: InvestmentCaseReportPayload }) {
  const requestResultsTab = useUIStore(s => s.requestResultsTab)
  const project = sectionOf(report, 'project')
  const payload = (project?.payload ?? {}) as Record<string, unknown>
  const streams = revenueStreams(report)
  const currency = typeof payload.currency === 'string' ? payload.currency : null
  const basis = typeof payload.basis_statement === 'string' && payload.basis_statement
    ? payload.basis_statement : null
  const link = (
    <button type="button" className="text-[11px] underline" data-testid="ic-revenue-to-curtailment"
            onClick={() => requestResultsTab('curtailment')}>
      Curtailment by cause → Operation › Curtailment</button>
  )

  if (!streams) {
    return (
      <section className="space-y-1" data-testid="ic-revenue-unavailable">
        <h3 className="text-[11px] font-semibold">Revenue by stream</h3>
        <p className="text-muted">
          Revenue by stream is not established: {project?.note ?? 'the operating cash is not established.'}</p>
        <p>{link}</p>
      </section>
    )
  }

  // The streams as columns beside the year, as the cash flows table reads.
  const cols: RevenueStreamRow[] = [...streams.rows, streams.total, streams.replacements]
  const csv = () => downloadCSV('investment_case_revenue_streams.csv',
    ['year', ...cols.map(c => c.id)],
    streams.years.map((y, i) => [y, ...cols.map(c => c.values[i] ?? '')]))
  const tone = (c: RevenueStreamRow) => c.id === streams.total.id ? 'font-semibold'
    : c.id === streams.replacements.id ? 'border-l border-border pl-2' : ''

  return (
    <section className="space-y-1">
      <div className="flex justify-between items-center">
        <h3 className="text-[11px] font-semibold">Revenue by stream</h3>
        <button type="button" className="text-[11px] underline" onClick={csv}>
          Export the revenue by stream (CSV)</button>
      </div>
      <p className="text-muted" data-testid="ic-revenue-units">
        {currency ?? 'Currency not stated'} per year{basis ? `; ${basis}` : ''}</p>
      <div className="overflow-x-auto">
        <table className="text-[11px] w-full" data-testid="ic-revenue-streams">
          <caption className="text-left text-muted">+ income, − costs; a dash is not established</caption>
          <thead><tr>
            <th scope="col" className="text-left">Year</th>
            {cols.map(c => (
              <th key={c.id} scope="col" className={`text-right ${tone(c)}`}
                  data-testid={`ic-revenue-col-${c.id}`}>{c.label}</th>
            ))}
          </tr></thead>
          <tbody>{streams.years.map((y, i) => (
            <tr key={y} data-testid={`ic-revenue-${y}`}>
              <th scope="row" className="text-left font-normal">{y}</th>
              {cols.map(c => (
                <td key={c.id} className={`text-right ${tone(c)}`}
                    data-established={c.values[i] != null}>{cell(c.values[i])}</td>
              ))}
            </tr>
          ))}</tbody>
        </table>
      </div>
      {streams.other_keys.length > 0 && (
        <p className="text-muted" data-testid="ic-revenue-other-keys">
          Other includes: {streams.other_keys.join(', ')}</p>
      )}
      <p>{link}</p>
    </section>
  )
}

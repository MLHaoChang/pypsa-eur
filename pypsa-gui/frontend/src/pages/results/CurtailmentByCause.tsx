// Results → Operation → Curtailment: each plant's curtailment over the
// horizon split by cause — the export limit, a negative price, both, or
// neither (IPP ticket 05). The split is the backend's; this view only lays it
// out. When the split is not established the plant totals are still shown,
// with one sentence saying why.
import { useQuery } from '@tanstack/react-query'
import { resultsApi, type CurtailmentByCausePayload, type CurtailmentCauses } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'

const CAUSES: ReadonlyArray<[keyof CurtailmentCauses, string]> = [
  ['export_limit', 'Export limit'], ['negative_price', 'Negative price'], ['both', 'Both'], ['other', 'Other'],
]

export const CAUSE_RULE = 'An hour counts as “both” when the export limit binds and the price is negative; '
  + '“other” is curtailment when neither applies.'

export const NOT_ESTABLISHED_REASON: Record<string, string> = {
  export_link_missing: 'No export link is set in the commercial settings, so curtailment is not split by cause.',
  export_price_missing: 'No export price is set, so curtailment is not split by cause.',
  no_commercial_solve_record: 'The last run did not price the export, so curtailment is not split by cause.',
  export_price_changed_since_solve: 'The export price changed since the last run; run again to split curtailment by cause.',
}

const fmtMwh = (v: number) => (Number.isFinite(v)
  ? v.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : '—')
const fmtShare = (v: number, total: number) => (total > 0 ? `${((v / total) * 100).toFixed(1)} %` : '—')

/** An energy cell: MWh, and its share of `total` on a second line. */
function EnergyCell({ mwh, total, of }: { mwh: number; total: number; of: string }) {
  return (
    <td className="py-1 text-right font-mono text-text">
      {fmtMwh(mwh)}
      <div className="text-[10px] text-muted" title={`share of ${of}`}>{fmtShare(mwh, total)}</div>
    </td>
  )
}

export function CurtailmentByCauseTable({ payload }: { payload: CurtailmentByCausePayload }) {
  const requestResultsTab = useUIStore(s => s.requestResultsTab)
  const split = payload.status === 'ok'
  const plants = Object.entries(payload.plants ?? {})
  return (
    <section className="rounded-[10px] border border-border bg-bg overflow-hidden"
             data-testid="curtailment-by-cause" data-status={payload.status}>
      <header className="px-4 py-2.5 border-b border-border bg-bg-2 flex justify-between items-center">
        <span className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Curtailment by cause</span>
        <button type="button" className="text-[11px] underline" data-testid="curtailment-to-revenue"
                onClick={() => requestResultsTab('investment', 'case')}>← Revenue by stream</button>
      </header>
      <div className="p-3 space-y-2">
        {!split && (
          <p className="text-[11px] text-muted" data-testid="curtailment-by-cause-reason">
            {NOT_ESTABLISHED_REASON[payload.reason]
              ?? `Curtailment is not split by cause: ${payload.reason}.`}</p>
        )}
        {plants.length > 0 ? (
          <table className="w-full text-[11.5px]">
            <thead>
              <tr className="border-b border-border text-muted">
                <th className="text-left py-1 font-medium">Plant</th>
                {split && CAUSES.map(([k, l]) => (
                  <th key={k} className="text-right py-1 font-medium">{l} (MWh)</th>
                ))}
                <th className="text-right py-1 font-medium">Total (MWh)</th>
              </tr>
            </thead>
            <tbody>
              {plants.map(([name, p]) => (
                <tr key={name} className="border-b border-border/40" data-testid={`curtailment-cause-${name}`}>
                  <td className="py-1 text-text font-mono">{name}</td>
                  {split && CAUSES.map(([k]) => (
                    <EnergyCell key={k} mwh={p.by_cause_mwh?.[k] ?? NaN} total={p.total_mwh}
                                of="the plant's curtailment" />
                  ))}
                  <EnergyCell mwh={p.total_mwh} total={p.available_mwh} of="the plant's available energy" />
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-[11px] text-muted">No plant can be curtailed.</p>
        )}
        <p className="text-[11px] text-muted">
          {split ? "Each cause's share is of the plant's curtailment; the total's, of its available energy."
            : "The total's share is of the plant's available energy."}</p>
        {split && <p className="text-[11px] text-muted" data-testid="curtailment-cause-rule">{CAUSE_RULE}</p>}
      </div>
    </section>
  )
}

export default function CurtailmentByCause() {
  const project = useUIStore(s => s.currentProject)
  const { data } = useQuery({ queryKey: nk(project, 'results', 'curtailment', 'by_cause'),
                              queryFn: () => resultsApi.getCurtailmentByCause() })
  return data ? <CurtailmentByCauseTable payload={data} /> : null
}

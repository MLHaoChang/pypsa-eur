// Results → Investment (IC P3 WP3.5; spec §12). The shell of the investment
// case: completeness chips from the commercial results, and the sections the
// P3 editors fill (Participants WP3.6, Library WP3.7a, Tariff WP3.7b, Contracts
// WP3.7c). A result the server does not establish is shown as such — never as
// a zero (ADR-0001).
import { useRef, useState, type KeyboardEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { commercialApi, type BillingPayload, type ValueFlowsPayload } from '../../api/commercial'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { CompletenessChips, type CompletenessRow } from '../../components/CompletenessChips'
import { fmtCurrency } from './shared'

const SECTIONS = [
  { id: 'participants', label: 'Participants' },
  { id: 'bill', label: 'Bill' },
  { id: 'library', label: 'Library' },
  { id: 'tariff', label: 'Tariff' },
  { id: 'contracts', label: 'Contracts' },
] as const
type SectionId = typeof SECTIONS[number]['id']

export function completeness(billing: BillingPayload | null | undefined,
                             flows: ValueFlowsPayload | null | undefined): CompletenessRow[] {
  const billOk = !!billing && Object.values(billing.summary ?? {}).some(v => v && v.total != null)
  const participants = flows?.status === 'ok' ? 'ok' : 'not_established'
  const cons = flows?.conservation_ok
  return [
    { name: 'bill', status: billOk ? 'ok' : 'not_established',
      note: billing ? null : 'no bill: solve with a commercial config first' },
    { name: 'participants', status: participants,
      note: flows?.status === 'ok' ? null : (flows?.reason ?? 'no value-flow result') },
    { name: 'conservation', status: cons === true ? 'ok' : cons === false ? 'failed' : 'not_established' },
  ]
}

function BillSection({ billing }: { billing: BillingPayload | null | undefined }) {
  if (!billing) {
    return <p className="text-[11px] text-muted py-2">No bill: solve with a commercial config first.</p>
  }
  const periods = Object.entries(billing.summary ?? {})
  return (
    <div className="space-y-3" data-testid="ic-bill-table">
      {billing.gap_summary?.gates?.length > 0 && (
        <p className="text-[11px] text-warn">Gates: {billing.gap_summary.gates.join(', ')}</p>
      )}
      {periods.map(([period, s]) => (
        <table key={period} className="text-[11px] w-full max-w-md">
          <caption className="text-left text-muted">{period === '_' ? 'Modelled year' : period}</caption>
          <thead><tr><th scope="col" className="text-left">Item</th>
            <th scope="col" className="text-right">Amount</th></tr></thead>
          <tbody>
            {s === null ? (
              <tr><td colSpan={2} className="text-muted">not established</td></tr>
            ) : Object.entries(s.per_item).map(([item, v]) => (
              <tr key={item}>
                <td>{item}</td>
                <td className="text-right">{v == null ? 'not established' : fmtCurrency(v, 2)}</td>
              </tr>
            ))}
            {s && (
              <tr className="border-t border-border">
                <td>Total</td>
                <td className="text-right">{s.total == null ? 'not established' : fmtCurrency(s.total, 2)}</td>
              </tr>
            )}
          </tbody>
        </table>
      ))}
    </div>
  )
}

export default function InvestmentTab() {
  const project = useUIStore(s => s.currentProject)
  const billing = useQuery({ queryKey: nk(project, 'results', 'billing'),
                             queryFn: () => commercialApi.getBilling() })
  const flows = useQuery({ queryKey: nk(project, 'results', 'value_flows'),
                           queryFn: () => commercialApi.getValueFlowsResult() })
  const [section, setSection] = useState<SectionId>('participants')
  const tabs = useRef<Array<HTMLButtonElement | null>>([])

  const onKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
    e.preventDefault()
    const next = (i + (e.key === 'ArrowRight' ? 1 : SECTIONS.length - 1)) % SECTIONS.length
    setSection(SECTIONS[next].id)
    tabs.current[next]?.focus()
  }

  return (
    <div className="space-y-3" data-testid="investment-tab">
      <CompletenessChips rows={completeness(billing.data, flows.data)} testId="ic-completeness"
                         itemTestIdPrefix="ic-section-" label="Investment case completeness" />
      <div role="tablist" aria-label="Investment case sections" className="flex gap-1 border-b border-border">
        {SECTIONS.map((s, i) => (
          <button key={s.id} ref={el => { tabs.current[i] = el }} type="button" role="tab"
                  id={`ic-tab-${s.id}`} aria-selected={section === s.id}
                  aria-controls={`ic-panel-${s.id}`} tabIndex={section === s.id ? 0 : -1}
                  onClick={() => setSection(s.id)} onKeyDown={e => onKey(e, i)}
                  className={`text-[11px] px-2 py-1 ${section === s.id ? 'border-b-2 border-accent' : 'text-muted'}`}>
            {s.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`ic-panel-${section}`} aria-labelledby={`ic-tab-${section}`}>
        {section === 'bill' && <BillSection billing={billing.data} />}
        {section === 'participants' && (
          <p className="text-[11px] text-muted py-2">
            {flows.data?.status === 'ok'
              ? 'Participants and value flows are set.'
              : (flows.data?.reason ?? 'No participants are set for this project.')}
          </p>
        )}
        {(section === 'library' || section === 'tariff' || section === 'contracts') && (
          <p className="text-[11px] text-muted py-2">This section is not available yet.</p>
        )}
      </div>
    </div>
  )
}

// Results → Investment (IC P3 WP3.5; spec §12). The shell of the investment
// case: completeness chips from the commercial results, and the sections the
// P3 editors fill (Participants WP3.6, Library WP3.7a, Tariff WP3.7b, Contracts
// WP3.7c), and the P4 finance case (Finance inputs WP4.7a, Investment case
// WP4.7b). A result the server does not establish is shown as such — never as
// a zero (ADR-0001).
import { useRef, useState, type KeyboardEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  commercialApi, NoCommercialConfigError, SolverInFlightError, type BillingPayload,
  type ValueFlowsPayload,
} from '../../api/commercial'
import type { CommercialConfig } from '../../api/types'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { CompletenessChips, type CompletenessRow } from '../../components/CompletenessChips'
import { fmtAmount } from './investment/valueFlows'
import ValueFlowsView from './investment/ValueFlowsView'
import ParticipantsDesigner from './investment/ParticipantsDesigner'
import TariffBuilder from './investment/TariffBuilder'
import LibraryBrowser from './investment/LibraryBrowser'
import ContractsEditor from './investment/ContractsEditor'
import FinanceInputsEditor from './investment/FinanceInputsEditor'
import InvestmentCaseView from './investment/InvestmentCaseView'
import { blankTariff } from './investment/tariffModel'
import SiteConnectionForm from './SiteConnectionForm'

const SECTIONS = [
  { id: 'participants', label: 'Participants' },
  { id: 'bill', label: 'Bill' },
  { id: 'library', label: 'Library' },
  { id: 'tariff', label: 'Tariff' },
  { id: 'contracts', label: 'Contracts' },
  { id: 'finance', label: 'Finance inputs' },
  { id: 'case', label: 'Investment case' },
] as const
type SectionId = typeof SECTIONS[number]['id']

export { fmtAmount }

/** The error a result query ended in, as the user should read it. */
export function loadFailure(error: unknown): string {
  return error instanceof SolverInFlightError
    ? 'A solve is running; the result follows when it finishes.'
    : error instanceof NoCommercialConfigError
      ? 'Set the site connection first.'
      : 'It could not be loaded.'
}

/** The commercial root (IC U1 follow-up b): the form when it is missing (or a
 *  result says the config is), else a summary line that opens it. */
function SiteConnectionSection({ config, needed }: { config: CommercialConfig | null | undefined
                                                     needed: boolean }) {
  const [editing, setEditing] = useState(false)
  const root = config?.poc_link
    ? { poc_link: config.poc_link, export_link: config.export_link ?? null,
        timezone: config.timezone ?? null }
    : null
  if (needed || !root) {
    return (
      <section className="space-y-2 border border-border rounded p-2" data-testid="ic-site-connection"
               aria-labelledby="ic-site-connection-title">
        <h3 id="ic-site-connection-title" className="text-[12px] font-semibold">Site connection</h3>
        <p className="text-[11px] text-muted">
          Set the site's connection to the grid first: the bill, the participants and the
          investment case are all read at the point of connection.</p>
        <SiteConnectionForm initial={root} />
      </section>
    )
  }
  return (
    <div className="space-y-2" data-testid="ic-site-connection">
      <p className="text-[11px] text-muted" data-testid="ic-site-connection-summary">
        Site connection: imports through {root.poc_link}
        {root.export_link ? `, exports through ${root.export_link}` : ', no export link'}
        {root.timezone ? `, site time ${root.timezone}` : ', snapshots in site time'}.{' '}
        <button type="button" className="underline" aria-expanded={editing}
                onClick={() => setEditing(e => !e)}>Edit site connection</button>
      </p>
      {editing && <SiteConnectionForm initial={root} onSaved={() => setEditing(false)}
                                      onCancel={() => setEditing(false)} />}
    </div>
  )
}

export function completeness(billing: BillingPayload | null | undefined,
                             flows: ValueFlowsPayload | null | undefined,
                             billingError?: unknown): CompletenessRow[] {
  // `ok` only when EVERY period has a total: one missing period of a
  // multi-period bill is a bill not established (never "ok").
  const periods = Object.entries(billing?.summary ?? {})
  const missing = periods.filter(([, v]) => !v || v.total == null).map(([k]) => k)
  const billOk = !!billing && periods.length > 0 && missing.length === 0
  const participants = flows?.status === 'ok' ? 'ok'
    : flows?.status === 'value_flows_invalid' ? 'failed' : 'not_established'
  const cons = flows?.conservation_ok
  return [
    { name: 'bill', label: 'Bill', status: billingError ? 'failed' : billOk ? 'ok' : 'not_established',
      note: billingError ? loadFailure(billingError)
        : !billing ? 'no bill: solve with a commercial config first'
          : missing.length ? `not established for ${missing.join(', ')}` : null },
    { name: 'participants', label: 'Participants', status: participants,
      note: flows?.status === 'ok' ? null : (flows?.reason ?? 'no value-flow result') },
    { name: 'conservation', label: 'Value-flow balance',
      status: cons === true ? 'ok' : cons === false ? 'failed' : 'not_established' },
  ]
}

function BillSection({ billing, error }: { billing: BillingPayload | null | undefined;
                                           error: unknown }) {
  if (error) {
    return <p className="text-[11px] text-warn py-2" data-testid="ic-bill-error">
      The bill: {loadFailure(error)}</p>
  }
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
                <td className="text-right">{fmtAmount(v)}</td>
              </tr>
            ))}
            {s && (
              <tr className="border-t border-border">
                <td>Total</td>
                <td className="text-right">{fmtAmount(s.total)}</td>
              </tr>
            )}
          </tbody>
        </table>
      ))}
    </div>
  )
}

function TariffSection() {
  const project = useUIStore(s => s.currentProject)
  const current = useQuery({ queryKey: nk(project, 'commercial', 'config'),
                             queryFn: () => commercialApi.getCommercial() })
  if (current.isPending) return <p className="text-[11px] text-muted py-2">Loading the tariff…</p>
  // A failed read must not open a blank builder whose save would replace the
  // real tariff (WP3.7b review #11).
  if (current.isError) {
    return <p className="text-[11px] text-warn py-2" data-testid="ic-tariff-error">
      The project's tariff could not be read; reload before editing it.</p>
  }
  const tariff = current.data?.import_tariff
  const ref = current.data?.import_tariff_ref
  return (
    <div className="space-y-2">
      <p className="text-[11px] text-muted">
        {tariff ? `Editing the project's import tariff ${tariff.id}.`
          : 'The project has no import tariff: build one.'}</p>
      {ref && (
        <p className="text-[11px] text-warn" data-testid="ic-tariff-library-ref">
          This tariff is Library item {ref.id} v{ref.version}. Saving it as the project tariff
          detaches it from the Library (the project keeps your edited copy).</p>
      )}
      <TariffBuilder key={tariff?.id ?? 'new'} initial={tariff ?? blankTariff()} />
    </div>
  )
}

export default function InvestmentTab() {
  const project = useUIStore(s => s.currentProject)
  const billing = useQuery({ queryKey: nk(project, 'results', 'billing'),
                             queryFn: () => commercialApi.getBilling() })
  const flows = useQuery({ queryKey: nk(project, 'results', 'value_flows'),
                           queryFn: () => commercialApi.getValueFlowsResult() })
  const commercial = useQuery({ queryKey: nk(project, 'commercial', 'config'),
                                queryFn: () => commercialApi.getCommercial() })
  const [section, setSection] = useState<SectionId>('participants')
  const [designing, setDesigning] = useState(false)
  const tabs = useRef<Array<HTMLButtonElement | null>>([])

  const onKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    const last = SECTIONS.length - 1
    const next = e.key === 'ArrowRight' ? (i + 1) % SECTIONS.length
      : e.key === 'ArrowLeft' ? (i + last) % SECTIONS.length
        : e.key === 'Home' ? 0 : e.key === 'End' ? last : null
    if (next === null) return
    e.preventDefault()
    setSection(SECTIONS[next].id)
    tabs.current[next]?.focus()
  }

  const participantsText = flows.isError
    ? `The value-flow result: ${loadFailure(flows.error)}`
    : flows.data == null
      ? 'No value-flow result yet: solve the project with a commercial config.'
      : flows.data.status === 'ok'
        ? 'Participants and value flows are set.'
        : flows.data.status === 'value_flows_invalid'
          ? `The stored participants do not validate${flows.data.reason ? `: ${flows.data.reason}` : '.'}`
          : (flows.data.reason ?? 'No participants are set for this project.')

  // A result that says there is no (valid) commercial config is not a dead end:
  // the Site connection form is shown in its place.
  const rootMissing = [billing.error, flows.error].some(e => e instanceof NoCommercialConfigError)
    || (commercial.isSuccess && !commercial.data?.poc_link)

  return (
    <div className="space-y-3" data-testid="investment-tab">
      {(commercial.isSuccess || rootMissing) && (
        <SiteConnectionSection config={commercial.data} needed={rootMissing} />
      )}
      <CompletenessChips rows={completeness(billing.data, flows.data,
                                            billing.isError ? billing.error : undefined)}
                         testId="ic-completeness"
                         itemTestIdPrefix="ic-section-" label="Investment case completeness" />
      <div role="tablist" aria-label="Investment case sections" className="flex gap-1 border-b border-border">
        {SECTIONS.map((s, i) => (
          <button key={s.id} ref={el => { tabs.current[i] = el }} type="button" role="tab"
                  id={`ic-tab-${s.id}`} aria-selected={section === s.id}
                  aria-controls={section === s.id ? `ic-panel-${s.id}` : undefined}
                  tabIndex={section === s.id ? 0 : -1}
                  onClick={() => setSection(s.id)} onKeyDown={e => onKey(e, i)}
                  className={`text-[11px] px-2 py-1 ${section === s.id ? 'border-b-2 border-accent' : 'text-muted'}`}>
            {s.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`ic-panel-${section}`} aria-labelledby={`ic-tab-${section}`}
           tabIndex={0}>
        {section === 'bill' && (
          <BillSection billing={billing.data} error={billing.isError ? billing.error : null} />
        )}
        {section === 'participants' && (
          <div className="space-y-3">
            {flows.data?.status === 'ok' ? <ValueFlowsView payload={flows.data} /> : (
              <p className="text-[11px] text-muted py-2" data-testid="ic-participants-state">
                {participantsText}
              </p>
            )}
            <button type="button" className="text-[11px] underline" aria-expanded={designing}
                    onClick={() => setDesigning(d => !d)}>
              {designing ? 'Close the participants designer' : 'Define participants'}
            </button>
            {designing && <ParticipantsDesigner />}
          </div>
        )}
        {section === 'tariff' && <TariffSection />}
        {section === 'library' && <LibraryBrowser />}
        {section === 'contracts' && <ContractsEditor />}
        {section === 'finance' && <FinanceInputsEditor />}
        {section === 'case' && <InvestmentCaseView />}
      </div>
    </div>
  )
}

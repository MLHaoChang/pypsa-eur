// M3 (plan 2026-10-14-m3-results-summary.md): the answer-first Results page.
// One status line and four cards (cost, emissions, adequacy, key capacities),
// each read from an endpoint the detailed tabs already use, with the same
// query keys so the cache is shared, and a link into its tab. Absent is never
// shown as zero: a quantity the solve did not model says so, and a standard
// that was not set is not shown as met.
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowRight } from 'lucide-react'
import { resultsApi, simulationApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { useResultsFilter } from './filterContext'
import { fmtCurrency, fmtEnergy } from './shared'
import { kpiFigure, splitValueUnit } from '../../utils/format'
import {
  capacityRows, costView, emissionsView, reserveMarginVerdict,
  type EmissionsPayload, type LostLoadPayload, type ReserveMarginSummary,
} from './summary'
import type { AdequacyReportPayload } from './adequacy'
import { CARD_GROUPS, type CardGroupId } from './groups'
import { freshnessOf } from './GroupHeader'
import { StatePill } from '../../components/ResultState'
import { AddCardToScope } from '../../components/flowScope/ScopeCards'
import { useFlowScope } from '../../components/flowScope/useFlowScope'
import { inScope, type ScopeCardId } from '../../api/flow'
import type { SimulationStatus } from '../../api/types'

/** The Results tabs a card links to. */
export type SummaryLink = 'capex' | 'emissions' | 'lostload' | 'adequacy' | 'economics' | 'loadflow' | 'investment'

const MAX_CAPACITY_ROWS = 6

function fmtTonnes(t: number): string {
  const a = Math.abs(t)
  if (a >= 1e6) return `${(t / 1e6).toFixed(2)} MtCO₂`
  if (a >= 1e3) return `${(t / 1e3).toFixed(2)} ktCO₂`
  return `${t.toFixed(a >= 1 ? 1 : 3)} tCO₂`
}

function fmtCapacity(v: number, unit: 'MW' | 'MWh'): string {
  if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(2)} ${unit === 'MW' ? 'GW' : 'GWh'}`
  return `${v.toFixed(v >= 10 ? 0 : 1)} ${unit}`
}

function Card({ title, testId, link, linkLabel, onOpen, hint, children }: {
  title: string; testId: string; link: SummaryLink; linkLabel: string
  onOpen: (t: SummaryLink) => void; hint?: string; children: ReactNode
}) {
  return (
    <section data-testid={testId} title={hint}
      className="rounded-[10px] border border-border bg-bg-2 px-4 py-3 flex flex-col gap-2 min-w-0">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[12px] font-semibold text-muted">{title}</h3>
        <button type="button" onClick={() => onOpen(link)}
          className="text-[10.5px] text-accent hover:underline inline-flex items-center gap-1">
          {linkLabel} <ArrowRight size={10} />
        </button>
      </div>
      {children}
    </section>
  )
}

// The card's headline follows the KPI tile standard (UX overhaul S3b,
// components/Kpi.tsx): the figure rounded by kpiFigure (owner sign-off R1),
// its unit smaller on the same unbreakable line. The detail lines below keep
// full precision, as tables do.
function Headline({ value, sub }: { value: string; sub?: ReactNode }) {
  const raw = splitValueUnit(value)
  const parts = kpiFigure(raw.value, raw.unit)
  return (
    <div>
      <div data-kpi-value={parts.unit ? `${parts.value} ${parts.unit}` : parts.value}
        className="flex items-baseline gap-1 whitespace-nowrap">
        <span className="text-[22px] font-semibold leading-none text-text tabular-nums">{parts.value}</span>
        {parts.unit && <span className="text-[13px] text-muted">{parts.unit}</span>}
      </div>
      {sub && <div className="mt-1 text-[11px] text-muted">{sub}</div>}
    </div>
  )
}

function Line({ label, value, tone }: { label: string; value: ReactNode; tone?: 'ok' | 'warn' }) {
  const color = tone === 'ok' ? 'text-success' : tone === 'warn' ? 'text-warn' : 'text-text'
  return (
    <div className="flex items-baseline justify-between gap-2 text-[11px]">
      <span className="text-muted">{label}</span>
      <span className={`font-mono tabular-nums ${color}`}>{value}</span>
    </div>
  )
}

function Absent({ children }: { children: ReactNode }) {
  return <p className="text-[11px] text-muted italic">{children}</p>
}

export default function SummaryTab({ onOpenTab, onOpenGroup }: {
  onOpenTab: (t: SummaryLink) => void
  /** A card with no tab yet (Control, Hand-off) opens as a group. */
  onOpenGroup?: (g: CardGroupId) => void
}) {
  const project = useUIStore(s => s.currentProject)
  const setSlidePanel = useUIStore(s => s.setSlidePanel)
  const requestIoModal = useUIStore(s => s.requestIoModal)
  const { selectedPeriod } = useResultsFilter()
  const { scope: flowScope, isSuccess: scopeRead } = useFlowScope()
  const { data: status } = useQuery({ queryKey: nk(project, 'simulationStatus'), queryFn: simulationApi.getStatus })
  const { data: cost } = useQuery({ queryKey: nk(project, 'results', 'cost_breakdown'), queryFn: resultsApi.getCostBreakdown })
  const { data: emissions } = useQuery({ queryKey: nk(project, 'results', 'emissions'), queryFn: resultsApi.getEmissions })
  const { data: lostLoad, isSuccess: lostLoadRead } = useQuery({
    queryKey: nk(project, 'results', 'lost_load'), queryFn: () => resultsApi.getLostLoad(),
  })
  const { data: margin } = useQuery({ queryKey: nk(project, 'results', 'reserve_margin'), queryFn: () => resultsApi.getReserveMargin() })
  const { data: adequacy } = useQuery({ queryKey: nk(project, 'results', 'adequacy'), queryFn: resultsApi.getAdequacy })
  const { data: stats } = useQuery({ queryKey: nk(project, 'results_statistics'), queryFn: resultsApi.getStatistics })

  const c = costView(cost, selectedPeriod)
  const e = emissionsView(emissions as EmissionsPayload | null | undefined, selectedPeriod)
  const caps = capacityRows(stats as Record<string, unknown>[] | null | undefined, selectedPeriod)
  const ll = (lostLoad ?? null) as LostLoadPayload | null
  const rm = reserveMarginVerdict(margin as ReserveMarginSummary | null | undefined)
  const target = (adequacy ?? null) as AdequacyReportPayload | null

  if (!c && !e && !caps.length) {
    return (
      <div className="p-6 text-[12px] text-muted" data-testid="summary-empty">
        No results yet: run the model, and its cost, emissions, reliability and capacities are summarised here.
      </div>
    )
  }

  const scope = selectedPeriod != null ? `period ${selectedPeriod}` : 'horizon'
  const shedHours = ll?.shed_hours == null ? null
    : selectedPeriod != null ? ll.shed_hours.by_period[String(selectedPeriod)] ?? null : ll.shed_hours.total

  return (
    <div className="p-4 flex flex-col gap-3" data-testid="results-summary">
      {status && (
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-[11px] text-muted" data-testid="summary-status">
          <span>Solve: <span className="text-text font-medium">{status.condition ?? status.status ?? '—'}</span></span>
          {status.solve_time != null && <span>{Number(status.solve_time).toFixed(1)} s</span>}
          {status.objective != null && (
            <span title="The LP objective (n.objective + its constant). The cost card below is the reported system cost from n.statistics(); the two differ by what the objective counts (e.g. existing fixed costs, shed load).">
              LP objective <span className="font-mono text-text">{fmtCurrency(status.objective)}</span>
            </span>
          )}
          <span>Showing the {scope}</span>
        </div>
      )}
      <CardRows
        status={status} onOpenTab={onOpenTab} onOpenGroup={onOpenGroup}
        isIn={card => !scopeRead || inScope(flowScope, card)}
        headline={{
          capacity: c ? `${fmtCurrency(c.total)} a year${ll && ll.total_mwh > 0 ? ` · ${fmtEnergy(ll.total_mwh)} unserved` : ''}` : null,
          reliability: target
            ? `${fmtEnergy(target.target.system.achieved_ens_mwh)} unserved of ${fmtEnergy(target.target.system.cap_mwh)} allowed`
            : rm ? `Reserve margin ${rm.text}` : null,
        }}
        openReports={() => setSlidePanel('reports')}
      />
      <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(240px,1fr))]">
        <Card title="Cost" testId="summary-cost" link="capex" linkLabel="Capacity Expansion" onOpen={onOpenTab}
          hint="From /results/cost_breakdown (n.statistics(): annualised capital cost with fixed O&M, plus operating cost). Shed load is not included; see Adequacy.">
          {c ? (
            <>
              <Headline value={fmtCurrency(c.total)} sub="total system cost per year" />
              <Line label="Capital cost (annualised)" value={fmtCurrency(c.capital)} />
              <Line label="Operating cost" value={fmtCurrency(c.operating)} />
              {c.newBuild != null && (
                <span title="Annualised capital of capacity this solve built: max(0, optimal − installed) × capital cost, as on the Capacity Expansion tab.">
                  <Line label="of which new build" value={fmtCurrency(c.newBuild)} />
                </span>
              )}
            </>
          ) : <Absent>No cost breakdown for this solve.</Absent>}
        </Card>

        <Card title="Emissions" testId="summary-emissions" link="emissions" linkLabel="Emissions" onOpen={onOpenTab}
          hint="From /results/emissions: dispatch × carrier CO₂ intensity ÷ efficiency. Caps are the primary-energy global constraints on co2_emissions; the price is their shadow price.">
          {e ? (
            <>
              <Headline value={`${e.total_tCO2} tCO₂`} />
              {e.caps.length ? e.caps.map(cap => (
                <Line key={cap.name} label={`Cap ${cap.name}${cap.cap_tCO2 != null ? ` (${fmtTonnes(cap.cap_tCO2)})` : ''}`}
                  tone={cap.binding ? 'warn' : 'ok'}
                  value={cap.binding ? `binding · ${fmtCurrency(cap.shadow_price_eur_per_tCO2)}/t` : 'not binding'} />
              )) : <Absent>No CO₂ cap set.</Absent>}
            </>
          ) : <Absent>No emissions for this solve.</Absent>}
        </Card>

        <Card title="Adequacy" testId="summary-adequacy" link="lostload" linkLabel="Lost load" onOpen={onOpenTab}
          hint="Unserved energy is the VoLL slack dispatch the solve captured (/results/lost_load). LOLE and EUE come from an adequacy study: open the Adequacy tab.">
          {ll ? (
            <>
              <Headline value={fmtEnergy(ll.total_mwh)} sub={`unserved energy${selectedPeriod != null ? ' (horizon)' : ''}`} />
              <Line label="Cost at VoLL (not in the total)" value={fmtCurrency(ll.total_cost_eur)} />
              {shedHours != null && <Line label="Hours with shed load" value={shedHours.toFixed(1)} tone={shedHours > 0 ? 'warn' : 'ok'} />}
            </>
          ) : lostLoadRead ? (
            <Absent>Unserved energy not modelled: no value of lost load was set, so the solve could not shed load.</Absent>
          ) : null}
          {rm && <Line label="Reserve margin" value={rm.text} tone={rm.met ? 'ok' : 'warn'} />}
          {target && (
            <Line label="Reliability target"
              value={`${fmtEnergy(target.target.system.achieved_ens_mwh)} of ${fmtEnergy(target.target.system.cap_mwh)}`}
              tone={target.target.binding === 'voll' ? 'ok' : 'warn'} />
          )}
          <button type="button" onClick={() => onOpenTab('adequacy')}
            className="self-start text-[10.5px] text-accent hover:underline">LOLE and EUE: Adequacy</button>
        </Card>

        <Card title="Key capacities" testId="summary-capacity" link="capex" linkLabel="Capacity Expansion" onOpen={onOpenTab}
          hint="From /results/statistics (PyPSA n.statistics()): optimal capacity, and new build as max(0, optimal − installed). A multi-period horizon shows the last period's fleet unless a period is selected.">
          {caps.length ? (
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-[9.5px] text-muted text-left">
                  <th className="font-normal">Carrier</th>
                  <th className="font-normal text-right">Capacity</th>
                  <th className="font-normal text-right">New build</th>
                </tr>
              </thead>
              <tbody>
                {caps.slice(0, MAX_CAPACITY_ROWS).map(r => (
                  <tr key={`${r.component}-${r.carrier}`} data-testid={`summary-cap-${r.component}-${r.carrier}`}>
                    <td className="truncate" title={r.component}>{r.carrier || r.component}</td>
                    <td className="text-right font-mono tabular-nums">{fmtCapacity(r.capacity, r.unit)}</td>
                    <td className="text-right font-mono tabular-nums">{r.newBuild > 0 ? fmtCapacity(r.newBuild, r.unit) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Absent>No capacities for this solve.</Absent>}
          {caps.length > MAX_CAPACITY_ROWS && (
            <p className="text-[10px] text-muted">and {caps.length - MAX_CAPACITY_ROWS} more</p>
          )}
        </Card>
      </div>
      {/* The visible next step (UX-flow rule 3): what follows the answer. */}
      <div data-testid="summary-handoff" className="flex flex-wrap items-center gap-3 text-[11px]">
        <span className="text-muted">Next: hand off</span>
        <button type="button" data-testid="summary-open-reports" onClick={() => setSlidePanel('reports')}
          className="inline-flex items-center gap-1 text-accent hover:underline">
          Reports <ArrowRight size={11} />
        </button>
        <button type="button" data-testid="summary-open-export" onClick={() => requestIoModal('export')}
          className="inline-flex items-center gap-1 text-accent hover:underline">
          Export <ArrowRight size={11} />
        </button>
      </div>
    </div>
  )
}

// One row per card in scope (card-workspaces.md §4.1): its name, a headline
// when the solve gave one, its state, and "Open ›". Cards outside the scope
// sit under "Not in scope" with "Add" (W4a). The headlines the solve cannot
// give yet (grid, control, commercial) say where to look instead.
const ROW_OPEN: Partial<Record<CardGroupId, SummaryLink>> = {
  capacity: 'capex', grid: 'loadflow', reliability: 'adequacy', commercial: 'investment',
}
const ROW_HINT: Partial<Record<CardGroupId, string>> = {
  grid: 'Line loading and voltages are on the Load Flow tab.',
  control: 'No control results yet.',
  commercial: 'NPV and IRR are on the Investment case tab.',
  handoff: 'Reports and exports.',
}

function CardRows({ status, onOpenTab, onOpenGroup, isIn, headline, openReports }: {
  status: SimulationStatus | undefined
  onOpenTab: (t: SummaryLink) => void
  onOpenGroup?: (g: CardGroupId) => void
  isIn: (card: ScopeCardId) => boolean
  headline: Partial<Record<CardGroupId, string | null>>
  openReports: () => void
}) {
  const cards = CARD_GROUPS.filter(g => g.id !== 'summary')
  const inRows = cards.filter(g => isIn(g.id as ScopeCardId))
  const outRows = cards.filter(g => !isIn(g.id as ScopeCardId))
  const fresh = freshnessOf(status)
  return (
    <div data-testid="summary-card-rows" className="rounded-[10px] border border-border bg-bg-2 px-4 py-2">
      <ul className="divide-y divide-border">
        {inRows.map(g => {
          const link = ROW_OPEN[g.id]
          const open = () => (g.id === 'handoff' ? openReports()
            : link ? onOpenTab(link) : onOpenGroup?.(g.id))
          const hasResults = g.id === 'capacity' || g.id === 'grid' || g.id === 'reliability'
          return (
            <li key={g.id} data-testid={`summary-row-${g.id}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5">
              <span className="w-44 shrink-0 text-[12px] font-semibold text-text">{g.label}</span>
              <span className="min-w-0 flex-1 text-[11px] text-muted">{headline[g.id] ?? ROW_HINT[g.id] ?? ''}</span>
              {hasResults && fresh !== 'fresh' && <StatePill state={fresh} />}
              <button type="button" onClick={open} data-testid={`summary-row-open-${g.id}`}
                className="text-[10.5px] text-accent hover:underline inline-flex items-center gap-1">
                Open <ArrowRight size={10} />
              </button>
            </li>
          )
        })}
      </ul>
      {outRows.length > 0 && (
        <div data-testid="summary-not-in-scope" className="mt-1 border-t border-border pt-1.5 text-[11px] text-muted">
          <span className="font-semibold">Not in scope</span>
          <ul>
            {outRows.map(g => (
              <li key={g.id} data-testid={`summary-out-${g.id}`} className="flex items-center gap-3 py-0.5">
                <span className="w-44 shrink-0">{g.label}</span>
                <AddCardToScope card={g.id as ScopeCardId} testId={`summary-out-add-${g.id}`} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

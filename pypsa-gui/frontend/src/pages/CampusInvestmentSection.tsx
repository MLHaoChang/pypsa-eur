// The investment results of the campus electrical panel (plan C9).
//
// Shown only when the run bought assets. Thin like the panel: every figure is
// READ from the state the backend sent, and nothing is derived beyond the two
// sums a reader would do by hand: the electrical cost plus the hub's, and the
// electrical share of that sum.
//
// The cost is labelled honestly. The electrical cost is an annualised cost per
// investment period at assumed placeholder prices. The hub's system cost is the
// solved project's, per year for a multi-period project; a single-period
// project has only its total as solved, and a project whose cost cannot be
// computed has none, which is said rather than shown as zero.
//
// The joint optimisation (plan C12) is a block of its own beside it, when a job
// has finished: what it buys and its cost against the least-cost choice, need by
// need, whether it kept the least-cost choice or was cancelled, and its
// iteration history.
import {
  type CampusMilp, type CampusState, type CostBasis, type CostRow, type HistoryRow, type HubCost, type InvestmentRow,
  type InvestStatus, type MilpHistoryRow, type MilpStop,
} from '../api/campusElectrical'
import { PageSection, Tag } from '../components/PageKit'

const STATUS_TEXT: Record<InvestStatus, string> = {
  chosen: 'chosen', kept: 'kept', not_needed: 'not needed', unresolved: 'unresolved',
}
const STATUS_TONE: Record<InvestStatus, 'ok' | 'err' | 'neutral' | 'accent'> = {
  chosen: 'ok', kept: 'accent', not_needed: 'neutral', unresolved: 'err',
}

/** Euro with thousands separators; millions as `€6.89 M`. */
export function formatEur(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return '—'
  const sign = v < 0 ? '-' : ''
  const a = Math.abs(v)
  if (Math.round(a) >= 1e6) return `${sign}€${(a / 1e6).toFixed(2)} M`
  return `${sign}€${Math.round(a).toLocaleString('en-US')}`
}

function percent(share: number): string {
  return `${(share * 100).toFixed(1)} %`
}

const SCOPE_TEXT = {
  campus: 'PCC switchgear: costed to the campus',
  grid_operator: 'PCC switchgear: owned by the grid operator, not costed',
} as const

/** `0.07` as `7 %`, `0.035` as `3.5 %`. */
function ratePercent(rate: number): string {
  return `${Number((rate * 100).toFixed(2))} %`
}

/** The one plain line of what the costs stand on. The library's costs are in the
 *  library's price year even when the project states another one: no escalation
 *  is applied, so the year named here is the library's and the mismatch is said
 *  apart, not hidden in the line. */
function CostBasisLine({ basis }: { basis: CostBasis }) {
  const mismatch = basis.price_year_mismatch
  const year = mismatch ? basis.library_price_year : basis.price_year
  const yearFrom = mismatch ? 'asset library' : basis.price_year_from
  return (
    <>
      <p data-testid="invest-cost-basis" className="text-[12px] mb-2">
        Annualised at {ratePercent(basis.discount_rate)} (from {basis.discount_rate_from}); costs in {year} money
        (from {yearFrom}).
      </p>
      {mismatch && (
        <p data-testid="invest-price-year-mismatch" role="alert" className="text-[11.5px] text-warn mb-2">
          The project's finance inputs state {basis.price_year} money, but the library's costs are in{' '}
          {basis.library_price_year} money. No escalation is applied.
        </p>
      )}
    </>
  )
}

/** The hub's cost to set beside one electrical period, or null when it has none there. */
function hubFor(hub: HubCost | null, period: number, periods: number): number | null {
  if (!hub) return null
  if (hub.basis === 'per_period') return hub.per_period?.[String(period)] ?? null
  return periods === 1 ? hub.total : null
}

export default function CampusInvestmentSection({ state }: { state: CampusState }) {
  const r = state.results!
  if (r.investment == null) return null
  const cost = r.cost ?? []
  const unresolved = r.unresolved ?? []
  const history = r.history ?? []
  const hub = state.hub_cost
  const compared = hub != null && cost.some(c => hubFor(hub, c.period, cost.length) != null)

  return (
    <div data-testid="campus-investment" className="space-y-4">
      <PageSection
        title="Investment"
        hint="what the study buys, at least cost, re-checked by AC load flow"
      >
        <p className="text-[11.5px] text-muted mb-2">
          Prices come from the asset library; every cost in the shipped library is one of the assumed placeholders,
          an order of magnitude and not a quote.
        </p>
        {r.cost_basis && <CostBasisLine basis={r.cost_basis} />}
        {r.scope && (
          <p data-testid="invest-scope" className="text-[12px] mb-2">{SCOPE_TEXT[r.scope.pcc_switchgear]}</p>
        )}
        <InvestmentTable rows={r.investment} />
        {unresolved.length > 0 && (
          <div data-testid="unresolved" role="alert" className="mt-3 rounded border border-danger/50 bg-danger/5 px-3 py-2">
            <div className="text-[12px] font-semibold text-danger">
              {unresolved.length === 1 ? '1 need' : `${unresolved.length} needs`} the library could not meet
            </div>
            <ul className="text-[12px] mt-1 space-y-1">
              {unresolved.map(u => (
                <li key={u.need}><span className="font-medium">{u.need}</span>: {u.reason}</li>
              ))}
            </ul>
          </div>
        )}
        <HistoryList rows={history} />
      </PageSection>

      {state.milp?.results && (
        <MilpBlock milp={state.milp} marginPct={Math.round((state.settings?.margin ?? 0.2) * 100)} />
      )}

      <PageSection title="Cost per period" hint="the electrical annualised cost, per year, beside the hub's system cost">
        <CostTable cost={cost} hub={hub} />
        <p className="text-[11px] text-muted mt-2">
          Reported alongside the hub's cost; the hub's optimisation is unchanged. Unresolved needs are priced but
          not summed.
        </p>
        {hub == null && (
          <p className="text-[11.5px] text-warn mt-1">
            The hub system cost is not available: {state.hub_cost_reason ?? 'no reason given'}.
          </p>
        )}
        {hub != null && hub.basis === 'single_period' && cost.length === 1 && (
          <p className="text-[11.5px] text-muted mt-1">
            Single period: the hub's cost as solved, with no per-year split.
          </p>
        )}
        {hub != null && !compared && (
          <p className="text-[11.5px] text-muted mt-1">
            There is no per-period split of the hub's system cost for this project, so the two are not summed.
            Its total as solved is {formatEur(hub.total)}.
          </p>
        )}
      </PageSection>
    </div>
  )
}

const STOP_TEXT: Record<MilpStop, string> = {
  converged: 'converged',
  stalled: 'stalled: the MILP proposed nothing better',
  delta_floor: 'stopped: the trust region shrank to its floor',
  max_iter: 'stopped at the iteration cap',
  cancelled: 'cancelled',
}

/** The joint optimisation's choice beside the least-cost one. */
function MilpBlock({ milp, marginPct }: { milp: CampusMilp; marginPct: number }) {
  const m = milp.results!
  const s = m.summary
  const comparison = m.comparison ?? []
  const saving = s.c8_cost - s.milp_cost
  return (
    <div data-testid="campus-milp">
      <PageSection title="Joint optimisation" hint="every asset chosen together (MILP), beside the least-cost choice">
        {milp.stale && (
          <p data-testid="milp-stale" className="text-[12px] text-warn border border-warn/40 rounded px-3 py-2 mb-2">
            The least-cost run this was based on has changed since. Run the joint optimisation again before using
            these numbers.
          </p>
        )}
        <p data-testid="milp-summary" className="text-[12px] mb-1">
          Joint optimisation {formatEur(s.milp_cost)}/a against least cost {formatEur(s.c8_cost)}/a
          {saving > 1 ? `, ${formatEur(saving)}/a less` : ''}. {s.iterations === 1 ? '1 iteration' : `${s.iterations} iterations`},{' '}
          {STOP_TEXT[m.stop] ?? m.stop}.
        </p>
        {m.stop === 'cancelled' && (
          <p data-testid="milp-cancelled" className="text-[12px] text-warn mb-1">
            Cancelled before it finished: {m.fallback == null
              ? 'the best choice found so far is shown, AC-checked.'
              : 'it had found nothing cheaper, so the least-cost choice is kept.'}
          </p>
        )}
        {m.fallback != null && (
          <p data-testid="milp-fallback" className="text-[12px] mb-1">
            <Tag tone="neutral">least-cost choice kept</Tag> {m.fallback}
          </p>
        )}
        <p className="text-[11.5px] text-muted mb-2">
          The {marginPct} % design margin is the owner's rule: it is judged on the least-cost dispatch and is never met
          by extra inverter reactive power; the optimisation may use inverter Q only for the 100 % loading, the PCC band
          and the voltages. Costs are the asset library's placeholders, not quotes.
        </p>
        <table data-testid="milp-comparison" className="w-full text-[12px] mb-3">
          <thead>
            <tr className="text-left text-muted">
              <th className="font-normal pr-3">Need</th>
              <th className="font-normal pr-3">Least cost</th><th className="font-normal pr-3 text-right">€/a</th>
              <th className="font-normal pr-3">Joint</th><th className="font-normal text-right">€/a</th>
            </tr>
          </thead>
          <tbody>
            {comparison.map(c => (
              <tr key={c.need} data-testid={`milp-compare-${c.need}`}
                  className={`border-t border-border ${c.c8_choice !== c.milp_choice ? 'font-medium' : ''}`}>
                <td className="py-1 pr-3 whitespace-nowrap">{c.need}</td>
                <td className="pr-3">{c.c8_choice}</td>
                <td className="pr-3 tabular-nums text-right">{formatEur(c.c8_annualised_eur_per_a)}</td>
                <td className="pr-3">{c.milp_choice}</td>
                <td className="tabular-nums text-right">{formatEur(c.milp_annualised_eur_per_a)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {m.investment && <InvestmentTable rows={m.investment} testid="milp-invest" />}
        <MilpHistoryList rows={m.history ?? []} />
      </PageSection>
    </div>
  )
}

function MilpHistoryList({ rows }: { rows: MilpHistoryRow[] }) {
  return (
    <details data-testid="milp-history" className="mt-3 text-[12px]">
      <summary className="cursor-pointer text-muted">Iteration history ({rows.length})</summary>
      <ul className="mt-1 space-y-0.5">
        {rows.map(h => (
          <li key={h.iteration} className="tabular-nums">
            {h.iteration}. {formatEur(h.cost)}/a, {h.feasible ? 'AC-feasible' : 'not AC-feasible'},{' '}
            {h.accepted ? 'accepted' : 'not accepted'} — {h.choice}
            {h.note ? <span className="text-muted"> ({h.note})</span> : null}
          </li>
        ))}
      </ul>
    </details>
  )
}

function InvestmentTable({ rows, testid = 'invest' }: { rows: InvestmentRow[]; testid?: string }) {
  return (
    <table className="w-full text-[12px]">
      <thead>
        <tr className="text-left text-muted">
          <th className="font-normal pr-3">Need</th><th className="font-normal pr-3">Asset</th>
          <th className="font-normal pr-3">Units</th><th className="font-normal pr-3">Invest period</th>
          <th className="font-normal pr-3 text-right">Capex</th><th className="font-normal pr-3 text-right">Annualised cost</th>
          <th className="font-normal pr-3">Existing</th><th className="font-normal">Status</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(x => (
          <tr
            key={`${x.need}-${x.library_id ?? ''}`}
            data-testid={`${testid}-${x.need}`}
            className={`border-t border-border ${x.status === 'unresolved' ? 'bg-danger/10 text-danger' : ''}`}
          >
            <td className="py-1 pr-3 whitespace-nowrap">{x.need}</td>
            <td className="pr-3 whitespace-nowrap">
              {x.library_id ?? '—'}
              {x.length_km != null && <span className="text-muted"> · {x.length_km} km</span>}
            </td>
            <td className="pr-3 tabular-nums">{x.units}</td>
            <td className="pr-3 tabular-nums">{x.library_id == null ? '—' : (x.invest_period ?? '—')}</td>
            <td className="pr-3 tabular-nums text-right">{formatEur(x.capex_eur)}</td>
            <td className="pr-3 tabular-nums text-right">{formatEur(x.annualised_eur_per_a)}</td>
            <td className="pr-3">{x.existing ? 'yes' : 'no'}</td>
            <td><Tag tone={STATUS_TONE[x.status]}>{STATUS_TEXT[x.status] ?? x.status}</Tag></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function HistoryList({ rows }: { rows: HistoryRow[] }) {
  return (
    <details data-testid="invest-history" className="mt-3 text-[12px]">
      <summary className="cursor-pointer text-muted">Escalation history ({rows.length})</summary>
      {rows.length === 0 ? (
        <p className="mt-1 text-muted">No escalation: the cheapest candidate for every need passed its checks.</p>
      ) : (
        <ul className="mt-1 space-y-0.5">
          {rows.map(h => (
            <li key={`${h.iteration}-${h.need}`} className="tabular-nums">
              {h.iteration}. {h.need}: {h.from} → {h.to}
              <span className="text-muted"> — {h.check}: {h.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </details>
  )
}

function CostTable({ cost, hub }: { cost: CostRow[]; hub: HubCost | null }) {
  return (
    <table className="w-full text-[12px]">
      <thead>
        <tr className="text-left text-muted">
          <th className="font-normal pr-3">Period</th>
          <th className="font-normal pr-3 text-right">Capex invested</th>
          <th className="font-normal pr-3 text-right">Electrical, annualised</th>
          <th className="font-normal pr-3 text-right">Hub system cost</th>
          <th className="font-normal pr-3 text-right">Together</th>
          <th className="font-normal text-right">Electrical share</th>
        </tr>
      </thead>
      <tbody>
        {cost.map(c => {
          const h = hubFor(hub, c.period, cost.length)
          const sum = h == null ? null : c.annualised_eur_per_a + h
          return (
            <tr key={c.period} data-testid={`cost-${c.period}`} className="border-t border-border">
              <td className="py-1 pr-3 tabular-nums">{c.period}</td>
              <td className="pr-3 tabular-nums text-right">{formatEur(c.capex_eur)}</td>
              <td className="pr-3 tabular-nums text-right">{formatEur(c.annualised_eur_per_a)}</td>
              <td className="pr-3 tabular-nums text-right">{formatEur(h)}</td>
              <td className="pr-3 tabular-nums text-right">{formatEur(sum)}</td>
              <td className="tabular-nums text-right">{sum == null || sum === 0 ? '—' : percent(c.annualised_eur_per_a / sum)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

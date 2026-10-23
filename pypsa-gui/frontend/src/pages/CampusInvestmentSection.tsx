// The investment results of the campus electrical panel (plan C9).
//
// Shown only when the run bought assets. Thin like the panel: every figure is
// READ from the state the backend sent, and nothing is derived beyond the two
// sums a reader would do by hand: the electrical cost plus the hub's, and the
// electrical share of that sum.
//
// The cost is labelled honestly. The electrical cost is an annualised cost per
// investment period at the asset library's reference prices, not quotes. The hub's system cost is the
// solved project's, per year for a multi-period project; a single-period
// project has only its total as solved, and a project whose cost cannot be
// computed has none, which is said rather than shown as zero.
//
// Plan C14: the table has one row per purchase (an asset bought again at the end
// of its life is a `renew` row), the year each is bought, the cost table the
// part of each period's capex that buys again, and a line states the study
// horizon, flagging an assumed span, with the run's notes.
//
// The on-load taps (plan C15) are stated under the table: how they act (per
// critical hour and per case, an N-1 case after the tap changers act with the
// reactive dispatch held), how many critical hours moved one, who moves the PCC
// transformer's, and every position used in a collapsed table.
//
// The joint optimisation (plan C12) is a block of its own beside it, when a job
// has finished: what it buys and its cost against the least-cost choice, need by
// need, whether it kept the least-cost choice or was cancelled, and its
// iteration history.
//
// One line under Investment says which of the two feeds the investment case
// (owner decision 2026-10-12: the joint optimisation when it is fresh and found
// its own choice, else the least-cost one), so the switch is never silent.
import {
  type CampusMilp, type CampusState, type CostBasis, type CostRow, type HistoryRow, type HorizonScope, type HubCost,
  type InvestmentRow, type InvestStatus, type MilpHistoryRow, type MilpStop, type ParallelScope, type PccTapControl,
  type TapRow,
} from '../api/campusElectrical'
import type { ReactNode } from 'react'
import { PageSection, Tag } from '../components/PageKit'

const STATUS_TEXT: Record<InvestStatus, string> = {
  chosen: 'chosen', kept: 'kept', not_needed: 'not needed', unresolved: 'unresolved',
}
const STATUS_TONE: Record<InvestStatus, 'ok' | 'err' | 'neutral' | 'accent'> = {
  chosen: 'ok', kept: 'accent', not_needed: 'neutral', unresolved: 'err',
}

/** A wide table scrolls sideways inside its section, as the gridspine panel's
 *  hours table does: a page section clips its overflow, so a table wider than
 *  the panel (the assistant dock open) would otherwise lose its last columns. */
export function TableScroll({ children }: { children: ReactNode }) {
  return <div data-testid="campus-table-scroll" className="overflow-x-auto">{children}</div>
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

const TAP_SCOPE_TEXT: Record<PccTapControl, string> = {
  campus: 'PCC transformer taps: moved by the campus',
  grid_operator: "PCC transformer taps: held by the grid operator at the campus file's position",
}

/** How the on-load taps acted in the re-check (plan C15), and every position
 *  used, collapsed. Absent for a run from before taps were modelled. */
function TapsBlock({ taps, moved, hours, control }: {
  taps: TapRow[]; moved: number | null | undefined; hours: number; control: PccTapControl | undefined
}) {
  return (
    <div className="mt-3">
      <p data-testid="invest-taps-summary" className="text-[12px]">
        {taps.length === 0
          ? "No transformer has on-load tap data: every tap stays at the campus file's position."
          : `On-load taps: set per critical hour and per case (an N-1 case after the tap changers act, the reactive `
            + `dispatch held), parallel units together, nearest neutral; ${moved ?? 0} of the ${hours} critical `
            + `hours moved a tap. Off-circuit taps stay at the campus file's position.`}
      </p>
      {control && <p data-testid="invest-tap-scope" className="text-[12px]">{TAP_SCOPE_TEXT[control]}</p>}
      {taps.length > 0 && (
        <details data-testid="invest-taps" className="mt-1 text-[12px]">
          <summary className="cursor-pointer text-muted">Tap positions ({taps.length})</summary>
          <TableScroll>
            <table className="w-full text-[12px] mt-1">
              <thead>
                <tr className="text-left text-muted">
                  <th className="font-normal pr-3">Period</th><th className="font-normal pr-3">Hour</th>
                  <th className="font-normal pr-3">Case</th><th className="font-normal pr-3">Transformer</th>
                  <th className="font-normal pr-3">Group</th><th className="font-normal pr-3 text-right">Tap</th>
                  <th className="font-normal pr-3 text-right">Ratio</th><th className="font-normal text-right">Moved by</th>
                </tr>
              </thead>
              <tbody>
                {taps.map(t => (
                  <tr key={`${t.period}-${t.hour}-${t.case}-${t.transformer}`}
                      data-testid={`tap-${t.period}-${t.hour}-${t.case}-${t.transformer}`}
                      className={`border-t border-border ${t.moved_by !== 0 ? 'font-medium' : ''}`}>
                    <td className="py-1 pr-3 tabular-nums">{t.period}</td>
                    <td className="pr-3 tabular-nums">{t.hour}</td>
                    <td className="pr-3 whitespace-nowrap">{t.case}</td>
                    <td className="pr-3">{t.transformer}</td>
                    <td className="pr-3">{t.group}</td>
                    <td className="pr-3 tabular-nums text-right">{t.tap_pos}</td>
                    <td className="pr-3 tabular-nums text-right">{Number(t.ratio_pct.toFixed(2))} %</td>
                    <td className="tabular-nums text-right">{t.moved_by}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        </details>
      )}
    </div>
  )
}

/** `0.07` as `7 %`, `0.035` as `3.5 %`. */
function ratePercent(rate: number): string {
  return `${Number((rate * 100).toFixed(2))} %`
}

/** The rules a unit added next to existing transformers had to meet (plan C13),
 *  as the run wrote them: every tolerance is assumed. */
function ParallelLine({ rules }: { rules: ParallelScope }) {
  const vg = rules.unknown_vector_group === 'refuse'
    ? 'an unknown vector group is refused'
    : 'an unknown vector group is assumed compatible and flagged'
  return (
    <p data-testid="invest-parallel" className="text-[11.5px] text-muted mb-2">
      Units added beside existing transformers: impedance within ±{ratePercent(rules.uk_tol.value)} and no-load
      ratio within ±{ratePercent(rules.ratio_tol.value)} of each existing unit, tap steps within{' '}
      {Number(rules.step_tol_pct.value.toFixed(3))} point, the same vector-group clock ({vg}); no limit on the
      rating ratio. These tolerances are assumed, not taken from a standard.
    </p>
  )
}

/** The study horizon (plan C14), as the run wrote it: each period's span, an
 *  assumed one flagged, and the notes (assumed commissioning years, overdue and
 *  unpriced replacements, and that replacements are reported, not ranked). */
function HorizonLine({ horizon }: { horizon: HorizonScope }) {
  const spans = Object.entries(horizon.period_years).map(([p, w]) =>
    `${p}: ${w} ${w === 1 ? 'year' : 'years'}${horizon.source[p]?.startsWith('assumed') ? ' (assumed)' : ''}`)
  return (
    <>
      <p data-testid="invest-horizon" className="text-[12px] mb-1">
        Study horizon to {horizon.horizon_end}: {spans.join(', ')}. An asset whose life ends before then is bought
        again like for like.
      </p>
      {horizon.notes.length > 0 && (
        <ul data-testid="invest-horizon-notes" className="text-[11.5px] text-muted mb-2 list-disc pl-4">
          {horizon.notes.map(n => <li key={n}>{n}</li>)}
        </ul>
      )}
    </>
  )
}

/** The year a row is bought (plan C14), or when an existing asset went into service. */
function boughtText(x: InvestmentRow): string {
  if (x.existing) {
    if (x.commissioned == null) return '—'
    return `in service since ${x.commissioned}${x.commissioned_source?.startsWith('assumed') ? ' (assumed)' : ''}`
  }
  if (x.library_id == null) return '—'
  return String(x.build_year ?? x.invest_period ?? '—')
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

/** Which result the investment case's extra owner assets come from, as the backend
 *  chose it (`owner_assets_method`), and, when it is the least cost beside a joint
 *  optimisation, why. Nothing when the case takes no assets. */
function IcSourceLine({ state }: { state: CampusState }) {
  const method = state.owner_assets_method
  if (method !== 'milp' && method !== 'least_cost') return null
  const milp = state.milp?.results ? state.milp : null
  let why = ''
  if (method === 'least_cost' && milp) {
    if (milp.stale) why = ' The joint optimisation is stale; run it again to use it.'
    else if (milp.results!.fallback != null) why = ' The joint optimisation kept the least-cost choice.'
  }
  return (
    <p data-testid="invest-ic-source" className="text-[12px] mb-2">
      Investment case uses: {method === 'milp' ? 'joint optimisation (MILP)' : 'least cost'}.{why}
    </p>
  )
}

/** Owner decision 2026-10-13: the needs the run the case uses could not meet are passed to the
 *  investment case, which flags the case. Said only when there are any. */
function IcUnresolvedLine({ count }: { count: number }) {
  if (!(count > 0)) return null
  return (
    <p data-testid="invest-ic-unresolved" className="text-[12px] text-warn mb-2">
      {count === 1
        ? '1 unresolved need is passed to the investment case as a flag: the case is not compliant without it.'
        : `${count} unresolved needs are passed to the investment case as a flag: the case is not compliant without them.`}
    </p>
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
          Prices come from the asset library; the shipped library's costs are reference values from cited public
          sources (source: literature) or assumed where none was found, not quotes.
        </p>
        <IcSourceLine state={state} />
        <IcUnresolvedLine count={state.owner_assets_unresolved ?? 0} />
        {r.cost_basis && <CostBasisLine basis={r.cost_basis} />}
        {r.scope && (
          <p data-testid="invest-scope" className="text-[12px] mb-2">{SCOPE_TEXT[r.scope.pcc_switchgear]}</p>
        )}
        {r.scope?.parallel && <ParallelLine rules={r.scope.parallel} />}
        {r.scope?.horizon && <HorizonLine horizon={r.scope.horizon} />}
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
        {r.taps != null && (
          <TapsBlock taps={r.taps} moved={r.tap_hours_moved} hours={r.selection.length}
                     control={r.scope?.pcc_tap_control} />
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

/** `1 iteration`, `3 iterations`. */
export function iterationsText(n: number): string {
  return n === 1 ? '1 iteration' : `${n} iterations`
}

/** The joint optimisation's choice beside the least-cost one. Its counts agree:
 *  the engine's `iterations` (and the job's `iteration`) count the steps after
 *  the warm start, which is the least-cost choice re-solved and the history's
 *  row 0, so the warm start is named beside them rather than counted. */
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
          {saving > 1 ? `, ${formatEur(saving)}/a less` : ''}. {iterationsText(s.iterations)} after the warm start,{' '}
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
          and the voltages. Costs are the asset library's reference values, not quotes.
        </p>
        <TableScroll>
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
        </TableScroll>
        {m.investment && <InvestmentTable rows={m.investment} testid="milp-invest" />}
        <MilpHistoryList rows={m.history ?? []} />
      </PageSection>
    </div>
  )
}

function MilpHistoryList({ rows }: { rows: MilpHistoryRow[] }) {
  const steps = iterationsText(rows.filter(h => h.iteration > 0).length)
  const count = rows.some(h => h.iteration === 0) ? `warm start + ${steps}` : steps
  return (
    <details data-testid="milp-history" className="mt-3 text-[12px]">
      <summary className="cursor-pointer text-muted">Iteration history ({count})</summary>
      <ul className="mt-1 space-y-0.5">
        {rows.map(h => (
          <li key={h.iteration} className="tabular-nums">
            {h.iteration === 0 ? 'Warm start:' : `${h.iteration}.`} {formatEur(h.cost)}/a, {h.feasible ? 'AC-feasible' : 'not AC-feasible'},{' '}
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
    <TableScroll>
      <table className="w-full text-[12px]">
        <thead>
          <tr className="text-left text-muted">
            <th className="font-normal pr-3">Need</th><th className="font-normal pr-3">Action</th>
            <th className="font-normal pr-3">Asset</th>
            <th className="font-normal pr-3">Units</th><th className="font-normal pr-3">Bought</th>
            <th className="font-normal pr-3">Invest period</th>
            <th className="font-normal pr-3 text-right">Capex</th><th className="font-normal pr-3 text-right">Annualised cost</th>
            <th className="font-normal pr-3">Existing</th><th className="font-normal">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((x, i) => (
            <tr
              key={`${x.need}-${x.library_id ?? ''}-${x.action ?? ''}-${x.purchase ?? ''}-${i}`}
              data-testid={`${testid}-${x.need}`}
              className={`border-t border-border ${x.status === 'unresolved' ? 'bg-danger/10 text-danger' : ''}`}
            >
              <td className="py-1 pr-3">
                <div className="whitespace-nowrap">{x.need}</div>
                {x.reason && x.status !== 'unresolved' && (
                  <div data-testid="invest-reason" className="text-[11px] text-muted">{x.reason}</div>
                )}
              </td>
              <td data-testid="invest-action" className="pr-3 whitespace-nowrap">{x.action || '—'}</td>
              <td className="pr-3 whitespace-nowrap">
                {x.library_id ?? '—'}
                {x.length_km != null && <span className="text-muted"> · {x.length_km} km</span>}
              </td>
              <td className="pr-3 tabular-nums">{x.units}</td>
              <td data-testid="invest-bought" className="pr-3 tabular-nums whitespace-nowrap">{boughtText(x)}</td>
              <td className="pr-3 tabular-nums">{x.library_id == null ? '—' : (x.invest_period ?? '—')}</td>
              <td className="pr-3 tabular-nums text-right">{formatEur(x.capex_eur)}</td>
              <td className="pr-3 tabular-nums text-right">{formatEur(x.annualised_eur_per_a)}</td>
              <td className="pr-3">{x.existing ? 'yes' : 'no'}</td>
              <td><Tag tone={STATUS_TONE[x.status]}>{STATUS_TEXT[x.status] ?? x.status}</Tag></td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
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
  const renewals = cost.some(c => c.capex_replacement_eur != null)        // plan C14; absent before it
  return (
    <TableScroll>
      <table className="w-full text-[12px]">
        <thead>
          <tr className="text-left text-muted">
            <th className="font-normal pr-3">Period</th>
            <th className="font-normal pr-3 text-right">Capex invested</th>
            {renewals && <th className="font-normal pr-3 text-right">of which bought again</th>}
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
                {renewals && (
                  <td data-testid="cost-replacement" className="pr-3 tabular-nums text-right">
                    {formatEur(c.capex_replacement_eur)}
                  </td>
                )}
                <td className="pr-3 tabular-nums text-right">{formatEur(c.annualised_eur_per_a)}</td>
                <td className="pr-3 tabular-nums text-right">{formatEur(h)}</td>
                <td className="pr-3 tabular-nums text-right">{formatEur(sum)}</td>
                <td className="tabular-nums text-right">{sum == null || sum === 0 ? '—' : percent(c.annualised_eur_per_a / sum)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </TableScroll>
  )
}

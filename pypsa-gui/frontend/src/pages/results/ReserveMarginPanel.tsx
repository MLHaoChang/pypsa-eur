import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ShieldCheck } from 'lucide-react'
import { resultsApi } from '../../api/simulation'
import type {
  ReserveMarginAsset, ReserveMarginPayload, ReserveMarginPeriod,
  NetWindowBlock,
} from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { RESERVE_MARGIN_CAVEAT } from './adequacy'

// ── The firm-capacity (planning reserve margin) readout — Phase 8 §6 ────────
//
// GET /results/reserve_margin serves the PERSISTED solve-time stash: the peaks
// the wrapper measured with the load-scaling transforms still applied, joined
// to what the solve actually BUILT. Everything below is rendered from that
// payload and nothing is recomputed here — a second reader of the same
// question is how the two-readers-drift class of bug gets in.
//
// The panel exists because the standard is made of PROXIES. A reserve margin
// is one number over a peak, met by arithmetic on derating factors that are
// mostly carrier class averages nobody entered, over peak hours picked by a
// coincidence rule that is explicitly not ELCC. Every one of those inputs is
// therefore published: the derate WITH its basis and source, the peak
// timestamps and N, and the basis roll-up. A proxy nobody can inspect is a
// number nobody can check.
//
// It mounts unconditionally — the Adequacy tab's ★ no-early-return invariant.
// The 204 state (nothing solved, or solved with no margin) is the ORDINARY
// state of this surface, and it is an answer, not an empty panel.

/** MW, or an em dash where the payload has no number. */
function mw(v: number | null | undefined): string {
  return v == null || !isFinite(v) ? '—' : `${v.toFixed(1)} MW`
}

/** A fraction as the percentage the user typed. */
function pct(v: number | null | undefined): string {
  return v == null || !isFinite(v) ? '—' : `${(v * 100).toFixed(1)}%`
}

/**
 * ★ The scope label, and the reason it is not cosmetic.
 *
 * `Generator-p_nom` is ONE variable for the whole horizon (PyPSA builds it on
 * `extendables ∩ active_assets` with coords `(name,)` only), so when the
 * active extendable set is identical in every period the per-period
 * constraints share it and the system collapses into a SINGLE standard at the
 * maximum peak. That is a different standard from "per period", weaker in
 * every period but the peak one — and announcing it as per-period would be a
 * claim the constraint does not support.
 */
export function scopeSentence(horizonWide: boolean): string {
  if (horizonWide) {
    return 'ONE horizon-wide standard at the maximum peak, not a per-period '
      + 'one: `Generator-p_nom` is a single variable for the whole horizon, so '
      + 'the periods below share it and their constraints collapse into that '
      + 'one. Each period is listed for inspection, but only the largest peak '
      + 'sets the requirement the plan was built to.'
  }
  return 'Enforced per investment period: the active extendable set differs '
    + 'between periods, so each row below is its own constraint with its own '
    + 'peak and its own requirement.'
}

/** `met` — the plan reaches the standard. */
export function metText(row: Pick<ReserveMarginPeriod, 'met'>): string {
  return row.met ? 'met' : 'short'
}

/**
 * ★ `binding` — the standard SHAPED the plan, i.e. firm capacity is sitting on
 * the constraint's bound. Kept strictly separate from `met` (amendment
 * v1.2(5)): a margin the existing fixed fleet already satisfies is met and NOT
 * binding, and rendering one from the other would credit the margin for
 * capacity that was always there — the plan would look like the standard's
 * doing when the standard changed nothing.
 */
export function bindingText(row: Pick<ReserveMarginPeriod, 'binding'>): string {
  return row.binding ? 'binding' : 'not binding'
}

/**
 * The ceiling a plan built from this candidate set could reach.
 *
 * `null` + `max_achievable_unbounded` is NOT missing data: an active
 * extendable has an unbounded `p_nom_max`, the honest answer is `inf`, and
 * `inf` is not JSON (amendment v1.2(4) nulls it rather than clamping, because
 * a clamp invents a ceiling nobody entered). So it renders as the word, never
 * as a blank cell or a NaN.
 */
export function maxAchievableText(
  row: Pick<ReserveMarginPeriod, 'max_achievable_mw' | 'max_achievable_unbounded'>,
): string {
  if (row.max_achievable_unbounded) return 'unbounded'
  return mw(row.max_achievable_mw)
}

/**
 * ★ The FOR-is-optimistic note, returned only when a FOR row is actually
 * present.
 *
 * The equality test is deliberate and load-bearing: "EFORd" CONTAINS "FOR", so
 * a substring test would print this warning over every default fleet (the
 * defaults library is 9/10 EFORd) and the warning would stop meaning anything.
 */
/**
 * ★ The peak-hour list, SUMMARISED when it is long.
 *
 * Found by rendering the panel in a browser: on a flat-demand network the tie
 * rule (correctly) pulls in every snapshot, so this cell became 48 timestamps
 * inline — and on an 8760-hour horizon it would be 8760. The list is published
 * so the peak-coincidence proxy is CHECKABLE, and a wall of text is not
 * checkable. Beyond four entries, report the count and the span; the full list
 * stays in the cell's `title`.
 */
export function peakHoursCell(stamps: string[], n: number): string {
  if (!stamps || stamps.length === 0) return '—'
  if (stamps.length <= 4) return stamps.join(', ')
  const first = stamps[0]
  const last = stamps[stamps.length - 1]
  return `${stamps.length} hours (N = ${n}) · ${first} … ${last}`
}

/**
 * ★ The basis label for one derating row.
 *
 * A MUST-TAKE asset has no outage basis at all: its derate is a
 * peak-coincidence availability, not `1 − q`. Rendering that as an em-dash (or
 * `<blank>` in the roll-up) reads as data the user forgot to enter and sends
 * them hunting for an outage rate that should not exist. Name what it is.
 */
/** Phase 12b — the derate the row WOULD have had on the net-load window.
 *  `—` carries a title that says WHY there is no number, by `profile_kind`:
 *  the reader must be able to tell "no profile" from "a profile that is
 *  constant in this period" from "no net window this period". */
export function derateNetText(
  a: Pick<ReserveMarginAsset, 'derate_net' | 'profile_kind'>,
): { text: string; title: string } {
  if (a.derate_net != null && Number.isFinite(a.derate_net)) {
    return {
      text: a.derate_net.toFixed(3),
      title: 'What the derate WOULD have been had the standard been built on the net-load window. A second proxy in the margin\'s own units — never a correction.',
    }
  }
  switch (a.profile_kind) {
    case 'constant':
      return { text: '—', title: 'Constant in this period — window-independent.' }
    case 'varying':
      return { text: '—', title: 'No net-load window in this period (see the status above).' }
    case 'none':
      return { text: '—', title: 'No profile — window-independent.' }
    default:
      // A payload persisted before Phase 12b carries no `profile_kind`; saying
      // "no profile" about a wind row would be false (review finding 7).
      return { text: '—', title: 'Not computed by this backend.' }
  }
}

/** Phase 12b — one sentence per period. Never "corrected", never "VRE".
 *  `partial` prefixes the myopic caveat: the block is the LAST period solved. */
export function netWindowSentence(nw: NetWindowBlock | null | undefined,
                                  partial = false): string {
  const prefix = partial ? 'Last period solved (myopic) — ' : ''
  if (!nw) return prefix + 'Net-load window: not computed by this backend.'
  if (nw.status === 'nothing_netted') {
    return prefix + 'No profile-bearing capacity in the built plan; the net-load window is the gross window.'
  }
  if (nw.status === 'no_finite_demand') {
    return prefix + 'No finite demand in this period; no window could be selected on either series.'
  }
  if (nw.status === 'empty_window') {
    return prefix + 'The net-load window came back empty (a non-finite hour at the threshold); nothing can be compared.'
  }
  const m = (v: number | null) => (v == null ? '—' : `${v.toFixed(1)} MW`)
  const k = nw.netted_assets.length
  // `netted_mw` is the PERIOD MEAN of netted availability (spec v1.3(3)),
  // not availability on the window — the copy says which. The names live in
  // the table's "Netted" column; a clustered network has hundreds.
  return (
    prefix +
    `Net-load window: ${nw.n_hours} h; ${nw.overlap_hours ?? '—'} shared with the gross window; ` +
    `${k} asset${k === 1 ? '' : 's'} netted (see the table), mean netted availability over the period ${m(nw.netted_mw)}; ` +
    `firm credit ${m(nw.firm_gross_mw)} → ${m(nw.firm_net_mw)} on the net window.`
  )
}

export function basisLabel(basis: string | null | undefined,
                           source?: string | null): string {
  const b = (basis ?? '').trim()
  if (b) return b
  if ((source ?? '').trim() === 'missing') return 'must-take (availability)'
  return '—'
}

export function forOptimisticNote(
  bases: Record<string, number> | null | undefined,
): string | null {
  const hasFor = Object.entries(bases ?? {}).some(
    ([b, n]) => b.trim().toUpperCase() === 'FOR' && n > 0)
  if (!hasFor) return null
  return 'FOR-based rows are OPTIMISTIC: forced outage rate excludes '
    + 'reserve-shutdown hours, so 1 − FOR overstates firmness — and it '
    + 'overstates it most for the peakers that sit at the margin, which are '
    + 'exactly the units this standard is buying. EFORd is the correct UCAP '
    + 'derate; the tool never silently converts one into the other.'
}

/** The per-period achieved-vs-required table. */
function PeriodTable({ rows }: { rows: ReserveMarginPeriod[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[10px]" data-testid="reserve-margin-periods">
        <thead className="text-muted">
          <tr data-testid="rm-period-head">
            <th className="text-left font-medium py-1 pr-3">Period</th>
            <th className="text-left font-medium py-1 pr-3">Peak</th>
            <th className="text-left font-medium py-1 pr-3">Required firm</th>
            <th className="text-left font-medium py-1 pr-3">Achieved firm</th>
            <th className="text-left font-medium py-1 pr-3">Margin achieved</th>
            <th className="text-left font-medium py-1 pr-3">Standard</th>
            <th className="text-left font-medium py-1 pr-3">Shaped the plan?</th>
            <th className="text-left font-medium py-1 pr-3">Max achievable</th>
            <th className="text-left font-medium py-1 pr-3">N peak hours</th>
            <th className="text-left font-medium py-1 pr-3">Peak hours used</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr
              key={r.period}
              className="border-t border-border/50 align-top"
              data-testid={`rm-row-${r.period}`}
            >
              <td className="py-1 pr-3 font-medium">{r.period}</td>
              <td className="py-1 pr-3 font-mono" data-testid={`rm-peak-${r.period}`}>
                {mw(r.peak_mw)}
              </td>
              <td className="py-1 pr-3 font-mono" data-testid={`rm-required-${r.period}`}>
                {mw(r.required_mw)}
              </td>
              <td className="py-1 pr-3 font-mono" data-testid={`rm-firm-${r.period}`}>
                {mw(r.firm_mw)}
              </td>
              <td className="py-1 pr-3 font-mono" data-testid={`rm-achieved-${r.period}`}>
                {pct(r.margin_achieved)}
              </td>
              <td
                className={'py-1 pr-3 ' + (r.met ? 'text-accent' : 'text-danger')}
                data-testid={`rm-met-${r.period}`}
                title="Whether the plan REACHES the standard — derated firm capacity ≥ (1 + margin) × peak."
              >
                {metText(r)}
              </td>
              <td
                className="py-1 pr-3"
                data-testid={`rm-binding-${r.period}`}
                title="Whether the standard SHAPED the plan: firm capacity sitting on the constraint's bound. A margin the existing fleet already satisfies is met and NOT binding."
              >
                {bindingText(r)}
              </td>
              <td className="py-1 pr-3 font-mono" data-testid={`rm-max-${r.period}`}>
                {maxAchievableText(r)}
              </td>
              <td
                className="py-1 pr-3 font-mono"
                data-testid={`rm-peak-n-${r.period}`}
                title="N = the number of highest-demand snapshots the must-take credit was averaged over. Every snapshot tied with the Nth is included, so the list beside it can be longer."
              >
                N = {r.n_peak_hours}
              </td>
              <td
                className="py-1 pr-3 font-mono text-muted"
                data-testid={`rm-peak-hours-${r.period}`}
                title={r.peak_snapshots.join(', ')}
              >
                {peakHoursCell(r.peak_snapshots, r.n_peak_hours)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** The derating table — every proxy that entered the left-hand side. */
function DeratingTable({ assets }: { assets: ReserveMarginAsset[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[10px]" data-testid="reserve-margin-derating">
        <thead className="text-muted">
          <tr data-testid="rm-derating-head">
            <th className="text-left font-medium py-1 pr-3">Asset</th>
            <th className="text-left font-medium py-1 pr-3">Period</th>
            <th className="text-left font-medium py-1 pr-3">Kind</th>
            <th
              className="text-left font-medium py-1 pr-3"
              title="The capacity in the SOLVED plan (`p_nom_opt` for an extendable — the capacity this standard forced into being), not the candidate's bound."
            >
              Built capacity
            </th>
            <th className="text-left font-medium py-1 pr-3">Derate</th>
            <th
              className="text-left font-medium py-1 pr-3"
              title="What the derate WOULD have been on the net-load window (demand minus every varying-profile unit's availability). A second proxy in the margin's own units — never a correction. Hover a dash to see why there is no number."
            >
              Net derate
            </th>
            <th
              className="text-left font-medium py-1 pr-3"
              title="Whether this row shaped the net-load window: a varying profile AND built capacity. Netted capacity is not 'VRE' — a thermal maintenance schedule is netted too."
            >
              Netted
            </th>
            <th
              className="text-left font-medium py-1 pr-3"
              title="FOR or EFORd. `1 − FOR` is not a UCAP derate: FOR excludes reserve-shutdown hours and is optimistic exactly for peakers."
            >
              Basis
            </th>
            <th
              className="text-left font-medium py-1 pr-3"
              title="asset = the outage rate you entered; carrier_default = a class average from the defaults library, which you did not enter and which changes what gets built."
            >
              Source
            </th>
            <th className="text-left font-medium py-1 pr-3">Firm</th>
            <th className="text-left font-medium py-1 pr-3">Limitation</th>
          </tr>
        </thead>
        <tbody>
          {assets.map(a => {
            const id = `${a.name}-${a.period}`
            return (
              <tr
                key={id}
                className="border-t border-border/50 align-top"
                data-testid={`rm-asset-${id}`}
              >
                <td className="py-1 pr-3 font-mono">{a.name}</td>
                <td className="py-1 pr-3">{a.period}</td>
                <td className="py-1 pr-3" data-testid={`rm-asset-kind-${id}`}>
                  {a.kind}{a.extendable ? ' (extendable)' : ''}
                </td>
                <td className="py-1 pr-3 font-mono" data-testid={`rm-asset-capacity-${id}`}>
                  {mw(a.capacity_mw)}
                </td>
                <td className="py-1 pr-3 font-mono" data-testid={`rm-asset-derate-${id}`}>
                  {a.derate.toFixed(3)}
                </td>
                <td
                  className="py-1 pr-3 font-mono"
                  data-testid={`rm-asset-derate-net-${id}`}
                  title={derateNetText(a).title}
                >
                  {derateNetText(a).text}
                </td>
                <td
                  className={'py-1 pr-3 ' + (a.netted ? 'text-accent' : 'text-muted')}
                  data-testid={`rm-asset-netted-${id}`}
                >
                  {a.netted ? 'netted' : '—'}
                </td>
                <td className="py-1 pr-3 font-mono" data-testid={`rm-asset-basis-${id}`}>
                  {basisLabel(a.basis, a.source)}
                </td>
                <td className="py-1 pr-3 font-mono" data-testid={`rm-asset-source-${id}`}>
                  {a.source || '—'}
                </td>
                <td className="py-1 pr-3 font-mono">{mw(a.firm_mw)}</td>
                <td
                  className={'py-1 pr-3 ' + (a.energy_limited ? 'text-warn' : 'text-muted')}
                  data-testid={`rm-asset-energy-limited-${id}`}
                  title={a.energy_limited
                    ? 'Credited on POWER while its ENERGY limit is what actually binds it (a reservoir with inflow). Recorded, not corrected.'
                    : undefined}
                >
                  {a.energy_limited ? 'energy-limited' : '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function ReserveMarginPanel() {
  const currentProject = useUIStore(s => s.currentProject)
  const { data } = useQuery({
    queryKey: nk(currentProject, 'results', 'reserve_margin'),
    queryFn: () => resultsApi.getReserveMargin(),
  })
  const payload = (data ?? null) as ReserveMarginPayload | null
  const bases = Object.entries(payload?.derating_bases ?? {})
  const forNote = forOptimisticNote(payload?.derating_bases)

  return (
    <section className="border border-border rounded" data-testid="reserve-margin-panel">
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-border bg-panel text-[10px] font-semibold uppercase tracking-wide text-muted">
        <ShieldCheck size={11} /> Firm capacity — planning reserve margin
      </div>
      <div className="p-3 flex flex-col gap-3">
        {!payload ? (
          // An ANSWER, not a stub: this is the ordinary state of the surface
          // before a margin-set solve, and the next action is a setting.
          <p className="text-[11px] text-muted" data-testid="reserve-margin-empty">
            No firm-capacity standard was enforced by the last solve, so there
            is nothing to report against. Set{' '}
            <code className="font-mono">reserve_margin</code> in solver settings
            (0.15 = 15% of peak) and re-solve to get one. The margin is a
            constraint, never a cost — the LP cannot buy its way out of it — so
            a solve that meets it has actually built the capacity.
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <span
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-accent/10 text-accent text-[10px] font-semibold"
                data-testid="reserve-margin-headline"
              >
                standard: {pct(payload.margin)} over peak
              </span>
              <span
                className="px-2 py-0.5 rounded bg-panel border border-border text-[10px]"
                data-testid="reserve-margin-scope-chip"
              >
                {payload.horizon_wide ? 'horizon-wide' : 'per period'} ·{' '}
                {payload.by_period.length} period
                {payload.by_period.length === 1 ? '' : 's'}
              </span>
            </div>

            {/* ★ What the constraint actually is, in words. */}
            <p className="text-[10px] text-muted" data-testid="reserve-margin-scope">
              {scopeSentence(payload.horizon_wide)}
            </p>

            <PeriodTable rows={payload.by_period} />

            {/* Phase 12b — the net-load window, one line per period. A
                SECOND PROXY: the margin credits profile-bearing capacity on
                the hours GROSS demand peaks; a system with such capacity
                runs short on the hours NET demand peaks. The line says how
                far apart those windows are and what the credit would have
                been on the other one. It is never called a correction. */}
            <div className="flex flex-col gap-0.5" data-testid="rm-net-window">
              {payload.by_period.map(r => (
                <p
                  key={r.period}
                  className="text-[10px] text-muted"
                  data-testid={`rm-net-window-${r.period}`}
                  data-status={r.net_window?.status ?? 'absent'}
                  title={r.net_window?.snapshots?.length
                    ? r.net_window.snapshots.join(', ')
                    : undefined}
                >
                  {payload.by_period.length > 1 ? `${r.period}: ` : ''}
                  {netWindowSentence(r.net_window, Boolean(payload.partial_periods))}
                </p>
              ))}
            </div>

            {/* ★ Why two columns and not one green tick. */}
            <p className="text-[10px] text-muted" data-testid="reserve-margin-binding-note">
              "Standard" is whether the plan REACHES the margin; "shaped the
              plan" is whether firm capacity sits on the constraint's bound.
              They are different questions: a margin the existing fleet already
              satisfies is met and not binding, and reading it as binding would
              credit the margin for capacity that was always there.
            </p>

            <div className="flex flex-col gap-1">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                Derating — every proxy that entered the constraint
              </span>
              <DeratingTable assets={payload.assets} />
            </div>

            <div className="flex flex-col gap-1">
              <div className="flex flex-wrap items-center gap-2" data-testid="rm-bases">
                <span className="text-[10px] text-muted">Outage-rate bases:</span>
                {bases.length === 0 ? (
                  <span className="text-[10px] text-muted">none recorded</span>
                ) : bases.map(([basis, n]) => (
                  <span
                    key={basis}
                    className="px-2 py-0.5 rounded bg-panel border border-border text-[10px] font-mono"
                    data-testid={`rm-basis-${basis}`}
                  >
                    {basisLabel(basis, basis ? undefined : 'missing')} × {n}
                  </span>
                ))}
              </div>
              {forNote && (
                <p
                  className="inline-flex items-start gap-1 text-[10px] text-warn"
                  data-testid="rm-bases-note"
                >
                  <AlertTriangle size={11} className="mt-[1px] shrink-0" />
                  <span>{forNote}</span>
                </p>
              )}
            </div>
          </>
        )}

        {/* ★ At the point of display, in both states: the margin is a proxy
            standard, and a met one is not a met reliability target. */}
        <p className="text-[10px] text-muted" data-testid="reserve-margin-caveat">
          {RESERVE_MARGIN_CAVEAT}
        </p>
      </div>
    </section>
  )
}

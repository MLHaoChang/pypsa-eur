// Adequacy target UI pieces (plan Phase 1 Task 5; design spec §§5.1, 7).
// Kept as a small standalone module so the warning logic and the chips are
// unit-testable without mounting SolverSettings or LostLoadTab.
import { AlertTriangle, Target } from 'lucide-react'

// Payload of GET /results/adequacy (models/adequacy.py, serialized). Only
// the fields the UI renders — the backend owns the full contract.
export interface AdequacyReportPayload {
  engine: string
  fidelity: string
  target: {
    basis: string
    system: {
      cap_mwh: number
      achieved_ens_mwh: number
      achieved_shed_hours: number
      // The cap is enforced PER investment period, so cap_mwh /
      // achieved_ens_mwh above are SUMS that can read as comfortable while
      // the period that actually bound has no headroom at all. Optional so
      // an older cached report still renders.
      by_period?: Array<{
        period: string; cap_mwh: number; achieved_ens_mwh: number; binding: boolean
      }>
    }
    zones: Array<{ zone: string; cap_mwh: number; achieved_ens_mwh: number; binding: boolean }>
    binding: 'system_cap' | 'zone_cap' | 'voll'
    zone_field_populated: boolean
  }
  metrics: { ens_mwh: number; shed_hours: number }
  energy: { involuntary_mwh: number; demand_response_mwh: number }
}

// The "99 % trap" (spec §5.1): the target is entered in parts per ten
// thousand of electrical demand. Real reliability standards sit 2–3 orders
// of magnitude below a percent-scale number — a user typing "99 %
// availability" as 100 ‱+ is planning NOT to serve that share and gets a
// cheap-looking, badly under-built plan. Returns the warning text, or null
// when the value is unset/plausible.
export function ensTargetWarning(permyriad: number | null | undefined): string | null {
  if (permyriad == null || !isFinite(permyriad) || permyriad <= 0) return null
  if (permyriad <= 100) return null
  const pct = permyriad / 100
  return (
    `${permyriad}‱ = ${pct}% of demand deliberately unserved. Real ` +
    'standards are far tighter (adequate systems run ~0.1–1‱ of energy; ' +
    "GB's standard is 3 loss-of-load hours/yr). A generous target yields " +
    'a cheap-looking, badly under-built plan.'
  )
}

const BINDING_LABEL: Record<AdequacyReportPayload['target']['binding'], string> = {
  system_cap: 'ENS cap',
  zone_cap: 'zone ceiling',
  voll: 'VoLL',
}

// Which standard actually shaped the plan (spec §5.5) — without this badge,
// two users with the same stated target get different plans for reasons
// neither can observe.
export function AdequacyChips({ report }: { report: AdequacyReportPayload | null }) {
  if (!report) return null
  const t = report.target
  const bindingZones = t.zones.filter(z => z.binding).map(z => z.zone || '<blank>')
  const bindingLabel =
    t.binding === 'zone_cap' && bindingZones.length > 0
      ? `zone ceiling ${bindingZones.join(', ')}`
      : BINDING_LABEL[t.binding]
  // Multi-period only: the summed headline hides which period bound.
  const periodRows = t.system.by_period ?? []
  const multiPeriod = periodRows.length > 1
  const bindingPeriods = periodRows.filter(r => r.binding).map(r => r.period)
  const fidelityTip =
    'LP proxy (deterministic, perfect foresight, one realisation) — a ' +
    'relative diagnostic, NOT comparable to a statutory reliability standard.'
  return (
    <div className="flex flex-wrap items-center gap-2 mb-3" data-testid="adequacy-chips">
      <span
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-accent/10 text-accent text-[10px] font-semibold"
        title={fidelityTip}
      >
        <Target size={11} /> standard: {bindingLabel}
      </span>
      <span className="px-2 py-0.5 rounded bg-panel border border-border text-[10px]" title={fidelityTip}>
        ENS {t.system.achieved_ens_mwh.toFixed(1)} / cap {t.system.cap_mwh.toFixed(1)} MWh
      </span>
      <span className="px-2 py-0.5 rounded bg-panel border border-border text-[10px]" title={fidelityTip}>
        shed-hours {report.metrics.shed_hours.toFixed(1)} h
      </span>
      {report.energy.demand_response_mwh > 0 && (
        <span className="px-2 py-0.5 rounded bg-panel border border-border text-[10px]" title={fidelityTip}>
          DSR {report.energy.demand_response_mwh.toFixed(1)} MWh (not unserved)
        </span>
      )}
      {multiPeriod && (
        <span
          className={
            'px-2 py-0.5 rounded text-[10px] ' +
            (bindingPeriods.length
              ? 'bg-warn/10 text-warn'
              : 'bg-panel border border-border')
          }
          title={
            'The cap is enforced per investment period, so the ENS/cap figures ' +
            'beside this chip are sums across periods and overstate the ' +
            'headroom. Per period: ' +
            periodRows
              .map(r => `${r.period} ${r.achieved_ens_mwh.toFixed(1)}/${r.cap_mwh.toFixed(1)} MWh`)
              .join(' · ')
          }
        >
          {bindingPeriods.length
            ? `binding period${bindingPeriods.length > 1 ? 's' : ''}: ${bindingPeriods.join(', ')}`
            : `${periodRows.length} periods, none binding`}
        </span>
      )}
      {!t.zone_field_populated && t.zones.length > 0 && (
        <span
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-warn/10 text-warn text-[10px]"
          title="Every electrical bus has a blank `country`, so the per-zone ceiling collapsed into a second system cap."
        >
          <AlertTriangle size={11} /> zones unpopulated
        </span>
      )}
    </div>
  )
}

// Payload of GET /results/copt (Phase 2) — the analytic screening engine.
export interface CoptPayload {
  engine: string
  fidelity: string
  metrics: {
    lole_hours: number; eue_mwh: number; lolp_max: number
    // "hours_per_year" only when the modelled horizon really is a year;
    // otherwise "hours_per_horizon". Optional `horizon_years` says how long
    // that horizon is, so the chip can name it.
    time_basis: string
    horizon_years?: number | null
  }
  per_mode: Array<Record<string, unknown>>
  fleet: { units: number; must_take: number; delta_mw: number }
  voll_eur_per_mwh: number
}

// The screening EUE dwarfing the LP proxy's ENS means storage/network are
// carrying the adequacy — precisely when the classical number misleads and
// when a PRAS/Antares export is worth doing (spec §5.3). The DIVERGENCE is
// the product; neither number alone is the headline.
const DIVERGENCE_RATIO = 5

/**
 * LOLE is quoted per YEAR by convention and every standard is written that
 * way, but the engine sums over whatever horizon the model spans. Rendering a
 * bare "h" next to a sub-annual figure invites exactly the comparison that
 * must not be made — 80.86 h on a 168 h week reads as a system 27x inside a
 * 3 h/yr standard when the annualised truth is ~1400x outside it.
 *
 * So the unit carries the basis: "h/yr" only when the horizon is a year,
 * otherwise "h / <N> h horizon" naming what was actually modelled.
 */
export function basisSuffix(
  m: { time_basis?: string; horizon_years?: number | null },
): string {
  if (m.time_basis === 'hours_per_year') return 'h/yr'
  const hours = m.horizon_years != null && isFinite(m.horizon_years)
    ? Math.round(m.horizon_years * 8760)
    : null
  return hours && hours > 0 ? `h / ${hours} h horizon` : 'h / horizon'
}

export function CoptChips({ copt, proxyEnsMwh }: {
  copt: CoptPayload | null
  proxyEnsMwh: number | null
}) {
  if (!copt) return null
  const tip =
    'COPT screening (analytic convolution): thermal-only, storage-excluded, ' +
    'network-free. NOT comparable to a statutory standard. Its divergence ' +
    'from the LP proxy is the diagnostic.'
  const diverges =
    proxyEnsMwh != null && proxyEnsMwh >= 0 &&
    copt.metrics.eue_mwh > DIVERGENCE_RATIO * Math.max(proxyEnsMwh, 1e-9)
  return (
    <div className="flex flex-wrap items-center gap-2 mb-3" data-testid="copt-chips">
      <span className="px-2 py-0.5 rounded bg-panel border border-border text-[10px] font-semibold" title={tip}>
        COPT screening
      </span>
      <span className="px-2 py-0.5 rounded bg-panel border border-border text-[10px]" title={tip}>
        LOLE {copt.metrics.lole_hours.toFixed(1)} {basisSuffix(copt.metrics)}
      </span>
      <span className="px-2 py-0.5 rounded bg-panel border border-border text-[10px]" title={tip}>
        EUE {copt.metrics.eue_mwh.toFixed(1)} MWh
      </span>
      <span className="px-2 py-0.5 rounded bg-panel border border-border text-[10px] text-muted" title={tip}>
        {copt.fleet.units} unit(s), {copt.fleet.must_take} must-take
      </span>
      {diverges && (
        <span
          className="px-2 py-0.5 rounded bg-warn/10 text-warn text-[10px]"
          title="The storage-blind screening EUE far exceeds the storage-aware LP proxy's unserved energy: storage/network carry the adequacy here, and the classical screening number overstates the risk."
        >
          screening ≫ proxy — storage/network carry the adequacy
        </span>
      )}
    </div>
  )
}

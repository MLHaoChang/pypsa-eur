import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { resultsApi } from '../../api/simulation'
import { KPI } from './shared'
import { useResultsFilter } from './filterContext'

// ── Emissions tab ───────────────────────────────────────────────────────────
// Surfaces PyPSA's CO₂ totals from LP dispatch × per-carrier intensity /
// efficiency. Three sections:
//   1. KPI strip — total tCO₂ for the active scope, cap shadow prices.
//   2. Per-carrier breakdown table — tCO₂ + share %.
//   3. Per-generator breakdown — energy × intensity → tCO₂.
//
// Multi-period: the backend emits `by_period[]` and applies snapshot ×
// investment_period_weightings.years scaling. When a single period is
// selected via the global Results filter, we slice to that period's row
// from `by_period`; with "Aggregated" the horizon totals are shown.
//
// CO₂ caps: every active `primary_energy + co2_emissions` global constraint
// shows up in `caps[]`. Horizon-wide caps display a single shadow-price KPI
// at the top; per-period caps render in a dedicated table beneath.

export default function Emissions() {
  const { data: emissions } = useQuery({
    queryKey: ['results', 'emissions'],
    queryFn: resultsApi.getEmissions,
  })

  const filter = useResultsFilter()
  const selectedPeriod = filter.selectedPeriod

  // Pick the active scope's data: either the selected period's slice, or
  // the horizon totals. Falls back to horizon when the requested period
  // wasn't found (e.g. the user picked a period not in the solved set).
  const view = useMemo(() => {
    if (!emissions) return null
    const periodEntry = selectedPeriod != null
      ? emissions.by_period.find(p => p.period === selectedPeriod
                                    || String(p.period) === String(selectedPeriod))
      : undefined
    if (periodEntry) {
      return {
        scope: 'period' as const,
        period: periodEntry.period,
        total_tCO2: periodEntry.total_tCO2,
        by_carrier: periodEntry.by_carrier,
        by_generator: periodEntry.by_generator,
      }
    }
    return {
      scope: 'horizon' as const,
      period: null,
      total_tCO2: emissions.total_tCO2,
      by_carrier: emissions.by_carrier,
      by_generator: emissions.by_generator,
    }
  }, [emissions, selectedPeriod])

  // Cap relevant to the current scope. For horizon view: horizon-wide caps.
  // For period view: the cap matching the selected period (if any).
  const scopeCaps = useMemo(() => {
    if (!emissions) return []
    if (!view) return []
    if (view.scope === 'horizon') {
      return emissions.caps.filter(c => c.scope === 'horizon')
    }
    return emissions.caps.filter(c =>
      c.scope === 'period' && c.investment_period === view.period,
    )
  }, [emissions, view])

  if (!emissions) {
    return (
      <div className="p-6 text-center text-xs text-muted">
        No emissions data yet. Run a LOPF / SCLOPF solve (header → <span className="font-medium text-text">Run LOPF</span>) — CO₂ is computed from per-carrier intensity × dispatched energy.
      </div>
    )
  }
  if (!view) return null

  const scopeLabel = view.scope === 'horizon'
    ? 'horizon total'
    : `period ${view.period}`
  const allCaps = emissions.caps
  const hasPerPeriodCaps = allCaps.some(c => c.scope === 'period')

  return (
    <div className="flex flex-col gap-5 p-5 overflow-y-auto h-full text-sm [&>*]:shrink-0">

      {/* ── KPI strip ──────────────────────────────────────────── */}
      <section>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">
            CO₂ emissions
            <span className="text-[10px] font-normal text-muted ml-2">
              ({scopeLabel})
              {emissions.is_multi_period && view.scope === 'horizon' && (
                <span className="ml-2">— pick a period from the strip above for the per-year split.</span>
              )}
            </span>
          </h3>
          {scopeCaps.length > 0 && (
            <span className="text-[10px] text-muted">
              {scopeCaps.length === 1 ? 'Active cap:' : `${scopeCaps.length} active caps`}
              {scopeCaps.length === 1 && scopeCaps[0].cap_tCO2 != null && (
                <>
                  {' '}<span className="font-mono text-text">
                    {scopeCaps[0].cap_tCO2.toLocaleString(undefined, { maximumFractionDigits: 1 })} tCO₂
                  </span>{' '}
                  ({scopeCaps[0].name})
                </>
              )}
            </span>
          )}
        </div>
        <div className={`grid gap-3 ${scopeCaps.length === 1 ? 'grid-cols-3' : 'grid-cols-1'}`}>
          <KPI accent label={`Total emissions (${scopeLabel})`}
               value={`${view.total_tCO2.toLocaleString(undefined, { maximumFractionDigits: 1 })} tCO₂`}
               hint="Σ dispatch × snapshot_weight × period.years × co2_emissions / efficiency. Matches PyPSA's primary-energy accounting." />
          {scopeCaps.length === 1 && (
            <>
              <KPI label="Shadow price (marginal abatement)"
                   value={scopeCaps[0].shadow_price_eur_per_tCO2 != null
                     ? `${scopeCaps[0].shadow_price_eur_per_tCO2.toFixed(2)} €/tCO₂`
                     : '—'}
                   hint={`LP dual on the CO₂ cap. Reads as the marginal cost of avoiding one tCO₂. Cap scope: ${scopeCaps[0].scope}${scopeCaps[0].binding ? ' — currently BINDING.' : ''}`} />
              <KPI label="Cap slack"
                   value={scopeCaps[0].slack_tCO2 != null
                     ? `${scopeCaps[0].slack_tCO2.toLocaleString(undefined, { maximumFractionDigits: 1 })} tCO₂`
                     : '—'}
                   hint="cap − emissions. Positive = headroom remaining. Zero or negative = cap binds." />
            </>
          )}
        </div>
      </section>

      {/* ── All CO₂ caps table (when multiple exist OR per-period caps) ── */}
      {(allCaps.length > 1 || hasPerPeriodCaps) && (
        <section>
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2">
            CO₂ caps ({allCaps.length})
            <span className="ml-2 text-[10px] font-normal text-muted">
              {hasPerPeriodCaps
                ? 'Per-period caps set via primary_energy global constraints with investment_period.'
                : 'Horizon-wide caps.'}
            </span>
          </h3>
          <div className="border border-border rounded overflow-auto max-h-64">
            <table className="w-full text-xs border-collapse" style={{ minWidth: 720 }}>
              <thead className="sticky top-0 bg-bg-2 z-10">
                <tr className="border-b border-border">
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Name</th>
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Scope</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Cap (tCO₂)</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Used (tCO₂)</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Slack</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="LP dual on the cap — €/tCO₂ marginal abatement cost.">μ (€/tCO₂)</th>
                </tr>
              </thead>
              <tbody>
                {allCaps.map((c, i) => (
                  <tr key={c.name}
                      className={`${i % 2 === 0 ? 'bg-bg' : 'bg-panel'} ${c.binding ? 'ring-1 ring-warn/30' : ''}`}>
                    <td className="px-2 py-1 font-mono text-[11px]">{c.name}</td>
                    <td className="px-2 py-1 text-[11px]">
                      {c.scope === 'horizon'
                        ? <span className="text-muted">horizon</span>
                        : <span className="font-mono text-accent">{c.investment_period}</span>}
                    </td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">
                      {c.cap_tCO2 != null
                        ? c.cap_tCO2.toLocaleString(undefined, { maximumFractionDigits: 1 })
                        : '—'}
                    </td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">
                      {c.used_tCO2.toLocaleString(undefined, { maximumFractionDigits: 1 })}
                    </td>
                    <td className={`px-2 py-1 font-mono text-[11px] text-right
                      ${c.slack_tCO2 != null && c.slack_tCO2 < 0 ? 'text-danger font-semibold' : ''}`}>
                      {c.slack_tCO2 != null
                        ? c.slack_tCO2.toLocaleString(undefined, { maximumFractionDigits: 1 })
                        : '—'}
                      {c.binding && <span className="ml-1 text-warn text-[9px]">BIND</span>}
                    </td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">
                      {c.shadow_price_eur_per_tCO2.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ── Per-period totals (multi-period only, when not period-scoped) ── */}
      {emissions.is_multi_period && view.scope === 'horizon' && emissions.by_period.length > 0 && (
        <section>
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2">
            By investment period
            <span className="ml-2 text-[10px] font-normal text-muted">
              Use the Period strip above to drill into per-year carrier / generator splits.
            </span>
          </h3>
          <div className="border border-border rounded overflow-auto max-h-64">
            <table className="w-full text-xs border-collapse" style={{ minWidth: 560 }}>
              <thead className="sticky top-0 bg-bg-2 z-10">
                <tr className="border-b border-border">
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Period</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Total (tCO₂)</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Share of horizon</th>
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Top carriers</th>
                </tr>
              </thead>
              <tbody>
                {emissions.by_period.map((p, i) => {
                  const topCarriers = p.by_carrier.slice(0, 3)
                    .map(c => `${c.carrier} (${c.tCO2.toLocaleString(undefined, { maximumFractionDigits: 0 })})`)
                    .join(' · ')
                  const share = emissions.total_tCO2 > 0
                    ? (100 * p.total_tCO2 / emissions.total_tCO2) : 0
                  return (
                    <tr key={String(p.period)} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                      <td className="px-2 py-1 font-mono text-[11px] text-accent">{p.period}</td>
                      <td className="px-2 py-1 font-mono text-[11px] text-right">
                        {p.total_tCO2.toLocaleString(undefined, { maximumFractionDigits: 1 })}
                      </td>
                      <td className="px-2 py-1 font-mono text-[11px] text-right">{share.toFixed(1)} %</td>
                      <td className="px-2 py-1 text-[11px] text-muted">{topCarriers || '—'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ── Per-carrier breakdown ─────────────────────────────── */}
      {view.total_tCO2 === 0 ? (
        <p className="text-[11px] text-muted">
          {view.scope === 'period'
            ? `No CO₂ emitted in period ${view.period}.`
            : <>All dispatched carriers have <code>co2_emissions = 0</code>. Set per-carrier emission intensities (tCO₂/MWh of primary energy) in the Carriers tab to activate this report.</>}
        </p>
      ) : view.by_carrier.length > 0 && (
        <section>
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2">
            By carrier
            <span className="ml-2 text-[10px] font-normal text-muted">({scopeLabel})</span>
          </h3>
          <div className="border border-border rounded overflow-auto max-h-64">
            <table className="w-full text-xs border-collapse" style={{ minWidth: 560 }}>
              <thead className="sticky top-0 bg-bg-2 z-10">
                <tr className="border-b border-border">
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Carrier</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">tCO₂</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Share</th>
                </tr>
              </thead>
              <tbody>
                {view.by_carrier.map((row, i) => (
                  <tr key={row.carrier} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                    <td className="px-2 py-1 font-mono text-[11px]">{row.carrier || '(blank)'}</td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">
                      {row.tCO2.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                    </td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">{row.share_pct.toFixed(1)} %</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ── Per-generator breakdown ───────────────────────────── */}
      {view.by_generator.filter(r => r.tCO2 > 0).length > 0 && (
        <section>
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2">
            By generator
            <span className="ml-2 text-[10px] font-normal text-muted">({scopeLabel})</span>
          </h3>
          <div className="border border-border rounded overflow-auto max-h-80">
            <table className="w-full text-xs border-collapse" style={{ minWidth: 640 }}>
              <thead className="sticky top-0 bg-bg-2 z-10">
                <tr className="border-b border-border">
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Generator</th>
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Carrier</th>
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Component</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Energy (MWh)</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">tCO₂</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Intensity (tCO₂/MWhₑ)</th>
                </tr>
              </thead>
              <tbody>
                {view.by_generator.filter(r => r.tCO2 > 0).map((row, i) => (
                  <tr key={row.name} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                    <td className="px-2 py-1 font-mono text-[11px]">{row.name}</td>
                    <td className="px-2 py-1 text-[11px] text-muted">{row.carrier || '(blank)'}</td>
                    <td className="px-2 py-1 text-[11px] text-muted">{row.component ?? 'Generator'}</td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">
                      {row.energy_mwh.toLocaleString(undefined, { maximumFractionDigits: 1 })}
                    </td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">
                      {row.tCO2.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                    </td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">
                      {row.intensity_tCO2_per_MWh_out.toFixed(3)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  )
}

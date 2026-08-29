import { useMemo, useRef } from 'react'
import {
  ResponsiveContainer, ComposedChart, Area, Line,
  XAxis, YAxis, CartesianGrid, Tooltip as RTooltip, Legend,
} from 'recharts'
import type { Generator, Load, StorageUnit, Link as LinkT } from '../../api/types'
import { type TSPayload, shortStamp, stampWithPeriod, fmtPower, isRenewableCarrier,
  ChartActions, downloadCSV, ChartCard, colourForCarrier,
  CHART_GRID, CHART_AXIS, CHART_LEGEND, CHART_TOOLTIP, yAxisLabel } from './shared'

// ── Per-period generation dispatch stack ────────────────────────────────────
// Carrier-stacked area chart showing hourly generation by fuel type, with the
// system load drawn on top as a dashed line. Energy-charts order (bottom→top):
// renewables → storage discharge → thermal. Curves use monotone smoothing.
// Storage charge is a separate negative stack below zero.
//
// Renders only when `selectedPeriod` is set (the parent Dispatch tab gates
// this). Period filtering is applied via `range` from useResultsFilter.

interface DispatchStackProps {
  gensTS: TSPayload | null
  loadTS: TSPayload | null
  storPowerTS: TSPayload | null
  generators: Generator[]
  loads: Load[]
  storageUnits: StorageUnit[]
  range: { from: number; to: number }
  // Optional: link p0 (signed MW; positive = bus0→bus1, i.e. consumption on
  // bus0 for electrolyzers) + names of electrolyzer links so the stack can
  // render electrolyzer consumption as a below-axis "consume" series next
  // to storage_charge. Omit both for a pure-electricity dispatch chart.
  linkTS?: TSPayload | null
  electrolyzerNames?: Set<string>
  // Names of loads on the carrier-of-interest bus (electrical / heat / H2 /
  // …). The dashed Load line at the top of the chart should only sum
  // demand on the chart's chosen bus — adding H2 / heat MW to electricity
  // MW is a mixed-unit total that doesn't make physical sense on the same
  // axis. Caller is expected to derive this from its load-carrier mapping.
  // When omitted, falls back to summing every load column (legacy
  // behaviour for single-carrier networks).
  electricalLoadNames?: Set<string>
  // Names of storage units on the carrier-of-interest bus. When provided,
  // storage_charge / storage_discharge are restricted to these columns —
  // so H2 storage filling from the electrolyzer's H2 output and heat
  // storage filling from the boiler / heat-pump output don't appear
  // below the electrical-bus zero line (they belong on the H2 / heat bus,
  // not the electrical one). When omitted, every storage column counts
  // (legacy behaviour for single-carrier networks).
  electricalStorageNames?: Set<string>
  // Names of generators on the carrier-of-interest bus. When provided,
  // the carrier-stacked area only sums generators whose name is in this
  // set — so a heat-dump Generator on dc_lowT_heat doesn't appear in the
  // electrical-bus stack just because both share the snapshot index.
  // When omitted, every generator column counts (legacy single-carrier
  // behaviour).
  genNames?: Set<string>
  // Full Link records (bus0/bus1/bus2/efficiency/efficiency2 etc.) — used
  // alongside `busNames` to compute per-link cross-carrier contributions
  // to this bus carrier's energy balance. Without these, an electrolyzer
  // producing H2 to the H2 bus is invisible on the H2 dispatch chart
  // (only storage charge/discharge + load shown), and the heat-pump
  // delivering high-T heat is invisible on the heat chart. Pass the
  // whole `links` array — the component filters to the relevant ports.
  links?: LinkT[]
  // Bus names belonging to this carrier (e.g. {Bus 2, Bus 3, bus1, Bus 4}
  // for AC; {dc_lowT_heat, dc_highT_heat} for heat). Combined with
  // `links` to attribute each link's port flows to the right carrier.
  busNames?: Set<string>
  // Human-readable label for the carrier this stack represents — drives
  // the chart's title, hint, and the load-line legend label. Defaults to
  // 'electricity' so single-carrier networks keep their original labels.
  carrierLabel?: string
}

// Per-carrier colours come from the shared `colourForCarrier` (see shared.tsx)
// so the dispatch stack and the aggregated bar charts stay in lockstep.

export default function DispatchStack({
  gensTS, loadTS, storPowerTS,
  generators, loads, storageUnits, range,
  linkTS, electrolyzerNames, electricalLoadNames, electricalStorageNames,
  links, busNames, genNames, carrierLabel = 'electricity',
}: DispatchStackProps) {
  const hasElectrolyzer = !!(linkTS && electrolyzerNames && electrolyzerNames.size > 0)
  // Filters are ACTIVE when the prop is provided (even if the Set is empty —
  // that means "this bus carrier has no assets of this kind"). Treating empty
  // as "no filter, count everything" is the legacy single-carrier fallback;
  // for the per-bus-carrier multi-stack rendering (parent passes group.gens /
  // group.loads / group.storage), we must respect an empty Set so a heat bus
  // with NO storage doesn't sum Battery + H2 storage on the heat chart.
  const hasElectricalFilter = electricalLoadNames !== undefined
  const hasElectricalStorageFilter = electricalStorageNames !== undefined
  const hasGenFilter = genNames !== undefined

  // Map every generator/load/storage column to its carrier. Generators
  // without a carrier get an "other" bucket. Sorted carriers (renewables,
  // then storage, then thermal) make the stack visually consistent across
  // runs — the bottom of every bar is always the same carrier.
  const { genCarriers, loadCarriers, stackOrder, colourByCarrier } = useMemo(() => {
    const carrierByGen = new Map<string, string>()
    for (const g of generators) carrierByGen.set(g.name, (g.carrier ?? 'other'))
    const carrierByLoad = new Map<string, string>()
    for (const l of loads) carrierByLoad.set(l.name, (l.carrier ?? 'electricity'))
    // Collect all generator carriers actually in the TS columns. When the
    // caller scoped this stack to a specific bus-carrier via `genNames`,
    // only consider generators in that set so the carrier strip doesn't
    // grow legend entries for generators that belong to a different bus
    // (e.g. heat-dump in the electrical stack's legend).
    const genCarrierSet = new Set<string>()
    if (gensTS) for (const c of gensTS.columns) {
      if (hasGenFilter && !genNames!.has(c)) continue
      genCarrierSet.add(carrierByGen.get(c) ?? 'other')
    }
    // Energy-charts order (bottom → top): renewables, storage discharge,
    // thermal. Solar/wind form the base silhouette; gas fills residual on top.
    const thermal = [...genCarrierSet].filter(c => !isRenewableCarrier(c))
    const renew   = [...genCarrierSet].filter(c => isRenewableCarrier(c))
    thermal.sort()
    renew.sort()
    const stack: string[] = [...renew]
    // When the parent scoped this stack to a specific bus carrier via
    // `electricalStorageNames`, only show storage when AT LEAST ONE storage
    // unit on that bus exists — otherwise a heat-bus chart inherits Battery
    // + H2 storage from the network-wide array even though neither is on a
    // heat bus.
    const hasStorageOnThisCarrier = storPowerTS != null && (
      electricalStorageNames !== undefined
        ? electricalStorageNames.size > 0
        : storageUnits.length > 0
    )
    if (hasStorageOnThisCarrier) stack.push('storage_discharge')
    stack.push(...thermal)
    const colours: Record<string, string> = {}
    stack.forEach((c, i) => { colours[c] = colourForCarrier(c, i) })
    colours['storage_discharge'] = colourForCarrier('storage')
    colours['storage_charge']    = colourForCarrier('storage')
    colours['electrolyzer_consume'] = '#0ea5e9'   // sky-blue, matches Links style
    colours['link_inflows']      = '#16a34a'      // emerald — cross-carrier production
    colours['link_outflows']     = '#dc2626'      // red — cross-carrier consumption
    colours['load']              = colourForCarrier('load')
    return {
      genCarriers: carrierByGen,
      loadCarriers: carrierByLoad,
      stackOrder: stack,
      colourByCarrier: colours,
    }
    // genNames + hasGenFilter close over the carrier-detection skip at line ~80
    // — without them in the deps, a parent that flips between filtered and
    // unfiltered sets would leak the wrong legend (e.g. a heat-dump generator
    // still showing in the electrical stack's strip after the parent narrowed
    // the filter). QA-flagged dep gap.
  }, [generators, loads, storageUnits, gensTS, storPowerTS, genNames, hasGenFilter])

  // Per-link contribution coefficients for this bus carrier. For each link
  // l that has AT LEAST ONE port (bus0/bus1/bus2) on a bus in `busNames`,
  // compute the effective coefficient on p0: `c = Σ over touching ports of
  // (port==0 ? -1 : efficiency_port)`. Per-snapshot contribution =
  // c × p0(t). Positive c → link adds to the carrier's supply (e.g., the
  // H2 bus sees +efficiency × p0 from an electrolyzer when bus1 is H2);
  // negative c → link drains the carrier (e.g., the electrical bus sees
  // -p0 from an electrolyzer when bus0 is electrical).
  //
  // Without this generalisation, the dispatch chart on H2 / heat carriers
  // shows ONLY storage charge/discharge + load — the LP-real generation
  // (electrolyzer producing H2, heat-pump producing heat, datacenter
  // delivering waste heat) is invisible. Surfaced by user: H2 chart shows
  // storage_discharge=192 MW + Load=192 MW but no electrolyzer (which
  // should appear as ~600 MW production when storage charges).
  const linkCoeffs = useMemo(() => {
    const coeffs = new Map<string, number>()
    if (!links || !busNames || busNames.size === 0) return coeffs
    for (const l of links) {
      let c = 0
      if (l.bus0 && busNames.has(l.bus0)) c += -1
      if (l.bus1 && busNames.has(l.bus1)) c += (l.efficiency ?? 1.0)
      const bus2 = (l as { bus2?: string; efficiency2?: number }).bus2
      const eff2 = (l as { bus2?: string; efficiency2?: number }).efficiency2
      if (bus2 && busNames.has(bus2)) c += (eff2 ?? 1.0)
      if (Number.isFinite(c) && c !== 0) coeffs.set(l.name, c)
    }
    return coeffs
  }, [links, busNames])

  const hasLinkContribs = linkCoeffs.size > 0

  // Build per-row aggregation: { snapshot, <carrier_1>, <carrier_2>, ...,
  //   storage_charge, load_total }
  // Storage discharge goes UP (positive contribution to the stack), charge
  // goes DOWN (negative — rendered below the zero axis).
  const rows = useMemo(() => {
    if (!gensTS) return []
    const rFrom = Math.max(0, range.from)
    const rTo = Math.min(gensTS.data.length - 1, range.to)
    const out: Array<Record<string, number | string>> = []
    // Multi-period: prefix each x-axis stamp with its period so 2026 / 2027 /
    // 2028 of the same May day don't look identical on the chart.
    const periodArr = gensTS.periods ?? []
    for (let row = rFrom; row <= rTo; row++) {
      const period = typeof periodArr[row] === 'number' ? periodArr[row] as number : undefined
      const r: Record<string, number | string> = {
        snapshot: gensTS.index[row],
        period_label: period != null ? String(period) : '',
      }
      // Generation by carrier. Same `genNames` filter the carrier-detection
      // step applied — keeps the stack and the legend internally consistent.
      for (const carrier of stackOrder) r[carrier] = 0
      gensTS.columns.forEach((col, ci) => {
        if (hasGenFilter && !genNames!.has(col)) return
        const v = gensTS.data[row][ci]
        if (!Number.isFinite(v)) return
        const carrier = genCarriers.get(col) ?? 'other'
        r[carrier] = (r[carrier] as number) + v
      })
      // Storage discharge (positive p) into `storage_discharge`; charge
      // (negative p) into `storage_charge` as NEGATIVE for below-axis
      // rendering (stackOffset="sign" routes negatives below zero).
      // Multi-carrier networks: filter to ELECTRICAL-bus storage only so
      // H2 storage filling from electrolyser output and heat storage
      // filling from a heat-pump don't appear below the electrical zero
      // line (they belong on the H2 / heat balance, not the electrical
      // one). When `electricalStorageNames` is omitted every column
      // counts (legacy single-carrier behaviour).
      if (storPowerTS && storPowerTS.data[row]) {
        let dch = 0, ch = 0
        for (let i = 0; i < storPowerTS.columns.length; i++) {
          if (hasElectricalStorageFilter && !electricalStorageNames!.has(storPowerTS.columns[i])) continue
          const v = storPowerTS.data[row][i]
          if (!Number.isFinite(v)) continue
          if (v > 0) dch += v
          else       ch += -v
        }
        r['storage_discharge'] = ((r['storage_discharge'] ?? 0) as number) + dch
        r['storage_charge'] = -ch  // negative for below-axis rendering
      }
      // Cross-carrier link contributions to THIS bus carrier. For each
      // link with at least one touching port, contribution = coeff × p0(t).
      // Positive → generation on this carrier (e.g., electrolyzer producing
      // H2 on H2 bus; heat-pump producing high-T heat on heat bus).
      // Negative → load on this carrier (e.g., electrolyzer consuming AC
      // from the electrical bus; heat-pump bus2 with eff2=-1 drawing low-T
      // heat). Aggregates to one positive series + one negative series so
      // the legend stays manageable on networks with many links.
      let linkInflow = 0, linkOutflow = 0
      if (hasLinkContribs && linkTS?.data[row]) {
        for (let i = 0; i < linkTS.columns.length; i++) {
          const coeff = linkCoeffs.get(linkTS.columns[i])
          if (coeff === undefined) continue
          const p0 = linkTS.data[row][i]
          if (!Number.isFinite(p0)) continue
          const contrib = coeff * p0
          if (contrib > 0) linkInflow += contrib
          else             linkOutflow += -contrib
        }
        r['link_inflows']  = linkInflow
        r['link_outflows'] = -linkOutflow   // negative for below-axis rendering
      }
      // Electrolyzer consumption — legacy single-carrier path. When the
      // parent supplies `busNames` we skip this since the general link
      // logic above already attributes the consumption to the correct
      // carrier's below-axis series (avoids double-counting). When no
      // busNames is given (single-carrier electricity-only project),
      // fall back to the dedicated electrolyzer_consume series.
      if (!hasLinkContribs && hasElectrolyzer && linkTS?.data[row]) {
        let consume = 0
        for (let i = 0; i < linkTS.columns.length; i++) {
          if (!electrolyzerNames!.has(linkTS.columns[i])) continue
          const v = linkTS.data[row][i]
          if (!Number.isFinite(v)) continue
          // Positive p0 = consuming electricity at bus0. Negative would
          // mean reverse flow (very unusual for an electrolyser) — clip to
          // zero so we don't ambiguously stack a reversed branch above the
          // axis as "generation".
          if (v > 0) consume += v
        }
        r['electrolyzer_consume'] = -consume  // negative for below-axis rendering
      }
      // Load (dashed line) — total CONSUMPTION on this bus carrier:
      // native loads + storage charge magnitude + link outflows magnitude.
      // Mass balance: generation stack height = total consumption at every
      // snapshot. Previously this only summed native loads, so on H2 the
      // dashed line was 192 MW (residential H2 demand) but the stack went
      // up to ~800 MW (storage charging + electrolyzer producing) with
      // no visible explanation. Cross-carrier networks need to see the
      // FULL consumption picture or the chart is unreadable.
      let load = 0
      if (loadTS && loadTS.data[row]) {
        for (let i = 0; i < loadTS.columns.length; i++) {
          if (hasElectricalFilter && !electricalLoadNames!.has(loadTS.columns[i])) continue
          const v = loadTS.data[row][i]
          if (Number.isFinite(v)) load += v
        }
      }
      // Add the consumption-side magnitudes so the dashed line equals the
      // generation stack height (mass balance closure).
      const storageChargeMag = -(r['storage_charge'] as number | undefined ?? 0)
      load += storageChargeMag
      load += linkOutflow  // already in magnitude form
      r['load_total'] = load
      out.push(r)
    }
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gensTS, loadTS, storPowerTS, linkTS, electrolyzerNames, hasElectrolyzer,
      electricalLoadNames, hasElectricalFilter,
      electricalStorageNames, hasElectricalStorageFilter,
      linkCoeffs, hasLinkContribs,
      genNames, hasGenFilter,
      genCarriers, stackOrder, range.from, range.to])

  // Above the early return, unconditionally: a hook below it is called only
  // on data-bearing renders, so the first render after dispatch data arrives
  // calls one MORE hook than the empty render before it — React #310, and the
  // whole Results panel dies in its error boundary. That is how this actually
  // failed in a browser: nondeterministic, only when the empty state rendered
  // first.
  const chartRef = useRef<HTMLDivElement | null>(null)

  if (!gensTS || rows.length === 0) {
    return (
      <ChartCard title="Generation Dispatch Stack">
        <div className="py-7 text-center text-[11.5px] text-muted">
          No dispatch data for the selected period.
        </div>
      </ChartCard>
    )
  }

  const hasStorage = stackOrder.includes('storage_discharge')
  const exportCSV = () => {
    // Build wide CSV: snapshot + every carrier column + storage_charge +
    // link_inflows / link_outflows (when present, otherwise electrolyzer_consume) + load_total.
    const cols = [...stackOrder, 'storage_charge']
    if (hasLinkContribs) {
      cols.push('link_inflows', 'link_outflows')
    } else if (hasElectrolyzer) {
      cols.push('electrolyzer_consume')
    }
    cols.push('load_total')
    const header = ['snapshot', ...cols]
    const csvRows = rows.map(r => [r.snapshot, ...cols.map(c => r[c] ?? '')])
    downloadCSV('dispatch_stack.csv', header, csvRows)
  }

  return (
    <ChartCard
      title={`Generation Dispatch Stack — ${carrierLabel}`}
      hint={`${carrierLabel}-bus balance · carrier-stacked hourly generation · dashed line = total consumption (load + storage charge + link outflows)${
        hasStorage ? ` · storage discharge above zero, charge below` : ''}${
        hasLinkContribs ? ` · cross-carrier link inflows in emerald, outflows in red below zero` : ''}`}
      right={<ChartActions chartRef={chartRef} onExportCSV={exportCSV} filename={`dispatch_stack_${carrierLabel}`} />}
      bodyClassName="p-3"
    >
      <div ref={chartRef}>
        <ResponsiveContainer width="100%" height={300}>
          <ComposedChart data={rows} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
            stackOffset="sign">
            <CartesianGrid {...CHART_GRID} />
            <XAxis
              dataKey="snapshot"
              tickFormatter={(s: string, idx?: number) => {
                // Multi-period: prepend the period year so identical
                // operational-year timestamps under different periods aren't
                // ambiguous. Single-period rows have period_label='' so the
                // formatter falls through to the bare ISO stamp.
                if (typeof idx === 'number' && rows[idx]) {
                  const p = rows[idx].period_label
                  if (p) return stampWithPeriod(s, Number(p))
                }
                return shortStamp(s)
              }}
              {...CHART_AXIS} />
            <YAxis {...CHART_AXIS} label={yAxisLabel('Power (MW)')} />
            <RTooltip
              {...CHART_TOOLTIP}
              labelFormatter={(s: string, payload?: Array<{ payload?: Record<string, unknown> }>) => {
                const p = payload?.[0]?.payload?.period_label
                if (typeof p === 'string' && p) return stampWithPeriod(s, Number(p))
                return shortStamp(s)
              }}
              formatter={(v: number, name: string) => {
                // storage_charge / link_outflows / electrolyzer_consume are
                // stored as negative for below-zero rendering; show the value
                // as-is so tooltip and chart geometry agree.
                const display = name === 'storage_charge'
                  ? `${v.toFixed(1)} MW (charging)`
                  : name === 'link_outflows'
                  ? `${v.toFixed(1)} MW (consumed by cross-carrier links)`
                  : name === 'link_inflows'
                  ? `${v.toFixed(1)} MW (produced by cross-carrier links)`
                  : name === 'electrolyzer_consume'
                  ? `${v.toFixed(1)} MW (electrolyser H₂ production)`
                  : `${v.toFixed(1)} MW`
                return [display, name]
              }} />
            <Legend {...CHART_LEGEND} />
            {/* Carrier stack — order matters: bottom first. */}
            {stackOrder.map(carrier => (
              <Area key={carrier} type="monotone" dataKey={carrier}
                stackId="gen"
                stroke={colourByCarrier[carrier]}
                fill={colourByCarrier[carrier]}
                fillOpacity={0.85}
                strokeWidth={0} isAnimationActive={false} />
            ))}
            {/* Cross-carrier link inflows (positive) — e.g., electrolyzer
                producing H2 on the H2 bus, heat-pump producing high-T heat
                on the heat bus. Same stackId as carrier gens so they stack
                on top of native generation. */}
            {hasLinkContribs && (
              <Area type="monotone" dataKey="link_inflows"
                stackId="gen"
                stroke={colourByCarrier['link_inflows']}
                fill={colourByCarrier['link_inflows']}
                fillOpacity={0.65}
                strokeWidth={0}
                name="link_inflows"
                isAnimationActive={false} />
            )}
            {/* Storage charge — separate stack below zero. Recharts'
                stackOffset="sign" lets positive + negative coexist when
                stackIds differ. */}
            {hasStorage && (
              <Area type="monotone" dataKey="storage_charge"
                stackId="charge"
                stroke={colourByCarrier['storage_charge']}
                fill={colourByCarrier['storage_charge']}
                fillOpacity={0.55}
                strokeWidth={0}
                isAnimationActive={false} />
            )}
            {/* Cross-carrier link outflows (negative, below axis) — e.g.,
                electrolyzer consuming AC on the electrical bus, heat-pump
                drawing low-T heat. Replaces the legacy electrolyzer_consume
                series when the parent passes busNames (general logic
                supersedes the special case). */}
            {hasLinkContribs && (
              <Area type="monotone" dataKey="link_outflows"
                stackId="charge"
                stroke={colourByCarrier['link_outflows']}
                fill={colourByCarrier['link_outflows']}
                fillOpacity={0.55}
                strokeWidth={0}
                name="link_outflows"
                isAnimationActive={false} />
            )}
            {/* Electrolyzer consumption — legacy fallback for single-carrier
                networks. When busNames is provided, link_outflows above
                supersedes this series (avoids double-counting). */}
            {hasElectrolyzer && !hasLinkContribs && (
              <Area type="monotone" dataKey="electrolyzer_consume"
                stackId="charge"
                stroke={colourByCarrier['electrolyzer_consume']}
                fill={colourByCarrier['electrolyzer_consume']}
                fillOpacity={0.55}
                strokeWidth={0}
                name="electrolyzer_consume"
                isAnimationActive={false} />
            )}
            {/* System load — drawn as a dashed line ON TOP of the stack so
                operators can read at a glance where dispatch covers demand. */}
            <Line type="monotone" dataKey="load_total"
              stroke="var(--color-text)"
              strokeDasharray="4 3"
              strokeWidth={1.4}
              dot={false}
              isAnimationActive={false}
              name={hasElectricalFilter ? `Load (${carrierLabel})` : 'Load (total)'} />
          </ComposedChart>
        </ResponsiveContainer>
        {/* Per-carrier colours live in the Recharts <Legend> above. A static
            category strip used to sit here but was dropped — its hardcoded
            Thermal/Renewables/Storage swatches didn't match the chart's
            per-carrier palette, which was confusing on multi-carrier runs. */}
        <div className="mt-1.5 flex items-center justify-end text-[10px] text-muted font-mono">
          <span>{fmtPower(rows.reduce((a, r) =>
            Math.max(a, Number(r['load_total'] ?? 0)), 0))} peak load</span>
        </div>
      </div>
    </ChartCard>
  )
}

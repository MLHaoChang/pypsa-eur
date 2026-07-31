import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { TrendingUp, Activity, Network as NetworkIcon, Filter, ChevronDown, ChevronRight, Layers, DollarSign, Cloud, Wallet, Scissors, AlertTriangle, BatteryCharging, PanelRightOpen, PanelRightClose, Crosshair } from 'lucide-react'
import { simulationApi, resultsApi } from '../api/simulation'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { ResultsFilterProvider } from './results/filterContext'
import { type TSPayload, type WeightCtx } from './results/shared'
import CompareView, { type Tab as CompareTab } from './CompareView'
import { ErrorBoundary } from '../components/ErrorBoundary'
import CapacityExpansion from './results/CapacityExpansion'
import Dispatch from './results/Dispatch'
import LoadFlow from './results/LoadFlow'
import Prices from './results/Prices'
import Emissions from './results/Emissions'
import Economics from './results/Economics'
import AggregatedOverview from './results/AggregatedOverview'
import Curtailment from './results/Curtailment'
import LostLoadTab from './results/LostLoadTab'
import StorageCycling from './results/StorageCycling'
import AssetDetail from './results/asset/AssetDetail'
import { PageHeader } from '../components/PageKit'

// ── Tabbed shell for the Results panel ────────────────────────────────────────
// Three sections per the user's request:
//   • Capacity Expansion — CAPEX + investment breakdown (LOPF-only relevance)
//   • Dispatch          — UC / dispatch with per-asset drill-down
//   • Load Flow         — line flows, bus angles & marginal prices
//
// All three share the same status query (objective / solve_time / condition)
// at the top so the user always sees solve metadata regardless of tab.

type ResultsTab =
  | 'overview' | 'capex' | 'dispatch' | 'loadflow' | 'prices' | 'emissions'
  | 'economics' | 'curtailment' | 'lostload' | 'storage' | 'asset'
const RESULTS_TAB_KEY = 'results:active-tab'

const VALID_TABS: ReadonlySet<ResultsTab> = new Set<ResultsTab>([
  'overview', 'capex', 'dispatch', 'loadflow', 'prices', 'emissions',
  'economics', 'curtailment', 'lostload', 'storage', 'asset',
])

function loadInitialTab(): ResultsTab {
  try {
    const v = localStorage.getItem(RESULTS_TAB_KEY)
    if (v && VALID_TABS.has(v as ResultsTab)) return v as ResultsTab
  } catch { /* ignore */ }
  return 'dispatch'
}

// `multiOnly` tabs appear only for multi-period results — the Overview
// consolidates per-period economics / generation mix, which is meaningless
// on a flat single-period run.
const TABS: Array<{ id: ResultsTab; label: string; Icon: typeof TrendingUp; tip: string; multiOnly?: boolean }> = [
  { id: 'overview',   label: 'Overview',           Icon: Layers,           tip: 'Per-period economics, generation mix & storage — the whole horizon at a glance', multiOnly: true },
  { id: 'capex',      label: 'Capacity Expansion', Icon: TrendingUp,       tip: 'CAPEX, OPEX, sized assets' },
  { id: 'dispatch',   label: 'Dispatch',           Icon: Activity,         tip: 'Per-snapshot generation, storage, loads' },
  { id: 'loadflow',   label: 'Load Flow',          Icon: NetworkIcon,      tip: 'Line flows, voltages, losses' },
  { id: 'prices',     label: 'Prices',             Icon: DollarSign,       tip: 'Marginal prices, duration curve, drivers' },
  { id: 'economics',  label: 'Economics',          Icon: Wallet,           tip: 'Per-asset revenue, profit, LCOE/LCOS — by group or by individual asset' },
  { id: 'emissions',  label: 'Emissions',          Icon: Cloud,            tip: 'CO₂ totals, cap shadow price, per-carrier breakdown' },
  { id: 'curtailment',label: 'Curtailment',        Icon: Scissors,         tip: 'Renewable energy rejected by the LP — total + per-carrier + time series' },
  { id: 'lostload',   label: 'Lost load',          Icon: AlertTriangle,    tip: 'VOLL slack dispatch (unserved demand) — per-carrier and per-bus breakdown' },
  { id: 'storage',    label: 'Storage cycling',    Icon: BatteryCharging,  tip: 'Equivalent full-cycle count per storage unit + carrier rollup' },
  { id: 'asset',      label: 'Asset Detail',       Icon: Crosshair,        tip: 'One asset in full — every applicable result, as numbers or charts, exportable' },
]

// Maps the Results tab the user is viewing → the equivalent CompareView tab,
// so opening the docked comparison rail starts on the same metric. Most IDs
// match; only the four below differ between the two tab vocabularies.
const RESULTS_TO_COMPARE_TAB: Record<ResultsTab, CompareTab> = {
  overview: 'overview',
  capex: 'capacity',
  dispatch: 'dispatch',
  loadflow: 'loading',
  prices: 'prices',
  economics: 'economics',
  emissions: 'emissions',
  curtailment: 'curtailment',
  lostload: 'lost_load',
  storage: 'storage_cycling',
  asset: 'overview',
}

// Min px kept for BOTH the live Results pane and the comparison rail when the
// splitter is dragged. The store's setCompareRailWidth enforces the same floor.
const RAIL_MIN_W = 360

export default function Results() {
  const [tab, setTabState] = useState<ResultsTab>(loadInitialTab)
  const setTab = (t: ResultsTab) => {
    setTabState(t)
    try { localStorage.setItem(RESULTS_TAB_KEY, t) } catch { /* ignore */ }
  }

  // ── Docked comparison rail ─────────────────────────────────────────────
  // The A-vs-B CompareView coexists on the right; the live Results tabs stay
  // fully interactive on the left. State lives in the store so the rail
  // survives result-tab switches and page reloads until explicitly closed.
  const currentProject    = useUIStore(s => s.currentProject)
  const compareRailOpen   = useUIStore(s => s.compareRailOpen)
  const compareRailWidth  = useUIStore(s => s.compareRailWidth)
  const toggleCompareRail = useUIStore(s => s.toggleCompareRail)
  const setCompareRailOpen  = useUIStore(s => s.setCompareRailOpen)
  const setCompareRailWidth = useUIStore(s => s.setCompareRailWidth)
  const resultsTabRequest = useUIStore(s => s.resultsTabRequest)
  const clearResultsTabRequest = useUIStore(s => s.clearResultsTabRequest)
  const splitWrapRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<{ startX: number; startW: number } | null>(null)

  // Chat / agent navigation — switch Results sub-tab on request.
  useEffect(() => {
    if (!resultsTabRequest) return
    const allowed = new Set(TABS.map(x => x.id))
    if (allowed.has(resultsTabRequest as ResultsTab)) {
      setTab(resultsTabRequest as ResultsTab)
    }
    clearResultsTabRequest()
  }, [resultsTabRequest, clearResultsTabRequest])

  // Vertical splitter — transpose of BottomPanel's row-resize. The handle sits
  // on the rail's LEFT edge, so dragging left grows the rail. Clamp against the
  // wrapper's measured width (NOT window width — it's offset by sidebar/panel).
  const onSplitMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    const wrapW = splitWrapRef.current?.getBoundingClientRect().width ?? window.innerWidth
    dragRef.current = { startX: e.clientX, startW: useUIStore.getState().compareRailWidth }
    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current) return
      const delta = dragRef.current.startX - ev.clientX
      const next = Math.max(RAIL_MIN_W, Math.min(wrapW - RAIL_MIN_W, dragRef.current.startW + delta))
      setCompareRailWidth(next)
    }
    const onUp = () => {
      dragRef.current = null
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [setCompareRailWidth])

  // When the rail opens, clamp a stale-wide persisted width against the current
  // wrapper so the left pane never drops below its floor (e.g. after the window
  // was resized narrower while the rail was closed).
  useEffect(() => {
    if (!compareRailOpen) return
    const wrapW = splitWrapRef.current?.getBoundingClientRect().width
    if (wrapW && compareRailWidth > wrapW - RAIL_MIN_W) {
      setCompareRailWidth(Math.max(RAIL_MIN_W, wrapW - RAIL_MIN_W))
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compareRailOpen])
  // Used by every tab — fetched once here, propagated via props so they don't
  // each issue their own poll.
  const { data: status } = useQuery({
    queryKey: nk(currentProject, 'simulationStatus'),
    queryFn: simulationApi.getStatus,
    refetchInterval: (q) => (q.state.data?.running ? 1500 : false),
  })

  // ── Horizon filter ─────────────────────────────────────────────────────
  // An expandable date-from / date-to filter at the top of the panel. When
  // set, all three tabs (Capacity Expansion KPIs derived from time-series,
  // Dispatch aggregations, Load Flow loading %) recompute against the sliced
  // range. Default: full horizon (both null = no clamp).
  const [filterOpen, setFilterOpen] = useState(false)
  const [fromIso, setFromIso] = useState<string>('')
  const [toIso, setToIso] = useState<string>('')
  // Snapshot list — needed to populate sensible default placeholders + to
  // give the user feedback on what the filter actually slices.
  const { data: snap } = useQuery({
    queryKey: nk(currentProject, 'snapshots'), queryFn: networkApi.getSnapshots, staleTime: 5_000,
  })
  const firstSnap = snap?.snapshots?.[0]
  const lastSnap = snap?.snapshots?.[snap.count - 1]
  // datetime-local input format: "YYYY-MM-DDTHH:mm".
  const firstSnap16 = firstSnap?.slice(0, 16)
  const lastSnap16  = lastSnap?.slice(0, 16)

  // Seed the From/To fields with the simulation horizon the first time
  // snapshots are available. Two reasons:
  //   1. A populated value suppresses the browser's native datetime-local
  //      placeholder — which on mixed OS locales renders as glyph soup
  //      ("yyyy/mm/日") and is not overridable with CSS or `lang`.
  //   2. The user immediately sees what the model actually spans, so it's
  //      obvious what they're narrowing when they edit the dates.
  // Setting both to the full horizon is functionally equivalent to "no
  // filter" — the slice covers everything — and isFiltered below treats it
  // that way so the warn-coloured banner only appears when the user has
  // genuinely narrowed the range.
  const seededRef = useRef(false)
  useEffect(() => {
    if (seededRef.current) return
    if (!firstSnap16 || !lastSnap16) return
    setFromIso(firstSnap16)
    setToIso(lastSnap16)
    seededRef.current = true
  }, [firstSnap16, lastSnap16])

  // datetime-local inputs return "YYYY-MM-DDTHH:mm"; PyPSA ISO timestamps are
  // "YYYY-MM-DDTHH:mm:ss". The resolveRange string compare doesn't need
  // microsecond precision — left-pad lighter values so they still compare
  // correctly against fuller backend timestamps.
  const normIso = (s: string) => s ? (s.length === 16 ? s + ':00' : s) : null

  // ── Period sub-tab (multi-period only) ────────────────────────────────
  // Sorted unique periods derived from /api/network/snapshots. Single-period
  // (flat) snapshots return no `periods` array → uniquePeriods is empty and
  // the strip is hidden. The 'all' value means "aggregated horizon" — the
  // default that lets users see weight-scaled totals across every period.
  const uniquePeriods = useMemo<Array<number | string>>(() => {
    if (!snap?.periods || snap.periods.length === 0) return []
    const seen = new Set<number | string>()
    for (const p of snap.periods) seen.add(p)
    const arr = [...seen]
    const allNumeric = arr.every(p => typeof p === 'number')
    return allNumeric ? (arr as number[]).sort((a, b) => a - b) : arr.map(String).sort()
  }, [snap])
  type SelectedPeriod = number | string | 'all'
  const [selectedPeriod, setSelectedPeriod] = useState<SelectedPeriod>('all')
  // Clamp selectedPeriod when the underlying period set changes (e.g. user
  // demotes multi→flat or rebuilds with different periods).
  useEffect(() => {
    if (uniquePeriods.length === 0) {
      if (selectedPeriod !== 'all') setSelectedPeriod('all')
    } else if (selectedPeriod !== 'all' && !uniquePeriods.includes(selectedPeriod)) {
      setSelectedPeriod('all')
    }
  }, [uniquePeriods, selectedPeriod])

  // Multi-period networks replicate ONE operational year (the timestep level)
  // across every investment period, so the raw snapshot ISO carries the base
  // year (e.g. 2026) even when the user picked period 2027. Display the
  // selected period's year in the Horizon inputs to avoid that confusion;
  // fromIso/toIso stay stored in the base year so resolveRange still matches.
  const baseYear = firstSnap16?.slice(0, 4) ?? ''
  const displayYear = typeof selectedPeriod === 'number' ? String(selectedPeriod) : baseYear
  const toDisplay = (iso: string) =>
    iso && baseYear && displayYear !== baseYear && iso.startsWith(baseYear)
      ? displayYear + iso.slice(4) : iso
  const toStore = (iso: string) =>
    iso && baseYear && displayYear !== baseYear && iso.startsWith(displayYear)
      ? baseYear + iso.slice(4) : iso

  const filterValue = {
    fromIso: normIso(fromIso),
    toIso: normIso(toIso),
    selectedPeriod: selectedPeriod === 'all' ? null : selectedPeriod,
  }
  // Active filter = the user moved at least one bound off the full horizon.
  // When fromIso === firstSnap16 AND toIso === lastSnap16 we treat it as
  // unfiltered, so the seeded defaults don't trigger the warn banner.
  const isFiltered =
    (!!fromIso && fromIso !== firstSnap16) ||
    (!!toIso   && toIso   !== lastSnap16)

  return (
    <div className="flex flex-col h-full text-sm">
      <PageHeader
        eyebrow="SIMULATION · RESULTS"
        title="Optimization results"
        subtitle="Capacity expansion, dispatch, load flow, prices, and emissions from the last solve."
        actions={
          status && (
            <span className="font-mono text-[11px] text-muted">
              {status.condition ?? '—'}
              {status.solve_time != null ? ` · ${status.solve_time.toFixed(2)} s` : ''}
            </span>
          )
        }
      />
      {/* ── Tab strip ──────────────────────────────────────────────── */}
      <div className="flex items-center shrink-0 border-b border-border bg-panel px-2 gap-0 overflow-x-auto">
        {TABS.filter(t => !t.multiOnly || uniquePeriods.length > 0).map(({ id, label, Icon, tip }) => {
          const active = tab === id
          return (
            <button
              key={id}
              onClick={() => setTab(id)}
              title={tip}
              className={`h-9 px-3 shrink-0 flex items-center gap-1.5 text-[12px] font-medium border-b-2 -mb-px transition-colors
                ${active
                  ? 'border-accent text-accent'
                  : 'border-transparent text-muted hover:text-text'}`}
            >
              <Icon size={13} />
              {label}
            </button>
          )
        })}
        <div className="flex-1" />
        {/* Docked comparison rail toggle — keeps the live results visible while
            an A-vs-B scenario comparison sits alongside on the right. */}
        <button
          onClick={toggleCompareRail}
          title="Compare with another saved project, side-by-side"
          className={`h-7 px-2.5 ml-1 mr-1 flex items-center gap-1.5 text-[11px] font-medium rounded transition-colors
            ${compareRailOpen
              ? 'bg-accent text-white'
              : 'text-muted hover:text-text border border-border'}`}
        >
          {compareRailOpen ? <PanelRightClose size={13} /> : <PanelRightOpen size={13} />}
          Compare
        </button>
      </div>

      {/* ── Horizon filter (expandable) ──────────────────────────── */}
      <div className={`shrink-0 border-b border-border bg-bg ${isFiltered ? 'bg-warn/5' : ''}`}>
        <button
          onClick={() => setFilterOpen(o => !o)}
          className="w-full flex items-center gap-2 px-3 py-1.5 text-[11px] text-muted hover:text-text"
        >
          {filterOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
          <Filter size={11} />
          Horizon filter
          {isFiltered && (
            <span className="ml-2 text-warn font-mono text-[10px]">
              {toDisplay(fromIso) || '…'} → {toDisplay(toIso) || '…'}
            </span>
          )}
          <span className="flex-1" />
          {isFiltered && (
            <span
              onClick={(e) => {
                e.stopPropagation()
                // "Clear" restores to the full simulation horizon, not to
                // empty fields — empty re-introduces the localised
                // placeholder glyph soup.
                if (firstSnap16) setFromIso(firstSnap16)
                if (lastSnap16)  setToIso(lastSnap16)
              }}
              className="text-[10px] text-muted hover:text-danger cursor-pointer px-1"
            >clear</span>
          )}
        </button>
        {filterOpen && (
          <div className="px-3 pb-3 pt-1 flex items-end gap-2 text-[11px]">
            <label className="flex flex-col gap-0.5">
              <span className="text-[10px] text-muted">From</span>
              <input
                type="datetime-local" value={toDisplay(fromIso)}
                min={firstSnap16 ? toDisplay(firstSnap16) : undefined}
                max={lastSnap16 ? toDisplay(lastSnap16) : undefined}
                onChange={e => setFromIso(toStore(e.target.value))}
                className="px-2 py-1 border border-border rounded font-mono text-[11px] bg-bg"
              />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-[10px] text-muted">To</span>
              <input
                type="datetime-local" value={toDisplay(toIso)}
                min={firstSnap16 ? toDisplay(firstSnap16) : undefined}
                max={lastSnap16 ? toDisplay(lastSnap16) : undefined}
                onChange={e => setToIso(toStore(e.target.value))}
                className="px-2 py-1 border border-border rounded font-mono text-[11px] bg-bg"
              />
            </label>
            <span className="text-[10px] text-muted">
              {firstSnap16 && lastSnap16 && (
                <>Network spans <span className="font-mono">{toDisplay(firstSnap16)}</span> → <span className="font-mono">{toDisplay(lastSnap16)}</span></>
              )}
            </span>
            <span className="flex-1" />
            {isFiltered && (
              <button
                onClick={() => {
                  if (firstSnap16) setFromIso(firstSnap16)
                  if (lastSnap16)  setToIso(lastSnap16)
                }}
                className="px-2 py-1 text-[11px] text-muted hover:text-danger"
              >Reset</button>
            )}
          </div>
        )}
      </div>

      {/* ── Period sub-tab strip (multi-period only) ─────────────── */}
      {/* Aggregated horizon = "all" — KPIs sum × weighting across every period.
          Per-period entries narrow each tab to a single period for hourly
          deep-dive. Hidden on single-period (flat) snapshots: uniquePeriods
          is empty so no strip renders. */}
      {uniquePeriods.length > 0 && (
        <div className="shrink-0 border-b border-border bg-bg flex items-center gap-1 px-2 py-1.5">
          <Layers size={11} className="text-muted" />
          <span className="text-[10px] uppercase tracking-wider text-muted mr-1">Period</span>
          <button
            onClick={() => setSelectedPeriod('all')}
            title="Show aggregated KPIs and charts spanning every investment period (weight-scaled to annual totals)."
            className={`h-6 px-2 text-[11px] font-mono rounded transition-colors
              ${selectedPeriod === 'all'
                ? 'bg-accent text-white'
                : 'text-muted hover:text-text border border-border'}`}
          >
            Aggregated
          </button>
          {uniquePeriods.map(p => (
            <button
              key={p}
              onClick={() => setSelectedPeriod(p)}
              title={`Show only investment period ${p}. Charts switch to hourly resolution within this period.`}
              className={`h-6 px-2 text-[11px] font-mono rounded transition-colors
                ${selectedPeriod === p
                  ? 'bg-accent text-white'
                  : 'text-muted hover:text-text border border-border'}`}
            >
              {p}
            </button>
          ))}
        </div>
      )}

      {/* ── Tab body ───────────────────────────────────────────────── */}
      {/* The active tab ALWAYS renders its own content. The Period strip above
          (Aggregated / 2026 / …) is a pure FILTER passed down via
          filterValue.selectedPeriod — each tab handles aggregated-vs-period
          itself. The cross-period consolidated view lives in its own
          "Overview" tab (multi-period only), not as an override that hijacks
          whichever tab the user clicked. */}
      <div ref={splitWrapRef} className="flex flex-1 min-h-0 overflow-hidden">
        <ResultsFilterProvider value={filterValue}>
          <div className="flex-1 min-w-0 overflow-hidden">
            {(() => {
              // Overview is multi-period-only; if it's somehow the active tab on
              // a single-period run, fall back to Dispatch.
              const t = (tab === 'overview' && uniquePeriods.length === 0) ? 'dispatch' : tab
              return (
                <>
                  {t === 'overview'    && <AggregatedResultsBody />}
                  {t === 'capex'       && <CapacityExpansion />}
                  {t === 'dispatch'    && <Dispatch />}
                  {t === 'loadflow'    && <LoadFlow />}
                  {t === 'prices'      && <Prices />}
                  {t === 'economics'   && <Economics />}
                  {t === 'emissions'   && <Emissions />}
                  {t === 'curtailment' && <Curtailment />}
                  {t === 'lostload'    && <LostLoadTab />}
                  {t === 'storage'     && <StorageCycling />}
                  {t === 'asset'       && <AssetDetail />}
                </>
              )
            })()}
          </div>
        </ResultsFilterProvider>

        {/* Comparison rail — embeds the A-vs-B CompareView. data-no-panel-close
            is REQUIRED: native <select> dropdowns dismiss via a document
            mousedown whose target is outside the panel, which would otherwise
            trip App's click-outside handler and close Results out from under
            the rail. */}
        {compareRailOpen && (
          <>
            <div
              className="w-1 shrink-0 cursor-col-resize bg-border/60 hover:bg-accent/50 transition-colors"
              onMouseDown={onSplitMouseDown}
              title="Drag to resize"
            />
            <div
              data-no-panel-close
              style={{ width: compareRailWidth }}
              className="shrink-0 min-w-0 overflow-hidden border-l border-border bg-bg"
            >
              {/* Own boundary so a crash inside the comparison rail shows an
                  inline fallback there instead of taking down the live Results
                  pane on the left (and vice-versa). */}
              <ErrorBoundary label="Comparison failed to render">
                <CompareView
                  embedded
                  onClose={() => setCompareRailOpen(false)}
                  initialTab={RESULTS_TO_COMPARE_TAB[tab]}
                />
              </ErrorBoundary>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ── Aggregated body ──────────────────────────────────────────────────────────
// Wraps AggregatedOverview with the WeightCtx assembled from snapshots +
// investment_periods + the first available /results/* TS payload (for the
// parallel `periods` array). Lives at the top level so the rest of the file
// stays focused on tab/strip orchestration.
function AggregatedResultsBody() {
  const currentProject = useUIStore(s => s.currentProject)
  const { data: snap } = useQuery({ queryKey: nk(currentProject, 'snapshots'),           queryFn: networkApi.getSnapshots })
  const { data: inv }  = useQuery({ queryKey: nk(currentProject, 'investmentPeriods'),   queryFn: networkApi.getInvestmentPeriods })
  // Use generators TS as the reference for the `periods` parallel array —
  // matches what Dispatch.tsx does. Falls back to /snapshots.periods if
  // generators TS hasn't loaded yet.
  const { data: gensTS } = useQuery({ queryKey: nk(currentProject, 'results', 'generators'), queryFn: () => resultsApi.getGeneratorResults() })
  const weightCtx: WeightCtx = useMemo(() => {
    const refTs = gensTS as TSPayload | null
    return {
      snapshots: snap?.snapshots ?? refTs?.index,
      snapshotPeriods: refTs?.periods ?? snap?.periods,
      snapshotWeights: (snap?.weightings as unknown) as WeightCtx['snapshotWeights'],
      periodWeights: inv?.weightings as unknown as WeightCtx['periodWeights'],
    }
  }, [snap, inv, gensTS])
  return <AggregatedOverview weightCtx={weightCtx} />
}

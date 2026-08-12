import { useState, useEffect, useLayoutEffect, useMemo, useRef, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { TrendingUp, Activity, Network as NetworkIcon, Filter, ChevronDown, ChevronRight, Layers, DollarSign, Cloud, Wallet, Scissors, AlertTriangle, BatteryCharging, PanelRightOpen, PanelRightClose, Crosshair } from 'lucide-react'
import { simulationApi, resultsApi } from '../api/simulation'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { ResultsFilterProvider, defaultWindow } from './results/filterContext'
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
import {
  desiredFromDrag,
  renderedRailWidth as constrainRailWidth,
} from './results/railWidth'

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
  // Subscribed purely so the wrapper is re-measured when the dock toggles —
  // the dock is a fixed-width sibling in App.tsx's body row, so opening it
  // shrinks this component's wrapper without any other signal reaching here.
  const assistantDockOpen = useUIStore(s => s.assistantDockOpen)
  const toggleCompareRail = useUIStore(s => s.toggleCompareRail)
  const setCompareRailOpen  = useUIStore(s => s.setCompareRailOpen)
  const setCompareRailWidth = useUIStore(s => s.setCompareRailWidth)
  const resultsTabRequest = useUIStore(s => s.resultsTabRequest)
  const clearResultsTabRequest = useUIStore(s => s.clearResultsTabRequest)
  const splitWrapRef = useRef<HTMLDivElement>(null)
  // `delta` is the pointer's travel, or null if it never moved. `startW` is
  // the on-screen width at mousedown (what the preview follows) and
  // `storedAtStart` is the user's preference as it stood then (what the write
  // may not fall below on a widening gesture) — the two differ exactly when
  // the rail is space-constrained, which is where the loss used to happen.
  const dragRef = useRef<
    { startX: number; startW: number; storedAtStart: number; delta: number | null } | null
  >(null)
  // Detaches the in-flight gesture's window listeners. Held in a ref so a
  // gesture whose mouseup was lost (released outside the window, pointer
  // stolen) can be torn down by the next mousedown and by unmount.
  const dragDetachRef = useRef<(() => void) | null>(null)
  // The live width during a drag. Local, not the store: the rail must follow
  // the pointer, but following is not choosing, and only the release chooses.
  const [dragWidth, setDragWidth] = useState<number | null>(null)

  // Chat / agent navigation — switch Results sub-tab on request.
  useEffect(() => {
    if (!resultsTabRequest) return
    const allowed = new Set(TABS.map(x => x.id))
    if (allowed.has(resultsTabRequest as ResultsTab)) {
      setTab(resultsTabRequest as ResultsTab)
    }
    clearResultsTabRequest()
  }, [resultsTabRequest, clearResultsTabRequest])

  // Vertical splitter. The handle sits on the rail's LEFT edge, so dragging
  // left grows the rail.
  //
  // The gesture records EXACTLY what the user dragged to, floored at
  // RAIL_MIN_W. It does not consult the wrapper width, the ceiling, or the
  // stored value. That is not an oversight — see results/railWidth.ts for the
  // four separate defects that all traced back to the write path knowing how
  // much room there was. The most recent: `wrapW` captured here goes stale if
  // the assistant dock opens mid-drag, which `applyUiNavigate` will do on an
  // agent turn without any idea a mouse button is held.
  //
  // Constraining is the render's job, from a live measurement, every render.
  const onSplitMouseDown = useCallback((e: React.MouseEvent) => {
    // Primary button only. Without this a middle-click drag (autoscroll on
    // Windows/Linux) or a right-drag resizes the rail and writes a preference.
    if (e.button !== 0) return
    e.preventDefault()
    // A mouseup released outside the window (or a stolen pointer) leaves the
    // previous gesture's listeners attached. Two live handlers would then both
    // fire and the older one's decision could survive the current gesture's.
    // Tear down before arming.
    dragDetachRef.current?.()

    // Measured once, to place the handle under the cursor. `startW` is the
    // CONSTRAINED (on-screen) width, so it is below the stored width whenever
    // the rail does not fit.
    //
    // Do not read this as harmless. `startW` is the numeric base of everything
    // the gesture records, so a wrong value here corrupts the persisted
    // preference just as surely as a wrong ceiling did — an earlier version of
    // this comment claimed it could "at worst offset the grab point", and that
    // claim is what let a 1px nudge silently overwrite a 700px preference with
    // 461. `desiredFromDrag` is what makes it safe, by taking `storedAtStart`
    // alongside it; `startW` alone is not a safe thing to write.
    const wrapW = splitWrapRef.current?.getBoundingClientRect().width ?? window.innerWidth
    const storedAtStart = useUIStore.getState().compareRailWidth
    const startW = constrainRailWidth(storedAtStart, wrapW)
    // `delta` stays null until the pointer actually moves, so a bare click on
    // the splitter records nothing.
    dragRef.current = { startX: e.clientX, startW, storedAtStart, delta: null }
    setDragWidth(startW)

    const onMove = (ev: MouseEvent) => {
      const d = dragRef.current
      if (!d) return
      // A mouseup released outside the window never reaches us, so the
      // gesture would otherwise never end: the preview stays frozen over a
      // store that never agreed to it, through dock toggles and resizes, and
      // the listeners stay live. The next move with no button held is the
      // only signal we get, so treat it as the release we missed and finish
      // at the last position we actually observed.
      //
      // Clearing the preview in `detach()` alone does NOT fix this — detach
      // runs on the NEXT mousedown, which immediately sets a fresh preview
      // anyway, so it is inert. Something has to end the stranded gesture.
      if (ev.buttons === 0) { finish(); return }
      // The preview follows the pointer pixel-for-pixel from the on-screen
      // origin. What gets RECORDED is decided separately at release — the two
      // origins are deliberately not the same number. Unclamped: the floor is
      // applied by `desiredFromDrag`, the ceiling by the render, live, so a
      // layout change mid-drag needs nothing kept in sync.
      d.delta = d.startX - ev.clientX
      setDragWidth(d.startW + d.delta)
    }
    function finish() {
      const d = dragRef.current
      const delta = d?.delta ?? null
      detach()
      if (d == null || delta == null) return
      setCompareRailWidth(desiredFromDrag(d.storedAtStart, d.startW, delta))
    }
    const onUp = () => { finish() }
    function detach() {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      dragRef.current = null
      // Clearing the preview belongs HERE, not only in `onUp`. A lost mouseup
      // otherwise freezes `dragWidth` at its last value, and because the
      // preview wins over the store in the render below, the rail would keep
      // showing a width the store never agreed to — surviving dock toggles and
      // resizes until some later drag happened to clear it.
      setDragWidth(null)
      dragDetachRef.current = null
    }
    dragDetachRef.current = detach
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [setCompareRailWidth])

  // A gesture in flight when Results unmounts must not leave listeners behind.
  useEffect(() => () => { dragDetachRef.current?.() }, [])

  // ── Desired width vs rendered width ────────────────────────────────────
  //
  // `compareRailWidth` in the store is the width the user ASKED for. It is
  // written by exactly one thing — an actual splitter drag — and it is
  // persisted to localStorage. Nothing else may touch it.
  //
  // What gets rendered is that width constrained to what currently fits:
  // `min(desired, wrapW - RAIL_MIN_W)`, floored at RAIL_MIN_W. The constraint
  // is recomputed from a measurement, never stored.
  //
  // This separation is the whole point. The previous version clamped by
  // WRITING the smaller value back through `setCompareRailWidth`, which
  // persists — so a user who dragged the rail to 700 on a 1440px laptop and
  // opened the assistant once had their 700 rewritten to 500 in both the store
  // and localStorage. Closing the dock did not bring it back, and neither did
  // a reload: the preference was destroyed, silently, by a layout event. It
  // "only ever shrank", which satisfied the letter of a clamp while causing
  // exactly the data loss a clamp was supposed to avoid.
  //
  // Now the rail still visibly shrinks when space runs out — same pixels on
  // screen — but the desired width survives, so it comes back when the dock
  // closes, the window grows, or the app reloads.
  const [wrapWidth, setWrapWidth] = useState<number | null>(null)

  const measureWrap = useCallback(() => {
    const w = splitWrapRef.current?.getBoundingClientRect().width
    setWrapWidth(w && w > 0 ? w : null)
  }, [])

  // useLayoutEffect, not useEffect: this runs after the DOM has been updated
  // (so the dock's 380px is already reflected in the measurement) but before
  // paint, so the constrained width is what the user actually sees rather than
  // a one-frame flash of the unconstrained one.
  useLayoutEffect(() => {
    measureWrap()
  }, [measureWrap, compareRailOpen, assistantDockOpen])

  // Coalesced to one measurement per frame. Every resize event now produces a
  // genuinely new `setWrapWidth` — unlike the old clamp, which only wrote when
  // the rail was over-wide and so was a no-op for most of a drag-resize. Left
  // unthrottled, dragging a window edge would re-render the whole Results tree
  // (charts included) once per event.
  useEffect(() => {
    let frame: number | null = null
    const onResize = () => {
      if (frame != null) return
      frame = requestAnimationFrame(() => { frame = null; measureWrap() })
    }
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      if (frame != null) cancelAnimationFrame(frame)
    }
  }, [measureWrap])

  // `dragWidth` is a DESIRED width too — the in-flight equivalent of the
  // stored one — so it goes through exactly the same constraint. That is what
  // keeps the handle tracking the cursor while the rendered rail never
  // overflows its container, including when the dock opens mid-gesture.
  //
  // Before the first measurement lands there is nothing to constrain against,
  // so render the desired width; the layout effect corrects it pre-paint.
  const desiredWidth = dragWidth ?? compareRailWidth
  const railWidth = wrapWidth == null
    ? desiredWidth
    : constrainRailWidth(desiredWidth, wrapWidth)
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
  // The bounds `defaultWindow()` seeded automatically, as opposed to bounds
  // the user has since typed — see the `isDefaultWindow` derivation below
  // (FIX 4, results-tabs-window final review). Only the flat (`kind ===
  // 'iso'`) branch narrows `fromIso`/`toIso` away from the full horizon;
  // the multi-period branch (`kind === 'period'`) narrows via
  // `selectedPeriod` instead, which `isFiltered` already ignores, so this
  // ref is only ever populated — and only ever needs to be — for the flat
  // default-window case.
  const seededDefaultRef = useRef<{ fromIso: string; toIso: string } | null>(null)
  useEffect(() => {
    if (seededRef.current) return
    if (!firstSnap16 || !lastSnap16) return
    // Seed the inputs to the full span first — this is what suppresses the
    // native datetime-local placeholder and shows the model's real extent.
    setFromIso(firstSnap16)
    setToIso(lastSnap16)
    // Then narrow to the opening window, if this network warrants one.
    const w = defaultWindow(snap?.snapshots ?? [], snap?.periods)
    if (w.kind === 'period') {
      setSelectedPeriod(w.period)
    } else if (w.kind === 'iso') {
      const from16 = w.fromIso.slice(0, 16)
      const to16 = w.toIso.slice(0, 16)
      setFromIso(from16)
      setToIso(to16)
      seededDefaultRef.current = { fromIso: from16, toIso: to16 }
    }
    seededRef.current = true
  }, [firstSnap16, lastSnap16, snap])

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

  // Raw "narrower than the full horizon" check on the iso bounds alone,
  // regardless of what caused it — a user edit, or the seeded flat-window
  // default. This is what should drive VISIBILITY (the chip / clear / reset
  // controls in the strip below): they need to show whenever the view isn't
  // the whole horizon, whether or not the user is the one who narrowed it.
  const isNarrowed =
    (!!fromIso && fromIso !== firstSnap16) ||
    (!!toIso   && toIso   !== lastSnap16)

  // True while fromIso/toIso still equal exactly what the seeding effect put
  // there for the flat (`kind === 'iso'`) opening window — i.e. the user
  // hasn't touched the bounds since. The flat default (DEFAULT_FLAT_WINDOW =
  // 720 h) narrows fromIso/toIso away from firstSnap16/lastSnap16 the same
  // way a user-typed narrowing would — so without this check, `isFiltered`
  // below couldn't tell "the app opened this way" from "the user did this",
  // and every flat network over WINDOW_THRESHOLD opened already flagged as
  // user-filtered. The moment the user edits either bound (Reset included —
  // Reset sets them back to the FULL horizon, not to this default), the
  // comparison stops matching and isFiltered resumes reflecting the real
  // narrowing.
  const isDefaultWindow =
    !!seededDefaultRef.current
    && fromIso === seededDefaultRef.current.fromIso
    && toIso === seededDefaultRef.current.toIso

  // Active filter = the user caused the narrowing — i.e. it's narrowed AND
  // it is NOT (still) the seeded default. Drives WARN STYLING ONLY (the
  // header tint + chip colour below); visibility is `isWindowed`,
  // independent of this, so the default window's controls stay visible even
  // though isFiltered — and therefore the warn styling — is false for it.
  const isFiltered = isNarrowed && !isDefaultWindow

  // True whenever the ACTIVE view is not the whole horizon, whoever caused
  // it — deliberately independent of `isFiltered` so a default window still
  // has to be visible, just not alarming. Before FIX 4 this used `isFiltered`
  // directly, which — for the flat >8760 default — was BOTH the visibility
  // flag AND (incorrectly) the warn-styling flag: the two purposes hadn't
  // been split apart, so making isFiltered accurate for styling would have
  // also hidden the strip for a genuinely windowed default view. Splitting
  // isNarrowed (visibility) from isFiltered (styling) here is what lets both
  // be correct at once.
  const isWindowed = isNarrowed || selectedPeriod !== 'all'

  const resetHorizon = () => {
    if (firstSnap16) setFromIso(firstSnap16)
    if (lastSnap16)  setToIso(lastSnap16)
    setSelectedPeriod('all')
  }

  // Tabs that want the horizon filter inline (Asset Detail) drive THIS state
  // through `controls` rather than keeping a second copy — one filter for the
  // whole panel, so the strip above and the in-tab control can never diverge.
  // Values are pre-translated into the display year; see toDisplay/toStore.
  const filterValue = {
    fromIso: normIso(fromIso),
    toIso: normIso(toIso),
    selectedPeriod: selectedPeriod === 'all' ? null : selectedPeriod,
    controls: {
      fromInput: toDisplay(fromIso),
      toInput: toDisplay(toIso),
      setFromInput: (v: string) => setFromIso(toStore(v)),
      setToInput: (v: string) => setToIso(toStore(v)),
      firstSnap: firstSnap16 ? toDisplay(firstSnap16) : '',
      lastSnap: lastSnap16 ? toDisplay(lastSnap16) : '',
      periods: uniquePeriods,
      selectedPeriod,
      setSelectedPeriod,
      isFiltered,
      reset: resetHorizon,
    },
  }

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
          {isWindowed && (
            <span className={`ml-2 font-mono text-[10px] ${isFiltered ? 'text-warn' : 'text-muted'}`}>
              {selectedPeriod !== 'all'
                ? `period ${selectedPeriod}`
                : `${toDisplay(fromIso) || '…'} → ${toDisplay(toIso) || '…'}`}
            </span>
          )}
          <span className="flex-1" />
          {isWindowed && (
            <span
              onClick={(e) => {
                e.stopPropagation()
                // "Clear" restores to the full simulation horizon, not to
                // empty fields — empty re-introduces the localised
                // placeholder glyph soup.
                resetHorizon()
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
            {isWindowed && (
              <button
                onClick={resetHorizon}
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
              data-testid="compare-rail-splitter"
              title="Drag to resize"
            />
            <div
              data-no-panel-close
              data-testid="compare-rail"
              style={{ width: railWidth }}
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

import { manualNavigation } from '../utils/navigationEpoch'
import RunBoundView from './results/RunBoundView'
import { useState, useEffect, useLayoutEffect, useMemo, useRef, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Gauge, TrendingUp, Activity, Network as NetworkIcon, Filter, ChevronDown, ChevronRight, Layers, DollarSign, Cloud, Wallet, Scissors, AlertTriangle, BatteryCharging, PanelRightOpen, PanelRightClose, Crosshair, ShieldAlert, ShieldCheck, Briefcase, Workflow, CalendarClock, Zap } from 'lucide-react'
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
import AdequacyTab from './results/AdequacyTab'
import FmeaTab from './results/FmeaTab'
import InvestmentTab from './results/InvestmentTab'
import StorageCycling from './results/StorageCycling'
import EnergyFlowsTab from './results/EnergyFlowsTab'
import LoadShapeTab from './results/LoadShapeTab'
import { GridCodeTab, ElectricalDesignTab } from './results/GridStudyTabs'
import AssetDetail from './results/asset/AssetDetail'
import SummaryTab from './results/SummaryTab'
import type { SimulationStatus } from '../api/types'
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

import { CARD_GROUPS, cardPlaceOf, type CardGroupId, type ResultsTab } from './results/groups'
import GroupHeader from './results/GroupHeader'
import { ScopeGate } from '../components/flowScope/ScopeGate'
import { useFlowScope } from '../components/flowScope/useFlowScope'
import { inScope, type ScopeCardId } from '../api/flow'
import { X } from 'lucide-react'
import { openFlowTarget, openSectionInGuided } from '../layout/flowNavigation'
import type { FlowTarget } from '../api/flow'

import BackToHubResults from '../components/BackToHubResults'

// What an empty card group points to (one line each to re-point).
// Mode-aware, through the shared navigation.
const flowTarget = (section: FlowTarget['section'], panel: string): FlowTarget =>
  ({ section, panel, component: null, entity: null, field: null })
const EMPTY_GROUP_NEXT: Record<string, { note: string; label: string; section: FlowTarget['section']; panel: string }> = {
  control: { note: 'Control results come from the island study. ', label: 'Run the island study in Grid study',
    section: 'scenarios', panel: 'campusStudy' },
  handoff: { note: 'Reports and exports are made from Reports. ', label: 'Open Reports', section: 'results', panel: 'reports' },
}
const RESULTS_TAB_KEY = 'results:active-tab'

const VALID_TABS: ReadonlySet<ResultsTab> = new Set<ResultsTab>([
  'summary', 'overview', 'capex', 'dispatch', 'loadflow', 'prices', 'emissions',
  'economics', 'curtailment', 'lostload', 'adequacy', 'storage', 'asset',
  'fmea', 'investment', 'flows', 'loadshape', 'grid_code', 'electrical_design',
])

function loadInitialTab(): ResultsTab {
  try {
    const v = localStorage.getItem(RESULTS_TAB_KEY)
    if (v && VALID_TABS.has(v as ResultsTab)) return v as ResultsTab
  } catch { /* ignore */ }
  return 'summary'
}

// M3 (plan 2026-10-14-m3-results-summary.md): Results opens on Summary after
// each new solve, then keeps the tab the user picks until the next one. A
// solve is told apart by its condition, objective and solve time; the last
// one seen is remembered beside the active tab.
const SOLVE_SEEN_KEY = 'results:solve-seen'

function solveSignature(s: SimulationStatus | undefined): string | null {
  if (!s || s.running || s.objective == null) return null
  return `${s.condition ?? ''}|${s.objective}|${s.solve_time ?? ''}`
}

// `multiOnly` tabs appear only for multi-period results — the Overview
// consolidates per-period economics / generation mix, which is meaningless
// on a flat single-period run. `expertOnly` tabs are hidden in Guided mode
// (guided-mode spec §3.5): Guided keeps adequacy and FMEA, the two the
// hub-design flow reads.
const TABS: Array<{ id: ResultsTab; label: string; Icon: typeof TrendingUp; tip: string; multiOnly?: boolean; expertOnly?: boolean }> = [
  { id: 'summary',    label: 'Summary',            Icon: Gauge,            tip: 'The solve at a glance: cost, emissions, adequacy and the key capacities, each linking to its tab' },
  { id: 'overview',   label: 'Overview',           Icon: Layers,           tip: 'Per-period economics, generation mix & storage — the whole horizon at a glance', multiOnly: true, expertOnly: true },
  { id: 'capex',      label: 'Capacity Expansion', Icon: TrendingUp,       tip: 'CAPEX, OPEX, sized assets', expertOnly: true },
  { id: 'dispatch',   label: 'Dispatch',           Icon: Activity,         tip: 'Per-snapshot generation, storage, loads', expertOnly: true },
  { id: 'loadflow',   label: 'Load Flow',          Icon: NetworkIcon,      tip: 'Line flows, voltages, losses', expertOnly: true },
  { id: 'grid_code',  label: 'Grid code & connection', Icon: ShieldCheck, tip: 'The grid study: campus PCC compliance against the grid code, and a planning → dynamics study\'s connection capacity and connection-point assessment', expertOnly: true },
  { id: 'electrical_design', label: 'Electrical design', Icon: Zap, tip: 'The campus study: transformer sizing, reactive compensation, short circuit, critical hours and the investment that meets them', expertOnly: true },
  { id: 'flows',      label: 'Energy flow',        Icon: Workflow,         tip: 'Where the site\'s energy came from, what it passed through and where it went — Source → Via → Destination in MWh, with a CSV export' },
  { id: 'loadshape',  label: 'Load shape',         Icon: CalendarClock,    tip: 'How the site\'s load and grid exchange are shaped in time — import and export heatmaps, a one-week profile, monthly and annual site energy' },
  { id: 'prices',     label: 'Prices',             Icon: DollarSign,       tip: 'Marginal prices, duration curve, drivers', expertOnly: true },
  { id: 'economics',  label: 'Asset economics',    Icon: Wallet,           tip: 'Per-asset revenue, profit, LCOE/LCOS — by group or by individual asset', expertOnly: true },
  { id: 'emissions',  label: 'Emissions',          Icon: Cloud,            tip: 'CO₂ totals, cap shadow price, per-carrier breakdown', expertOnly: true },
  { id: 'curtailment',label: 'Curtailment',        Icon: Scissors,         tip: 'Renewable energy rejected by the LP — total + per-carrier + time series', expertOnly: true },
  { id: 'lostload',   label: 'Lost load',          Icon: AlertTriangle,    tip: 'VOLL slack dispatch (unserved demand) — per-carrier and per-bus breakdown', expertOnly: true },
  { id: 'adequacy',   label: 'Adequacy',           Icon: ShieldCheck,      tip: 'Reliability targets, COPT screening, the cost-vs-availability frontier, sequential Monte Carlo and the reliability-targeted planning loop' },
  { id: 'storage',    label: 'Storage cycling',    Icon: BatteryCharging,  tip: 'Equivalent full-cycle count per storage unit + carrier rollup', expertOnly: true },
  { id: 'fmea',       label: 'FMEA',               Icon: ShieldAlert,      tip: 'Failure modes ranked by €/yr criticality — computed rows + expert class-D rows, mitigability, CSV export' },
  { id: 'investment', label: 'Investment case',    Icon: Briefcase,        tip: 'The investment case: the site bill, participants and value flows, the Library, tariffs and contracts', expertOnly: true },
  { id: 'asset',      label: 'Asset Detail',       Icon: Crosshair,        tip: 'One asset in full — every applicable result, as numbers or charts, exportable', expertOnly: true },
]

// The tabs Guided mode shows (every other row is `expertOnly`).
const GUIDED_TABS: ReadonlySet<ResultsTab> = new Set<ResultsTab>(
  TABS.filter(t => !t.expertOnly).map(t => t.id),
)

// Maps the Results tab the user is viewing → the equivalent CompareView tab,
// so opening the docked comparison rail starts on the same metric. Most IDs
// match; only the four below differ between the two tab vocabularies.
const RESULTS_TO_COMPARE_TAB: Record<ResultsTab, CompareTab> = {
  summary: 'overview',
  overview: 'overview',
  capex: 'capacity',
  dispatch: 'dispatch',
  loadflow: 'loading',
  prices: 'prices',
  economics: 'economics',
  emissions: 'emissions',
  curtailment: 'curtailment',
  lostload: 'lost_load',
  // No adequacy compare tab exists: the A-vs-B rail compares RESULT FRAMES,
  // and every adequacy surface is a study run in this session against the
  // live network rather than a frame saved with a project. Unserved energy is
  // the nearest quantity the rail can actually put side by side.
  adequacy: 'lost_load',
  storage: 'storage_cycling',
  // No energy-flow compare tab exists; dispatch is the nearest A-vs-B view.
  flows: 'dispatch',
  // Same for the load shape: dispatch is the nearest A-vs-B view.
  loadshape: 'dispatch',
  asset: 'overview',
  // No FMEA compare tab exists (yet) — alias to overview like `asset`.
  fmea: 'overview',
  // No investment compare tab exists (IC P3 WP3.5) — alias to overview.
  investment: 'overview',
  // The grid studies are not result frames the rail can compare.
  grid_code: 'overview',
  electrical_design: 'overview',
}

export default function Results() {
  const [tab, setTabState] = useState<ResultsTab>(loadInitialTab)
  // A group with no tab yet (Control, Hand-off) is picked as a group.
  const [emptyGroup, setEmptyGroup] = useState<CardGroupId | null>(null)
  const setTab = (t: ResultsTab) => {
    setEmptyGroup(null)
    setTabState(t)
    try { localStorage.setItem(RESULTS_TAB_KEY, t) } catch { /* ignore */ }
  }
  // The tab most recently asked for EXPLICITLY (asset detail, the
  // assistant's results_tab, a greeting chip) — as opposed to restored from
  // storage. Guided honours it even when the tab is hidden, showing it as a
  // temporary "advanced" tab until the user picks another (guided-mode spec
  // §10 addendum, gate P23 B3). Cleared by any click on the strip.
  const [requestedTab, setRequestedTab] = useState<ResultsTab | null>(null)
  const pickTab = (t: ResultsTab) => {
    manualNavigation()
    setRequestedTab(null)
    setTab(t)
  }
  // UX overhaul S3: the tab last used in each group, so returning to a group
  // lands where the user left it (in memory only; the stored active tab is
  // still the one thing persisted).
  const lastInGroup = useRef<Partial<Record<CardGroupId, ResultsTab>>>({})
  // Asset Detail is a drawer over the tab beneath it (R1): the last other tab.
  const lastBodyTab = useRef<ResultsTab>('summary')

  // ── Docked comparison rail ─────────────────────────────────────────────
  // The A-vs-B CompareView coexists on the right; the live Results tabs stay
  // fully interactive on the left. State lives in the store so the rail
  // survives result-tab switches and page reloads until explicitly closed.
  const selectedEntity = useUIStore(s => s.selectedComponent)
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
  const resultsWindowRequest = useUIStore(s => s.resultsWindowRequest)
  const clearResultsWindowRequest = useUIStore(s => s.clearResultsWindowRequest)
  const discussedRun = useUIStore(s => s.discussedRun)
  const clearDiscussedRun = useUIStore(s => s.clearDiscussedRun)
  const uiMode = useUIStore(s => s.uiMode)
  // Guided (spec §3.7): a STORED tab Guided does not show is displayed as
  // adequacy. Display only — `tab` and `results:active-tab` keep the user's
  // choice, so switching back to Expert restores it. An explicitly requested
  // hidden tab is shown (§10 addendum, B3) as `advancedTab`.
  const advancedTab: ResultsTab | null =
    (uiMode === 'guided' && requestedTab === tab && !GUIDED_TABS.has(tab)) ? tab : null
  const effectiveTab: ResultsTab =
    (uiMode === 'guided' && !GUIDED_TABS.has(tab) && advancedTab == null) ? 'adequacy' : tab
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

  // Apply the assistant's tab in this render, before paint. Waiting for an
  // effect lets a burst of requests overwrite `resultsTabRequest` so only
  // the last tab is ever shown.
  if (
    resultsTabRequest
    && TABS.some((item) => item.id === resultsTabRequest)
  ) {
    if (resultsTabRequest !== tab) setTab(resultsTabRequest as ResultsTab)
    if (requestedTab !== resultsTabRequest) setRequestedTab(resultsTabRequest as ResultsTab)
  }
  useEffect(() => {
    if (!resultsTabRequest) return
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

  // A solve not seen on this page yet opens Summary, unless a tab was asked
  // for explicitly (the assistant's results_tab, an asset link): that wins.
  const signature = solveSignature(status)
  useEffect(() => {
    if (!signature) return
    let seen: string | null = null
    try { seen = localStorage.getItem(SOLVE_SEEN_KEY) } catch { /* ignore */ }
    if (seen === signature) return
    try { localStorage.setItem(SOLVE_SEEN_KEY, signature) } catch { /* ignore */ }
    if (requestedTab == null) setTab('summary')
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature])

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

  // A checked chart's "Open in Results" brings its window: the tab then shows
  // the window that was checked, not the opening default. Applied once the
  // horizon is seeded (the effect above runs first), then cleared.
  useEffect(() => {
    if (!resultsWindowRequest || !firstSnap16 || !lastSnap16) return
    const from16 = resultsWindowRequest.start.replace(' ', 'T').slice(0, 16)
    const to16 = resultsWindowRequest.end.replace(' ', 'T').slice(0, 16)
    if (from16 >= firstSnap16 && to16 <= lastSnap16 && from16 <= to16) {
      setFromIso(from16)
      setToIso(to16)
    }
    clearResultsWindowRequest()
  }, [resultsWindowRequest, firstSnap16, lastSnap16, clearResultsWindowRequest])

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
    manualNavigation()
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
      setFromInput: (v: string) => { manualNavigation(); setFromIso(toStore(v)) },
      setToInput: (v: string) => { manualNavigation(); setToIso(toStore(v)) },
      firstSnap: firstSnap16 ? toDisplay(firstSnap16) : '',
      lastSnap: lastSnap16 ? toDisplay(lastSnap16) : '',
      periods: uniquePeriods,
      selectedPeriod,
      setSelectedPeriod: (p: number | string) => { manualNavigation(); setSelectedPeriod(p) },
      isFiltered,
      reset: resetHorizon,
    },
  }

  // ── UX overhaul S3: groups ───────────────────────────────────────────
  const tabVisible = (t: (typeof TABS)[number]) =>
    (!t.multiOnly || uniquePeriods.length > 0) && (!t.expertOnly || uiMode === 'expert' || t.id === advancedTab)
  const visibleTabIds = new Set<ResultsTab>(TABS.filter(tabVisible).map(t => t.id))
  // The drawer sits over the last non-asset tab; a first load on 'asset' falls back to Summary.
  const bodyTab: ResultsTab = effectiveTab === 'asset' ? lastBodyTab.current : effectiveTab
  if (effectiveTab !== 'asset') lastBodyTab.current = effectiveTab
  const place = cardPlaceOf(bodyTab)
  const activeGroup: CardGroupId | null = emptyGroup ?? place?.group ?? null
  const shownGroup = CARD_GROUPS.find(g => g.id === activeGroup)
  // Card tabs with at least one visible view, per group.
  const visibleCardTabs = (g: (typeof CARD_GROUPS)[number]) =>
    g.tabs.filter(ct => ct.views.some(v => visibleTabIds.has(v)))
  const groupTabs = shownGroup && emptyGroup == null ? visibleCardTabs(shownGroup) : []
  const activeCardTab = place?.cardTab ?? null
  const subViews = (() => {
    const ct = shownGroup?.tabs.find(t => t.id === activeCardTab)
    return ct ? ct.views.filter(v => visibleTabIds.has(v)).map(id => TABS.find(t => t.id === id)!) : []
  })()
  if (activeGroup && emptyGroup == null) lastInGroup.current[activeGroup] = bodyTab
  const { scope: flowScope, isSuccess: scopeRead } = useFlowScope()
  // While the scope is unread everything counts as in scope (no flicker); the
  // Summary landing and a group's own tabs never depend on it.
  const groupOutside = (g: CardGroupId) =>
    g !== 'summary' && scopeRead && !inScope(flowScope, g as ScopeCardId)
  const pickGroup = (g: CardGroupId) => {
    const group = CARD_GROUPS.find(x => x.id === g)!
    const cts = visibleCardTabs(group)
    if (cts.length === 0) { manualNavigation(); setRequestedTab(null); setEmptyGroup(g); return }
    const views = cts.flatMap(ct => ct.views.filter(v => visibleTabIds.has(v)))
    const remembered = lastInGroup.current[g]
    pickTab(remembered && views.includes(remembered) ? remembered : views[0])
  }
  const pickCardTab = (id: string) => {
    const ct = shownGroup!.tabs.find(t => t.id === id)!
    const views = ct.views.filter(v => visibleTabIds.has(v))
    pickTab(views.includes(lastInGroup.current[shownGroup!.id] as ResultsTab)
      ? lastInGroup.current[shownGroup!.id]! : views[0])
  }
  const renderTab = ({ id, label, Icon, tip }: (typeof TABS)[number]) => {
    const active = effectiveTab === id
    const advanced = id === advancedTab
    return (
      <button
        key={id}
        onClick={() => pickTab(id)}
        title={advanced ? `${tip} — an advanced tab, shown because it was asked for; it leaves the strip when you pick another tab.` : tip}
        data-testid={`results-tab-${id}`}
        data-advanced={advanced ? 'true' : undefined}
        className={`h-8 px-3 shrink-0 flex items-center gap-1.5 text-[12px] font-medium border-b-2 -mb-px transition-colors
          ${active
            ? 'border-accent text-accent'
            : 'border-transparent text-muted hover:text-text'}`}
      >
        <Icon size={13} />
        {label}
        {advanced && (
          <span className="ml-1 px-1 py-px rounded text-[9px] font-mono font-bold uppercase tracking-[0.08em] border border-accent/40">
            Advanced
          </span>
        )}
      </button>
    )
  }

  // Guided names the group on screen, not always the reliability entry it was
  // opened from (UX template audit M1).
  // Summary and Reliability keep the hub design's reliability header (P29).
  const hubHeader = !shownGroup || shownGroup.id === 'reliability' || shownGroup.id === 'summary'
  const outside = activeGroup != null && groupOutside(activeGroup)
  const hasEarlierResults = status != null && status.objective != null
  const guidedTitle = hubHeader ? 'Reliability results' : `${shownGroup.label} results`
  const guidedSubtitle = hubHeader
    ? 'Adequacy and failure-mode (FMEA) risk results for the hub design.'
    : `${shownGroup.question}.`

  const renderBody = (t: ResultsTab) => (
    <>
      {t === 'summary'     && <SummaryTab onOpenTab={pickTab} onOpenGroup={pickGroup} />}
      {t === 'overview'    && <AggregatedResultsBody />}
      {t === 'capex'       && <CapacityExpansion />}
      {t === 'dispatch'    && <Dispatch />}
      {t === 'loadflow'    && <LoadFlow />}
      {t === 'prices'      && <Prices />}
      {t === 'economics'   && <Economics />}
      {t === 'emissions'   && <Emissions />}
      {t === 'curtailment' && <Curtailment />}
      {t === 'lostload'    && <LostLoadTab />}
      {t === 'adequacy'    && <AdequacyTab />}
      {t === 'fmea'        && <FmeaTab />}
      {t === 'storage'     && <StorageCycling />}
      {t === 'flows'       && <EnergyFlowsTab />}
      {t === 'loadshape'   && <LoadShapeTab />}
      {t === 'investment'  && <InvestmentTab />}
      {t === 'grid_code'   && <GridCodeTab />}
      {t === 'electrical_design' && <ElectricalDesignTab />}
    </>
  )

  return (
    <div
      className="flex flex-col h-full text-sm"
      data-testid="results-active-tab"
      data-active-results-tab={effectiveTab}
      data-assurance-tab={effectiveTab}
    >
      <PageHeader
        eyebrow={discussedRun ? 'RESULTS · RETAINED EVIDENCE' : uiMode === 'guided' ? 'HUB DESIGN · RESULTS' : 'RESULTS · OPTIMIZATION'}
        title={discussedRun ? 'Retained run results' : uiMode === 'guided' ? guidedTitle : 'Optimization results'}
        subtitle={discussedRun ? `Recorded evidence for run ${discussedRun.runId}. Live inputs remain unchanged.` : uiMode === 'guided'
          ? guidedSubtitle
          : 'Capacity expansion, dispatch, load flow, prices, and emissions from the last solve.'}
        actions={
          <>
            {/* Guided reaches these tabs from the hub design's "Open the
                detail"; this is the way back to the step it came from (UX
                template audit M10, owner decision D2). */}
            {uiMode === 'guided' && <BackToHubResults />}
            {status && !discussedRun && (
              <span className="font-mono text-[11px] text-muted">
                {status.condition ?? '—'}
                {status.solve_time != null ? ` · ${status.solve_time.toFixed(2)} s` : ''}
              </span>
            )}
          </>
        }
      />
      {/* What the views below read from. Expert only: in Guided the hub
          design has already named the project and the window, and
          "selected entity: none" read like an error (UX template audit M2). */}
      {uiMode !== 'guided' && (
        <div className="mx-4 text-xs text-muted" data-testid="results-source-context">
          Current project: {currentProject ?? 'unsaved'}
          {selectedEntity && <> · selected {selectedEntity.type}: {selectedEntity.name}</>}
          {!discussedRun && <> · window {toDisplay(fromIso) || 'start'} → {toDisplay(toIso) || 'end'} · period {selectedPeriod}</>}
        </div>
      )}
      {discussedRun && (
        <div
          className="mx-4 mt-2 mb-1 flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border/60 pb-2 text-[11px]"
          data-testid="discussed-run-chrome"
          data-run-id={discussedRun.runId}
        >
          <span className="font-mono text-foreground">
            Discussed run {discussedRun.runId}
            {discussedRun.status ? ` · ${discussedRun.status}` : ''}
            {discussedRun.mode ? ` · ${discussedRun.mode}` : ''}
          </span>
          {discussedRun.section && (
            <span className="text-muted">section {discussedRun.section}</span>
          )}
          {discussedRun.baselineRunId && (
            <span className="text-muted">baseline {discussedRun.baselineRunId}</span>
          )}
          <span className="text-muted">
            Results below read only retained evidence for this run. Live inputs remain unchanged.
          </span>
          <button
            type="button"
            className="ml-auto text-muted underline-offset-2 hover:underline"
            onClick={() => { manualNavigation(); clearDiscussedRun() }}
          >
            Clear
          </button>
        </div>
      )}
      {/* ── Group strip + tab strip (UX overhaul S3, owner decision D1a) ── */}
      {/* Results are grouped by the question they answer. The group row picks
          a group; the row below holds that group's tabs, which keep their ids
          (and test ids), so stored tabs, deep links and the assistant's
          results_tab are unchanged. Asset detail is an inspector, not a
          category: it sits on the right of the group row. */}
      <div className="flex items-center shrink-0 border-b border-border bg-panel px-2 gap-0 overflow-x-auto">
        {CARD_GROUPS.filter(g => g.id === 'summary' || uiMode === 'expert' || visibleCardTabs(g).length > 0).map(g => {
          const active = activeGroup === g.id
          const off = groupOutside(g.id)
          return (
            <button
              key={g.id}
              onClick={() => pickGroup(g.id)}
              title={off ? `${g.question}. Outside scope: add ${g.label} to the scope to run it.` : g.question}
              data-testid={`results-group-${g.id}`}
              data-outside-scope={off ? 'true' : undefined}
              aria-pressed={active}
              className={`h-9 px-3 shrink-0 flex items-center text-[12.5px] font-semibold border-b-2 -mb-px transition-colors
                ${active ? 'border-accent text-text' : 'border-transparent text-muted hover:text-text'}
                ${off ? 'opacity-50' : ''}`}
            >
              {g.label}
            </button>
          )
        })}
        <div className="flex-1" />
        {visibleTabIds.has('asset') && renderTab(TABS.find(t => t.id === 'asset')!)}
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
      {shownGroup && (
        <GroupHeader group={shownGroup} status={status} outsideScope={outside} />
      )}
      {shownGroup && groupTabs.length > 0 && !(shownGroup.id === 'summary') && (
        <div className="flex items-center shrink-0 border-b border-border bg-bg px-2 gap-0 overflow-x-auto"
          data-testid="results-subtabs">
          {groupTabs.map(ct => {
            const tabActive = activeCardTab === ct.id
            return (
              <button key={ct.id} onClick={() => pickCardTab(ct.id)} data-testid={`results-cardtab-${ct.id}`}
                data-views={ct.views.filter(v => visibleTabIds.has(v)).join(',')}
                className={`h-8 px-3 shrink-0 flex items-center text-[12px] font-medium border-b-2 -mb-px transition-colors
                  ${tabActive ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-text'}`}>
                {ct.label}
              </button>
            )
          })}
        </div>
      )}
      {(subViews.length > 1 || subViews.some(t => t.id === advancedTab)) && (
        <div className="flex items-center shrink-0 border-b border-border bg-bg px-2 gap-0 overflow-x-auto"
          data-testid="results-subviews">
          {subViews.map(t => renderTab(t))}
        </div>
      )}

      {/* ── Horizon filter (expandable) ──────────────────────────── */}
      {!discussedRun && <div className={`shrink-0 border-b border-border bg-bg ${isFiltered ? 'bg-warn/5' : ''}`}>
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
                onChange={e => { manualNavigation(); setFromIso(toStore(e.target.value)) }}
                className="px-2 py-1 border border-border rounded font-mono text-[11px] bg-bg"
              />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-[10px] text-muted">To</span>
              <input
                type="datetime-local" value={toDisplay(toIso)}
                min={firstSnap16 ? toDisplay(firstSnap16) : undefined}
                max={lastSnap16 ? toDisplay(lastSnap16) : undefined}
                onChange={e => { manualNavigation(); setToIso(toStore(e.target.value)) }}
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
      </div>}

      {/* ── Period sub-tab strip (multi-period only) ─────────────── */}
      {/* Aggregated horizon = "all" — KPIs sum × weighting across every period.
          Per-period entries narrow each tab to a single period for hourly
          deep-dive. Hidden on single-period (flat) snapshots: uniquePeriods
          is empty so no strip renders. */}
      {!discussedRun && uniquePeriods.length > 0 && (
        <div className="shrink-0 border-b border-border bg-bg flex items-center gap-1 px-2 py-1.5">
          <Layers size={11} className="text-muted" />
          <span className="text-[10px] uppercase tracking-wider text-muted mr-1">Period</span>
          <button
            onClick={() => { manualNavigation(); setSelectedPeriod('all') }}
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
              onClick={() => { manualNavigation(); setSelectedPeriod(p) }}
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
              if (discussedRun) return <RunBoundView run={discussedRun} tab={effectiveTab} />
              const t = (bodyTab === 'overview' && uniquePeriods.length === 0) ? 'dispatch' : bodyTab
              // A group with no tab yet, or a card outside the scope (RC3 A):
              // the scope gate says so, the earlier results stay below, dimmed
              // and read-only.
              if (emptyGroup != null || outside) {
                const g = emptyGroup ?? activeGroup!
                const label = CARD_GROUPS.find(x => x.id === g)!.label
                return (
                  <div className="h-full overflow-auto" data-testid="results-gbody">
                    {outside ? (
                      <div className="p-4"><ScopeGate card={g as ScopeCardId} testId="results-outside-scope">{null}</ScopeGate></div>
                    ) : (
                      <p data-testid="results-gempty" className="p-4 text-[12px] text-muted">
                        {EMPTY_GROUP_NEXT[g]?.note}
                        <button type="button" data-testid="results-gempty-open"
                          onClick={() => {
                            const n = EMPTY_GROUP_NEXT[g]
                            if (useUIStore.getState().uiMode === 'guided') openSectionInGuided(n.section)
                            else openFlowTarget(flowTarget(n.section, n.panel))
                          }}
                          className="text-accent hover:underline">
                          {EMPTY_GROUP_NEXT[g]?.label} ›
                        </button>
                      </p>
                    )}
                    {outside && emptyGroup == null && hasEarlierResults && (
                      <div data-testid="results-earlier" className="border-t border-border">
                        <p className="px-4 py-2 text-[11px] text-muted">
                          From the latest run. Read-only while {label} is outside the scope.
                        </p>
                        <div inert className="opacity-50 pointer-events-none select-none">
                          {renderBody(t)}
                        </div>
                      </div>
                    )}
                  </div>
                )
              }
              return renderBody(t)
            })()}
          </div>
        </ResultsFilterProvider>
        {effectiveTab === 'asset' && (
          <aside data-testid="asset-drawer" aria-label="Asset detail"
            className="w-[min(900px,72%)] shrink-0 min-w-0 overflow-auto border-l border-border bg-bg">
            <div className="flex items-center justify-between px-3 py-1.5 border-b border-border">
              <span className="text-[12px] font-semibold">Asset Detail</span>
              <button type="button" data-testid="asset-drawer-close" aria-label="Close asset detail"
                onClick={() => pickTab(lastBodyTab.current)} className="text-muted hover:text-text"><X size={14} /></button>
            </div>
            <ResultsFilterProvider value={filterValue}><AssetDetail /></ResultsFilterProvider>
          </aside>
        )}
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
                  // Seeded from the tab on screen: in Guided a stored hidden
                  // tab is displayed as adequacy, and the rail must match it.
                  initialTab={RESULTS_TO_COMPARE_TAB[effectiveTab]}
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

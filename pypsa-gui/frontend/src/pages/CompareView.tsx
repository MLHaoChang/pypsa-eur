import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { ArrowRight, Loader, AlertTriangle, Layers } from 'lucide-react'
import { projectsApi } from '../api/projects'
import { useUIStore } from '../store/uiStore'
import { ErrorBoundary } from '../components/ErrorBoundary'
import type {
  CompareState, ProjectInfo, ResultsSummary, CarrierPeriodValue,
  LineLoadingEntry, CarrierEconomics, StorageUnitCycles, LostLoadBus,
  AssetLCOHEntry, CarrierPriceStats, LostLoadByCarrier,
} from '../api/types'
import { canonicaliseCarrier, carrierDisplayName, type ComponentClass } from './results/carrierAliases'
import { useCarrierFilter, CarrierFilter, bindCarrierFilter } from './results/CarrierFilter'
import { COST_UNAVAILABLE } from './results/shared'

// ── Compare view ─────────────────────────────────────────────────────────────
// Tabbed A/B comparison across two saved projects. Each tab loads its own
// payload via React Query — the pickers + tab bar stay sticky, content lazy-
// loads per tab. All data is read from each project's last-saved netcdf via
// a transient pypsa.Network on the backend; the active singleton is never
// touched, so the user can compare scenarios without disturbing in-memory
// state.

export type Tab =
  | 'overview'        // existing high-level network/solver/capacity tables
  | 'capacity'        // Phase 1
  | 'dispatch'        // Phase 1
  | 'loading'         // Phase 2 — line + transformer loading
  | 'prices'          // Phase 2 — price duration curve
  | 'emissions'       // Phase 3 — CO2 totals + intensity
  | 'economics'       // Phase 3 — per-carrier revenue / OPEX / CAPEX / LCOE
  | 'curtailment'     // Phase 4 — renewable curtailment in GWh + rate
  | 'lost_load'       // Phase 4 — VOLL slack dispatch (unserved demand)
  | 'storage_cycling' // Phase 4 — per-unit storage cycle count

const TABS: { id: Tab; label: string; description?: string }[] = [
  { id: 'overview',        label: 'Overview',           description: 'Network counts, solver state, capacity at a glance' },
  { id: 'capacity',        label: 'Capacity expansion', description: 'p_nom_opt + new CAPEX per carrier, per investment period' },
  { id: 'dispatch',        label: 'Dispatch',           description: 'Energy mix per carrier + OPEX, per period' },
  { id: 'loading',         label: 'Line loading',       description: 'Per-branch peak / mean loading and binding hours' },
  { id: 'prices',          label: 'Prices',             description: 'Marginal-price duration curve and per-period statistics' },
  { id: 'emissions',       label: 'Emissions',          description: 'Total CO₂ (kt), per-carrier breakdown, and system intensity (kg/MWh)' },
  { id: 'economics',       label: 'Economics',          description: 'Per-carrier revenue, OPEX, annuitised CAPEX, and levelised cost (LCOE)' },
  { id: 'curtailment',     label: 'Curtailment',        description: 'Renewable energy curtailed (GWh) and curtailment rate (%) per carrier' },
  { id: 'lost_load',       label: 'Lost load',          description: 'Unserved demand (VOLL slack) — energy not delivered and the associated VOLL cost' },
  { id: 'storage_cycling', label: 'Storage cycling',    description: 'Per-storage-unit equivalent full cycles. One cycle = a full charge + discharge of total energy capacity.' },
]

// Persisted A/B/tab selection. Shared by the standalone full-page route AND
// the embedded rail (Results), so a comparison the user set in one surfaces in
// the other and survives the rail being closed + reopened. Mirroring is
// intentional — it keeps a single "current comparison" mental model.
const CMP_A_KEY = 'compare:a'
const CMP_B_KEY = 'compare:b'
const CMP_TAB_KEY = 'compare:tab'
const VALID_COMPARE_TABS: ReadonlySet<Tab> = new Set<Tab>(TABS.map(t => t.id))
function storedCmp(key: string): string | null {
  try { return localStorage.getItem(key) } catch { return null }
}
function persistCmp(key: string, v: string) {
  try { localStorage.setItem(key, v) } catch { /* noop */ }
}

interface CompareViewProps {
  // When true, render the compact embedded chrome (close button, tighter
  // padding) for the docked rail inside Results. Standalone route omits it.
  embedded?: boolean
  onClose?: () => void
  // First-use hint for the active tab (e.g. seeded from the Results tab the
  // user is on). Only applied when there's no persisted tab — never overrides
  // the user's last rail tab, which would be surprising since the rail
  // remounts on every open.
  initialTab?: Tab
}

export default function CompareView({ embedded = false, onClose, initialTab }: CompareViewProps = {}) {
  const currentProject = useUIStore(s => s.currentProject)

  const { data: projects = [] } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
    staleTime: 5_000,
  })

  // Default side A to the active project. Side B left empty until the user
  // picks something distinct. Selections persist (and the delete-reset effect
  // below self-heals a stale persisted name once the project list loads).
  const [a, setAState] = useState<string>(() => storedCmp(CMP_A_KEY) ?? currentProject ?? '')
  const [b, setBState] = useState<string>(() => storedCmp(CMP_B_KEY) ?? '')
  const [tab, setTabState] = useState<Tab>(() => {
    // Embedded rail: it remounts on every open, so honour `initialTab` (the
    // live Results tab the user is on) FIRST — the rail opens on the matching
    // metric each time. While it stays open the user can switch it freely
    // (persisted below), but reopening re-seeds to the current Results tab.
    // The standalone route passes no `initialTab` and falls through to its own
    // persisted tab, preserving cross-session memory there.
    if (initialTab && VALID_COMPARE_TABS.has(initialTab)) return initialTab
    const p = storedCmp(CMP_TAB_KEY)
    if (p && VALID_COMPARE_TABS.has(p as Tab)) return p as Tab
    return 'overview'
  })
  const setA = (v: string) => { setAState(v); persistCmp(CMP_A_KEY, v) }
  const setB = (v: string) => { setBState(v); persistCmp(CMP_B_KEY, v) }
  const setTab = (t: Tab) => { setTabState(t); persistCmp(CMP_TAB_KEY, t) }

  const compareNavRequest = useUIStore(s => s.compareNavRequest)
  const clearCompareNavRequest = useUIStore(s => s.clearCompareNavRequest)
  useEffect(() => {
    if (!compareNavRequest) return
    if (compareNavRequest.a) setA(compareNavRequest.a)
    if (compareNavRequest.b) setB(compareNavRequest.b)
    if (compareNavRequest.tab && VALID_COMPARE_TABS.has(compareNavRequest.tab as Tab)) {
      setTab(compareNavRequest.tab as Tab)
    }
    clearCompareNavRequest()
  }, [compareNavRequest, clearCompareNavRequest])

  // Reset selection if a project is deleted under us.
  useEffect(() => {
    const list = projects as ProjectInfo[]
    if (list.length === 0) return
    const names = new Set(list.map(p => p.name))
    if (a && !names.has(a)) setA('')
    if (b && !names.has(b)) setB('')
  }, [projects, a, b])

  const bothPicked = !!a && !!b

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className={`${embedded ? 'p-3 pb-2 space-y-2' : 'p-4 pb-2 space-y-3'} shrink-0 border-b border-border/40 bg-bg`}>
        {embedded && (
          <div className="flex items-center gap-2">
            <Layers size={13} className="text-accent shrink-0" />
            <span className="text-[12px] font-semibold text-text">Scenario comparison</span>
            <span className="flex-1" />
            <button
              onClick={onClose}
              title="Close comparison (Esc)"
              className="flex items-center gap-1 text-[11px] text-muted hover:text-text px-1.5 py-0.5 rounded hover:bg-panel transition-colors"
            >
              Close <span className="text-[14px] leading-none">×</span>
            </button>
          </div>
        )}
        <p className="text-[11px] text-muted">
          {embedded
            ? "A vs B from each project's last saved state — not unsaved in-memory edits."
            : "Side-by-side comparison of two projects. Figures are read from each project's last saved state — they don't reflect unsaved in-memory edits."}
        </p>
        <div className="flex items-center gap-3">
          <ScenarioPicker label="Scenario A" value={a} exclude={b} projects={projects as ProjectInfo[]} onChange={setA} />
          <ArrowRight size={16} className="text-muted shrink-0 mt-4" />
          <ScenarioPicker label="Scenario B" value={b} exclude={a} projects={projects as ProjectInfo[]} onChange={setB} />
        </div>
        {bothPicked && <TabBar tab={tab} onChange={setTab} />}
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {!bothPicked && (
          <div className="text-xs text-muted py-8 text-center border border-dashed border-border rounded">
            Pick two projects to compare.
          </div>
        )}
        {/* Boundary around ONLY the tab body, keyed on the A/B/tab selection,
            so a crash in one comparison shows an inline fallback here while the
            pickers + tab bar above stay interactive — changing A/B/tab remounts
            this (clearing the error) so a bad comparison is recoverable without
            a page reload. */}
        {bothPicked && (
          <ErrorBoundary key={`${a}|${b}|${tab}`} label="This comparison failed to render">
            {tab === 'overview'        && <OverviewTab       a={a} b={b} />}
            {tab === 'capacity'        && <CapacityTab       a={a} b={b} />}
            {tab === 'dispatch'        && <DispatchTab       a={a} b={b} />}
            {tab === 'loading'         && <LoadingTab        a={a} b={b} />}
            {tab === 'prices'          && <PricesTab         a={a} b={b} />}
            {tab === 'emissions'       && <EmissionsTab      a={a} b={b} />}
            {tab === 'economics'       && <EconomicsTab      a={a} b={b} />}
            {tab === 'curtailment'     && <CurtailmentTab    a={a} b={b} />}
            {tab === 'lost_load'       && <LostLoadTab       a={a} b={b} />}
            {tab === 'storage_cycling' && <StorageCyclingTab a={a} b={b} />}
          </ErrorBoundary>
        )}
      </div>
    </div>
  )
}

// ── Picker + Tab bar ─────────────────────────────────────────────────────────

interface PickerProps {
  label: string
  value: string
  exclude: string
  projects: ProjectInfo[]
  onChange: (name: string) => void
}

function ScenarioPicker({ label, value, exclude, projects, onChange }: PickerProps) {
  return (
    <label className="flex-1 min-w-0">
      <span className="text-[10px] text-muted block mb-0.5">{label}</span>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        className="w-full text-xs bg-bg border border-border rounded px-2 py-1.5 focus:outline-none focus:border-accent"
      >
        <option value="">— select —</option>
        {projects.map(p => (
          <option key={p.name} value={p.name} disabled={p.name === exclude}>
            {p.name}{p.parent_project ? `  (scenario of ${p.parent_project})` : ''}
          </option>
        ))}
      </select>
    </label>
  )
}

function TabBar({ tab, onChange }: { tab: Tab; onChange: (t: Tab) => void }) {
  return (
    <div className="flex gap-1 border-b border-border -mb-px">
      {TABS.map(t => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          title={t.description}
          className={
            'text-[11px] px-3 py-1.5 border-b-2 transition-colors ' +
            (tab === t.id
              ? 'border-accent text-text font-medium'
              : 'border-transparent text-muted hover:text-text')
          }
        >
          {t.label}
        </button>
      ))}
    </div>
  )
}

// ── Period selector (shared by Phase-1 tabs) ─────────────────────────────────
// "all" = aggregated total. For flat networks, the selector is hidden
// (only the aggregated value is meaningful).

type PeriodChoice = 'all' | string  // string = year stringified, e.g. "2026"

function PeriodSelector({
  periods, value, onChange,
}: {
  periods: number[]
  value: PeriodChoice
  onChange: (p: PeriodChoice) => void
}) {
  if (periods.length === 0) return null  // flat — no selector
  return (
    <div className="flex items-center gap-2 mb-3">
      <span className="text-[10px] text-muted">Period</span>
      <div className="flex gap-0.5">
        <PeriodBtn label="All" active={value === 'all'} onClick={() => onChange('all')} />
        {periods.map(p => (
          <PeriodBtn key={p} label={String(p)} active={value === String(p)} onClick={() => onChange(String(p))} />
        ))}
      </div>
    </div>
  )
}

function PeriodBtn({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={
        'text-[10px] px-2 py-0.5 rounded ' +
        (active ? 'bg-accent text-white' : 'bg-bg border border-border text-text hover:bg-border/30')
      }
    >
      {label}
    </button>
  )
}

// ── Overview tab (existing high-level tables) ────────────────────────────────

// Exported solely so CompareView.availability.test.tsx can render it in
// isolation (Task 6 follow-up to Task 5) — see LoadingTab's matching note.
export function OverviewTab({ a, b }: { a: string; b: string }) {
  const qA = useQuery({ queryKey: ['compare-state', a], queryFn: () => projectsApi.compareState(a), staleTime: 30_000 })
  const qB = useQuery({ queryKey: ['compare-state', b], queryFn: () => projectsApi.compareState(b), staleTime: 30_000 })
  // Pull results-summary for the storage breakdown — compare-state only
  // tracks storage_units' p_nom (installed) and stores' e_nom (MWh). It has
  // no field for storage_units' OPTIMISED power (p_nom_opt) or their energy
  // capacity (p_nom_opt × max_hours). The new endpoint covers both, so the
  // Overview's storage table now reads from it and shows the actual built
  // numbers (e.g. 617.4 MW battery, 2469.6 MWh battery) instead of the
  // pre-build parent's 50 MW with a blank energy column.
  const qSumA = useQuery({ queryKey: ['results-summary', a], queryFn: () => projectsApi.resultsSummary(a), staleTime: 30_000 })
  const qSumB = useQuery({ queryKey: ['results-summary', b], queryFn: () => projectsApi.resultsSummary(b), staleTime: 30_000 })

  if (qA.isLoading || qB.isLoading || qSumA.isLoading || qSumB.isLoading) return <LoadingSpinner />
  if (qA.isError || qB.isError) return <ErrorBanner which={qA.isError ? a : b} />
  if (qSumA.isError || qSumB.isError) return <ErrorBanner which={qSumA.isError ? a : b} />
  const sa = qA.data!
  const sb = qB.data!
  const summaryA = qSumA.data!
  const summaryB = qSumB.data!
  // This tab mixes TWO different sources per row: CountsTable/SolverTable
  // and OverviewCapacityTable's main total columns read compare-state
  // (structural network properties — bus/line counts, installed/optimised
  // MW — always meaningful even pre-solve, never "unresolved" in the
  // ADR-0001 sense, so they carry no `available` flag at all). The "built"
  // columns of OverviewCapacityTable and ALL of OverviewStorageTable /
  // OverviewLinksTable instead read the results-summary `capacity` block,
  // which DOES carry `available` — false whenever that side has no fresh
  // solve. Per-cell marking (rather than one tab-wide banner) is the right
  // call here specifically because of that split: a banner would either
  // hide CountsTable/SolverTable's always-valid figures unnecessarily, or
  // fail to flag OverviewStorageTable/OverviewLinksTable's figures when
  // only the results-summary side is unresolved. `?? false` so an absent
  // flag reads as unavailable, never as available.
  const capAvailableA = summaryA.capacity?.available ?? false
  const capAvailableB = summaryB.capacity?.available ?? false
  return (
    <div className="space-y-4">
      <CountsTable a={sa} b={sb} />
      <SolverTable a={sa} b={sb} />
      <OverviewCapacityTable
        a={sa} b={sb}
        newCapA={summaryA.capacity?.new_capacity_mw_by_carrier}
        newCapB={summaryB.capacity?.new_capacity_mw_by_carrier}
        availableA={capAvailableA}
        availableB={capAvailableB}
      />
      <OverviewStorageTable
        aName={sa.name} bName={sb.name}
        summaryA={summaryA} summaryB={summaryB}
        availableA={capAvailableA}
        availableB={capAvailableB}
      />
      <OverviewLinksTable
        aName={sa.name} bName={sb.name}
        summaryA={summaryA} summaryB={summaryB}
        availableA={capAvailableA}
        availableB={capAvailableB}
      />
    </div>
  )
}

// ── Capacity tab (Phase 1) ───────────────────────────────────────────────────

function CapacityTab({ a, b }: { a: string; b: string }) {
  const qA = useQuery({ queryKey: ['results-summary', a], queryFn: () => projectsApi.resultsSummary(a), staleTime: 30_000 })
  const qB = useQuery({ queryKey: ['results-summary', b], queryFn: () => projectsApi.resultsSummary(b), staleTime: 30_000 })
  const [period, setPeriod] = useState<PeriodChoice>('all')
  // Hooks MUST run in the same order on every render — keep useMemo above
  // any early returns (loading/error/unsolved). qA.data / qB.data are
  // undefined while loading, so guard with ?? [].
  const periods = useMemo(() => {
    const pa = qA.data?.periods ?? []
    const pb = qB.data?.periods ?? []
    return [...new Set<number>([...pa, ...pb])].sort((x, y) => x - y)
  }, [qA.data?.periods, qB.data?.periods])

  // Canonicalise carrier maps so 'Gas'/'gas'/'GAS' collapse into one row.
  // Generation carriers + storage carriers go through different alias maps
  // (battery/BESS only collapses for StorageUnit-derived rows). Memoised
  // separately from the post-merge maps to keep the table data identity
  // stable across re-renders.
  const capMaps = useMemo(() => {
    const sa = qA.data
    const sb = qB.data
    const capA = canonicaliseCarrierMap(sa?.capacity?.capacity_mw_by_carrier ?? {}, 'Generator')
    const capB = canonicaliseCarrierMap(sb?.capacity?.capacity_mw_by_carrier ?? {}, 'Generator')
    const stmwA = canonicaliseCarrierMap(sa?.capacity?.storage_mw_by_carrier ?? {}, 'StorageUnit')
    const stmwB = canonicaliseCarrierMap(sb?.capacity?.storage_mw_by_carrier ?? {}, 'StorageUnit')
    // Links are now their own backend map (no longer folded into
    // capacity_mw_by_carrier). Merge them back in for this COMBINED capacity
    // tab so heat-pumps / electrolyzers / datacenters still appear in the
    // total + built tables and the carrier filter. The Overview tab keeps them
    // in a dedicated Links table instead.
    const linkA = canonicaliseCarrierMap(sa?.capacity?.link_capacity_mw_by_carrier ?? {}, 'Link')
    const linkB = canonicaliseCarrierMap(sb?.capacity?.link_capacity_mw_by_carrier ?? {}, 'Link')
    const totalCapexA = canonicaliseCarrierMap(sa?.capacity?.capex_meur_by_carrier ?? {}, 'Generator')
    const totalCapexB = canonicaliseCarrierMap(sb?.capacity?.capex_meur_by_carrier ?? {}, 'Generator')
    const newCapexA = canonicaliseCarrierMap(sa?.capacity?.new_capex_meur_by_carrier ?? {}, 'Generator')
    const newCapexB = canonicaliseCarrierMap(sb?.capacity?.new_capex_meur_by_carrier ?? {}, 'Generator')
    // New (built) MW capacity, per component class. Backend now keeps these
    // separate (gen / storage / link) so each is internally consistent with
    // its own total; the combined tab merges all three for the cross-fleet
    // "what did this scenario build?" view.
    const newGenA = canonicaliseCarrierMap(sa?.capacity?.new_capacity_mw_by_carrier ?? {}, 'Generator')
    const newGenB = canonicaliseCarrierMap(sb?.capacity?.new_capacity_mw_by_carrier ?? {}, 'Generator')
    const newStA = canonicaliseCarrierMap(sa?.capacity?.new_storage_mw_by_carrier ?? {}, 'StorageUnit')
    const newStB = canonicaliseCarrierMap(sb?.capacity?.new_storage_mw_by_carrier ?? {}, 'StorageUnit')
    const newLinkA = canonicaliseCarrierMap(sa?.capacity?.new_link_capacity_mw_by_carrier ?? {}, 'Link')
    const newLinkB = canonicaliseCarrierMap(sb?.capacity?.new_link_capacity_mw_by_carrier ?? {}, 'Link')
    return {
      mergedA: mergeBuckets(capA, stmwA, linkA), mergedB: mergeBuckets(capB, stmwB, linkB),
      newCapA: mergeBuckets(newGenA, newStA, newLinkA),
      newCapB: mergeBuckets(newGenB, newStB, newLinkB),
      totalCapexA, totalCapexB, newCapexA, newCapexB,
    }
  }, [qA.data, qB.data])

  // Union of canonical carriers across the merged capacity map drives the
  // filter chip strip. New / total CAPEX maps share the same carriers in
  // practice, so a single filter applies to all three sections cleanly.
  const availableCarriers = useMemo(
    () => unionCarriers(capMaps.mergedA, capMaps.mergedB),
    [capMaps.mergedA, capMaps.mergedB],
  )
  const filter = useCarrierFilter({ availableCarriers, storageKey: 'compare:capacity:carrier' })

  if (qA.isLoading || qB.isLoading) return <LoadingSpinner />
  if (qA.isError || qB.isError) return <ErrorBanner which={qA.isError ? a : b} />
  const sa = qA.data!
  const sb = qB.data!

  if (!sa.has_solve || !sb.has_solve) {
    return (
      <UnsolvedBanner
        unsolved={[!sa.has_solve && sa.project, !sb.has_solve && sb.project].filter(Boolean) as string[]}
      />
    )
  }

  // Whether this side's capacity block resolved at all. `?? false` so an
  // absent flag reads as unavailable, never available (ADR-0001; see
  // ABTable's matching props for the full rationale).
  const availableA = sa.capacity?.available ?? false
  const availableB = sb.capacity?.available ?? false

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4 flex-wrap">
        <PeriodSelector periods={periods} value={period} onChange={setPeriod} />
        <CarrierFilter {...bindCarrierFilter(filter, availableCarriers)} label="Carrier" />
      </div>

      <Section
        title="Total operational capacity by carrier (MW)"
        subtitle={periodLabel(period) + ' — p_nom_opt (brownfield + new build). A row showing equal values in both scenarios with Δ=0 (e.g. heat-dump 500/500) reflects FIXED capacity, not anything this scenario built. See "New (built) capacity" below for what each scenario actually expanded.'}
      >
        <ABBarChart
          aName={sa.project}
          bName={sb.project}
          mapA={capMaps.mergedA}
          mapB={capMaps.mergedB}
          period={period}
          unit="MW"
          selectedCarrier={filter.selectedCarrier}
          availableA={availableA}
          availableB={availableB}
        />
        <ABTable
          aName={sa.project}
          bName={sb.project}
          mapA={capMaps.mergedA}
          mapB={capMaps.mergedB}
          period={period}
          unit="MW"
          fmt={fmtMW}
          selectedCarrier={filter.selectedCarrier}
          availableA={availableA}
          availableB={availableB}
        />
      </Section>

      <Section
        title="New (built) capacity by carrier (MW)"
        subtitle={periodLabel(period) + ' — max(0, p_nom_opt − p_nom) for plain assets + vintage p_nom_opt for multi-period builds. Brownfield (non-extendable) capacity is excluded — only the actual scenario investments appear here.'}
      >
        <ABBarChart
          aName={sa.project}
          bName={sb.project}
          mapA={capMaps.newCapA}
          mapB={capMaps.newCapB}
          period={period}
          unit="MW"
          selectedCarrier={filter.selectedCarrier}
          availableA={availableA}
          availableB={availableB}
        />
        <ABTable
          aName={sa.project}
          bName={sb.project}
          mapA={capMaps.newCapA}
          mapB={capMaps.newCapB}
          period={period}
          unit="MW"
          fmt={fmtMW}
          totalsRow
          selectedCarrier={filter.selectedCarrier}
          availableA={availableA}
          availableB={availableB}
        />
      </Section>

      <Section
        title="Total annuitised CAPEX by carrier (M€)"
        subtitle={periodLabel(period) + ' — Σ p_nom_opt × annuitised_capital_cost × ipw.years[P]. Matches the live Results panel.'}
      >
        <ABBarChart
          aName={sa.project}
          bName={sb.project}
          mapA={capMaps.totalCapexA}
          mapB={capMaps.totalCapexB}
          period={period}
          unit="M€"
          selectedCarrier={filter.selectedCarrier}
          availableA={availableA}
          availableB={availableB}
        />
        <ABTable
          aName={sa.project}
          bName={sb.project}
          mapA={capMaps.totalCapexA}
          mapB={capMaps.totalCapexB}
          period={period}
          unit="M€"
          fmt={v => `${v.toFixed(2)} M€`}
          totalsRow
          selectedCarrier={filter.selectedCarrier}
          availableA={availableA}
          availableB={availableB}
        />
      </Section>

      <Section
        title="New (incremental) CAPEX by carrier (M€)"
        subtitle={periodLabel(period) + ' — max(0, p_nom_opt − p_nom) × annuitised_capital_cost. Only the additional spend over the existing fleet.'}
      >
        <ABBarChart
          aName={sa.project}
          bName={sb.project}
          mapA={capMaps.newCapexA}
          mapB={capMaps.newCapexB}
          period={period}
          unit="M€"
          selectedCarrier={filter.selectedCarrier}
          availableA={availableA}
          availableB={availableB}
        />
        <ABTable
          aName={sa.project}
          bName={sb.project}
          mapA={capMaps.newCapexA}
          mapB={capMaps.newCapexB}
          period={period}
          unit="M€"
          fmt={v => `${v.toFixed(3)} M€`}
          totalsRow
          selectedCarrier={filter.selectedCarrier}
          availableA={availableA}
          availableB={availableB}
        />
      </Section>
    </div>
  )
}

// ── Dispatch tab (Phase 1) ───────────────────────────────────────────────────

function DispatchTab({ a, b }: { a: string; b: string }) {
  const qA = useQuery({ queryKey: ['results-summary', a], queryFn: () => projectsApi.resultsSummary(a), staleTime: 30_000 })
  const qB = useQuery({ queryKey: ['results-summary', b], queryFn: () => projectsApi.resultsSummary(b), staleTime: 30_000 })
  const [period, setPeriod] = useState<PeriodChoice>('all')
  // Same hook-order discipline as CapacityTab — useMemo above the early
  // returns so React sees the same hook sequence on the first (loading)
  // and second (data-loaded) renders.
  const periods = useMemo(() => {
    const pa = qA.data?.periods ?? []
    const pb = qB.data?.periods ?? []
    return [...new Set<number>([...pa, ...pb])].sort((x, y) => x - y)
  }, [qA.data?.periods, qB.data?.periods])

  // Generator dispatch is the dominant contributor; storage discharge sits
  // in its own tab. So canonicalise as Generator — lowercase pass-through
  // for non-storage carriers, no surprise aliasing.
  const dispatchMaps = useMemo(() => ({
    dA: canonicaliseCarrierMap(qA.data?.dispatch?.dispatch_gwh_by_carrier ?? {}, 'Generator'),
    dB: canonicaliseCarrierMap(qB.data?.dispatch?.dispatch_gwh_by_carrier ?? {}, 'Generator'),
  }), [qA.data?.dispatch?.dispatch_gwh_by_carrier, qB.data?.dispatch?.dispatch_gwh_by_carrier])

  const availableCarriers = useMemo(
    () => unionCarriers(dispatchMaps.dA, dispatchMaps.dB),
    [dispatchMaps.dA, dispatchMaps.dB],
  )
  const filter = useCarrierFilter({ availableCarriers, storageKey: 'compare:dispatch:carrier' })

  if (qA.isLoading || qB.isLoading) return <LoadingSpinner />
  if (qA.isError || qB.isError) return <ErrorBanner which={qA.isError ? a : b} />
  const sa = qA.data!
  const sb = qB.data!

  if (!sa.has_solve || !sb.has_solve) {
    return (
      <UnsolvedBanner
        unsolved={[!sa.has_solve && sa.project, !sb.has_solve && sb.project].filter(Boolean) as string[]}
      />
    )
  }

  const opexA = sa.dispatch?.opex_meur ?? { total: 0, by_period: {} }
  const opexB = sb.dispatch?.opex_meur ?? { total: 0, by_period: {} }
  const loadA = sa.dispatch?.total_load_gwh ?? { total: 0, by_period: {} }
  const loadB = sb.dispatch?.total_load_gwh ?? { total: 0, by_period: {} }
  // Whether this side's dispatch block resolved at all — see CapacityTab's
  // matching computation for the full ADR-0001 rationale.
  const availableA = sa.dispatch?.available ?? false
  const availableB = sb.dispatch?.available ?? false

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4 flex-wrap">
        <PeriodSelector periods={periods} value={period} onChange={setPeriod} />
        <CarrierFilter {...bindCarrierFilter(filter, availableCarriers)} label="Carrier" />
      </div>

      <Section title="Energy dispatched by carrier (GWh)" subtitle={periodLabel(period)}>
        <ABBarChart
          aName={sa.project}
          bName={sb.project}
          mapA={dispatchMaps.dA}
          mapB={dispatchMaps.dB}
          period={period}
          unit="GWh"
          selectedCarrier={filter.selectedCarrier}
          availableA={availableA}
          availableB={availableB}
        />
        <ABTable
          aName={sa.project}
          bName={sb.project}
          mapA={dispatchMaps.dA}
          mapB={dispatchMaps.dB}
          period={period}
          unit="GWh"
          fmt={v => `${v.toFixed(1)} GWh`}
          totalsRow
          selectedCarrier={filter.selectedCarrier}
          availableA={availableA}
          availableB={availableB}
        />
      </Section>

      <Section title="Operational expenditure (M€)" subtitle={periodLabel(period) + ' — Σ marginal_cost × dispatch'}>
        <ABKpiPair
          aName={sa.project}
          bName={sb.project}
          aValue={readPV(opexA, period)}
          bValue={readPV(opexB, period)}
          unit="M€"
          fmt={v => `${v.toFixed(2)} M€`}
          deltaIsImprovementWhenNegative
          availableA={availableA}
          availableB={availableB}
        />
      </Section>

      <Section title="Total served load (GWh)" subtitle={periodLabel(period) + ' — should be near-identical between scenarios for a fair compare'}>
        <ABKpiPair
          aName={sa.project}
          bName={sb.project}
          aValue={readPV(loadA, period)}
          bValue={readPV(loadB, period)}
          unit="GWh"
          fmt={v => `${v.toFixed(1)} GWh`}
          availableA={availableA}
          availableB={availableB}
        />
      </Section>
    </div>
  )
}

// ── Loading tab (Phase 2) ────────────────────────────────────────────────────
// Top-N most congested lines / transformers, A vs B. Compares per-branch
// peak loading and snapshot-weighted mean. Binding-hour count surfaces in
// the table only — the chart focuses on loading % to keep the axis units
// coherent.

const LOADING_TOP_N = 10

// Exported solely so CompareView.availability.test.tsx can render it in
// isolation (Task 6 follow-up to Task 5) — every other tab in this file
// stays module-private except EmissionsTab/EconomicsTab/StorageCyclingTab
// (Task 5) for the same reason.
export function LoadingTab({ a, b }: { a: string; b: string }) {
  const qA = useQuery({ queryKey: ['results-summary', a], queryFn: () => projectsApi.resultsSummary(a), staleTime: 30_000 })
  const qB = useQuery({ queryKey: ['results-summary', b], queryFn: () => projectsApi.resultsSummary(b), staleTime: 30_000 })
  const [period, setPeriod] = useState<PeriodChoice>('all')
  // Hooks above early returns — same discipline as Phase-1 tabs.
  const periods = useMemo(() => {
    const pa = qA.data?.periods ?? []
    const pb = qB.data?.periods ?? []
    return [...new Set<number>([...pa, ...pb])].sort((x, y) => x - y)
  }, [qA.data?.periods, qB.data?.periods])

  // Build the union ranking BEFORE any conditional return. Previously this
  // sat after the `!has_solve` early return, which broke the rules-of-hooks
  // contract: on the first (loading) render the hook count was N; once
  // data arrived and the early returns no longer triggered, the count
  // became N+1 → React threw "Rendered more hooks than during the
  // previous render". QA-flagged.
  const merged = useMemo(() => mergeLoadingByName(
    qA.data?.loading?.lines ?? [], qB.data?.loading?.lines ?? [], period,
  ), [qA.data?.loading, qB.data?.loading, period])

  if (qA.isLoading || qB.isLoading) return <LoadingSpinner />
  if (qA.isError || qB.isError) return <ErrorBanner which={qA.isError ? a : b} />
  const sa = qA.data!
  const sb = qB.data!
  if (!sa.has_solve || !sb.has_solve) {
    return <UnsolvedBanner unsolved={[!sa.has_solve && sa.project, !sb.has_solve && sb.project].filter(Boolean) as string[]} />
  }
  // Whether this side's loading block resolved at all — `?? false` so an
  // absent flag (an older cached payload) reads as unavailable, never as
  // available (see ADR-0001 / EconomicsTab's matching note). Both projects
  // are guaranteed has_solve here, so this can only be "the branch loading
  // walk itself failed," not "not solved yet."
  const availableA = sa.loading?.available ?? false
  const availableB = sb.loading?.available ?? false

  if (merged.length === 0) {
    // Neither side has ANY branch row — could genuinely mean "no branches
    // with a populated p0 timeseries," but if either side's block never
    // resolved we can't back up that claim for it, so say so instead of
    // guessing (same reasoning as EconomicsTab's availableCarriers===0 gate).
    if (!availableA || !availableB) {
      return (
        <div className="space-y-3">
          <PeriodSelector periods={periods} value={period} onChange={setPeriod} />
          <p className="text-[11px] text-muted py-2">{COST_UNAVAILABLE}</p>
        </div>
      )
    }
    return (
      <div className="space-y-3">
        <PeriodSelector periods={periods} value={period} onChange={setPeriod} />
        <p className="text-[11px] text-muted py-2">No branches with a populated p0 timeseries in either project.</p>
      </div>
    )
  }
  // Split into AC branches (Lines + Transformers) and Links. Links use
  // p_nom-relative loading and a different physical interpretation (a
  // 30 % H2 pipeline isn't comparable to a 30 % AC line), so the user
  // wants them in their own section. is_link is set by the backend on
  // Link rows (also distinguished by carrier).
  const lineRows = merged.filter(r => !r.is_link)
  const linkRows = merged.filter(r => r.is_link)
  const topLines = lineRows.slice(0, LOADING_TOP_N)
  const topLinks = linkRows.slice(0, LOADING_TOP_N)
  return (
    <div className="space-y-4">
      <PeriodSelector periods={periods} value={period} onChange={setPeriod} />

      {lineRows.length > 0 && (
        <>
          <Section
            title={`Peak loading — top ${topLines.length} most congested AC branches`}
            subtitle={periodLabel(period) + ' — bars at 100 % indicate the line is hitting its s_nom limit'}
          >
            <LoadingBarChart aName={sa.project} bName={sb.project} rows={topLines} kind="peak" availableA={availableA} availableB={availableB} />
            <LoadingTable aName={sa.project} bName={sb.project} rows={lineRows} availableA={availableA} availableB={availableB} />
          </Section>

          <Section
            title="Mean loading — AC branches"
            subtitle={periodLabel(period) + ' — snapshot-weighted average over the selected horizon'}
          >
            <LoadingBarChart aName={sa.project} bName={sb.project} rows={topLines} kind="mean" availableA={availableA} availableB={availableB} />
          </Section>
        </>
      )}

      {linkRows.length > 0 && (
        <Section
          title={`Link loading — top ${topLinks.length} (electrolysers / heat pumps / H₂ pipelines / DC links)`}
          subtitle={periodLabel(period) + ' — Links report |p0| ÷ p_nom_opt; 100 % means the link is dispatching at its full sized capacity. Carrier shown alongside name.'}
        >
          <LoadingBarChart aName={sa.project} bName={sb.project} rows={topLinks} kind="peak" availableA={availableA} availableB={availableB} />
          <LoadingTable aName={sa.project} bName={sb.project} rows={linkRows} availableA={availableA} availableB={availableB} />
        </Section>
      )}
    </div>
  )
}

interface MergedLoadingRow {
  name: string
  is_transformer: boolean
  // Multi-port Link flag — set by the backend on Link rows so the Compare
  // View renders them in their own section (Links carry p_nom rather than
  // s_nom, and "loading" means a different thing on an H2 pipeline vs an
  // AC line). Carrier is the Link's PyPSA carrier ('H2 pipeline', 'DC',
  // 'heat-pump', ...), shown alongside the loading number.
  is_link: boolean
  carrier: string | null
  // Whether this branch actually appears in each side's `lines` list — as
  // opposed to peak_a/peak_b etc. defaulting to 0 via readPV's `!pv` guard.
  // Both a genuinely-zero-loading branch AND a branch entirely absent from
  // one side's fleet produce the same defaulted numeric fields below, so
  // LoadingTable needs this to tell "not built on this side" (em-dash) apart
  // from "built with 0 % loading" (a real value) — same ladder rung as
  // StorageUnitTable's r.a/r.b presence check.
  hasA: boolean
  hasB: boolean
  s_nom_a: number
  s_nom_b: number
  peak_a: number
  peak_b: number
  mean_a: number
  mean_b: number
  binding_a: number
  binding_b: number
}

function mergeLoadingByName(
  linesA: LineLoadingEntry[],
  linesB: LineLoadingEntry[],
  period: PeriodChoice,
): MergedLoadingRow[] {
  const mapA = new Map(linesA.map(L => [L.name, L]))
  const mapB = new Map(linesB.map(L => [L.name, L]))
  const names = new Set<string>([...mapA.keys(), ...mapB.keys()])
  const rows: MergedLoadingRow[] = []
  for (const name of names) {
    const A = mapA.get(name)
    const B = mapB.get(name)
    rows.push({
      name,
      is_transformer: !!(A?.is_transformer || B?.is_transformer),
      is_link: !!(A?.is_link || B?.is_link),
      carrier: A?.carrier ?? B?.carrier ?? null,
      hasA: A !== undefined,
      hasB: B !== undefined,
      s_nom_a: A?.s_nom_opt ?? 0,
      s_nom_b: B?.s_nom_opt ?? 0,
      peak_a: readPV(A?.peak_loading, period),
      peak_b: readPV(B?.peak_loading, period),
      mean_a: readPV(A?.mean_loading, period),
      mean_b: readPV(B?.mean_loading, period),
      binding_a: readPV(A?.binding_hours, period),
      binding_b: readPV(B?.binding_hours, period),
    })
  }
  // Rank by worst peak side so the chart top-N is the most informative.
  rows.sort((x, y) => Math.max(y.peak_a, y.peak_b) - Math.max(x.peak_a, x.peak_b))
  return rows
}

function LoadingBarChart({
  aName, bName, rows, kind, availableA = true, availableB = true,
}: {
  aName: string
  bName: string
  rows: MergedLoadingRow[]
  kind: 'peak' | 'mean'
  // Whether this side's loading block resolved at all — same guard as
  // ABBarChart (Task 5; see ADR-0001 / ABBarChart's "a zero-height bar
  // reads as a measured zero" comment). Without this, an unavailable
  // side's rows default peak_x/mean_x to 0 via readPV, and this chart
  // plotted a confident 0 % bar right next to the other side's real one —
  // the same defect ABTable/ABBarChart were fixed for one file away.
  // Defaults to true so this continues to render standalone in
  // tests/callers that don't care about availability.
  availableA?: boolean
  availableB?: boolean
}) {
  // Glyph prefix surfaces what kind of branch this is:
  //   ⚡ = transformer (carries no current outside of LP unless solved)
  //   ⇄ = multi-port Link (electrolyser / heat-pump / H2 pipeline)
  // Plain row = AC Line.
  const data = rows.map(r => ({
    name: (r.is_transformer ? '⚡ ' : r.is_link ? '⇄ ' : '') + r.name,
    [aName]: (kind === 'peak' ? r.peak_a : r.mean_a) * 100,  // → percent
    [bName]: (kind === 'peak' ? r.peak_b : r.mean_b) * 100,
  }))
  // Neither side resolved — there is nothing to plot, not even an empty
  // chart frame (mirrors ABBarChart's matching guard exactly).
  if (!availableA && !availableB) {
    return <p className="text-[11px] text-muted py-2">{COST_UNAVAILABLE}</p>
  }
  return (
    <div className="w-full h-72 mb-2">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 8, left: 100, bottom: 16 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(127,127,127,0.2)" />
          <XAxis type="number" tick={{ fontSize: 11 }} domain={[0, 100]}
                 label={{ value: kind === 'peak' ? 'Peak loading %' : 'Mean loading %', position: 'insideBottom', offset: -2, style: { fontSize: 10, fill: '#888' } }} />
          <YAxis dataKey="name" type="category" tick={{ fontSize: 10 }} width={100} />
          <Tooltip contentStyle={{ fontSize: 11 }} formatter={(v: number) => `${v.toFixed(1)} %`} />
          {/* Top-aligned legend keeps clear of the X-axis label below. */}
          <Legend verticalAlign="top" align="right" height={20} wrapperStyle={{ fontSize: 11, paddingBottom: 4 }} />
          {/* Reference line at the s_nom limit so 100 % bars visually pop. */}
          {kind === 'peak' && <ReferenceLine x={100} stroke="#dc2626" strokeDasharray="4 4" />}
          {/* An unavailable side plots NO bar at all — a zero-height bar
              reads as a measured zero, which is exactly the defect
              ADR-0001 forbids (same comment as ABBarChart). */}
          {availableA && <Bar dataKey={aName} fill="#3b82f6" />}
          {availableB && <Bar dataKey={bName} fill="#f59e0b" />}
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function LoadingTable({
  aName, bName, rows, availableA = true, availableB = true,
}: {
  aName: string
  bName: string
  rows: MergedLoadingRow[]
  // Whether this side's loading block resolved at all — same third state as
  // ABTable/StorageUnitTable (Task 5; see ADR-0001). A branch absent from
  // this side's `lines` array is already ambiguous between "not built here"
  // and "the block never resolved" (both leave r.hasA/r.hasB false) — this
  // flag disambiguates and takes priority. Defaults to true so this
  // continues to render standalone in tests that don't care about
  // availability. `?? false` at the call site, never `?? true`.
  availableA?: boolean
  availableB?: boolean
}) {
  if (rows.length === 0) return null
  const fmtPct = (v: number) => `${(v * 100).toFixed(1)} %`
  const fmtHr  = (v: number) => v >= 1 ? `${v.toFixed(0)} h` : (v > 0 ? `${v.toFixed(1)} h` : '—')
  const dash = <span className="text-muted" title="Branch not present in this scenario">—</span>
  const unavailable = <span className="text-muted" title="This scenario's figures could not be resolved">{COST_UNAVAILABLE}</span>
  return (
    <table className="w-full text-xs mt-2">
      <thead>
        <tr className="border-b border-border text-muted">
          <th className="text-left py-1 font-medium">Branch</th>
          <th className="text-right py-1 font-medium">{aName} peak</th>
          <th className="text-right py-1 font-medium">{bName} peak</th>
          <th className="text-right py-1 font-medium w-20">Δ peak</th>
          <th className="text-right py-1 font-medium">{aName} mean</th>
          <th className="text-right py-1 font-medium">{bName} mean</th>
          <th className="text-right py-1 font-medium">{aName} binding</th>
          <th className="text-right py-1 font-medium">{bName} binding</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(r => (
          <tr key={r.name} className="border-b border-border/40">
            <td className="py-1 text-text">
              {r.is_transformer && <span title="Transformer" className="text-muted">⚡ </span>}
              {r.is_link && <span title={`Link · ${r.carrier ?? 'unspecified carrier'}`} className="text-muted">⇄ </span>}
              {r.name}
              {r.is_link && r.carrier && (
                <span className="text-[10px] text-muted ml-1">· {carrierDisplayName(canonicaliseCarrier('Link', r.carrier))}</span>
              )}
            </td>
            <td className="py-1 text-right font-mono text-text">{!availableA ? unavailable : r.hasA ? fmtPct(r.peak_a) : dash}</td>
            <td className="py-1 text-right font-mono text-text">{!availableB ? unavailable : r.hasB ? fmtPct(r.peak_b) : dash}</td>
            <td className="py-1 text-right font-mono">
              {(availableA && availableB && r.hasA && r.hasB)
                ? <Delta v={(r.peak_b - r.peak_a) * 100} fmt={v => `${v.toFixed(1)} pp`} invert />
                : <span className="text-muted" title="Δ undefined — a scenario is unresolved or lacks this branch">—</span>}
            </td>
            <td className="py-1 text-right font-mono text-text">{!availableA ? unavailable : r.hasA ? fmtPct(r.mean_a) : dash}</td>
            <td className="py-1 text-right font-mono text-text">{!availableB ? unavailable : r.hasB ? fmtPct(r.mean_b) : dash}</td>
            <td className="py-1 text-right font-mono text-text">{!availableA ? unavailable : r.hasA ? fmtHr(r.binding_a) : dash}</td>
            <td className="py-1 text-right font-mono text-text">{!availableB ? unavailable : r.hasB ? fmtHr(r.binding_b) : dash}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ── Prices tab (Phase 2) ─────────────────────────────────────────────────────
// Marginal-price duration curve overlay + per-period mean/median/p90 table.
// Duration curve = bus-price percentile distribution. Index 0 = highest
// price (typical peak-demand hour), index 100 = lowest. Sampled at 101
// constant percentile points so A/B are directly comparable regardless of
// network size or snapshot count.

// Exported solely so CompareView.availability.test.tsx can render it in
// isolation (Task 6 follow-up to Task 5) — see LoadingTab's matching note.
export function PricesTab({ a, b }: { a: string; b: string }) {
  const qA = useQuery({ queryKey: ['results-summary', a], queryFn: () => projectsApi.resultsSummary(a), staleTime: 30_000 })
  const qB = useQuery({ queryKey: ['results-summary', b], queryFn: () => projectsApi.resultsSummary(b), staleTime: 30_000 })
  const periods = useMemo(() => {
    const pa = qA.data?.periods ?? []
    const pb = qB.data?.periods ?? []
    return [...new Set<number>([...pa, ...pb])].sort((x, y) => x - y)
  }, [qA.data?.periods, qB.data?.periods])

  if (qA.isLoading || qB.isLoading) return <LoadingSpinner />
  if (qA.isError || qB.isError) return <ErrorBanner which={qA.isError ? a : b} />
  const sa = qA.data!
  const sb = qB.data!
  if (!sa.has_solve || !sb.has_solve) {
    return <UnsolvedBanner unsolved={[!sa.has_solve && sa.project, !sb.has_solve && sb.project].filter(Boolean) as string[]} />
  }

  const pa = sa.prices
  const pb = sb.prices
  if (!pa || !pb) {
    return <p className="text-[11px] text-muted py-2">No marginal-price data in either project (buses_t.marginal_price empty).</p>
  }
  // Whether this side's prices block resolved at all — `?? false` so an
  // absent flag reads as unavailable, never as available (see ADR-0001 /
  // EconomicsTab's matching note). Both projects are guaranteed has_solve
  // here, so this can only mean the price-duration walk itself failed.
  const availableA = pa.available ?? false
  const availableB = pb.available ?? false
  if (!availableA && !availableB) {
    return <p className="text-[11px] text-muted py-2">{COST_UNAVAILABLE}</p>
  }
  if (pa.duration_curve.length === 0 && pb.duration_curve.length === 0) {
    return <p className="text-[11px] text-muted py-2">No marginal-price data in either project (buses_t.marginal_price empty).</p>
  }

  return (
    <div className="space-y-4">
      <Section
        title="Price duration curve (€/MWh)"
        // bus_count defaults to 0 on an unresolved side (same `available`
        // gate as everything else in this block — see
        // _compute_prices_summary's `if not has_solve: return
        // PricesComparison()`), so a plain `pa.bus_count || pb.bus_count`
        // would silently pass through the OTHER side's real count as if it
        // meant something for the unresolved side too. Only count sides
        // that actually resolved.
        subtitle={`Sorted bus-marginal prices across all snapshots × ${Math.max(availableA ? pa.bus_count : 0, availableB ? pb.bus_count : 0)} bus(es). 0 % = highest-price hour, 100 % = lowest. Y-axis clipped to the [min, p99] band when a single-hour scarcity spike (LP dual at a binding constraint) dominates the visual scale — the absolute extremes are shown beside the chart.`}
      >
        <DurationCurveChart
          aName={sa.project} bName={sb.project}
          a={pa.duration_curve} b={pb.duration_curve}
          maxA={pa.max_price} minA={pa.min_price}
          maxB={pb.max_price} minB={pb.min_price}
          availableA={availableA}
          availableB={availableB}
        />
      </Section>

      <Section title="Per-period price statistics (€/MWh)" subtitle="Snapshot-weighted; p90 = top-decile, useful for peak-pricing exposure">
        <PricesTable
          aName={sa.project}
          bName={sb.project}
          periods={['all', ...periods.map(String)]}
          meanA={pa.mean_price}     meanB={pb.mean_price}
          medianA={pa.median_price} medianB={pb.median_price}
          p90A={pa.p90_price}       p90B={pb.p90_price}
          availableA={availableA}
          availableB={availableB}
        />
      </Section>

      {((pa.by_carrier_stats && Object.keys(pa.by_carrier_stats).length > 0)
        || (pb.by_carrier_stats && Object.keys(pb.by_carrier_stats).length > 0)) && (
        <Section
          title="Per-bus-carrier price statistics (€/MWh)"
          subtitle="Sector-coupled networks have very different price regimes by carrier — H₂ buses tend to be priced higher than electrical, heat lower. Mean / median / p90 are snapshot-weighted across all buses tagged with each carrier."
        >
          <PerCarrierPricesTable
            aName={sa.project}
            bName={sb.project}
            statsA={pa.by_carrier_stats ?? {}}
            statsB={pb.by_carrier_stats ?? {}}
            availableA={availableA}
            availableB={availableB}
          />
        </Section>
      )}
    </div>
  )
}

// Per-bus-carrier price stats — one row per canonical carrier with mean /
// median / p90 across A and B. bus_count gives the user context for how
// many buses contribute to each row (sector-coupled networks have very
// different bus counts per carrier — typically many electrical, a handful
// of H2 / heat buses).
function PerCarrierPricesTable({
  aName, bName, statsA, statsB, availableA = true, availableB = true,
}: {
  aName: string
  bName: string
  statsA: Record<string, CarrierPriceStats>
  statsB: Record<string, CarrierPriceStats>
  // Whether this side's prices block resolved at all — same third state as
  // ABTable (Task 5; see ADR-0001). Defaults to true so this continues to
  // render standalone in tests/callers that don't care about availability.
  availableA?: boolean
  availableB?: boolean
}) {
  // Canonicalise carrier keys so 'AC' and 'electricity' collapse into one
  // row. Backend already canonicalises bus carriers but a paranoid second
  // pass is cheap insurance against legacy save files.
  const canonA = useMemo(() => canonicaliseCarrierStats(statsA), [statsA])
  const canonB = useMemo(() => canonicaliseCarrierStats(statsB), [statsB])
  const carriers = useMemo(
    () => [...new Set([...Object.keys(canonA), ...Object.keys(canonB)])].sort(),
    [canonA, canonB],
  )
  if (carriers.length === 0) {
    return <p className="text-[11px] text-muted">No per-carrier price data — backend payload empty.</p>
  }
  const fmt = (v: number) => `${v.toFixed(2)} €/MWh`
  const dash = <span className="text-muted" title="Carrier not present in this scenario">—</span>
  const unavailable = <span className="text-muted" title="This scenario's figures could not be resolved">{COST_UNAVAILABLE}</span>
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-border text-muted">
          <th className="text-left py-1 font-medium">Carrier</th>
          <th className="text-right py-1 font-medium">Buses</th>
          <th className="text-right py-1 font-medium">{aName} mean</th>
          <th className="text-right py-1 font-medium">{bName} mean</th>
          <th className="text-right py-1 font-medium">{aName} median</th>
          <th className="text-right py-1 font-medium">{bName} median</th>
          <th className="text-right py-1 font-medium">{aName} p90</th>
          <th className="text-right py-1 font-medium">{bName} p90</th>
        </tr>
      </thead>
      <tbody>
        {carriers.map(c => {
          const A = canonA[c]
          const B = canonB[c]
          const busCount = Math.max(A?.bus_count ?? 0, B?.bus_count ?? 0)
          return (
            <tr key={c} className="border-b border-border/40">
              <td className="py-1 text-text">{carrierDisplayName(c)}</td>
              <td className="py-1 text-right font-mono text-muted">{busCount || '—'}</td>
              <td className="py-1 text-right font-mono text-text">{!availableA ? unavailable : A ? fmt(A.mean_price.total) : dash}</td>
              <td className="py-1 text-right font-mono text-text">{!availableB ? unavailable : B ? fmt(B.mean_price.total) : dash}</td>
              <td className="py-1 text-right font-mono text-text">{!availableA ? unavailable : A ? fmt(A.median_price.total) : dash}</td>
              <td className="py-1 text-right font-mono text-text">{!availableB ? unavailable : B ? fmt(B.median_price.total) : dash}</td>
              <td className="py-1 text-right font-mono text-text">{!availableA ? unavailable : A ? fmt(A.p90_price.total) : dash}</td>
              <td className="py-1 text-right font-mono text-text">{!availableB ? unavailable : B ? fmt(B.p90_price.total) : dash}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// Defensive canonicalisation: backend should already produce canonical
// carrier keys but a project saved before that change ships could have
// raw 'AC' / 'electricity' / 'h2' variants. Mean / median / p90 within
// the SAME canonical carrier across spelling variants is too rough to
// weight properly — last-write-wins is the simplest sane behaviour and
// matches what users see in the single-project Results panel.
function canonicaliseCarrierStats(
  raw: Record<string, CarrierPriceStats>,
): Record<string, CarrierPriceStats> {
  const out: Record<string, CarrierPriceStats> = {}
  for (const [k, v] of Object.entries(raw)) {
    const key = canonicaliseCarrier('Line', k)  // Line keeps lower-case pass-through
    if (!(key in out)) out[key] = v
  }
  return out
}

function DurationCurveChart({
  aName, bName, a, b,
  maxA, minA, maxB, minB,
  availableA = true, availableB = true,
}: {
  aName: string
  bName: string
  a: number[]
  b: number[]
  // Absolute extremes from the backend. Used to (a) annotate the chart with
  // tooltip-style badges and (b) detect when the duration_curve[0] is a
  // single-hour spike worth clipping out of the Y-axis (otherwise the bulk
  // of the curve compresses into a thin band near zero on a 0..70k scale).
  maxA?: number
  minA?: number
  maxB?: number
  minB?: number
  // Whether this side's prices block resolved at all — same third state as
  // ABTable (Task 5; see ADR-0001). The curve line itself is already safe
  // without this (an unresolved side's `duration_curve` is `[]`, so
  // `a[i] ?? null` produces a gap Recharts never draws — not a fabricated
  // zero-line), but max_price/min_price default to `0.0` on the backend
  // (schemas.py: `max_price: float = 0.0`) and are ALWAYS serialised, so
  // without this guard the extreme-badge strip below read a confident
  // "max 0.0 €/MWh · min 0.0 €/MWh" for an unresolved side right beside the
  // other side's real extremes. Defaults to true so this continues to
  // render standalone in tests/callers that don't care about availability.
  availableA?: boolean
  availableB?: boolean
}) {
  const data = useMemo(() => {
    const n = Math.max(a.length, b.length)
    if (n === 0) return []
    return Array.from({ length: n }, (_, i) => ({
      pct: (i / Math.max(n - 1, 1)) * 100,  // 0..100
      [aName]: a[i] ?? null,
      [bName]: b[i] ?? null,
    }))
  }, [a, b, aName, bName])
  // Y-axis clipping to the [p1, p99] band (as the subtitle promises). LP duals
  // at binding constraints / VOLL can spike to millions or hundreds of millions
  // €/MWh, and there can be MORE THAN ONE such spike — so the old "clip to the
  // 2nd-highest sample" rule failed (top2 was itself a spike, leaving the axis
  // spanning hundreds of millions and the real curve invisible). Percentile
  // clipping is robust to any number of tail outliers. The absolute extremes
  // stay visible in the side badges.
  // `domain` returned as a Recharts-compatible [number|'auto', number|'auto'].
  const yDomain = useMemo((): [number | 'auto', number | 'auto'] => {
    const samples: number[] = []
    for (const arr of [a, b]) {
      for (const v of arr) if (Number.isFinite(v)) samples.push(v)
    }
    if (samples.length < 5) return ['auto', 'auto']
    const asc = [...samples].sort((x, y) => x - y)
    const quantile = (p: number): number => {
      const idx = (asc.length - 1) * p
      const lo = Math.floor(idx), hi = Math.ceil(idx)
      return lo === hi ? asc[lo] : asc[lo] + (asc[hi] - asc[lo]) * (idx - lo)
    }
    const p99 = quantile(0.99)
    const p01 = quantile(0.01)
    const top = asc[asc.length - 1]
    const bot = asc[0]
    const band = Math.max(p99 - p01, 1)
    // Only clip when an extreme sits FAR outside the central band (a genuine
    // spike) — otherwise let recharts auto-fit so normal price ranges fill
    // the chart.
    const spikeHigh = (top - p99) > band
    const spikeLow = (p01 - bot) > band
    if (!spikeHigh && !spikeLow) return ['auto', 'auto']
    const high = spikeHigh ? p99 + 0.1 * band : top
    const low = spikeLow ? p01 - 0.1 * band : Math.min(0, bot)
    return [low, high]
  }, [a, b])
  const isClipped = yDomain[0] !== 'auto'
  // Compact €/MWh tick labels — k/M suffixes keep the axis readable and
  // prevent raw 9-digit numbers when a (clipped-out) spike widens the domain.
  const yTickFmt = (v: number): string => {
    if (!Number.isFinite(v)) return ''
    const a = Math.abs(v)
    if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`
    if (a >= 1e3) return `${(v / 1e3).toFixed(1)}k`
    return v.toFixed(0)
  }
  // Annotation strip — show A's and B's absolute extremes as small badges.
  const fmt = (v: number | undefined) =>
    typeof v === 'number' && Number.isFinite(v)
      ? `${v >= 1000 ? (v / 1000).toFixed(1) + 'k' : v.toFixed(1)} €/MWh`
      : '—'
  // Side-aware formatter: an unavailable side's max/min is a `0.0` DEFAULT,
  // not a measurement (see the `availableA`/`availableB` prop comment
  // above) — `fmt` alone can't tell that apart from a genuine `0.0 €/MWh`
  // extreme, so gate on availability first.
  const fmtSide = (v: number | undefined, available: boolean) =>
    available ? fmt(v) : COST_UNAVAILABLE
  return (
    <div className="w-full">
      <div className="w-full h-72">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 4, right: 16, left: 8, bottom: 24 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(127,127,127,0.2)" />
            <XAxis dataKey="pct" type="number" tick={{ fontSize: 11 }} domain={[0, 100]}
                   label={{ value: 'Percentile of (bus × snapshot) observations', position: 'insideBottom', offset: -8, style: { fontSize: 10, fill: '#888' } }} />
            <YAxis tick={{ fontSize: 11 }}
                   domain={yDomain}
                   allowDataOverflow={isClipped}
                   tickFormatter={yTickFmt}
                   label={{ value: '€/MWh', angle: -90, position: 'insideLeft', style: { fontSize: 10, fill: '#888' } }} />
            <Tooltip
              contentStyle={{ fontSize: 11 }}
              labelFormatter={(v: number) => `${v.toFixed(1)} %`}
              formatter={(v: number) => `${(v ?? 0).toFixed(2)} €/MWh`}
            />
            {/* Legend at the top — the default bottom position collides with
                the X-axis label, hiding both behind each other. */}
            <Legend verticalAlign="top" align="right" height={20} wrapperStyle={{ fontSize: 11, paddingBottom: 4 }} />
            {/* Horizontal reference at 0 €/MWh — negative prices below the line
                jump out visually (oversupply / curtailment regime). */}
            <ReferenceLine y={0} stroke="#dc2626" strokeDasharray="4 4" />
            <Line dataKey={aName} stroke="#3b82f6" strokeWidth={2} dot={false} />
            <Line dataKey={bName} stroke="#f59e0b" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      {/* Absolute extremes — always rendered when the backend exposes them.
          When the chart was clipped, this is the user's only way to see the
          spike magnitude; when not clipped, it's a quick at-a-glance summary
          of price range without scanning the curve. */}
      {(maxA != null || minA != null || maxB != null || minB != null) && (
        <div className="mt-1 flex items-center gap-4 text-[10px] font-mono text-muted flex-wrap">
          {isClipped && (
            <span className="text-warn font-medium">
              ⚠ Y-axis clipped to [min, top non-spike] — see absolute extremes below
            </span>
          )}
          <span>
            <span className="text-[#3b82f6] font-medium">{aName}</span>:
            {' '}max {fmtSide(maxA, availableA)} · min {fmtSide(minA, availableA)}
          </span>
          <span>
            <span className="text-[#f59e0b] font-medium">{bName}</span>:
            {' '}max {fmtSide(maxB, availableB)} · min {fmtSide(minB, availableB)}
          </span>
        </div>
      )}
    </div>
  )
}

function PricesTable({
  aName, bName, periods,
  meanA, meanB, medianA, medianB, p90A, p90B,
  availableA = true, availableB = true,
}: {
  aName: string
  bName: string
  periods: string[]                              // includes 'all' as the first row
  meanA: CarrierPeriodValue;   meanB: CarrierPeriodValue
  medianA: CarrierPeriodValue; medianB: CarrierPeriodValue
  p90A: CarrierPeriodValue;    p90B: CarrierPeriodValue
  // Whether this side's prices block resolved at all — same third state as
  // ABKpiPair (Task 5; see ADR-0001). No present/absent ladder underneath
  // (these are period-scalar KPIs, not a carrier map) — just "unavailable"
  // vs "real value," same as ABKpiPair. Defaults to true so this continues
  // to render standalone in tests/callers that don't care about availability.
  availableA?: boolean
  availableB?: boolean
}) {
  const fmt = (v: number) => `${v.toFixed(2)} €/MWh`
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-border text-muted">
          <th className="text-left py-1 font-medium">Period</th>
          <th className="text-right py-1 font-medium">Metric</th>
          <th className="text-right py-1 font-medium">{aName}</th>
          <th className="text-right py-1 font-medium">{bName}</th>
          <th className="text-right py-1 font-medium w-24">Δ</th>
        </tr>
      </thead>
      <tbody>
        {periods.map(p => (
          <PriceRowGroup
            key={p}
            label={p === 'all' ? 'All periods' : `Period ${p}`}
            meanA={readPV(meanA, p)} meanB={readPV(meanB, p)}
            medianA={readPV(medianA, p)} medianB={readPV(medianB, p)}
            p90A={readPV(p90A, p)} p90B={readPV(p90B, p)}
            fmt={fmt}
            availableA={availableA}
            availableB={availableB}
          />
        ))}
      </tbody>
    </table>
  )
}

function PriceRowGroup({
  label, meanA, meanB, medianA, medianB, p90A, p90B, fmt,
  availableA = true, availableB = true,
}: {
  label: string
  meanA: number; meanB: number
  medianA: number; medianB: number
  p90A: number; p90B: number
  fmt: (n: number) => string
  availableA?: boolean
  availableB?: boolean
}) {
  const unavailable = <span className="text-muted" title="This scenario's figures could not be resolved">{COST_UNAVAILABLE}</span>
  const cellA = (v: number) => availableA ? fmt(v) : unavailable
  const cellB = (v: number) => availableB ? fmt(v) : unavailable
  const delta = (aVal: number, bVal: number) => (availableA && availableB)
    ? <Delta v={bVal - aVal} fmt={fmt} invert />
    : <span className="text-muted" title="Δ undefined — a scenario is unresolved">—</span>
  return (
    <>
      <tr className="border-b border-border/40">
        <td rowSpan={3} className="py-1 text-text align-top">{label}</td>
        <td className="py-1 text-right text-muted">mean</td>
        <td className="py-1 text-right font-mono text-text">{cellA(meanA)}</td>
        <td className="py-1 text-right font-mono text-text">{cellB(meanB)}</td>
        <td className="py-1 text-right font-mono">{delta(meanA, meanB)}</td>
      </tr>
      <tr className="border-b border-border/40">
        <td className="py-1 text-right text-muted">median</td>
        <td className="py-1 text-right font-mono text-text">{cellA(medianA)}</td>
        <td className="py-1 text-right font-mono text-text">{cellB(medianB)}</td>
        <td className="py-1 text-right font-mono">{delta(medianA, medianB)}</td>
      </tr>
      <tr className="border-b border-border/40">
        <td className="py-1 text-right text-muted">p90</td>
        <td className="py-1 text-right font-mono text-text">{cellA(p90A)}</td>
        <td className="py-1 text-right font-mono text-text">{cellB(p90B)}</td>
        <td className="py-1 text-right font-mono">{delta(p90A, p90B)}</td>
      </tr>
    </>
  )
}

// ── Emissions tab (Phase 3) ──────────────────────────────────────────────────
// Total CO₂ (kt) + per-carrier kt + system intensity (kg/MWh). The intensity
// is the headline metric for fair scenario comparison: two scenarios with
// different total demand can have very different kt yet identical intensity,
// and vice versa.

// Exported solely so CompareView.availability.test.tsx can render it in
// isolation (Task 5 follow-up) — every other tab in this file stays
// module-private. Same rationale as EconomicsTab's export above.
export function EmissionsTab({ a, b }: { a: string; b: string }) {
  const qA = useQuery({ queryKey: ['results-summary', a], queryFn: () => projectsApi.resultsSummary(a), staleTime: 30_000 })
  const qB = useQuery({ queryKey: ['results-summary', b], queryFn: () => projectsApi.resultsSummary(b), staleTime: 30_000 })
  const [period, setPeriod] = useState<PeriodChoice>('all')
  // Hooks above early returns.
  const periods = useMemo(() => {
    const pa = qA.data?.periods ?? []
    const pb = qB.data?.periods ?? []
    return [...new Set<number>([...pa, ...pb])].sort((x, y) => x - y)
  }, [qA.data?.periods, qB.data?.periods])

  // Emission carriers are generator carriers (gas / coal / oil). Canonicalise
  // as Generator → lower-case pass-through merges 'Gas' / 'gas' / 'GAS'.
  const emMaps = useMemo(() => ({
    A: canonicaliseCarrierMap(qA.data?.emissions?.by_carrier_kt ?? {}, 'Generator'),
    B: canonicaliseCarrierMap(qB.data?.emissions?.by_carrier_kt ?? {}, 'Generator'),
  }), [qA.data?.emissions?.by_carrier_kt, qB.data?.emissions?.by_carrier_kt])
  const availableCarriers = useMemo(
    () => unionCarriers(emMaps.A, emMaps.B),
    [emMaps.A, emMaps.B],
  )
  const filter = useCarrierFilter({ availableCarriers, storageKey: 'compare:emissions:carrier' })

  if (qA.isLoading || qB.isLoading) return <LoadingSpinner />
  if (qA.isError || qB.isError) return <ErrorBanner which={qA.isError ? a : b} />
  const sa = qA.data!
  const sb = qB.data!
  if (!sa.has_solve || !sb.has_solve) {
    return <UnsolvedBanner unsolved={[!sa.has_solve && sa.project, !sb.has_solve && sb.project].filter(Boolean) as string[]} />
  }

  const emA = sa.emissions
  const emB = sb.emissions
  if (!emA || !emB) {
    return <p className="text-[11px] text-muted py-2">No emissions data — neither project has carriers with co2_emissions set.</p>
  }
  // Whether this side's emissions block resolved at all — see CapacityTab's
  // matching computation for the full ADR-0001 rationale.
  const availableA = emA.available ?? false
  const availableB = emB.available ?? false

  const fmtKt = (v: number) => `${v.toFixed(1)} kt`
  const fmtKgMwh = (v: number) => `${v.toFixed(1)} kg/MWh`

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4 flex-wrap">
        <PeriodSelector periods={periods} value={period} onChange={setPeriod} />
        <CarrierFilter {...bindCarrierFilter(filter, availableCarriers)} label="Emitter" />
      </div>

      <Section title="Total CO₂ emissions" subtitle={periodLabel(period)}>
        <ABKpiPair
          aName={sa.project}
          bName={sb.project}
          aValue={readPV(emA.total_kt, period)}
          bValue={readPV(emB.total_kt, period)}
          unit="kt CO₂"
          fmt={fmtKt}
          deltaIsImprovementWhenNegative
          availableA={availableA}
          availableB={availableB}
        />
      </Section>

      <Section title="System emission intensity" subtitle={periodLabel(period) + ' — total kt × 10⁶ ÷ total dispatch MWh, neutral to demand differences'}>
        <ABKpiPair
          aName={sa.project}
          bName={sb.project}
          aValue={readPV(emA.intensity_kg_per_mwh, period)}
          bValue={readPV(emB.intensity_kg_per_mwh, period)}
          unit="kg/MWh"
          fmt={fmtKgMwh}
          deltaIsImprovementWhenNegative
          availableA={availableA}
          availableB={availableB}
        />
      </Section>

      <Section title="By emitter carrier (kt)" subtitle={periodLabel(period) + ' — only carriers with non-zero co2_emissions appear'}>
        <ABBarChart
          aName={sa.project}
          bName={sb.project}
          mapA={emMaps.A}
          mapB={emMaps.B}
          period={period}
          unit="kt"
          selectedCarrier={filter.selectedCarrier}
          availableA={availableA}
          availableB={availableB}
        />
        <ABTable
          aName={sa.project}
          bName={sb.project}
          mapA={emMaps.A}
          mapB={emMaps.B}
          period={period}
          unit="kt"
          fmt={fmtKt}
          totalsRow
          selectedCarrier={filter.selectedCarrier}
          availableA={availableA}
          availableB={availableB}
        />
      </Section>
    </div>
  )
}

// ── Economics tab (Phase 3) ──────────────────────────────────────────────────
// Per-carrier revenue / OPEX / CAPEX / LCOE roll-up. The LCOE column is
// the headline number — it tells you which carrier delivered energy most
// cheaply when capex and opex are amortized together. A bar chart of LCOE
// across carriers tops the section so the user can scan the cost-effective
// ones at a glance.

// Exported solely so CompareView.availability.test.tsx can render it in
// isolation (Task 5) — every other tab in this file stays module-private.
export function EconomicsTab({ a, b }: { a: string; b: string }) {
  const qA = useQuery({ queryKey: ['results-summary', a], queryFn: () => projectsApi.resultsSummary(a), staleTime: 30_000 })
  const qB = useQuery({ queryKey: ['results-summary', b], queryFn: () => projectsApi.resultsSummary(b), staleTime: 30_000 })
  const [period, setPeriod] = useState<PeriodChoice>('all')
  const periods = useMemo(() => {
    const pa = qA.data?.periods ?? []
    const pb = qB.data?.periods ?? []
    return [...new Set<number>([...pa, ...pb])].sort((x, y) => x - y)
  }, [qA.data?.periods, qB.data?.periods])

  // Canonicalise the per-carrier roll-up by Generator alias (storage rows
  // appear under Storage Cycling). The same EconomicsTable now consumes the
  // canonical keys + a friendly display label.
  const ecMaps = useMemo(() => {
    const rawA = qA.data?.economics?.by_carrier ?? {}
    const rawB = qB.data?.economics?.by_carrier ?? {}
    const canon = (
      side: Record<string, CarrierEconomics>,
    ): Record<string, CarrierEconomics> => {
      // Clone a (possibly absent) per-period value bucket. The split buckets
      // are optional on the backend type but always present in practice; the
      // ?? guards keep canon total when a scenario omits one.
      const clonePV = (pv?: CarrierPeriodValue): CarrierPeriodValue =>
        ({ total: pv?.total ?? 0, by_period: { ...(pv?.by_period ?? {}) } })
      const out: Record<string, CarrierEconomics> = {}
      for (const [c, e] of Object.entries(side)) {
        const key = canonicaliseCarrier('Generator', c)
        const existing = out[key]
        if (!existing) {
          out[key] = {
            revenue_meur: clonePV(e.revenue_meur),
            opex_meur:    clonePV(e.opex_meur),
            // Carry the OPEX split buckets through the merge — the table renders
            // them as sub-rows AND the LCOE re-derivation below needs the
            // curtailment / lost-load penalties to subtract them out.
            gen_cost_meur:            clonePV(e.gen_cost_meur),
            storage_charge_cost_meur: clonePV(e.storage_charge_cost_meur),
            curtailment_cost_meur:    clonePV(e.curtailment_cost_meur),
            lost_load_cost_meur:      clonePV(e.lost_load_cost_meur),
            capex_meur:   clonePV(e.capex_meur),
            dispatch_gwh: clonePV(e.dispatch_gwh),
            lcoe_eur_per_mwh: clonePV(e.lcoe_eur_per_mwh),
          }
        } else {
          const sum = (acc?: CarrierPeriodValue, add?: CarrierPeriodValue) => {
            if (!acc || !add) return
            acc.total += add.total
            for (const [p, v] of Object.entries(add.by_period)) {
              acc.by_period[p] = (acc.by_period[p] ?? 0) + v
            }
          }
          sum(existing.revenue_meur, e.revenue_meur)
          sum(existing.opex_meur, e.opex_meur)
          sum(existing.gen_cost_meur, e.gen_cost_meur)
          sum(existing.storage_charge_cost_meur, e.storage_charge_cost_meur)
          sum(existing.curtailment_cost_meur, e.curtailment_cost_meur)
          sum(existing.lost_load_cost_meur, e.lost_load_cost_meur)
          sum(existing.capex_meur, e.capex_meur)
          sum(existing.dispatch_gwh, e.dispatch_gwh)
          // LCOE doesn't sum across spellings — re-derived below from the
          // merged cost + dispatch sums.
        }
      }
      // Re-derive LCOE = (capex + CASH opex) / dispatch after merging, where
      // CASH opex EXCLUDES the curtailment + lost-load penalties (non-cash
      // modelling soft-constraints). Matches the backend's _compute_economics_summary
      // and the per-asset Results LCOE so the same carrier reads identically on
      // both tabs. The penalties remain visible in the OPEX (total) row + their
      // own split sub-rows — just not inside the LCOE ratio.
      for (const e of Object.values(out)) {
        const cashOpexTotal =
          e.opex_meur.total - (e.curtailment_cost_meur?.total ?? 0) - (e.lost_load_cost_meur?.total ?? 0)
        const denomTotal = e.dispatch_gwh.total * 1000  // GWh → MWh
        e.lcoe_eur_per_mwh.total =
          denomTotal > 0 ? (e.capex_meur.total + cashOpexTotal) * 1e6 / denomTotal : 0
        const periods = new Set<string>([
          ...Object.keys(e.capex_meur.by_period),
          ...Object.keys(e.opex_meur.by_period),
          ...Object.keys(e.dispatch_gwh.by_period),
        ])
        const recomputed: Record<string, number> = {}
        for (const p of periods) {
          const denom = (e.dispatch_gwh.by_period[p] ?? 0) * 1000
          const cashOpexP =
            (e.opex_meur.by_period[p] ?? 0)
            - (e.curtailment_cost_meur?.by_period[p] ?? 0)
            - (e.lost_load_cost_meur?.by_period[p] ?? 0)
          recomputed[p] = denom > 0
            ? ((e.capex_meur.by_period[p] ?? 0) + cashOpexP) * 1e6 / denom
            : 0
        }
        e.lcoe_eur_per_mwh.by_period = recomputed
      }
      return out
    }
    return { A: canon(rawA), B: canon(rawB) }
  }, [qA.data?.economics?.by_carrier, qB.data?.economics?.by_carrier])

  const availableCarriers = useMemo(
    () => [...new Set([...Object.keys(ecMaps.A), ...Object.keys(ecMaps.B)])].sort(),
    [ecMaps.A, ecMaps.B],
  )
  const filter = useCarrierFilter({ availableCarriers, storageKey: 'compare:economics:carrier' })

  if (qA.isLoading || qB.isLoading) return <LoadingSpinner />
  if (qA.isError || qB.isError) return <ErrorBanner which={qA.isError ? a : b} />
  const sa = qA.data!
  const sb = qB.data!
  if (!sa.has_solve || !sb.has_solve) {
    return <UnsolvedBanner unsolved={[!sa.has_solve && sa.project, !sb.has_solve && sb.project].filter(Boolean) as string[]} />
  }
  const ecA = ecMaps.A
  const ecB = ecMaps.B
  // Whether this side's economics block resolved at all — `?? false` so an
  // absent flag (e.g. an older cached payload) reads as unavailable, never
  // as available (see ADR-0001; coalescing to `true` would reintroduce the
  // fabricated-zero defect this task exists to remove).
  const availableA = sa.economics?.available ?? false
  const availableB = sb.economics?.available ?? false
  if (availableCarriers.length === 0) {
    // Both maps are empty here — either because a side never resolved
    // (by_carrier defaults to {} on every unresolved block) or because both
    // sides genuinely have no economic assets. If either side is
    // unresolved, "both projects have empty asset lists" is a claim we
    // can't back up for that side — say so instead of guessing.
    if (!availableA || !availableB) {
      return <p className="text-[11px] text-muted py-2">{COST_UNAVAILABLE}</p>
    }
    return <p className="text-[11px] text-muted py-2">No economic data — both projects have empty asset lists.</p>
  }

  // Derive per-metric maps for the existing ABBarChart / ABTable so we
  // can reuse the same building blocks Phase 1 already shipped.
  const projectMetric = (
    side: Record<string, CarrierEconomics>,
    metric: keyof CarrierEconomics,
  ): Record<string, CarrierPeriodValue> =>
    Object.fromEntries(Object.entries(side).map(([c, e]) => [c, e[metric] as CarrierPeriodValue]))

  const lcoeA = projectMetric(ecA, 'lcoe_eur_per_mwh')
  const lcoeB = projectMetric(ecB, 'lcoe_eur_per_mwh')

  const fmtMEur = (v: number) => `${v.toFixed(2)} M€`
  const fmtEurMwh = (v: number) => `${v.toFixed(2)} €/MWh`

  // Per-asset LCOH rows for the new table (gap 4) — electrolysers / heat
  // pumps / P2X Links with non-trivial dispatch. Concatenated A+B with side
  // tagging so the table can show both scenarios per asset on adjacent rows
  // when the same asset name exists in both projects.
  const lcohA = sa.economics?.per_asset_lcoh ?? []
  const lcohB = sb.economics?.per_asset_lcoh ?? []

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4 flex-wrap">
        <PeriodSelector periods={periods} value={period} onChange={setPeriod} />
        <CarrierFilter {...bindCarrierFilter(filter, availableCarriers)} label="Carrier" />
      </div>

      <Section title="Levelised cost of energy (LCOE) by carrier" subtitle={periodLabel(period) + ' — (annuitised CAPEX + cash OPEX) ÷ dispatch. Excludes the curtailment & lost-load penalties (non-cash modelling terms, shown separately below) so this matches the per-asset LCOE/LCOS in Results → Economics.'}>
        <ABBarChart
          aName={sa.project}
          bName={sb.project}
          mapA={lcoeA}
          mapB={lcoeB}
          period={period}
          unit="€/MWh"
          selectedCarrier={filter.selectedCarrier}
          availableA={availableA}
          availableB={availableB}
        />
      </Section>

      <Section
        title="Per-carrier economic summary"
        subtitle={periodLabel(period) + ' — each carrier merges ALL its components (e.g. “H2” = electrolyser Link + H₂ storage + stores). Dispatch is the Link’s electricity input, so a carrier LCOE is not an LCOH — see the per-asset section below and the Results → Economics → LCOH panel for electrolyser-only figures. LCOE uses cash OPEX only: the curtailment & lost-load sub-rows are shown but excluded from the ratio.'}
      >
        <EconomicsTable
          aName={sa.project}
          bName={sb.project}
          carriers={availableCarriers.filter(c => filter.selectedCarrier == null || c === filter.selectedCarrier)}
          ecA={ecA}
          ecB={ecB}
          period={period}
          fmtMEur={fmtMEur}
          fmtEurMwh={fmtEurMwh}
          availableA={availableA}
          availableB={availableB}
        />
      </Section>

      {(lcohA.length > 0 || lcohB.length > 0) && (
        <Section
          title="Per-asset levelised cost (LCOH / LCOE)"
          subtitle={periodLabel(period) + ' — extendable Links (electrolysers, heat pumps, P2X) with non-trivial dispatch. LCOH unit = €/MWh of the Link’s output, regardless of carrier label.'}
        >
          <AssetLCOHTable
            aName={sa.project}
            bName={sb.project}
            rowsA={lcohA}
            rowsB={lcohB}
            period={period}
            fmtMEur={fmtMEur}
            fmtEurMwh={fmtEurMwh}
            selectedCarrier={filter.selectedCarrier}
          />
        </Section>
      )}
    </div>
  )
}

// One mega-table with all four metrics per carrier — chosen over four
// stacked tables because the user wants to scan a carrier across all
// dimensions, not the same metric across carriers.
function EconomicsTable({
  aName, bName, carriers, ecA, ecB, period, fmtMEur, fmtEurMwh,
  availableA = true, availableB = true,
}: {
  aName: string
  bName: string
  carriers: string[]
  ecA: Record<string, CarrierEconomics>
  ecB: Record<string, CarrierEconomics>
  period: PeriodChoice
  fmtMEur: (n: number) => string
  fmtEurMwh: (n: number) => string
  // Whether this side's economics block resolved at all (Task 5; see
  // ADR-0001 and ABTable's matching props for the full rationale). Takes
  // ecA/ecB rather than per-carrier maps, but the cell rule is identical:
  // an unavailable side's numeric cells read COST_UNAVAILABLE and any Δ
  // against it is undefined, regardless of what the (block-defaulted-empty)
  // map happens to contain. Defaults to true so untouched call sites keep
  // compiling.
  availableA?: boolean
  availableB?: boolean
}) {
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-border text-muted">
          <th className="text-left  py-1 font-medium">Carrier</th>
          <th className="text-right py-1 font-medium">Metric</th>
          <th className="text-right py-1 font-medium">{aName}</th>
          <th className="text-right py-1 font-medium">{bName}</th>
          <th className="text-right py-1 font-medium w-24">Δ</th>
        </tr>
      </thead>
      <tbody>
        {carriers.map(c => {
          const A = ecA[c]
          const B = ecB[c]
          // Each carrier gets four rows (revenue, opex, capex, LCOE), with
          // the carrier label on the first row only (rowSpan) so the table
          // reads like grouped sections without losing the per-cell delta.
          // Per-carrier rows: Revenue + 4-way OPEX split + total OPEX +
          // CAPEX + Dispatch + LCOE. The 4 split rows let users see WHAT
          // they paid for per carrier (gas: mostly gen cost; battery: gen
          // + charge cost; solar: curtailment cost; dc_highT_heat:
          // lost-load cost). Sub-rows render only when ANY scenario has a
          // non-trivial value for that bucket — keeps the table compact
          // on carriers without storage / curtailment / lost-load.
          type Row = { label: string; va: number; vb: number; fmt: (n: number) => string; invert: boolean; sub?: boolean }
          const splitRows: Row[] = [
            { label: 'Gen cost',           va: readPV(A?.gen_cost_meur, period),            vb: readPV(B?.gen_cost_meur, period),            fmt: fmtMEur, invert: true, sub: true },
            { label: 'Storage charge cost', va: readPV(A?.storage_charge_cost_meur, period), vb: readPV(B?.storage_charge_cost_meur, period), fmt: fmtMEur, invert: true, sub: true },
            { label: 'Curtailment cost',   va: readPV(A?.curtailment_cost_meur, period),    vb: readPV(B?.curtailment_cost_meur, period),    fmt: fmtMEur, invert: true, sub: true },
            { label: 'Lost-load cost',     va: readPV(A?.lost_load_cost_meur, period),      vb: readPV(B?.lost_load_cost_meur, period),      fmt: fmtMEur, invert: true, sub: true },
          ]
          const visibleSplits = splitRows.filter(r => Math.abs(r.va) > 1e-6 || Math.abs(r.vb) > 1e-6)
          const rows: Row[] = [
            { label: 'Revenue',  va: readPV(A?.revenue_meur, period), vb: readPV(B?.revenue_meur, period), fmt: fmtMEur,    invert: false },
            { label: 'OPEX (total)', va: readPV(A?.opex_meur, period), vb: readPV(B?.opex_meur, period), fmt: fmtMEur, invert: true },
            ...visibleSplits,
            { label: 'CAPEX',    va: readPV(A?.capex_meur,   period), vb: readPV(B?.capex_meur,   period), fmt: fmtMEur,    invert: true  },
            { label: 'Dispatch', va: readPV(A?.dispatch_gwh, period), vb: readPV(B?.dispatch_gwh, period), fmt: v => `${v.toFixed(1)} GWh`, invert: false },
            { label: 'LCOE',     va: readPV(A?.lcoe_eur_per_mwh, period), vb: readPV(B?.lcoe_eur_per_mwh, period), fmt: fmtEurMwh, invert: true  },
          ]
          return rows.map((r, i) => (
            <tr key={`${c}-${r.label}`} className="border-b border-border/40">
              {i === 0 && (
                <td rowSpan={rows.length} className="py-1 text-text align-top font-medium">{carrierDisplayName(c)}</td>
              )}
              <td className={`py-1 text-right ${r.sub ? 'text-muted/70 pr-1 pl-3 italic' : 'text-muted'}`}>{r.label}</td>
              <td className={`py-1 text-right font-mono ${r.sub ? 'text-text/80' : 'text-text'}`}>
                {availableA ? r.fmt(r.va) : <span className="text-muted" title="This scenario's figures could not be resolved">{COST_UNAVAILABLE}</span>}
              </td>
              <td className={`py-1 text-right font-mono ${r.sub ? 'text-text/80' : 'text-text'}`}>
                {availableB ? r.fmt(r.vb) : <span className="text-muted" title="This scenario's figures could not be resolved">{COST_UNAVAILABLE}</span>}
              </td>
              <td className="py-1 text-right font-mono">
                {(availableA && availableB)
                  ? <Delta v={r.vb - r.va} fmt={r.fmt} invert={r.invert} />
                  : <span className="text-muted" title="Δ undefined — a scenario is unresolved">—</span>}
              </td>
            </tr>
          ))
        })}
      </tbody>
    </table>
  )
}

// Per-asset LCOH table — one row per Link (electrolyser / heat pump / P2X)
// that has non-trivial dispatch in either project. Reads the new
// per_asset_lcoh field added by the backend Compare View phase. The table
// pairs A vs B per asset name so a vintage electrolyser can be compared
// side-by-side; orphans (asset in A but not B, or vice-versa) still
// render with — placeholders.
function AssetLCOHTable({
  aName, bName, rowsA, rowsB, period, fmtMEur, fmtEurMwh, selectedCarrier,
}: {
  aName: string
  bName: string
  rowsA: AssetLCOHEntry[]
  rowsB: AssetLCOHEntry[]
  period: PeriodChoice
  fmtMEur: (n: number) => string
  fmtEurMwh: (n: number) => string
  selectedCarrier?: string | null
}) {
  // Pair by asset name so the same electrolyser surfaces once with both
  // scenarios on adjacent columns. Unioning + sorting by horizon LCOH on
  // the A side (falling back to B) puts the cheapest assets first —
  // matches the user's intent to scan for the most cost-effective unit.
  const merged = useMemo(() => {
    const mapA = new Map(rowsA.map(r => [r.name, r]))
    const mapB = new Map(rowsB.map(r => [r.name, r]))
    const names = new Set<string>([...mapA.keys(), ...mapB.keys()])
    const out: Array<{ name: string; carrier: string | null; A: AssetLCOHEntry | undefined; B: AssetLCOHEntry | undefined }> = []
    for (const name of names) {
      const A = mapA.get(name)
      const B = mapB.get(name)
      out.push({ name, carrier: A?.carrier ?? B?.carrier ?? null, A, B })
    }
    // Sort by min available LCOH (cheapest first). NaN / 0 → infinity so
    // orphans / zero-dispatch rows sink to the bottom.
    out.sort((x, y) => {
      const xv = Math.min(
        readPV(x.A?.lcoh_eur_per_mwh, period) || Number.POSITIVE_INFINITY,
        readPV(x.B?.lcoh_eur_per_mwh, period) || Number.POSITIVE_INFINITY,
      )
      const yv = Math.min(
        readPV(y.A?.lcoh_eur_per_mwh, period) || Number.POSITIVE_INFINITY,
        readPV(y.B?.lcoh_eur_per_mwh, period) || Number.POSITIVE_INFINITY,
      )
      return xv - yv
    })
    return out
  }, [rowsA, rowsB, period])

  // Filter by canonical carrier. Each row's raw `carrier` runs through
  // canonicaliseCarrier so a "h2" row matches the filter chip even when
  // the chip was rendered from a "hydrogen" upstream carrier in the
  // per-carrier list.
  const filtered = useMemo(() => {
    if (selectedCarrier == null) return merged
    return merged.filter(r => canonicaliseCarrier('Link', r.carrier) === selectedCarrier)
  }, [merged, selectedCarrier])

  if (filtered.length === 0) {
    return <p className="text-[11px] text-muted py-2">No assets match the selected carrier.</p>
  }

  const fmtGwh = (v: number) => `${v.toFixed(1)} GWh`
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-border text-muted">
          <th className="text-left  py-1 font-medium">Asset</th>
          <th className="text-left  py-1 font-medium">Carrier</th>
          <th className="text-right py-1 font-medium">{aName} output</th>
          <th className="text-right py-1 font-medium">{bName} output</th>
          <th className="text-right py-1 font-medium">{aName} LCOH</th>
          <th className="text-right py-1 font-medium">{bName} LCOH</th>
          <th className="text-right py-1 font-medium w-24">Δ LCOH</th>
        </tr>
      </thead>
      <tbody>
        {filtered.map(r => {
          const oA = readPV(r.A?.output_gwh, period)
          const oB = readPV(r.B?.output_gwh, period)
          const lA = readPV(r.A?.lcoh_eur_per_mwh, period)
          const lB = readPV(r.B?.lcoh_eur_per_mwh, period)
          const dash = '—'
          return (
            <tr key={r.name} className="border-b border-border/40"
              title={`Asset CAPEX (A): ${fmtMEur(readPV(r.A?.capex_meur, period))}\nAsset OPEX  (A): ${fmtMEur(readPV(r.A?.opex_meur, period))}\nInput cost  (A): ${fmtMEur(readPV(r.A?.input_cost_meur, period))}\n— vs —\nAsset CAPEX (B): ${fmtMEur(readPV(r.B?.capex_meur, period))}\nAsset OPEX  (B): ${fmtMEur(readPV(r.B?.opex_meur, period))}\nInput cost  (B): ${fmtMEur(readPV(r.B?.input_cost_meur, period))}`}
            >
              <td className="py-1 text-text font-mono">{r.name}</td>
              <td className="py-1 text-muted">{r.carrier ? carrierDisplayName(canonicaliseCarrier('Link', r.carrier)) : dash}</td>
              <td className="py-1 text-right font-mono text-text">{r.A ? fmtGwh(oA) : dash}</td>
              <td className="py-1 text-right font-mono text-text">{r.B ? fmtGwh(oB) : dash}</td>
              <td className="py-1 text-right font-mono text-text">{r.A && lA > 0 ? fmtEurMwh(lA) : dash}</td>
              <td className="py-1 text-right font-mono text-text">{r.B && lB > 0 ? fmtEurMwh(lB) : dash}</td>
              <td className="py-1 text-right font-mono">
                {r.A && r.B && lA > 0 && lB > 0
                  ? <Delta v={lB - lA} fmt={fmtEurMwh} invert />
                  : <span className="text-muted">{dash}</span>}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ── Curtailment tab (Phase 4) ────────────────────────────────────────────────
// Per-carrier renewable curtailment in GWh + system rate. Storage isn't
// considered a curtailment source (no primary-energy resource); the backend
// already filters generators with a non-trivial p_max_pu profile so the
// numbers stay meaningful.

function CurtailmentTab({ a, b }: { a: string; b: string }) {
  const qA = useQuery({ queryKey: ['results-summary', a], queryFn: () => projectsApi.resultsSummary(a), staleTime: 30_000 })
  const qB = useQuery({ queryKey: ['results-summary', b], queryFn: () => projectsApi.resultsSummary(b), staleTime: 30_000 })
  const [period, setPeriod] = useState<PeriodChoice>('all')
  const periods = useMemo(() => {
    const pa = qA.data?.periods ?? []
    const pb = qB.data?.periods ?? []
    return [...new Set<number>([...pa, ...pb])].sort((x, y) => x - y)
  }, [qA.data?.periods, qB.data?.periods])

  // Curtailment is renewable-generator scoped — canonicalise as Generator
  // for case-insensitive spelling merge.
  const curtMaps = useMemo(() => ({
    gwhA: canonicaliseCarrierMap(qA.data?.curtailment?.by_carrier_gwh ?? {}, 'Generator'),
    gwhB: canonicaliseCarrierMap(qB.data?.curtailment?.by_carrier_gwh ?? {}, 'Generator'),
    rateA: canonicaliseCarrierMap(qA.data?.curtailment?.rate_pct_by_carrier ?? {}, 'Generator'),
    rateB: canonicaliseCarrierMap(qB.data?.curtailment?.rate_pct_by_carrier ?? {}, 'Generator'),
  }), [qA.data?.curtailment, qB.data?.curtailment])

  const availableCarriers = useMemo(
    () => unionCarriers(curtMaps.gwhA, curtMaps.gwhB),
    [curtMaps.gwhA, curtMaps.gwhB],
  )
  const filter = useCarrierFilter({ availableCarriers, storageKey: 'compare:curtailment:carrier' })

  if (qA.isLoading || qB.isLoading) return <LoadingSpinner />
  if (qA.isError || qB.isError) return <ErrorBanner which={qA.isError ? a : b} />
  const sa = qA.data!
  const sb = qB.data!
  if (!sa.has_solve || !sb.has_solve) {
    return <UnsolvedBanner unsolved={[!sa.has_solve && sa.project, !sb.has_solve && sb.project].filter(Boolean) as string[]} />
  }

  const cA = sa.curtailment
  const cB = sb.curtailment
  const hasAnyA = cA && Object.keys(cA.by_carrier_gwh).length > 0
  const hasAnyB = cB && Object.keys(cB.by_carrier_gwh).length > 0
  if (!hasAnyA && !hasAnyB) {
    return <p className="text-[11px] text-muted py-2">No curtailment data — neither project has renewable generators with a time-varying p_max_pu profile.</p>
  }
  // A ONE-SIDED absence is not zero. `readPV` maps a null payload to 0, so a
  // project that never reported curtailment rendered identically to one that
  // curtails exactly nothing — and against a side with a real rate the delta
  // read a fabricated -100.00% ("B eliminated all curtailment"), which is not
  // information the payload contains. Withhold the comparison and name the
  // side that has no data. EmissionsTab already bails on either side missing.
  if (!hasAnyA || !hasAnyB) {
    const missing = [!hasAnyA && sa.project, !hasAnyB && sb.project].filter(Boolean) as string[]
    return (
      <p className="text-[11px] text-muted py-2">
        No curtailment data for {missing.join(' and ')} — nothing to compare against.
        This side may be unresolved, or may genuinely curtail nothing; either way there
        is no matching entry to pair it with, so no rate or delta is shown.
      </p>
    )
  }

  const fmtGwh = (v: number) => `${v.toFixed(1)} GWh`
  const fmtPct = (v: number) => `${v.toFixed(2)}%`
  // Whether this side's curtailment block resolved at all — see
  // CapacityTab's matching computation for the full ADR-0001 rationale.
  // In practice the `!hasAnyA || !hasAnyB` bail above already prevents a
  // mixed available/unavailable pair from reaching this render, since
  // available=false implies by_carrier_gwh={} (hasAnyX=false too) — but
  // computing it explicitly keeps ABBarChart/ABTable correct on their own
  // terms rather than relying on this tab's incidental early return.
  const availableA = cA?.available ?? false
  const availableB = cB?.available ?? false

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4 flex-wrap">
        <PeriodSelector periods={periods} value={period} onChange={setPeriod} />
        <CarrierFilter {...bindCarrierFilter(filter, availableCarriers)} label="Carrier" />
      </div>

      <Section title="Total curtailed energy" subtitle={periodLabel(period) + ' — renewable output the LP rejected (cost-feasible but not dispatched)'}>
        <ABKpiPair
          aName={sa.project}
          bName={sb.project}
          aValue={readPV(cA?.total_gwh, period)}
          bValue={readPV(cB?.total_gwh, period)}
          unit="GWh"
          fmt={fmtGwh}
          deltaIsImprovementWhenNegative
          availableA={availableA}
          availableB={availableB}
        />
      </Section>

      <Section title="System curtailment rate" subtitle={periodLabel(period) + ' — curtailed / (curtailed + dispatched), aggregated across all renewable carriers'}>
        <ABKpiPair
          aName={sa.project}
          bName={sb.project}
          aValue={readPV(cA?.system_rate_pct, period)}
          bValue={readPV(cB?.system_rate_pct, period)}
          unit="%"
          fmt={fmtPct}
          deltaIsImprovementWhenNegative
          availableA={availableA}
          availableB={availableB}
        />
      </Section>

      <Section title="Curtailment by carrier (GWh)" subtitle={periodLabel(period)}>
        <ABBarChart
          aName={sa.project}
          bName={sb.project}
          mapA={curtMaps.gwhA}
          mapB={curtMaps.gwhB}
          period={period}
          unit="GWh"
          selectedCarrier={filter.selectedCarrier}
          availableA={availableA}
          availableB={availableB}
        />
        <ABTable
          aName={sa.project}
          bName={sb.project}
          mapA={curtMaps.gwhA}
          mapB={curtMaps.gwhB}
          period={period}
          unit="GWh"
          fmt={fmtGwh}
          totalsRow
          selectedCarrier={filter.selectedCarrier}
          availableA={availableA}
          availableB={availableB}
        />
      </Section>

      <Section title="Curtailment rate by carrier (%)" subtitle={periodLabel(period) + ' — relative inefficiency per carrier, independent of fleet size'}>
        <ABBarChart
          aName={sa.project}
          bName={sb.project}
          mapA={curtMaps.rateA}
          mapB={curtMaps.rateB}
          period={period}
          unit="%"
          selectedCarrier={filter.selectedCarrier}
          availableA={availableA}
          availableB={availableB}
        />
        <ABTable
          aName={sa.project}
          bName={sb.project}
          mapA={curtMaps.rateA}
          mapB={curtMaps.rateB}
          period={period}
          unit="%"
          fmt={fmtPct}
          selectedCarrier={filter.selectedCarrier}
          availableA={availableA}
          availableB={availableB}
        />
      </Section>
    </div>
  )
}

// ── Lost-load tab (Phase 4) ──────────────────────────────────────────────────
// Unserved demand via the VOLL slack mechanism: when the LP can't meet load
// at any price ≤ VOLL, slack generators absorb the gap. Captured in
// `_state["last_lost_load"]` at solve time and persisted into
// results_state.pkl on save. Per-bus table shows the worst-affected nodes.

// Exported solely so CompareView.availability.test.tsx can render it in
// isolation (Task 7 follow-up) — same rationale as EconomicsTab's export.
export function LostLoadTab({ a, b }: { a: string; b: string }) {
  const qA = useQuery({ queryKey: ['results-summary', a], queryFn: () => projectsApi.resultsSummary(a), staleTime: 30_000 })
  const qB = useQuery({ queryKey: ['results-summary', b], queryFn: () => projectsApi.resultsSummary(b), staleTime: 30_000 })
  const [period, setPeriod] = useState<PeriodChoice>('all')
  const periods = useMemo(() => {
    const pa = qA.data?.periods ?? []
    const pb = qB.data?.periods ?? []
    return [...new Set<number>([...pa, ...pb])].sort((x, y) => x - y)
  }, [qA.data?.periods, qB.data?.periods])

  if (qA.isLoading || qB.isLoading) return <LoadingSpinner />
  if (qA.isError || qB.isError) return <ErrorBanner which={qA.isError ? a : b} />
  const sa = qA.data!
  const sb = qB.data!
  if (!sa.has_solve || !sb.has_solve) {
    return <UnsolvedBanner unsolved={[!sa.has_solve && sa.project, !sb.has_solve && sb.project].filter(Boolean) as string[]} />
  }

  const llA = sa.lost_load
  const llB = sb.lost_load
  // Deliberately keyed on `captured`, NOT `available` — a solver that ran
  // and shed nothing (captured=true, available=false) has a REAL measured
  // zero and must render `0.0 MWh`; only an unread capture (captured=false)
  // is truly unavailable. Wiring `available` here would relabel that happy
  // path as broken (see task-7-brief.md / ADR-0001).
  const availableA = llA?.captured ?? false
  const availableB = llB?.captured ?? false

  // Three-state gate, keyed on `captured` (NOT `available`, which no longer
  // means "no shedding" post Task 7 — see LostLoadComparison in api/types.ts).
  //   1. Neither side's capture was read — we know nothing about either;
  //      render the plain unavailable marker, never a prose claim about
  //      what the LP found (review finding C1: the old `!available &&
  //      !available` gate fired this exact banner, asserting "the LP found
  //      a feasible dispatch" for a side whose capture was never opened).
  //   2. Both captures WERE read and both are confirmed zero — the genuine
  //      "neither shed load" happy path; safe to make the prose claim.
  //   3. Anything else (mixed captured/uncaptured, or at least one side has
  //      real shedding) falls through to the KPI section below, whose
  //      captured-keyed availableA/availableB already render the correct
  //      per-side state for each half independently.
  if (!availableA && !availableB) {
    return <p className="text-[11px] text-muted py-2">{COST_UNAVAILABLE}</p>
  }
  if (availableA && availableB && !llA?.available && !llB?.available) {
    return (
      <div className="space-y-2 py-4">
        <p className="text-[11px] text-text">Neither project shed load.</p>
        <p className="text-[10px] text-muted">
          Lost-load data appears only when the solver runs with <code className="font-mono">voll &gt; 0</code> and the LP can't meet demand — usually a sign of insufficient capacity, missing transmission, or aggressive emission caps. For these scenarios the LP found a feasible dispatch without unserved demand.
        </p>
      </div>
    )
  }

  const fmtMwh = (v: number) => `${v.toFixed(1)} MWh`
  const fmtMEur = (v: number) => `${v.toFixed(2)} M€`

  return (
    <div className="space-y-4">
      <PeriodSelector periods={periods} value={period} onChange={setPeriod} />

      <Section title="Total lost load (MWh)" subtitle={periodLabel(period) + ' — demand the LP could not meet'}>
        <ABKpiPair
          aName={sa.project}
          bName={sb.project}
          aValue={readPV(llA?.total_mwh, period)}
          bValue={readPV(llB?.total_mwh, period)}
          unit="MWh"
          fmt={fmtMwh}
          deltaIsImprovementWhenNegative
          availableA={availableA}
          availableB={availableB}
        />
      </Section>

      <Section title="VOLL cost (M€)" subtitle={periodLabel(period) + ' — total_mwh × VOLL price the solver used'}>
        <ABKpiPair
          aName={sa.project}
          bName={sb.project}
          aValue={readPV(llA?.total_cost_meur, period)}
          bValue={readPV(llB?.total_cost_meur, period)}
          unit="M€"
          fmt={fmtMEur}
          deltaIsImprovementWhenNegative
          availableA={availableA}
          availableB={availableB}
        />
      </Section>

      <Section title="VOLL price" subtitle="€/MWh — penalty rate the LP paid for unserved demand">
        {/* Review finding C2: this panel was ungated — an unread capture's
            `voll_eur_per_mwh` defaults to 0.0, which rendered `0 €/MWh`
            beside the marker the KPIs above already show for the same
            side, self-contradicting it (asserting a real zero-VOLL solve
            for a project whose capture couldn't even be opened). Gated on
            the same captured-keyed availableA/availableB as everything
            else on this tab. */}
        <div className="grid grid-cols-2 gap-3">
          <div className="border border-border rounded p-2">
            <div className="text-[10px] text-muted">{sa.project}</div>
            <div className="text-sm font-mono text-text">
              {availableA
                ? `${(llA?.voll_eur_per_mwh ?? 0).toFixed(0)} €/MWh`
                : <span className="text-muted" title="This scenario's figures could not be resolved">{COST_UNAVAILABLE}</span>}
            </div>
          </div>
          <div className="border border-border rounded p-2">
            <div className="text-[10px] text-muted">{sb.project}</div>
            <div className="text-sm font-mono text-text">
              {availableB
                ? `${(llB?.voll_eur_per_mwh ?? 0).toFixed(0)} €/MWh`
                : <span className="text-muted" title="This scenario's figures could not be resolved">{COST_UNAVAILABLE}</span>}
            </div>
          </div>
        </div>
      </Section>

      {((llA?.by_carrier?.length ?? 0) > 0 || (llB?.by_carrier?.length ?? 0) > 0) && (
        <Section
          title="Lost load by bus carrier"
          subtitle={periodLabel(period) + ' — answers "is shedding concentrated on the electrical side, or on heat / H₂?". Sums across all buses tagged with each carrier.'}
        >
          <LostLoadByCarrierTable
            aName={sa.project}
            bName={sb.project}
            byCarrierA={llA?.by_carrier ?? []}
            byCarrierB={llB?.by_carrier ?? []}
            period={period}
            fmtMwh={fmtMwh}
            fmtMEur={fmtMEur}
            availableA={availableA}
            availableB={availableB}
          />
        </Section>
      )}

      <Section title="Per-bus lost-load breakdown" subtitle={periodLabel(period) + ' — top affected buses (by horizon-wide MWh)'}>
        <LostLoadBusTable
          aName={sa.project}
          bName={sb.project}
          busA={llA?.by_bus ?? []}
          busB={llB?.by_bus ?? []}
          period={period}
          fmtMwh={fmtMwh}
          fmtMEur={fmtMEur}
          availableA={availableA}
          availableB={availableB}
        />
      </Section>
    </div>
  )
}

// Per-bus-carrier lost-load roll-up. Sector-coupled networks shed
// load on multiple carriers and the user wants to scan which carrier
// is shedding most without scrolling the per-bus list. Carrier keys
// are canonicalised so 'AC' / 'electricity' / 'electrical' all merge.
function LostLoadByCarrierTable({
  aName, bName, byCarrierA, byCarrierB, period, fmtMwh, fmtMEur,
  availableA = true, availableB = true,
}: {
  aName: string
  bName: string
  byCarrierA: LostLoadByCarrier[]
  byCarrierB: LostLoadByCarrier[]
  period: PeriodChoice
  fmtMwh: (n: number) => string
  fmtMEur: (n: number) => string
  // Whether this side's capture was READ AT ALL — keyed on `captured`, not
  // `available`, by the caller (see LostLoadTab / ADR-0001). Same priority
  // as ABTable/StorageUnitTable: an unread side outranks "carrier absent
  // from this scenario," which outranks the real value. Defaults to true so
  // any other future call site keeps compiling.
  availableA?: boolean
  availableB?: boolean
}) {
  // Defensive canonicalisation in case backend emits raw carrier spellings
  // from a legacy save file. Merge happens by canonical key so duplicate
  // rows fold together with energy + cost summed.
  const canon = (rows: LostLoadByCarrier[]): Record<string, LostLoadByCarrier> => {
    const out: Record<string, LostLoadByCarrier> = {}
    for (const r of rows) {
      const key = canonicaliseCarrier('Load', r.carrier)
      const existing = out[key]
      if (!existing) {
        out[key] = {
          carrier: key,
          bus_count: r.bus_count,
          energy_mwh: { total: r.energy_mwh.total, by_period: { ...r.energy_mwh.by_period } },
          cost_meur:  { total: r.cost_meur.total,  by_period: { ...r.cost_meur.by_period } },
        }
      } else {
        existing.bus_count += r.bus_count
        existing.energy_mwh.total += r.energy_mwh.total
        existing.cost_meur.total  += r.cost_meur.total
        for (const [p, v] of Object.entries(r.energy_mwh.by_period)) {
          existing.energy_mwh.by_period[p] = (existing.energy_mwh.by_period[p] ?? 0) + v
        }
        for (const [p, v] of Object.entries(r.cost_meur.by_period)) {
          existing.cost_meur.by_period[p] = (existing.cost_meur.by_period[p] ?? 0) + v
        }
      }
    }
    return out
  }
  const canonA = canon(byCarrierA)
  const canonB = canon(byCarrierB)
  const carriers = [...new Set([...Object.keys(canonA), ...Object.keys(canonB)])]
    .sort((x, y) => {
      const xv = Math.max(readPV(canonA[x]?.energy_mwh, period), readPV(canonB[x]?.energy_mwh, period))
      const yv = Math.max(readPV(canonA[y]?.energy_mwh, period), readPV(canonB[y]?.energy_mwh, period))
      return yv - xv  // worst-first
    })
  if (carriers.length === 0) {
    return <p className="text-[11px] text-muted">No per-carrier breakdown for the selected period.</p>
  }
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-border text-muted">
          <th className="text-left  py-1 font-medium">Carrier</th>
          <th className="text-right py-1 font-medium">Buses</th>
          <th className="text-right py-1 font-medium">{aName} MWh</th>
          <th className="text-right py-1 font-medium">{bName} MWh</th>
          <th className="text-right py-1 font-medium">{aName} M€</th>
          <th className="text-right py-1 font-medium">{bName} M€</th>
          <th className="text-right py-1 font-medium w-20">Δ MWh</th>
        </tr>
      </thead>
      <tbody>
        {carriers.map(c => {
          const A = canonA[c]
          const B = canonB[c]
          const buses = Math.max(A?.bus_count ?? 0, B?.bus_count ?? 0)
          const ae = readPV(A?.energy_mwh, period)
          const be = readPV(B?.energy_mwh, period)
          const ac = readPV(A?.cost_meur, period)
          const bc = readPV(B?.cost_meur, period)
          const unavailable = <span className="text-muted" title="This scenario's figures could not be resolved">{COST_UNAVAILABLE}</span>
          const dash = <span className="text-muted" title="Carrier not present in this scenario">—</span>
          return (
            <tr key={c} className="border-b border-border/40">
              <td className="py-1 text-text">{carrierDisplayName(c)}</td>
              <td className="py-1 text-right font-mono text-muted">{buses || '—'}</td>
              <td className="py-1 text-right font-mono text-text">{!availableA ? unavailable : A ? fmtMwh(ae) : dash}</td>
              <td className="py-1 text-right font-mono text-text">{!availableB ? unavailable : B ? fmtMwh(be) : dash}</td>
              <td className="py-1 text-right font-mono text-text">{!availableA ? unavailable : A ? fmtMEur(ac) : dash}</td>
              <td className="py-1 text-right font-mono text-text">{!availableB ? unavailable : B ? fmtMEur(bc) : dash}</td>
              <td className="py-1 text-right font-mono">
                {(availableA && availableB)
                  ? <Delta v={be - ae} fmt={fmtMwh} invert />
                  : <span className="text-muted" title="Δ undefined — a scenario is unresolved">—</span>}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function LostLoadBusTable({
  aName, bName, busA, busB, period, fmtMwh, fmtMEur,
  availableA = true, availableB = true,
}: {
  aName: string
  bName: string
  busA: LostLoadBus[]
  busB: LostLoadBus[]
  period: PeriodChoice
  fmtMwh: (n: number) => string
  fmtMEur: (n: number) => string
  // Same third state as LostLoadByCarrierTable above — keyed on `captured`
  // by the caller. Defaults to true so any other future call site keeps
  // compiling.
  availableA?: boolean
  availableB?: boolean
}) {
  // Union the bus lists from both sides so we can show a row per bus that
  // appears in either scenario. Order by max(A, B) energy desc.
  const byBusA = new Map(busA.map(r => [r.bus, r]))
  const byBusB = new Map(busB.map(r => [r.bus, r]))
  const allBuses = [...new Set<string>([...byBusA.keys(), ...byBusB.keys()])]
  const sorted = allBuses
    .map(name => ({
      name,
      a: byBusA.get(name),
      b: byBusB.get(name),
      score: Math.max(readPV(byBusA.get(name)?.energy_mwh, period), readPV(byBusB.get(name)?.energy_mwh, period)),
    }))
    .sort((x, y) => y.score - x.score)
  if (sorted.length === 0) {
    return <p className="text-[11px] text-muted">No per-bus breakdown for the selected period.</p>
  }
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-border text-muted">
          <th className="text-left  py-1 font-medium">Bus</th>
          <th className="text-right py-1 font-medium">{aName} MWh</th>
          <th className="text-right py-1 font-medium">{bName} MWh</th>
          <th className="text-right py-1 font-medium">{aName} M€</th>
          <th className="text-right py-1 font-medium">{bName} M€</th>
          <th className="text-right py-1 font-medium w-20">Δ MWh</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map(r => {
          const ae = readPV(r.a?.energy_mwh, period)
          const be = readPV(r.b?.energy_mwh, period)
          const ac = readPV(r.a?.cost_meur,  period)
          const bc = readPV(r.b?.cost_meur,  period)
          const unavailable = <span className="text-muted" title="This scenario's figures could not be resolved">{COST_UNAVAILABLE}</span>
          return (
            <tr key={r.name} className="border-b border-border/40">
              <td className="py-1 text-text font-mono">{r.name}</td>
              <td className="py-1 text-right font-mono text-text">{availableA ? fmtMwh(ae) : unavailable}</td>
              <td className="py-1 text-right font-mono text-text">{availableB ? fmtMwh(be) : unavailable}</td>
              <td className="py-1 text-right font-mono text-text">{availableA ? fmtMEur(ac) : unavailable}</td>
              <td className="py-1 text-right font-mono text-text">{availableB ? fmtMEur(bc) : unavailable}</td>
              <td className="py-1 text-right font-mono">
                {(availableA && availableB)
                  ? <Delta v={be - ae} fmt={fmtMwh} invert />
                  : <span className="text-muted" title="Δ undefined — a scenario is unresolved">—</span>}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ── Storage cycling tab (Phase 4) ────────────────────────────────────────────
// Per-storage-unit equivalent full cycles + a carrier rollup. Replaces the
// inline section in DispatchTab — gives room for per-unit detail (name,
// p_nom, energy capacity, throughput, cycles) which is what the user needs
// to see if a particular battery is being overworked across scenarios.

// Exported solely so CompareView.availability.test.tsx can render it in
// isolation (Task 5 follow-up) — every other tab in this file stays
// module-private. Same rationale as EconomicsTab's export above.
export function StorageCyclingTab({ a, b }: { a: string; b: string }) {
  const qA = useQuery({ queryKey: ['results-summary', a], queryFn: () => projectsApi.resultsSummary(a), staleTime: 30_000 })
  const qB = useQuery({ queryKey: ['results-summary', b], queryFn: () => projectsApi.resultsSummary(b), staleTime: 30_000 })
  const [period, setPeriod] = useState<PeriodChoice>('all')
  const periods = useMemo(() => {
    const pa = qA.data?.periods ?? []
    const pb = qB.data?.periods ?? []
    return [...new Set<number>([...pa, ...pb])].sort((x, y) => x - y)
  }, [qA.data?.periods, qB.data?.periods])

  // Storage carriers go through StorageUnit aliasing: battery / BESS /
  // Li-Ion all collapse into 'battery'; H2 / hydrogen → 'hydrogen'; etc.
  const cycMaps = useMemo(() => ({
    A: canonicaliseCarrierMap(qA.data?.storage_cycling?.cycles_by_carrier ?? {}, 'StorageUnit'),
    B: canonicaliseCarrierMap(qB.data?.storage_cycling?.cycles_by_carrier ?? {}, 'StorageUnit'),
  }), [qA.data?.storage_cycling?.cycles_by_carrier, qB.data?.storage_cycling?.cycles_by_carrier])

  const availableCarriers = useMemo(
    () => unionCarriers(cycMaps.A, cycMaps.B),
    [cycMaps.A, cycMaps.B],
  )
  const filter = useCarrierFilter({ availableCarriers, storageKey: 'compare:storage-cycling:carrier' })

  if (qA.isLoading || qB.isLoading) return <LoadingSpinner />
  if (qA.isError || qB.isError) return <ErrorBanner which={qA.isError ? a : b} />
  const sa = qA.data!
  const sb = qB.data!
  if (!sa.has_solve || !sb.has_solve) {
    return <UnsolvedBanner unsolved={[!sa.has_solve && sa.project, !sb.has_solve && sb.project].filter(Boolean) as string[]} />
  }

  const scA = sa.storage_cycling
  const scB = sb.storage_cycling
  const hasUnits = (scA?.by_unit.length ?? 0) > 0 || (scB?.by_unit.length ?? 0) > 0
  if (!hasUnits) {
    return <p className="text-[11px] text-muted py-2">No storage units with optimised capacity in either project.</p>
  }

  const fmtCyc = (v: number) => `${v.toFixed(1)} cyc`
  const fmtMwh = (v: number) => `${v.toFixed(1)} MWh`
  const fmtMw = (v: number) => `${v.toFixed(1)} MW`
  // Whether this side's storage-cycling block resolved at all — see
  // CapacityTab's matching computation for the full ADR-0001 rationale.
  const availableA = scA?.available ?? false
  const availableB = scB?.available ?? false

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4 flex-wrap">
        <PeriodSelector periods={periods} value={period} onChange={setPeriod} />
        <CarrierFilter {...bindCarrierFilter(filter, availableCarriers)} label="Storage" />
      </div>

      <Section
        title="Equivalent full cycles by carrier"
        subtitle={periodLabel(period) + ' — Σ |p| × Δt ÷ (2 × Σ p_nom_opt × max_hours). One cycle = a full charge + discharge.'}
      >
        <ABBarChart
          aName={sa.project}
          bName={sb.project}
          mapA={cycMaps.A}
          mapB={cycMaps.B}
          period={period}
          unit="cycles"
          selectedCarrier={filter.selectedCarrier}
          availableA={availableA}
          availableB={availableB}
        />
        <ABTable
          aName={sa.project}
          bName={sb.project}
          mapA={cycMaps.A}
          mapB={cycMaps.B}
          period={period}
          unit="cycles"
          fmt={fmtCyc}
          selectedCarrier={filter.selectedCarrier}
          availableA={availableA}
          availableB={availableB}
        />
      </Section>

      <Section title="Per-unit cycling detail" subtitle={periodLabel(period) + ' — sorted by horizon-wide cycle count (descending)'}>
        <StorageUnitTable
          aName={sa.project}
          bName={sb.project}
          unitsA={scA?.by_unit ?? []}
          unitsB={scB?.by_unit ?? []}
          period={period}
          fmtMw={fmtMw}
          fmtMwh={fmtMwh}
          fmtCyc={fmtCyc}
          selectedCarrier={filter.selectedCarrier}
          availableA={availableA}
          availableB={availableB}
        />
      </Section>
    </div>
  )
}

function StorageUnitTable({
  aName, bName, unitsA, unitsB, period, fmtMw, fmtMwh, fmtCyc, selectedCarrier,
  availableA = true, availableB = true,
}: {
  aName: string
  bName: string
  unitsA: StorageUnitCycles[]
  unitsB: StorageUnitCycles[]
  period: PeriodChoice
  fmtMw: (n: number) => string
  fmtMwh: (n: number) => string
  fmtCyc: (n: number) => string
  selectedCarrier?: string | null
  // Whether this side's storage-cycling block resolved at all — same third
  // state as ABTable/ABKpiPair (Task 5; see ADR-0001). An unresolved side's
  // `by_unit` is `[]`, so every unit only ever appears in the OTHER side's
  // map — `r.a`/`r.b` below is already `undefined` for it. Without this
  // guard `r.a?.p_nom_mw ?? 0` reads that absence as a real zero, plus a
  // signed Δ against the resolved side's real cycle count (C2). Defaults
  // to true so pre-existing call sites keep compiling.
  availableA?: boolean
  availableB?: boolean
}) {
  const byA = new Map(unitsA.map(u => [u.name, u]))
  const byB = new Map(unitsB.map(u => [u.name, u]))
  const all = [...new Set<string>([...byA.keys(), ...byB.keys()])]
  const sorted = all
    .map(name => {
      const a = byA.get(name)
      const b = byB.get(name)
      // Carrier filter check uses storage-unit canonicalisation so 'BESS' /
      // 'Li-Ion' rows show up under the 'battery' chip the user picked.
      const carrierRaw = a?.carrier ?? b?.carrier ?? ''
      const matchesCarrier = selectedCarrier == null
        ? true
        : canonicaliseCarrier('StorageUnit', carrierRaw) === selectedCarrier
      return {
        name,
        a,
        b,
        matchesCarrier,
        // Order by max cycles across scenarios so the worst-worked unit is
        // first — same convention as the line-loading table.
        score: Math.max(readPV(a?.cycles, period), readPV(b?.cycles, period)),
      }
    })
    .filter(r => r.matchesCarrier)
    .sort((x, y) => y.score - x.score)
  if (sorted.length === 0) {
    return <p className="text-[11px] text-muted">No per-unit cycling data for the selected period.</p>
  }
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-border text-muted">
          <th className="text-left  py-1 font-medium">Unit</th>
          <th className="text-right py-1 font-medium">Carrier</th>
          <th className="text-right py-1 font-medium">{aName} p_nom</th>
          <th className="text-right py-1 font-medium">{bName} p_nom</th>
          <th className="text-right py-1 font-medium">{aName} energy</th>
          <th className="text-right py-1 font-medium">{bName} energy</th>
          <th className="text-right py-1 font-medium">{aName} cycles</th>
          <th className="text-right py-1 font-medium">{bName} cycles</th>
          <th className="text-right py-1 font-medium w-20">Δ cyc</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map(r => {
          const carrierRaw = r.a?.carrier ?? r.b?.carrier ?? ''
          const carrierLabel = carrierRaw
            ? carrierDisplayName(canonicaliseCarrier('StorageUnit', carrierRaw))
            : ''
          const ca = readPV(r.a?.cycles, period)
          const cb = readPV(r.b?.cycles, period)
          const dash = <span className="text-muted" title="Unit not present in this scenario">—</span>
          const unavailable = <span className="text-muted" title="This scenario's figures could not be resolved">{COST_UNAVAILABLE}</span>
          // Three-state ladder, same priority as ABTable: the side's whole
          // block failing to resolve outranks "this unit isn't in that
          // side's fleet," which in turn outranks the real value — mirrors
          // AssetLCOHTable's r.A ? ... : dash pattern one rung further.
          return (
            <tr key={r.name} className="border-b border-border/40">
              <td className="py-1 text-text font-mono">{r.name}</td>
              <td className="py-1 text-right text-muted">{carrierLabel}</td>
              <td className="py-1 text-right font-mono text-text">{!availableA ? unavailable : r.a ? fmtMw(r.a.p_nom_mw) : dash}</td>
              <td className="py-1 text-right font-mono text-text">{!availableB ? unavailable : r.b ? fmtMw(r.b.p_nom_mw) : dash}</td>
              <td className="py-1 text-right font-mono text-text">{!availableA ? unavailable : r.a ? fmtMwh(r.a.energy_mwh) : dash}</td>
              <td className="py-1 text-right font-mono text-text">{!availableB ? unavailable : r.b ? fmtMwh(r.b.energy_mwh) : dash}</td>
              <td className="py-1 text-right font-mono text-text">{!availableA ? unavailable : r.a ? fmtCyc(ca) : dash}</td>
              <td className="py-1 text-right font-mono text-text">{!availableB ? unavailable : r.b ? fmtCyc(cb) : dash}</td>
              <td className="py-1 text-right font-mono">
                {(availableA && availableB && r.a && r.b)
                  ? <Delta v={cb - ca} fmt={fmtCyc} />
                  : <span className="text-muted" title="Δ undefined — a scenario is unresolved or lacks this unit">—</span>}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ── Reusable bar chart: grouped A vs B per carrier ───────────────────────────

function ABBarChart({
  aName, bName, mapA, mapB, period, unit, selectedCarrier, availableA = true, availableB = true,
}: {
  aName: string
  bName: string
  mapA: Record<string, CarrierPeriodValue>
  mapB: Record<string, CarrierPeriodValue>
  period: PeriodChoice
  unit: string
  // When set to a specific canonical carrier, only that bar pair renders.
  // null (or omitted) keeps the "all carriers" view. Kept as a string so
  // CarrierFilter's selectedCarrier prop flows straight through.
  selectedCarrier?: string | null
  // Whether this side's block resolved at all — see ABTable's matching
  // props for the full ADR-0001 rationale. Defaults to true so the ten
  // pre-existing call sites — all wired by Task 5, across its six tabs —
  // keep compiling untouched. None of Task 6's bespoke tables
  // (LoadingTab/PricesTab/LostLoadTab/OverviewTab) use ABBarChart at all.
  availableA?: boolean
  availableB?: boolean
}) {
  const data = useMemo(() => {
    const carriers = unionCarriers(mapA, mapB)
      .filter(c => selectedCarrier == null || c === selectedCarrier)
    return carriers.map(c => ({
      carrier: carrierDisplayName(c),
      [aName]: readPV(mapA[c], period),
      [bName]: readPV(mapB[c], period),
    }))
  }, [aName, bName, mapA, mapB, period, selectedCarrier])

  // Neither side resolved — there is nothing to plot, not even an empty
  // chart frame. Checked before the data.length===0 branch below: that one
  // means "resolved, but nothing for this period," a different statement.
  if (!availableA && !availableB) {
    return <p className="text-[11px] text-muted py-2">{COST_UNAVAILABLE}</p>
  }
  if (data.length === 0) {
    return <p className="text-[11px] text-muted py-2">No data for this period.</p>
  }
  return (
    <div className="w-full h-64 mb-2">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 8, left: 8, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(127,127,127,0.2)" />
          <XAxis dataKey="carrier" tick={{ fontSize: 11 }} />
          <YAxis tick={{ fontSize: 11 }} label={{ value: unit, angle: -90, position: 'insideLeft', style: { fontSize: 10, fill: '#888' } }} />
          <Tooltip contentStyle={{ fontSize: 11 }} formatter={(v: number) => `${(v ?? 0).toFixed(2)} ${unit}`} />
          {/* Legend at the top so it never collides with an axis label below. */}
          <Legend verticalAlign="top" align="right" height={20} wrapperStyle={{ fontSize: 11, paddingBottom: 4 }} />
          {/* An unavailable side plots NO bar at all — a zero-height bar
              reads as a measured zero, which is exactly the defect
              ADR-0001 forbids. */}
          {availableA && <Bar dataKey={aName} fill="#3b82f6" />}
          {availableB && <Bar dataKey={bName} fill="#f59e0b" />}
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── Reusable table: per-carrier A | B | Δ ────────────────────────────────────

function ABTable({
  aName, bName, mapA, mapB, period, unit, fmt, totalsRow = false, selectedCarrier,
  availableA = true, availableB = true,
}: {
  aName: string
  bName: string
  mapA: Record<string, CarrierPeriodValue>
  mapB: Record<string, CarrierPeriodValue>
  period: PeriodChoice
  unit: string
  fmt: (n: number) => string
  totalsRow?: boolean
  // Same semantics as ABBarChart.selectedCarrier — narrows the table to a
  // single carrier when set. Totals row still aggregates the visible rows
  // (so users can read the filtered total when they're scoped to one
  // carrier without re-doing the math).
  selectedCarrier?: string | null
  // Whether this side's block resolved at all — a THIRD state on top of
  // the present/absent ladder below (Task 5; see ADR-0001). Defaults to
  // true so pre-existing call sites this task didn't touch keep compiling.
  availableA?: boolean
  availableB?: boolean
}) {
  const carriers = unionCarriers(mapA, mapB)
    .filter(c => selectedCarrier == null || c === selectedCarrier)
  if (carriers.length === 0) return null
  let sumA = 0, sumB = 0
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-border text-muted">
          <th className="text-left py-1 font-medium">Carrier</th>
          <th className="text-right py-1 font-medium" title={aName}>{aName} ({unit})</th>
          <th className="text-right py-1 font-medium" title={bName}>{bName} ({unit})</th>
          <th className="text-right py-1 font-medium w-24">Δ ({unit})</th>
        </tr>
      </thead>
      <tbody>
        {carriers.map(c => {
          // Distinguish "carrier absent from this scenario" from "carrier
          // present but value=0". Without this, e.g. project B with no heat
          // sector shows `heat-dump: 0 MW` next to A's 500 MW — reads as "B
          // built nothing" when the carrier doesn't exist in B at all. Now:
          //   mapA[c] undefined  → render "—" (n/a)
          //   mapA[c] defined, total=0 → render "0 MW"
          // A THIRD state sits above both: the side's whole block never
          // resolved (availableA/B), in which case every cell on that side
          // reads COST_UNAVAILABLE regardless of what mapA/mapB happen to
          // contain — that takes priority over the present/absent check.
          const presentA = mapA[c] !== undefined
          const presentB = mapB[c] !== undefined
          const va = presentA ? readPV(mapA[c], period) : 0
          const vb = presentB ? readPV(mapB[c], period) : 0
          sumA += va; sumB += vb
          return (
            <tr key={c} className="border-b border-border/40">
              <td className="py-1 text-text">{carrierDisplayName(c)}</td>
              <td className="py-1 text-right font-mono text-text">
                {!availableA
                  ? <span className="text-muted" title="This scenario's figures could not be resolved">{COST_UNAVAILABLE}</span>
                  : presentA ? fmt(va) : <span className="text-muted" title="Carrier not present in this scenario">—</span>}
              </td>
              <td className="py-1 text-right font-mono text-text">
                {!availableB
                  ? <span className="text-muted" title="This scenario's figures could not be resolved">{COST_UNAVAILABLE}</span>
                  : presentB ? fmt(vb) : <span className="text-muted" title="Carrier not present in this scenario">—</span>}
              </td>
              <td className="py-1 text-right font-mono">
                {(availableA && availableB && presentA && presentB)
                  ? <Delta v={vb - va} fmt={fmt} neutral />
                  : <span className="text-muted" title="Δ undefined — a scenario is unresolved or lacks this carrier">—</span>}
              </td>
            </tr>
          )
        })}
        {totalsRow && (
          <tr className="border-t border-border font-medium">
            <td className="py-1 text-text">Total</td>
            <td className="py-1 text-right font-mono text-text">
              {availableA ? fmt(sumA) : <span className="text-muted" title="This scenario's figures could not be resolved">{COST_UNAVAILABLE}</span>}
            </td>
            <td className="py-1 text-right font-mono text-text">
              {availableB ? fmt(sumB) : <span className="text-muted" title="This scenario's figures could not be resolved">{COST_UNAVAILABLE}</span>}
            </td>
            <td className="py-1 text-right font-mono">
              {(availableA && availableB)
                ? <Delta v={sumB - sumA} fmt={fmt} neutral />
                : <span className="text-muted" title="Δ undefined — a scenario is unresolved">—</span>}
            </td>
          </tr>
        )}
      </tbody>
    </table>
  )
}

// ── Reusable KPI pair (single value per side) ────────────────────────────────

function ABKpiPair({
  aName, bName, aValue, bValue, unit, fmt, deltaIsImprovementWhenNegative = false,
  availableA = true, availableB = true,
}: {
  aName: string
  bName: string
  aValue: number
  bValue: number
  unit: string
  fmt: (n: number) => string
  deltaIsImprovementWhenNegative?: boolean
  // Whether this side's block resolved at all — same third state as
  // ABTable/ABBarChart/EconomicsTable (Task 5; see ADR-0001). Unlike those,
  // ABKpiPair takes a bare number rather than a map, so there is no
  // present/absent ladder underneath it — only "unavailable" vs "real
  // value" — but the priority is identical: an unavailable side's KPI
  // reads COST_UNAVAILABLE regardless of what aValue/bValue happen to
  // contain (an unresolved block's PVs default to 0, so without this guard
  // a zero-CO2 scenario compared against a real one rendered a fabricated
  // "0.0 kt" beside the other side's genuine figure). Defaults to true so
  // pre-existing call sites keep compiling.
  availableA?: boolean
  availableB?: boolean
}) {
  return (
    <div className="grid grid-cols-3 gap-3 text-center">
      <Kpi
        label={aName}
        value={availableA ? fmt(aValue) : <span className="text-muted" title="This scenario's figures could not be resolved">{COST_UNAVAILABLE}</span>}
      />
      <Kpi
        label={bName}
        value={availableB ? fmt(bValue) : <span className="text-muted" title="This scenario's figures could not be resolved">{COST_UNAVAILABLE}</span>}
      />
      <Kpi
        label="Δ"
        value={(availableA && availableB)
          ? <Delta v={bValue - aValue} fmt={fmt} invert={deltaIsImprovementWhenNegative} />
          : <span className="text-muted" title="Δ undefined — a scenario is unresolved">—</span>}
      />
      {/* Trailing unit row keeps the columns coherent when long values wrap. */}
      <div className="col-span-3 text-[10px] text-muted">{unit}</div>
    </div>
  )
}

function Kpi({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="border border-border rounded p-2 truncate" title={label}>
      <div className="text-[10px] text-muted truncate">{label}</div>
      <div className="text-sm font-mono text-text">{value}</div>
    </div>
  )
}

// ── Overview tables (kept from v1) ───────────────────────────────────────────

const COUNT_ROWS: { key: keyof CompareState; label: string }[] = [
  { key: 'bus_count',          label: 'Buses' },
  { key: 'generator_count',    label: 'Generators' },
  { key: 'line_count',         label: 'Lines' },
  { key: 'link_count',         label: 'Links' },
  { key: 'load_count',         label: 'Loads' },
  { key: 'storage_unit_count', label: 'Storage units' },
  { key: 'store_count',        label: 'Stores' },
  { key: 'snapshot_count',     label: 'Snapshots' },
]

function CountsTable({ a, b }: { a: CompareState; b: CompareState }) {
  return (
    <Section title="Network size">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border text-muted">
            <th className="text-left py-1 font-medium">Metric</th>
            <th className="text-right py-1 font-medium truncate max-w-[120px]" title={a.name}>{a.name}</th>
            <th className="text-right py-1 font-medium truncate max-w-[120px]" title={b.name}>{b.name}</th>
            <th className="text-right py-1 font-medium w-20">Δ</th>
          </tr>
        </thead>
        <tbody>
          {COUNT_ROWS.map(({ key, label }) => {
            const va = a[key] as number
            const vb = b[key] as number
            return (
              <tr key={key} className="border-b border-border/40">
                <td className="py-1 text-text">{label}</td>
                <td className="py-1 text-right font-mono text-text">{va}</td>
                <td className="py-1 text-right font-mono text-text">{vb}</td>
                <td className="py-1 text-right font-mono"><Delta v={vb - va} neutral /></td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </Section>
  )
}

function SolverTable({ a, b }: { a: CompareState; b: CompareState }) {
  return (
    <Section title="Solver state">
      <table className="w-full text-xs">
        <tbody>
          <tr className="border-b border-border/40">
            <td className="py-1 text-text">Dispatch status</td>
            <td className="py-1 text-right"><DispatchBadge status={a.dispatch_status} /></td>
            <td className="py-1 text-right"><DispatchBadge status={b.dispatch_status} /></td>
            <td className="py-1" />
          </tr>
          <tr className="border-b border-border/40">
            <td className="py-1 text-text">Objective</td>
            <td className="py-1 text-right font-mono text-text">{fmtCurrency(a.objective)}</td>
            <td className="py-1 text-right font-mono text-text">{fmtCurrency(b.objective)}</td>
            <td className="py-1 text-right font-mono">
              {a.objective != null && b.objective != null
                ? <Delta v={b.objective - a.objective} fmt={fmtCurrency} invert />
                : <span className="text-muted">—</span>}
            </td>
          </tr>
          <tr className="border-b border-border/40">
            <td className="py-1 text-text">Solve time</td>
            <td className="py-1 text-right font-mono text-text">{a.solve_time != null ? `${a.solve_time.toFixed(1)}s` : '—'}</td>
            <td className="py-1 text-right font-mono text-text">{b.solve_time != null ? `${b.solve_time.toFixed(1)}s` : '—'}</td>
            <td className="py-1" />
          </tr>
          <tr className="border-b border-border/40">
            <td className="py-1 text-text">Peak demand</td>
            <td className="py-1 text-right font-mono text-text">{fmtMW(a.peak_demand_mw)}</td>
            <td className="py-1 text-right font-mono text-text">{fmtMW(b.peak_demand_mw)}</td>
            <td className="py-1 text-right font-mono"><Delta v={b.peak_demand_mw - a.peak_demand_mw} fmt={fmtMW} neutral /></td>
          </tr>
          <tr className="border-b border-border/40">
            <td className="py-1 text-text">Total energy</td>
            <td className="py-1 text-right font-mono text-text">{fmtMWh(a.total_energy_mwh)}</td>
            <td className="py-1 text-right font-mono text-text">{fmtMWh(b.total_energy_mwh)}</td>
            <td className="py-1 text-right font-mono"><Delta v={b.total_energy_mwh - a.total_energy_mwh} fmt={fmtMWh} neutral /></td>
          </tr>
          <tr className="border-b border-border/40">
            <td className="py-1 text-text">Snapshot range</td>
            <td className="py-1 text-right font-mono text-muted text-[11px]">{fmtSnapRange(a)}</td>
            <td className="py-1 text-right font-mono text-muted text-[11px]">{fmtSnapRange(b)}</td>
            <td className="py-1" />
          </tr>
        </tbody>
      </table>
    </Section>
  )
}

function OverviewStorageTable({
  aName, bName, summaryA, summaryB, availableA = true, availableB = true,
}: {
  aName: string
  bName: string
  summaryA: ResultsSummary
  summaryB: ResultsSummary
  // Whether this side's capacity block resolved at all — same third state
  // as ABTable (Task 5; see ADR-0001). Every column in this table (total MW,
  // total MWh, built MW) reads the SAME results-summary `capacity` block, so
  // a side's unavailability applies uniformly across the whole row, unlike
  // OverviewCapacityTable where only the "built" columns are capacity-block
  // sourced. Defaults to true so this continues to render standalone in
  // tests/callers that don't care about availability.
  availableA?: boolean
  availableB?: boolean
}) {
  // Power capacity: storage_units' p_nom_opt summed by carrier — already
  // built into the results-summary endpoint via storage_mw_by_carrier.
  // Energy capacity: storage_units' p_nom_opt × max_hours + stores' e_nom_opt,
  // also pre-computed and exposed as storage_mwh_by_carrier. Use these
  // directly so the Overview table reflects the OPTIMISED (post-solve)
  // values, not the pre-build parent's p_nom (which was the bug — battery
  // showed 50 MW even though the LP built it up to 617 MW).
  const mwA = summaryA.capacity?.storage_mw_by_carrier ?? {}
  const mwB = summaryB.capacity?.storage_mw_by_carrier ?? {}
  const mwhA = summaryA.capacity?.storage_mwh_by_carrier ?? {}
  const mwhB = summaryB.capacity?.storage_mwh_by_carrier ?? {}
  // Built (increment-only) storage — same component scope as the totals above.
  const newMwA = summaryA.capacity?.new_storage_mw_by_carrier ?? {}
  const newMwB = summaryB.capacity?.new_storage_mw_by_carrier ?? {}
  const newMwhA = summaryA.capacity?.new_storage_mwh_by_carrier ?? {}
  const newMwhB = summaryB.capacity?.new_storage_mwh_by_carrier ?? {}
  const hasBuilt = Object.keys(newMwA).length > 0 || Object.keys(newMwB).length > 0
    || Object.keys(newMwhA).length > 0 || Object.keys(newMwhB).length > 0
  const carriers = useMemo(() => {
    const set = new Set([
      ...Object.keys(mwA), ...Object.keys(mwB),
      ...Object.keys(mwhA), ...Object.keys(mwhB),
    ])
    return [...set].sort()
  }, [mwA, mwB, mwhA, mwhB])
  if (carriers.length === 0) return null
  const unavailable = <span className="text-muted" title="This scenario's figures could not be resolved">{COST_UNAVAILABLE}</span>
  const deltaOrDash = (v: number, fmt: (n: number) => string) => (availableA && availableB)
    ? <Delta v={v} fmt={fmt} neutral />
    : <span className="text-muted" title="Δ undefined — a scenario is unresolved">—</span>
  return (
    <Section title="Storage capacity by carrier" subtitle="Optimised — power (p_nom_opt) and energy (p_nom_opt × max_hours, plus stores' e_nom_opt). 'built' = increment over brownfield p_nom / e_nom.">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border text-muted">
            <th className="text-left py-1 font-medium">Carrier</th>
            <th className="text-right py-1 font-medium">{aName} (MW)</th>
            <th className="text-right py-1 font-medium">{bName} (MW)</th>
            <th className="text-right py-1 font-medium w-20">Δ (MW)</th>
            <th className="text-right py-1 font-medium">{aName} (MWh)</th>
            <th className="text-right py-1 font-medium">{bName} (MWh)</th>
            <th className="text-right py-1 font-medium w-20">Δ (MWh)</th>
            {hasBuilt && (
              <>
                <th className="text-right py-1 font-medium" title="MW built (expansion) in scenario A">{aName} built (MW)</th>
                <th className="text-right py-1 font-medium" title="MW built (expansion) in scenario B">{bName} built (MW)</th>
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {carriers.map(c => {
            const suA = mwA[c]?.total ?? 0
            const suB = mwB[c]?.total ?? 0
            const stA = mwhA[c]?.total ?? 0
            const stB = mwhB[c]?.total ?? 0
            const bMwA = newMwA[c]?.total ?? 0
            const bMwB = newMwB[c]?.total ?? 0
            return (
              <tr key={c} className="border-b border-border/40">
                <td className="py-1 text-text">{c}</td>
                <td className="py-1 text-right font-mono text-text">{!availableA ? unavailable : suA ? fmtMW(suA) : '—'}</td>
                <td className="py-1 text-right font-mono text-text">{!availableB ? unavailable : suB ? fmtMW(suB) : '—'}</td>
                <td className="py-1 text-right font-mono">{deltaOrDash(suB - suA, fmtMW)}</td>
                <td className="py-1 text-right font-mono text-text">{!availableA ? unavailable : stA ? fmtMWh(stA) : '—'}</td>
                <td className="py-1 text-right font-mono text-text">{!availableB ? unavailable : stB ? fmtMWh(stB) : '—'}</td>
                <td className="py-1 text-right font-mono">{deltaOrDash(stB - stA, fmtMWh)}</td>
                {hasBuilt && (
                  <>
                    <td className="py-1 text-right font-mono text-text">{!availableA ? unavailable : bMwA > 1e-6 ? fmtMW(bMwA) : '—'}</td>
                    <td className="py-1 text-right font-mono text-text">{!availableB ? unavailable : bMwB > 1e-6 ? fmtMW(bMwB) : '—'}</td>
                  </>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
    </Section>
  )
}

// ── Overview: Links capacity by carrier ──────────────────────────────────────
// Heat-pumps / electrolyzers / datacenters / P2X converters. Kept separate
// from the generator table (different component class, MW-on-p0 semantics).
// Total = brownfield + built p_nom_opt; built = max(0, p_nom_opt − p_nom) +
// vintage builds. Renders nothing when neither project has links.
function OverviewLinksTable({
  aName, bName, summaryA, summaryB, availableA = true, availableB = true,
}: {
  aName: string
  bName: string
  summaryA: ResultsSummary
  summaryB: ResultsSummary
  // Whether this side's capacity block resolved at all — same third state
  // as ABTable (Task 5; see ADR-0001). Unlike OverviewCapacityTable, Links
  // aren't tracked in compare-state at all — BOTH the total and built
  // columns here read the results-summary `capacity` block, so a side's
  // unavailability applies to the whole row. Defaults to true so this
  // continues to render standalone in tests/callers that don't care about
  // availability.
  availableA?: boolean
  availableB?: boolean
}) {
  const totA = summaryA.capacity?.link_capacity_mw_by_carrier ?? {}
  const totB = summaryB.capacity?.link_capacity_mw_by_carrier ?? {}
  const newA = summaryA.capacity?.new_link_capacity_mw_by_carrier ?? {}
  const newB = summaryB.capacity?.new_link_capacity_mw_by_carrier ?? {}
  const hasBuilt = Object.keys(newA).length > 0 || Object.keys(newB).length > 0
  const carriers = useMemo(() => {
    const set = new Set([
      ...Object.keys(totA), ...Object.keys(totB),
      ...Object.keys(newA), ...Object.keys(newB),
    ])
    return [...set].sort()
  }, [totA, totB, newA, newB])
  if (carriers.length === 0) return null
  const unavailable = <span className="text-muted" title="This scenario's figures could not be resolved">{COST_UNAVAILABLE}</span>
  return (
    <Section title="Link capacity by carrier (total p_nom_opt = brownfield + built)" subtitle="Heat-pumps / electrolyzers / datacenters / P2X — MW on the link's p0 (input) side.">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border text-muted">
            <th className="text-left py-1 font-medium">Carrier</th>
            <th className="text-right py-1 font-medium">{aName}</th>
            <th className="text-right py-1 font-medium">{bName}</th>
            <th className="text-right py-1 font-medium w-24">Δ total</th>
            {hasBuilt && (
              <>
                <th className="text-right py-1 font-medium" title="MW built (expansion) in scenario A">{aName} built</th>
                <th className="text-right py-1 font-medium" title="MW built (expansion) in scenario B">{bName} built</th>
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {carriers.map(c => {
            const va = totA[c]?.total ?? 0
            const vb = totB[c]?.total ?? 0
            const bA = newA[c]?.total ?? 0
            const bB = newB[c]?.total ?? 0
            return (
              <tr key={c} className="border-b border-border/40">
                <td className="py-1 text-text">{c}</td>
                <td className="py-1 text-right font-mono text-text">{!availableA ? unavailable : va ? fmtMW(va) : '—'}</td>
                <td className="py-1 text-right font-mono text-text">{!availableB ? unavailable : vb ? fmtMW(vb) : '—'}</td>
                <td className="py-1 text-right font-mono">
                  {(availableA && availableB)
                    ? <Delta v={vb - va} fmt={fmtMW} neutral />
                    : <span className="text-muted" title="Δ undefined — a scenario is unresolved">—</span>}
                </td>
                {hasBuilt && (
                  <>
                    <td className="py-1 text-right font-mono text-text">{!availableA ? unavailable : bA > 1e-6 ? fmtMW(bA) : '—'}</td>
                    <td className="py-1 text-right font-mono text-text">{!availableB ? unavailable : bB > 1e-6 ? fmtMW(bB) : '—'}</td>
                  </>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
    </Section>
  )
}

function OverviewCapacityTable({
  a, b, newCapA, newCapB, availableA = true, availableB = true,
}: {
  a: CompareState
  b: CompareState
  newCapA?: Record<string, CarrierPeriodValue>
  newCapB?: Record<string, CarrierPeriodValue>
  // Whether this side's capacity block resolved at all — same third state as
  // ABTable (Task 5; see ADR-0001). Deliberately applied ONLY to the "built"
  // columns below (bA/bB, sourced from the results-summary capacity block),
  // NOT to the main total columns (va/vb, sourced from compare-state's
  // installed/optimised_capacity_by_carrier — structural network data that's
  // always meaningful, solved or not, and carries no `available` flag of its
  // own). Defaults to true so this continues to render standalone in
  // tests/callers that don't care about availability.
  availableA?: boolean
  availableB?: boolean
}) {
  const useOpt = a.optimised_capacity_by_carrier != null && b.optimised_capacity_by_carrier != null
  const capA = (useOpt ? a.optimised_capacity_by_carrier : a.installed_capacity_by_carrier) ?? {}
  const capB = (useOpt ? b.optimised_capacity_by_carrier : b.installed_capacity_by_carrier) ?? {}
  // Extract per-carrier "built" totals from the results-summary's
  // new_capacity_mw_by_carrier, when available. This separates inherited
  // brownfield (e.g. heat-dump 500 MW non-extendable) from each scenario's
  // actual investment. Falls back to empty maps when the field is missing
  // (older payloads or unsolved projects).
  const builtA: Record<string, number> = {}
  const builtB: Record<string, number> = {}
  for (const [carrier, v] of Object.entries(newCapA ?? {})) builtA[carrier] = v?.total ?? 0
  for (const [carrier, v] of Object.entries(newCapB ?? {})) builtB[carrier] = v?.total ?? 0
  const hasBuilt = Object.keys(builtA).length > 0 || Object.keys(builtB).length > 0
  const carriers = useMemo(() => {
    const set = new Set([
      ...Object.keys(capA),
      ...Object.keys(capB),
      ...Object.keys(builtA),
      ...Object.keys(builtB),
    ])
    return [...set].sort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [capA, capB, newCapA, newCapB])
  if (carriers.length === 0) {
    return (
      <Section title="Generator capacity by carrier">
        <p className="text-[11px] text-muted py-2">No generators in either project.</p>
      </Section>
    )
  }
  const title = `Generator capacity by carrier ${
    useOpt ? '(total p_nom_opt = brownfield + built)' : '(installed p_nom — brownfield only)'
  }`
  const unavailable = <span className="text-muted" title="This scenario's figures could not be resolved">{COST_UNAVAILABLE}</span>
  return (
    <Section title={title}>
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border text-muted">
            <th className="text-left py-1 font-medium">Carrier</th>
            <th className="text-right py-1 font-medium">{a.name}</th>
            <th className="text-right py-1 font-medium">{b.name}</th>
            <th className="text-right py-1 font-medium w-24">Δ total</th>
            {hasBuilt && (
              <>
                <th className="text-right py-1 font-medium" title="MW built (expansion) in scenario A">{a.name} built</th>
                <th className="text-right py-1 font-medium" title="MW built (expansion) in scenario B">{b.name} built</th>
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {carriers.map(c => {
            const va = capA[c] ?? 0
            const vb = capB[c] ?? 0
            const bA = builtA[c] ?? 0
            const bB = builtB[c] ?? 0
            return (
              <tr key={c} className="border-b border-border/40">
                <td className="py-1 text-text">{c}</td>
                <td className="py-1 text-right font-mono text-text">{fmtMW(va)}</td>
                <td className="py-1 text-right font-mono text-text">{fmtMW(vb)}</td>
                <td className="py-1 text-right font-mono"><Delta v={vb - va} fmt={fmtMW} neutral /></td>
                {hasBuilt && (
                  <>
                    <td className="py-1 text-right font-mono text-text">{!availableA ? unavailable : bA > 1e-6 ? fmtMW(bA) : '—'}</td>
                    <td className="py-1 text-right font-mono text-text">{!availableB ? unavailable : bB > 1e-6 ? fmtMW(bB) : '—'}</td>
                  </>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
    </Section>
  )
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function readPV(pv: CarrierPeriodValue | undefined, period: PeriodChoice): number {
  if (!pv) return 0
  if (period === 'all') return pv.total
  return pv.by_period[period] ?? 0
}

function unionCarriers(a: Record<string, CarrierPeriodValue>, b: Record<string, CarrierPeriodValue>): string[] {
  return [...new Set([...Object.keys(a), ...Object.keys(b)])].sort()
}

// Merge a per-carrier map under canonical carrier keys so 'Gas' / 'gas' /
// 'GAS' collapse into one row and 'battery' / 'bess' / 'Li-Ion' all become
// 'battery'. Without this, a user comparing two scenarios that happen to
// spell their carriers differently sees duplicate rows (one per spelling)
// even though the underlying data refers to the same carrier. Applied at
// the table-builder boundary so downstream code (ABTable / ABBarChart)
// stays agnostic. Keys produced are the canonical lower-case slugs; the
// display layer routes through `carrierDisplayName`.
function canonicaliseCarrierMap(
  map: Record<string, CarrierPeriodValue>,
  componentClass: ComponentClass,
): Record<string, CarrierPeriodValue> {
  const out: Record<string, CarrierPeriodValue> = {}
  for (const [rawCarrier, v] of Object.entries(map)) {
    const key = canonicaliseCarrier(componentClass, rawCarrier)
    const existing = out[key]
    if (!existing) {
      // Shallow clone of by_period so mutations on the merge target don't
      // leak back into the source map (React Query cache is shared).
      out[key] = { total: v.total, by_period: { ...v.by_period } }
    } else {
      existing.total += v.total
      for (const [p, x] of Object.entries(v.by_period)) {
        existing.by_period[p] = (existing.by_period[p] ?? 0) + x
      }
    }
  }
  return out
}

// Generators-by-carrier + storage-by-carrier sum into a single map for the
// capacity chart. Per-period values add column-wise; the union of by_period
// keys is preserved so the per-period selector finds all entries.
function mergeBuckets(
  ...maps: Record<string, CarrierPeriodValue>[]
): Record<string, CarrierPeriodValue> {
  const out: Record<string, CarrierPeriodValue> = {}
  for (const m of maps) {
    for (const [k, v] of Object.entries(m)) {
      if (!out[k]) out[k] = { total: 0, by_period: {} }
      out[k].total += v.total
      for (const [p, x] of Object.entries(v.by_period)) {
        out[k].by_period[p] = (out[k].by_period[p] ?? 0) + x
      }
    }
  }
  return out
}

function periodLabel(p: PeriodChoice): string {
  return p === 'all' ? 'All periods (aggregated, year-weighted)' : `Period ${p}`
}

// ── Shared layout primitives ─────────────────────────────────────────────────

function Section({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <div className="border border-border rounded">
      <div className="px-3 py-1.5 border-b border-border bg-panel">
        <div className="text-xs font-semibold text-text">{title}</div>
        {subtitle && <div className="text-[10px] text-muted">{subtitle}</div>}
      </div>
      <div className="p-3">{children}</div>
    </div>
  )
}

function LoadingSpinner() {
  return (
    <div className="flex items-center justify-center gap-2 text-xs text-muted py-8">
      <Loader size={14} className="animate-spin" /> Loading comparison…
    </div>
  )
}

function ErrorBanner({ which }: { which: string }) {
  return (
    <div className="flex items-center gap-2 text-xs text-danger py-8 justify-center">
      <AlertTriangle size={14} /> Failed to load '{which}'. It may be corrupted or missing.
    </div>
  )
}

function UnsolvedBanner({ unsolved }: { unsolved: string[] }) {
  return (
    <div className="flex items-center gap-2 text-xs text-warn border border-warn/30 bg-warn/5 rounded p-3">
      <AlertTriangle size={14} />
      <span>
        {unsolved.length === 1
          ? `Scenario '${unsolved[0]}' has no fresh solve — load it and re-run before comparing.`
          : `Neither scenario has a fresh solve — re-run both before comparing.`}
      </span>
    </div>
  )
}

// Cell delta: invert flips good/bad (cost decrease = good), neutral = muted.
// (Same semantics as the Overview tab kept from v1.)
function Delta({
  v, fmt, invert = false, neutral = false,
}: { v: number; fmt?: (n: number) => string; invert?: boolean; neutral?: boolean }) {
  if (!Number.isFinite(v) || Math.abs(v) < 1e-9) return <span className="text-muted">—</span>
  const positive = v > 0
  const good = invert ? !positive : positive
  const cls = neutral ? 'text-text' : good ? 'text-success' : 'text-danger'
  const sign = positive ? '+' : ''
  return <span className={cls}>{sign}{fmt ? fmt(v) : v}</span>
}

function DispatchBadge({ status }: { status: 'none' | 'fresh' | 'stale' }) {
  const map = {
    fresh: { cls: 'bg-success/15 text-success', label: 'SOLVED' },
    stale: { cls: 'bg-warn/15 text-warn',       label: 'STALE' },
    none:  { cls: 'bg-border/40 text-muted',    label: 'UNSOLVED' },
  }
  const s = map[status] ?? map.none
  return <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${s.cls}`}>{s.label}</span>
}

// ── Formatters ───────────────────────────────────────────────────────────────

function fmtCurrency(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return '—'
  const abs = Math.abs(v)
  if (abs >= 1e9) return `€${(v / 1e9).toFixed(2)}B`
  if (abs >= 1e6) return `€${(v / 1e6).toFixed(2)}M`
  if (abs >= 1e3) return `€${(v / 1e3).toFixed(2)}k`
  return `€${v.toFixed(0)}`
}
function fmtMW(v: number): string {
  if (!Number.isFinite(v)) return '—'
  const abs = Math.abs(v)
  if (abs >= 1e6) return `${(v / 1e6).toFixed(2)} TW`
  if (abs >= 1e3) return `${(v / 1e3).toFixed(2)} GW`
  return `${v.toFixed(1)} MW`
}
function fmtMWh(v: number): string {
  if (!Number.isFinite(v)) return '—'
  const abs = Math.abs(v)
  if (abs >= 1e6) return `${(v / 1e6).toFixed(2)} TWh`
  if (abs >= 1e3) return `${(v / 1e3).toFixed(2)} GWh`
  return `${v.toFixed(1)} MWh`
}

function fmtSnapRange(s: CompareState): string {
  if (!s.snapshot_start || !s.snapshot_end) return '—'
  const d = (iso: string) => iso.slice(0, 10)
  const start = d(s.snapshot_start)
  const end = d(s.snapshot_end)
  return start === end ? start : `${start} → ${end}`
}

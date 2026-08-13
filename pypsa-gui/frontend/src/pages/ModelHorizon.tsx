import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { confirmToast } from '../utils/toasts'
import { networkApi } from '../api/network'
import { simulationApi } from '../api/simulation'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { PageHeader, RowGrid, StatCard } from '../components/PageKit'
import type { Load } from '../api/types'
import {
  resolutionLabel, horizonRangeLabel,
  visibleSteps, isHorizonUnset, stepSummary,
  type WeightingRow, type HorizonStepId, type HorizonSummaryContext,
} from './modelHorizonModel'
import { StepShell, STEP_LABELS } from './modelHorizon/StepShell'
import { HorizonSummary } from './modelHorizon/HorizonSummary'
import { StepMode } from './modelHorizon/StepMode'
import { StepYears } from './modelHorizon/StepYears'
import { StepPeriodEconomics } from './modelHorizon/StepPeriodEconomics'
import { StepWindow, StepWindowAdvanced } from './modelHorizon/StepWindow'
import { StepSampling } from './modelHorizon/StepSampling'
import { StepWeights, StepWeightsAdvanced } from './modelHorizon/StepWeights'

// ── Load carrier canonicaliser ──────────────────────────────────────────────
// Mirrors loadCarrierKey in Dispatch.tsx + _canonical_load_carrier_key on the
// backend so the per-carrier `load_scalers_by_carrier` keys round-trip
// cleanly between the UI, REST API, and solver_service.
//
// Heat / hydrogen matching is substring-based so PyPSA-Eur conventions like
// "urban central heat", "rural heat", "low T heat", "district heat", and
// "H2 for industry", "H2 for power", "hydrogen storage" all funnel into
// the same canonical bucket. Electrical stays exact-match because its
// aliases ('', 'AC', 'el') are too short for safe substring fallback.
const LOAD_ELECTRICAL_ALIASES = new Set(['', 'ac', 'electricity', 'electric', 'electrical', 'el'])
const LOAD_HEAT_TOKENS = ['heat', 'thermal']
const LOAD_HYDROGEN_TOKENS = ['hydrogen', 'h2']
function loadCarrierKey(raw: string | undefined | null): string {
  const c = (raw ?? '').trim().toLowerCase()
  if (LOAD_ELECTRICAL_ALIASES.has(c)) return 'electrical'
  if (!c) return 'electrical'
  if (LOAD_HYDROGEN_TOKENS.some(tok => c.includes(tok))) return 'hydrogen'
  if (LOAD_HEAT_TOKENS.some(tok => c.includes(tok)))     return 'heat'
  return c || 'unspecified'
}
function loadCarrierLabel(key: string): string {
  if (key === 'electrical')  return 'Electrical'
  if (key === 'hydrogen')    return 'Hydrogen'
  if (key === 'heat')        return 'Heat'
  if (key === 'unspecified') return 'Unspecified'
  return key.charAt(0).toUpperCase() + key.slice(1)
}
function loadCarrierSortKey(key: string): string {
  if (key === 'electrical')  return '0_electrical'
  if (key === 'hydrogen')    return '1_hydrogen'
  if (key === 'heat')        return '2_heat'
  if (key === 'unspecified') return 'z_unspecified'
  return `m_${key}`
}

// What this page is: a shell, not a scroll. It owns every query, mutation,
// and piece of scratch state the six Model Horizon steps need, and renders
// exactly one of two things below the always-visible status strip —
// HorizonSummary (a returning user's landing view, one line per step) or
// StepShell routed to whichever single step `view` names. Never both, never
// stacked; see `view` state below for how the choice is made and kept.
//
// The six steps split along PyPSA's own two-level snapshot index —
// (period, timestep) — not along any UI convenience grouping:
//   • period-level — mode, years, economics. These configure the investment
//     periods themselves and are ABSENT ENTIRELY in single-period mode
//     (there is no period axis to configure); `visibleSteps()` in
//     modelHorizonModel.ts is the single source of truth for that filtering.
//   • timestep-level — window, sampling, weights. These configure the
//     operational index within each period (or the one flat index in
//     single-period mode) and are always present, in both modes.
//
// Each step's own UI lives in pages/modelHorizon/*.tsx. What stays here is
// wiring: thirteen `useMutation`s, the queries feeding them, and derived
// values (rangeStr, autoDiscountOn, summaryCtx, …) computed once and read by
// both the StatCard strip and the routed step, so the two can't drift apart.

// PyPSA's snapshot index is full ISO; the HTML datetime-local input only
// accepts "YYYY-MM-DDTHH:mm". This trims any seconds / fractional part the
// backend sent so the input doesn't reject the value.
function toLocal(iso: string): string {
  return iso.length >= 16 ? iso.slice(0, 16) : iso
}

// Extract a datetime-local-compatible string from a serialised snapshot entry.
// Handles both ISO ("2024-01-01T00:00:00") and the MultiIndex tuple-repr form
// ("(2030, Timestamp('2024-01-01 00:00:00'))") the snapshots endpoint emits.
function extractLocalFromSnapshot(s: string | undefined): string {
  if (!s) return ''
  if (s.startsWith('(')) {
    const m = s.match(/Timestamp\('([^']+)'\)/)
    if (!m) return ''
    return m[1].replace(' ', 'T').slice(0, 16)
  }
  return toLocal(s)
}

// Shown by the 'economics' and multi-period 'window' steps when there are
// zero investment years — both need at least one period before they have
// anything to show. Before this task's routing existed, that state was
// unreachable: `isMultiPeriod` gated the whole "Multi-period planning"
// section and the always-visible "Investment years" add-UI sat directly
// above these blocks in the old scroll, so a user could never land on an
// empty screen. The guided rail now lists Economics/Snapshot window as
// clickable regardless of period count, so the step itself has to say why
// it's empty and offer the one-click way out — a route to the Years step.
function NoPeriodsFallback({ message, onGoToYears }: { message: string; onGoToYears: () => void }) {
  return (
    <div className="border border-dashed border-border rounded p-4 flex flex-col items-start gap-2.5 text-[11px] text-muted">
      <p className="leading-relaxed">{message}</p>
      <button
        type="button"
        onClick={onGoToYears}
        className="px-2.5 py-1 border border-border rounded text-accent hover:border-accent hover:bg-accent/5 transition-colors"
      >
        Go to Investment years →
      </button>
    </div>
  )
}

export default function ModelHorizon() {
  const qc = useQueryClient()
  const currentProject = useUIStore(s => s.currentProject)
  const { data: snap } = useQuery({ queryKey: nk(currentProject, 'snapshots'), queryFn: networkApi.getSnapshots })
  const { data: ip } = useQuery({
    queryKey: nk(currentProject, 'investmentPeriods'),
    queryFn: networkApi.getInvestmentPeriods,
  })
  const { data: cfg } = useQuery({ queryKey: nk(currentProject, 'solverConfig'), queryFn: simulationApi.getSolverConfig })

  // ── Snapshot-range scratch state (single-period mode) ───────────────────
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [freq, setFreq] = useState<string>('h')
  // True once the user manually edits Start/End — suppresses re-seeding so a
  // later snapshots refetch (e.g. after a profile upload) can't clobber an
  // in-progress edit.
  const rangeTouchedRef = useRef(false)

  // Seed Start/End from the network. Prefer the uploaded time-series extent
  // (ts_start / ts_end — the user's actual data range) and fall back to the
  // current n.snapshots range. Re-runs when the uploaded extent changes (e.g.
  // a fresh 8760 h upload expands it) but never overwrites a manual edit.
  useEffect(() => {
    if (rangeTouchedRef.current || !snap) return
    if (snap.ts_start && snap.ts_end) {
      setStart(toLocal(snap.ts_start))
      setEnd(toLocal(snap.ts_end))
      return
    }
    // `snap.snapshots` is plain ISO in both modes (see the accurate note on
    // `snapshotsAreMulti` below) — the `startsWith('(')` guard here is
    // defensive only, kept in case a caller ever slips a raw tuple-repr
    // string through.
    const ss = snap.snapshots
    if (ss && ss.length > 0) {
      const first = ss[0], last = ss[ss.length - 1]
      if (typeof first === 'string' && !first.startsWith('(')) {
        setStart(toLocal(first))
        setEnd(toLocal(last))
      }
    }
  }, [snap?.ts_start, snap?.ts_end, snap?.snapshots])

  const periods: number[] = useMemo(() => ip?.periods ?? [], [ip])
  const periodWeightings = useMemo(
    () => (ip?.weightings ?? []) as Array<Record<string, number | string>>,
    [ip],
  )

  // Auto-discount anchors on the first ACTIVE period — same rule as
  // solver_service's `ref_year = periods_active[0]`.
  const refPeriod = useMemo(
    () => (periods.length > 0 ? Math.min(...periods) : 0),
    [periods],
  )
  const isMultiPeriod = cfg?.multi_investment_periods ?? false
  // MultiIndex snapshots are signalled by the backend returning a parallel
  // `periods` array (one investment period per snapshot). The `snapshots`
  // array itself is plain ISO timestep strings in BOTH modes, so it can't be
  // used to tell the modes apart — checking `startsWith('(')` on it always
  // returned false, which silently disabled the toggle-off cascade below.
  const snapshotsAreMulti = useMemo(
    () => (snap?.periods?.length ?? 0) > 0,
    [snap?.periods],
  )
  // Whether auto-discount will ACTUALLY write anything at solve time — not
  // just whether the checkbox is checked. Mirrors solver_service.py's gate
  // exactly (see `_apply_modelling_assumptions` step 4b, ~:4374-4379):
  // cfg.auto_discount_periods AND multi_investment_periods AND a MultiIndex
  // snapshot index AND a non-empty n.investment_periods. Toggle ON with flat
  // snapshots (or periods cleared) is inert on the backend; the PV preview
  // column must grey out in that state rather than claiming a value that
  // will never be written.
  const autoDiscountOn = Boolean(cfg?.auto_discount_periods)
    && isMultiPeriod
    && snapshotsAreMulti
    && periods.length > 0

  // ── Mutations ──────────────────────────────────────────────────────────
  // Snapshot reshape touches a defined set of query keys: the index itself
  // (`['snapshots']`, `['snapshots-list', …]`), the period weightings
  // (`['investmentPeriods']`), network meta (bus_count, snapshot_count),
  // every time-series result (`['results', …]`, `['results-summary', …]`,
  // and the legacy `results_*` underscore variants used by older viewer
  // components), the raw _t access (`['timeseries', …]`, `['timeseries-multi']`),
  // user-uploaded profile views (`['*_profiles']`, `['load_aggregate']`),
  // and the vintage-results aggregator. Other queries (projects list,
  // changelog, carriers, asset CRUD) are unaffected — invalidating them
  // would force a refetch storm across every mounted tab for no reason.
  //
  // The previous code called `qc.invalidateQueries()` with NO args, which
  // marks EVERY query in the cache stale. That triggered cascading
  // refetches across project metadata, audit log, carriers, etc. on every
  // snapshot change — measurable UI thrash on big projects.
  //
  // MAINTENANCE: when adding a new snapshot-derived query key (anything
  // whose data depends on n.snapshots, n.investment_periods, or the
  // result `_t` tables), make sure its head matches one of the prefixes
  // below — most use `startsWith` rather than exact match so e.g.
  // `['results', 'generators']`, `['results-summary', 'proj']`, and
  // `['results_lines']` are all covered by the single `results` prefix.
  const invalidateSnapshotDependent = () => {
    qc.invalidateQueries({
      predicate: (q) => {
        const k = q.queryKey
        if (!Array.isArray(k) || k.length === 0) return false
        const head = String(k[0])
        return (
          // Prefix matches catch the hyphen/underscore variants
          // (`results-summary`, `results_statistics`, etc.) that
          // exact-equality used to miss.
          head.startsWith('results') ||
          head.startsWith('timeseries') ||
          head.startsWith('snapshots') ||
          // Profile views re-resolve against the new snapshot index.
          head === 'load_aggregate' ||
          head === 'generator_profiles' ||
          head === 'load_profiles' ||
          head === 'link_profiles' ||
          // Network-state queries directly shaped by the snapshot reshape.
          head === 'investmentPeriods' ||
          head === 'meta' ||
          head === 'vintage_results' ||
          head === 'user_ts_extent' ||
          head === 'solverConfig' ||
          head === 'simulationStatus'
        )
      },
    })
  }

  const applySnapshots = useMutation({
    mutationFn: (cfg: { start: string; end: string; freq: string }) => networkApi.setSnapshots(cfg),
    onSuccess: () => {
      invalidateSnapshotDependent()
      toast.success('Snapshots updated')
    },
    onError: () => toast.error('Could not update snapshots'),
  })

  const applyPeriods = useMutation({
    mutationFn: (periods: number[]) => networkApi.setInvestmentPeriods({ periods }),
    onSuccess: () => {
      const proj = useUIStore.getState().currentProject
      qc.invalidateQueries({ queryKey: nk(proj, 'investmentPeriods') })
      qc.invalidateQueries({ queryKey: nk(proj, 'snapshots') })
      qc.invalidateQueries({ queryKey: nk(proj, 'meta') })
      // capex_budget_per_period and load_scalers_by_carrier are keyed by period
      // year; a removed period leaves its entries behind, and the table renders
      // from this cache.
      qc.invalidateQueries({ queryKey: nk(proj, 'solverConfig') })
      toast.success('Investment periods saved')
    },
    onError: () => toast.error('Could not save investment periods'),
  })

  // multi_investment_periods toggle. Partial PUT — backend uses
  // exclude_unset=True so omitted SolverConfig fields aren't reset to defaults.
  //
  // After flipping the flag we cascade to the snapshot index so the network's
  // shape stays consistent with the toggle:
  //   • Toggle OFF while snapshots are MultiIndex → demote to flat (clears
  //     n.investment_periods → set_investment_periods([])).
  //   • Toggle ON with saved investment_periods but flat snapshots → promote
  //     (rebuild MultiIndex via set_investment_periods(periods)).
  //   • All other cases are no-ops.
  // The cascade is what makes the StatusBar's snapshot count refresh
  // immediately — toggling the flag alone wouldn't touch n.snapshots and the
  // bottom-bar would stay stale.
  const toggleMultiPeriod = useMutation({
    mutationFn: async (enabled: boolean) => {
      await simulationApi.updateSolverConfig({ multi_investment_periods: enabled })
      if (!enabled && snapshotsAreMulti) {
        await networkApi.setInvestmentPeriods({ periods: [] })
      } else if (enabled && !snapshotsAreMulti && periods.length > 0) {
        await networkApi.setInvestmentPeriods({ periods })
      }
    },
    onSuccess: () => {
      const proj = useUIStore.getState().currentProject
      qc.invalidateQueries({ queryKey: nk(proj, 'solverConfig') })
      qc.invalidateQueries({ queryKey: nk(proj, 'investmentPeriods') })
      qc.invalidateQueries({ queryKey: nk(proj, 'snapshots') })
      qc.invalidateQueries({ queryKey: nk(proj, 'meta') })
    },
    onError: () => toast.error('Could not update multi-investment-periods flag'),
  })

  // Period weighting mutations.
  const applyBulkPeriodYears = useMutation({
    mutationFn: (v: number) => networkApi.updateInvestmentPeriodWeightings({ all_years: v }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'investmentPeriods') })
      toast.success('Period years updated')
    },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      toast.error(e.response?.data?.detail ?? 'Failed to update years'),
  })
  const applyBulkPeriodObjective = useMutation({
    mutationFn: (v: number) => networkApi.updateInvestmentPeriodWeightings({ all_objective: v }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'investmentPeriods') })
      toast.success('Period objective weights updated')
    },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      toast.error(e.response?.data?.detail ?? 'Failed to update objective weights'),
  })
  const updateOnePeriodCol = useMutation({
    mutationFn: (args: { period: number; col: 'years' | 'objective'; value: number }) =>
      networkApi.updateInvestmentPeriodWeightings({
        updates: { [args.period]: { [args.col]: args.value } },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'investmentPeriods') }),
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      toast.error(e.response?.data?.detail ?? 'Failed to update cell'),
  })

  // Per-carrier per-period load scaler. Outer key = canonical carrier
  // ('electrical' / 'hydrogen' / 'heat' / passthrough), inner key = period
  // year (str). Wholesale PUT — caller sends the full nested map.
  const updateLoadScalersByCarrier = useMutation({
    mutationFn: (next: Record<string, Record<string, number>>) =>
      simulationApi.updateSolverConfig({ load_scalers_by_carrier: next }),
    onSuccess: () => qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'solverConfig') }),
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      toast.error(e.response?.data?.detail ?? 'Failed to update per-carrier load scaler'),
  })

  // Pull loads to detect which carriers exist in the network. The per-carrier
  // scaler editor always shows the three canonical buckets — Electrical,
  // Hydrogen, Heat — so projects can pre-configure growth for a carrier
  // BEFORE the corresponding loads are added (typical workflow: set up the
  // multi-period horizon and growth assumptions, then build the network).
  // Any extra non-canonical carriers actually present in the network get
  // their own column appended at the end.
  const { data: loads = [] } = useQuery({ queryKey: nk(currentProject, 'loads'), queryFn: networkApi.getLoads })
  const networkLoadCarriers = useMemo(() => {
    const set = new Set<string>(['electrical', 'hydrogen', 'heat'])
    for (const l of loads as Load[]) set.add(loadCarrierKey(l.carrier))
    return Array.from(set).sort((a, b) => loadCarrierSortKey(a).localeCompare(loadCarrierSortKey(b)))
  }, [loads])

  // Auto-discount: when ON, the solver service overrides ipw.objective at
  // LP build time with (1+discount_rate)^-(P-ref_year) × years[P] — applies
  // a uniform social-discount to all future periods so the LP doesn't
  // front-load all CAPEX into period 1.
  const updateAutoDiscount = useMutation({
    mutationFn: (enabled: boolean) =>
      simulationApi.updateSolverConfig({ auto_discount_periods: enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'solverConfig') }),
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      toast.error(e.response?.data?.detail ?? 'Failed to toggle auto-discount'),
  })

  // Per-period CAPEX budget — keyed by period year string, value in EUR.
  // Replaces the whole map on each update (same pattern as load_scalers).
  // Empty / zero entries skip the LP constraint for that period.
  const updateCapexBudget = useMutation({
    mutationFn: (next: Record<string, number>) =>
      simulationApi.updateSolverConfig({ capex_budget_per_period: next }),
    onSuccess: () => qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'solverConfig') }),
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      toast.error(e.response?.data?.detail ?? 'Failed to update CAPEX budget'),
  })

  // Snapshot-weighting mutations.
  const [bulkWeight, setBulkWeight] = useState('')
  const applyBulkWeight = useMutation({
    mutationFn: (v: number) => networkApi.updateSnapshotWeightings({ all: v }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'snapshots') })
      toast.success('Weights updated')
      setBulkWeight('')
    },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      toast.error(e.response?.data?.detail ?? 'Failed to update weights'),
  })
  const updateOneWeight = useMutation({
    mutationFn: (args: { key: string; objective: number }) =>
      networkApi.updateSnapshotWeightings({ updates: { [args.key]: { objective: args.objective } } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'snapshots') }),
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      toast.error(e.response?.data?.detail ?? 'Failed to update weight'),
  })

  // Period chip add/remove (multi-period mode). The year-range validation
  // (1900–2200) and the add input's reset now live in StepYears.tsx — see
  // its `onAddPeriod` prop below — since that's presentational input
  // handling, not the mutation itself. `removePeriod` needs no validation so
  // it's passed straight through to StepYears unchanged.
  const [newPeriod, setNewPeriod] = useState<string>('')
  const removePeriod = (year: number) => {
    applyPeriods.mutate(periods.filter(y => y !== year))
  }

  // ── Period bulk-weight inputs ────────────────────────────────────────────
  const [bulkYears, setBulkYears] = useState('')
  const [bulkObjective, setBulkObjective] = useState('')

  // ── Multi-period snapshot constructor scratch state ────────────────────
  const [mpMode, setMpMode] = useState<'same' | 'per_period'>('same')
  const [mpStart, setMpStart] = useState<string>('')
  const [mpEnd, setMpEnd] = useState<string>('')
  const [mpFreq, setMpFreq] = useState<string>('h')
  const [mpHydrated, setMpHydrated] = useState(false)
  const [mpPerPeriod, setMpPerPeriod] = useState<Array<{ start: string; end: string; freq: string }>>([])

  useEffect(() => {
    setMpPerPeriod(prev => {
      if (prev.length === periods.length) return prev
      const next = [...prev]
      while (next.length < periods.length) next.push({ start: '', end: '', freq: 'h' })
      next.length = periods.length
      return next
    })
  }, [periods.length])

  // Pre-fill the Same-mode start/end from the current snapshots once. For a
  // MultiIndex network we use period-0's operational range; for flat we use
  // the whole range. Skipped for fresh networks (count ≤ 1 = default "now"
  // snapshot) where any default would be a stale timestamp.
  // Hydrates only once — typing into the inputs is preserved across refetches.
  useEffect(() => {
    if (mpHydrated) return
    if (!snap || !snap.snapshots || snap.count <= 1) return
    let lastIdx = snap.snapshots.length - 1
    if (snapshotsAreMulti && snap.periods) {
      const p0 = snap.periods[0]
      // Walk until the period changes; the previous index is period-0's last.
      for (let i = 1; i < snap.periods.length; i++) {
        if (snap.periods[i] !== p0) {
          lastIdx = i - 1
          break
        }
      }
    }
    const startStr = extractLocalFromSnapshot(snap.snapshots[0])
    const endStr = extractLocalFromSnapshot(snap.snapshots[lastIdx])
    if (startStr) setMpStart(startStr)
    if (endStr) setMpEnd(endStr)
    setMpHydrated(true)
  }, [snap, snapshotsAreMulti, mpHydrated])

  const applyMultiPeriodSnapshots = useMutation({
    mutationFn: (body: {
      periods: number[]
      start?: string; end?: string; freq?: string
      per_period?: Array<{ start: string; end: string; freq?: string }>
    }) => networkApi.setMultiPeriodSnapshots(body),
    onSuccess: r => {
      invalidateSnapshotDependent()
      toast.success(`MultiIndex built: ${r.periods.length} periods × ${r.rows_per_period.join('/')} = ${r.count} rows`)
    },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      toast.error(e.response?.data?.detail ?? 'Failed to build MultiIndex snapshots'),
  })

  const onApplySnapshots = () => {
    if (!start || !end) {
      toast.error('Pick a start and end datetime first')
      return
    }
    confirmToast(
      `Re-index the network to ${freq} between ${start} and ${end}? Time-series data will be re-aligned.`,
      () => applySnapshots.mutate({ start, end, freq }),
      { confirmLabel: 'Re-index' },
    )
  }

  // ── Representative-week sampling ───────────────────────────────────────
  // Picks N random ISO weeks/month from an uploaded full-year hourly profile.
  // `sampledWeeks` holds the last result for the summary table; it is purely
  // display state — the authoritative snapshot index lives on the network.
  const [sampleNWeeks, setSampleNWeeks] = useState('1')
  const [sampleSeed, setSampleSeed] = useState('')
  const [sampledWeeks, setSampledWeeks] = useState<
    Array<{ month: number; iso_week: number; start: string; end: string; weight: number }>
  >([])
  const sampleWeeks = useMutation({
    mutationFn: (body: { n_weeks: number; seed?: number }) => networkApi.sampleWeeks(body),
    onSuccess: r => {
      invalidateSnapshotDependent()
      setSampledWeeks(r.weeks)
      toast.success(
        `Sampled ${r.weeks.length} week(s) → ${r.count} snapshots` +
        (r.multi_period ? ` (${r.timesteps_per_period}/period × periods)` : ''),
      )
      if (r.had_custom_weights) {
        toast(
          'Your previous snapshot weights were replaced by the representative-week scaling.',
          { icon: '⚠️', duration: 6000 },
        )
      }
    },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      toast.error(e.response?.data?.detail ?? 'Failed to sample representative weeks'),
  })
  const onSampleWeeks = () => {
    const nw = parseInt(sampleNWeeks, 10)
    if (!Number.isFinite(nw) || nw < 1 || nw > 5) {
      toast.error('Weeks per month must be between 1 and 5')
      return
    }
    const seedRaw = sampleSeed.trim()
    const seedNum = seedRaw === '' ? undefined : parseInt(seedRaw, 10)
    confirmToast(
      `Sample ${nw} ISO week(s) per month and re-index the network? ` +
      `The current snapshot index will be replaced; time-series data is re-aligned.`,
      () => sampleWeeks.mutate({
        n_weeks: nw,
        seed: seedNum !== undefined && Number.isFinite(seedNum) ? seedNum : undefined,
      }),
      { confirmLabel: 'Sample' },
    )
  }

  const onApplyMultiPeriod = () => {
    if (periods.length === 0) {
      toast.error('Add at least one investment year first.')
      return
    }
    if (mpMode === 'same') {
      if (!mpStart || !mpEnd) { toast.error('Pick start + end first'); return }
      confirmToast(
        `Build (period × timestep) MultiIndex with ${periods.length} periods × ` +
        `same operational range ${mpStart}…${mpEnd}? Time-series data will be re-aligned.`,
        () => applyMultiPeriodSnapshots.mutate({
          periods, start: mpStart, end: mpEnd, freq: mpFreq,
        }),
        { confirmLabel: 'Build MultiIndex' },
      )
    } else {
      for (let i = 0; i < periods.length; i++) {
        const r = mpPerPeriod[i]
        if (!r || !r.start || !r.end) {
          toast.error(`Fill start + end for period ${periods[i]}`)
          return
        }
      }
      confirmToast(
        `Build MultiIndex with ${periods.length} distinct operational ranges? ` +
        `Time-series data will be re-aligned.`,
        () => applyMultiPeriodSnapshots.mutate({
          periods,
          per_period: mpPerPeriod.map(r => ({ start: r.start, end: r.end, freq: r.freq || 'h' })),
        }),
        { confirmLabel: 'Build MultiIndex' },
      )
    }
  }

  // ── Weightings CSV import/export ─────────────────────────────────────────
  // Pagination + row-level rendering now live in StepWeightsAdvanced
  // (modelHorizon/StepWeights.tsx) — it takes the full `snap.weightings` and
  // slices its own page, so nothing about that derivation needs to live here.
  const onDownloadCsv = async () => {
    try {
      const blob = await networkApi.downloadSnapshotWeightingsCsv()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'snapshot_weightings.csv'
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Download failed'
      toast.error(msg)
    }
  }
  const onUploadCsv = (file: File | undefined) => {
    if (!file) return
    confirmToast(
      `Upload ${file.name} and overwrite the per-snapshot weights?`,
      async () => {
        try {
          const r = await networkApi.uploadSnapshotWeightingsCsv(file)
          qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'snapshots') })
          toast.success(
            `Applied ${r.applied} cell(s) across ${r.columns.join('/')}` +
            (r.skipped > 0 ? `, ${r.skipped} row(s) skipped` : ''),
          )
        } catch (e: unknown) {
          const msg = e instanceof Error ? e.message : 'Upload failed'
          toast.error(msg)
        }
      },
      { confirmLabel: 'Upload' },
    )
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Layout
  // ─────────────────────────────────────────────────────────────────────────
  // ── Derived display values for the StatCard strip ──────────────────────
  // Resolution is a property of the network, not of the form below. `freq`
  // state seeds a NEW index; it must never be read back as status.
  const freqLabel = resolutionLabel(snap?.freq)
  // Prefer the small investment-period list (`periods`, already in scope
  // above) over `snap.periods` — the latter is the PER-SNAPSHOT parallel
  // array (periods[i] = the period snapshots[i] belongs to), which on a
  // multi-period hourly model is tens of thousands of entries long. Fall
  // back to `snap.periods` only when `periods` is empty but the snapshot
  // index is still MultiIndex: `n.investment_periods` can be unset on a
  // MultiIndex network built without going through /investment_periods
  // (e.g. constructed directly), which yields an empty `ip.periods` but a
  // non-empty `snap.periods` — without the fallback the card would silently
  // stop showing the period span the moment that happens. `horizonRangeLabel`
  // is a single O(n) pass with no spread, so handing it the large array in
  // this fallback case is safe.
  const rangeStr = useMemo(
    () => horizonRangeLabel(
      snap?.snapshots,
      periods.length > 0 ? periods : snap?.periods,
      snapshotsAreMulti,
    ),
    [snap?.snapshots, snap?.periods, periods, snapshotsAreMulti],
  )
  // Warn-banner text when the toggle and the actual snapshot index disagree.
  const modeMismatch = isMultiPeriod && !snapshotsAreMulti
    ? 'toggle ON · snapshots still flat — build MultiIndex below'
    : !isMultiPeriod && snapshotsAreMulti
    ? 'toggle OFF · snapshots are MultiIndex — re-index below'
    : null

  // ── Guided-step routing ─────────────────────────────────────────────────
  // Which steps this project's mode shows, in rail order.
  const steps = useMemo(() => visibleSteps(isMultiPeriod), [isMultiPeriod])

  const [view, setView] = useState<HorizonStepId | 'summary'>('mode')
  // Decide the LANDING view exactly once per mount, from the network
  // (isHorizonUnset(snap?.count)) rather than any stored UI state — a user
  // who navigates away and back gets the same answer for the same project,
  // because this effect re-runs from scratch on the next mount. Waits for
  // the first successful `snap` load so an already-configured project never
  // flashes step 1 before settling on the summary (isHorizonUnset(undefined)
  // reads as unset, which is why the check below only fires once `snap` is
  // no longer undefined). Deliberately does NOT re-fire on later snapshot
  // refetches — an in-progress edit on some step must not get yanked to a
  // different view just because that edit changed snap.count.
  const enteredRef = useRef(false)
  useEffect(() => {
    if (enteredRef.current || snap === undefined) return
    enteredRef.current = true
    setView(isHorizonUnset(snap.count) ? 'mode' : 'summary')
  }, [snap])
  // Guards the multi-period toggle's cascade: turning "Multi-investment
  // periods" off while parked on 'years' / 'economics' (both multi-only)
  // removes those ids from `steps`, which would otherwise strand `view` on a
  // step the rail no longer lists and render an empty frame. Falls back to
  // 'mode' — always present — rather than to the summary, so the toggle
  // stays in view for whoever just changed it.
  useEffect(() => {
    if (view !== 'summary' && !steps.includes(view)) setView('mode')
  }, [steps, view])

  // Whether every snapshot weighting is still at its default (objective 1).
  // Read straight off `snap.weightings` — the FULL set, not the paginated
  // `weightingRows` slice — so the summary sentence is correct even when a
  // custom weight lives on a page the user hasn't scrolled to.
  const weightsAreDefault = useMemo(() => {
    const rows = (snap?.weightings ?? []) as WeightingRow[]
    return rows.every(r => {
      const raw = r.objective
      const n = typeof raw === 'number' ? raw : Number(raw)
      return !Number.isFinite(n) || n === 1
    })
  }, [snap?.weightings])

  // Feeds BOTH the StatCard strip above and the summary/rail below — same
  // `isMultiPeriod` / `periods` / `snap?.count` / `snap?.freq` / `rangeStr`
  // values already used for the strip, never re-derived, so the two can't
  // drift apart.
  const summaryCtx: HorizonSummaryContext = {
    isMultiPeriod,
    periods,
    snapshotCount: snap?.count,
    freq: snap?.freq,
    rangeLabel: rangeStr,
    canSampleWeeks: Boolean(snap?.can_sample_weeks),
    weightsAreDefault,
  }

  // Content for StepShell's `advanced` disclosure, per current step. Only
  // 'window' (the per-period range table, and only in its 'per_period' mode
  // with at least one period) and 'weights' (CSV controls + the paginated
  // per-row table) have anything to hide behind THIS slot — every other step
  // gets `undefined`, which is exactly what StepShell already treats as "no
  // disclosure at all" (unchanged from Task 3).
  //
  // 'economics' also has Advanced content (the per-carrier load-scaler
  // columns + CAPEX budget column) but does NOT go through this slot —
  // StepPeriodEconomics.tsx owns that disclosure itself, because it's a
  // column split of one live table rather than a block of content that can
  // be relocated below a separate <details>. See that file's header comment.
  //
  // Only 'weights' unmounts while collapsed. Its table can be 8,760 rows on
  // an hourly model — always mounting it would defeat the point of hiding
  // it. The window per-period table is at most a handful of rows (one per
  // investment period), so it uses StepShell's default: a native,
  // always-mounted <details> that stays reachable by in-page find, same as
  // SolverSettings.tsx / results/Economics.tsx.
  let advancedContent: ReactNode
  let unmountAdvancedWhenCollapsed = false
  if (view === 'window' && isMultiPeriod && mpMode === 'per_period' && periods.length > 0) {
    advancedContent = (
      <StepWindowAdvanced
        periods={periods}
        mpPerPeriod={mpPerPeriod}
        onMpPerPeriodChange={(i, patch) => setMpPerPeriod(prev => prev.map((r, j) => j === i ? { ...r, ...patch } : r))}
      />
    )
  } else if (view === 'weights' && snap && snap.weightings && snap.weightings.length > 0) {
    advancedContent = (
      <StepWeightsAdvanced
        weightings={snap.weightings as WeightingRow[]}
        snapshots={snap.snapshots ?? []}
        snapshotsAreMulti={snapshotsAreMulti}
        onWeightChange={(key, objective) => updateOneWeight.mutate({ key, objective })}
        updateOneWeightPending={updateOneWeight.isPending}
        onDownloadCsv={onDownloadCsv}
        onUploadCsv={onUploadCsv}
      />
    )
    unmountAdvancedWhenCollapsed = true
  }

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        eyebrow="SIMULATION · MODEL HORIZON"
        title="Snapshot range &amp; weightings"
        subtitle="Configure the time slice the optimizer sees. Smaller windows solve faster; full-year captures seasonality."
      />
      <div className="flex flex-col gap-5 px-8 py-5 overflow-y-auto flex-1 min-h-0 text-sm">

      {/* ── Status strip — always shown, above whichever view is routed ── */}
      <RowGrid cols={3}>
        <StatCard
          accent
          eyebrow="Snapshots"
          value={(snap?.count ?? 0).toLocaleString()}
          sub={rangeStr}
        />
        <StatCard
          eyebrow="Resolution"
          value={freqLabel}
          // `freqLabel` is measured from the actual (possibly flat) snapshot
          // index, not the toggle — so the sub-label must branch on
          // `snapshotsAreMulti`, not `isMultiPeriod`. With the toggle ON but
          // snapshots still flat (the state the mode-mismatch banner below
          // warns about), `isMultiPeriod` would claim "per investment
          // period" over a value that was measured across a flat index.
          sub={snapshotsAreMulti ? 'per investment period' : 'timestep spacing'}
        />
        <StatCard
          eyebrow="Mode"
          value={isMultiPeriod ? 'Multi-period' : 'Single-period'}
          delta={modeMismatch ?? undefined}
          deltaState={modeMismatch ? 'warn' : undefined}
          sub={isMultiPeriod
            ? `${periods.length} investment period${periods.length === 1 ? '' : 's'}${periods.length ? ` · ${periods.join(', ')}` : ''}`
            : 'capacity decided once'}
        />
      </RowGrid>

      {/* ── Guided steps, routed off `view` (Task 3 shell) ── */}
      {view === 'summary' ? (
        <HorizonSummary steps={steps} ctx={summaryCtx} onOpen={setView} />
      ) : (
        <div className="flex flex-col gap-2.5">
          <button
            type="button"
            onClick={() => setView('summary')}
            className="self-start text-[11px] text-accent hover:underline"
          >← Back to summary</button>
          <StepShell
            steps={steps}
            current={view}
            onSelect={setView}
            title={`Step ${steps.indexOf(view) + 1} of ${steps.length} — ${STEP_LABELS[view]}`}
            advanced={advancedContent}
            unmountAdvancedWhenCollapsed={unmountAdvancedWhenCollapsed}
          >

      {/* ── Mode toggle (the single decision point) ───────────── */}
      {/* Task 5: moved to modelHorizon/StepMode.tsx. */}
      {view === 'mode' && (
        <StepMode
          isMultiPeriod={isMultiPeriod}
          disabled={!cfg || toggleMultiPeriod.isPending}
          onToggle={enabled => toggleMultiPeriod.mutate(enabled)}
        />
      )}

      {/* ── Investment years (only when toggle ON) ──────────────── */}
      {/* Task 5: moved to modelHorizon/StepYears.tsx. `years` and `economics`
          used to share one physical <section> with a generic "Multi-period
          planning" <h3> (the same heading Task 4 also left, deliberately, in
          StepWindow.tsx). Now that all three period-level steps are split,
          each carries its own honest heading instead — StepYears' is
          "Investment years", StepPeriodEconomics' is "Economics". */}
      {isMultiPeriod && view === 'years' && (
        <StepYears
          periods={periods}
          onRemovePeriod={removePeriod}
          newPeriod={newPeriod}
          onNewPeriodChange={setNewPeriod}
          onAddPeriod={y => {
            const next = Array.from(new Set([...periods, y])).sort((a, b) => a - b)
            applyPeriods.mutate(next)
          }}
          addPeriodPending={applyPeriods.isPending}
        />
      )}

      {/* ── Economics: period weightings + PV preview (only when toggle ON) ── */}
      {/* Task 5: moved to modelHorizon/StepPeriodEconomics.tsx. The per-carrier
          load-scaler columns and the CAPEX budget column render behind that
          component's OWN internal Advanced disclosure — not StepShell's
          `advanced` slot, see that file's header comment for why a column
          split of one table can't use the same "second block below a
          <details>" shape StepWindow/StepWeights use for their Advanced
          content. Accordingly `advancedContent` below still only branches on
          'window' and 'weights' — 'economics' deliberately gets no StepShell
          advanced content, same as 'mode' and 'years'. */}
      {isMultiPeriod && view === 'economics' && (
        <StepPeriodEconomics
          periods={periods}
          periodWeightings={periodWeightings}
          noPeriodsFallback={
            <NoPeriodsFallback
              message={stepSummary('economics', summaryCtx)}
              onGoToYears={() => setView('years')}
            />
          }
          refPeriod={refPeriod}
          autoDiscountOn={autoDiscountOn}
          discountRate={cfg?.discount_rate ?? 0}
          inflationRate={cfg?.inflation_rate ?? 0}
          bulkYears={bulkYears}
          onBulkYearsChange={setBulkYears}
          onApplyBulkYears={v => applyBulkPeriodYears.mutate(v)}
          applyBulkYearsPending={applyBulkPeriodYears.isPending}
          bulkObjective={bulkObjective}
          onBulkObjectiveChange={setBulkObjective}
          onApplyBulkObjective={v => applyBulkPeriodObjective.mutate(v)}
          applyBulkObjectivePending={applyBulkPeriodObjective.isPending}
          autoDiscountChecked={Boolean(cfg?.auto_discount_periods)}
          onAutoDiscountChange={enabled => updateAutoDiscount.mutate(enabled)}
          onPeriodColChange={args => updateOnePeriodCol.mutate(args)}
          updatePeriodColPending={updateOnePeriodCol.isPending}
          loadCarriers={networkLoadCarriers.map(c => ({ key: c, label: loadCarrierLabel(c) }))}
          loadScalersByCarrier={cfg?.load_scalers_by_carrier ?? {}}
          legacyLoadScalers={cfg?.load_scalers ?? {}}
          onLoadScalerChange={(carrier, period, value) => {
            // Wholesale PUT: clone the nested map, ensure the per-carrier
            // slot exists, write the new value. 1.0 entries are dropped so
            // the server-side map stays compact (anything missing falls
            // back to legacy load_scalers or 1.0 anyway).
            const next: Record<string, Record<string, number>> = {}
            for (const [c, m] of Object.entries(cfg?.load_scalers_by_carrier ?? {})) {
              next[c] = { ...m }
            }
            const slot = next[carrier] ?? {}
            if (value === 1) delete slot[String(period)]
            else slot[String(period)] = value
            if (Object.keys(slot).length === 0) delete next[carrier]
            else next[carrier] = slot
            updateLoadScalersByCarrier.mutate(next)
          }}
          loadScalerPending={updateLoadScalersByCarrier.isPending}
          capexBudgetPerPeriod={cfg?.capex_budget_per_period ?? {}}
          onCapexBudgetChange={(period, valueEur) => {
            const next: Record<string, number> = { ...(cfg?.capex_budget_per_period ?? {}) }
            if (valueEur == null) delete next[String(period)]
            else next[String(period)] = valueEur
            updateCapexBudget.mutate(next)
          }}
          capexBudgetPending={updateCapexBudget.isPending}
        />
      )}

      {/* ── Snapshot window — both single- and multi-period forms
          (Task 4: moved to modelHorizon/StepWindow.tsx) ──────────────── */}
      {view === 'window' && (
        <StepWindow
          isMultiPeriod={isMultiPeriod}
          periods={periods}
          noPeriodsFallback={
            <NoPeriodsFallback
              message="Add investment years first — the MultiIndex snapshot constructor needs at least one period to build a (period × timestep) index."
              onGoToYears={() => setView('years')}
            />
          }
          start={start}
          end={end}
          freq={freq}
          onStartChange={v => { rangeTouchedRef.current = true; setStart(v) }}
          onEndChange={v => { rangeTouchedRef.current = true; setEnd(v) }}
          onFreqChange={setFreq}
          resetRangeLabel={
            snap?.ts_start && snap?.ts_end
              ? `${toLocal(snap.ts_start).slice(0, 10)} → ${toLocal(snap.ts_end).slice(0, 10)}`
              : null
          }
          onResetToUploadedRange={() => {
            if (!snap?.ts_start || !snap?.ts_end) return
            setStart(toLocal(snap.ts_start))
            setEnd(toLocal(snap.ts_end))
            rangeTouchedRef.current = false
          }}
          onApplySnapshots={onApplySnapshots}
          applySnapshotsPending={applySnapshots.isPending}
          mpMode={mpMode}
          onMpModeChange={setMpMode}
          mpStart={mpStart}
          mpEnd={mpEnd}
          mpFreq={mpFreq}
          onMpStartChange={setMpStart}
          onMpEndChange={setMpEnd}
          onMpFreqChange={setMpFreq}
          onApplyMultiPeriod={onApplyMultiPeriod}
          applyMultiPeriodPending={applyMultiPeriodSnapshots.isPending}
        />
      )}

      {/* ── Representative weeks (works in both modes) ────────── */}
      {/* Samples random ISO weeks from an uploaded full-year hourly profile.
          Adapts automatically: flat networks get a flat sampled index,
          multi-period networks get the sample replicated under every period.
          Gated on snap.can_sample_weeks — the backend reports whether a
          full-year hourly profile is actually available.
          Task 4: moved to modelHorizon/StepSampling.tsx. */}
      {view === 'sampling' && (
        <StepSampling
          canSampleWeeks={Boolean(snap?.can_sample_weeks)}
          sampleNWeeks={sampleNWeeks}
          onSampleNWeeksChange={setSampleNWeeks}
          sampleSeed={sampleSeed}
          onSampleSeedChange={setSampleSeed}
          onSampleWeeks={onSampleWeeks}
          sampleWeeksPending={sampleWeeks.isPending}
          sampledWeeks={sampledWeeks}
        />
      )}

      {/* ── Snapshot weightings (always shown) ────────────────── */}
      {/* Task 4: moved to modelHorizon/StepWeights.tsx. The bulk apply-to-all
          control stays in the main step body; the CSV controls and the
          paginated per-row table (unusable as an always-visible element at
          8,760-row scale) render behind StepShell's `advanced` slot — see
          the `advanced` prop on <StepShell> below for the composition. */}
      {view === 'weights' && snap && snap.weightings && snap.weightings.length > 0 && (
        <StepWeights
          bulkWeight={bulkWeight}
          onBulkWeightChange={setBulkWeight}
          onApplyBulkWeight={v => applyBulkWeight.mutate(v)}
          applyBulkWeightPending={applyBulkWeight.isPending}
        />
      )}
          </StepShell>
        </div>
      )}
      </div>
    </div>
  )
}

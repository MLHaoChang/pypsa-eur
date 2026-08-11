import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Download, Plus, Upload, X } from 'lucide-react'
import { confirmToast } from '../utils/toasts'
import { networkApi } from '../api/network'
import { simulationApi } from '../api/simulation'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { PageHeader, RowGrid, StatCard } from '../components/PageKit'
import type { Load } from '../api/types'
import { buildWeightingRows, resolutionLabel, FREQ_OPTIONS, pvFactor, horizonRangeLabel, type WeightingRow } from './modelHorizonModel'

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

// User flow this page implements (top → bottom):
//   1. Status header — what the network is RIGHT NOW (mode, periods, snapshot
//      count + breakdown). Read-only summary so the user grounds themselves.
//   2. Mode toggle — "Is this a multi-period model?" A single decision point.
//   3. Mode-specific config — multi-period (years + period weights + MultiIndex
//      constructor) when ON, single-period snapshot range when OFF.
//   4. Snapshot weightings — used in both modes. Inline pagination for hourly
//      horizons + CSV download/upload for bulk edits at 8760-row scale.

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

const PAGE_SIZE = 100

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

  // Period chip add/remove (multi-period mode).
  const [newPeriod, setNewPeriod] = useState<string>('')
  const addPeriod = () => {
    const y = parseInt(newPeriod, 10)
    if (!Number.isFinite(y) || y < 1900 || y > 2200) {
      toast.error('Year must be between 1900 and 2200')
      return
    }
    const next = Array.from(new Set([...periods, y])).sort((a, b) => a - b)
    applyPeriods.mutate(next)
    setNewPeriod('')
  }
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

  // ── Weightings table: pagination + CSV import/export ────────────────────
  const totalWeightings = snap?.weightings?.length ?? 0
  const totalPages = Math.max(1, Math.ceil(totalWeightings / PAGE_SIZE))
  const [page, setPage] = useState(0)
  useEffect(() => {
    // Clamp when the underlying snapshot set shrinks (e.g. after re-indexing).
    if (page > 0 && page >= totalPages) setPage(0)
  }, [totalPages, page])
  const pageStart = page * PAGE_SIZE
  const pageEnd = Math.min(pageStart + PAGE_SIZE, totalWeightings)
  const pageRows = useMemo(
    () => snap?.weightings?.slice(pageStart, pageEnd) ?? [],
    [snap?.weightings, pageStart, pageEnd],
  )
  const weightingRows = useMemo(
    () => buildWeightingRows(
      pageRows as WeightingRow[],
      snap?.snapshots ?? [],
      snapshotsAreMulti,
      pageStart,
    ),
    [pageRows, snap?.snapshots, snapshotsAreMulti, pageStart],
  )

  const csvUploadRef = useRef<HTMLInputElement>(null)
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

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        eyebrow="SIMULATION · MODEL HORIZON"
        title="Snapshot range &amp; weightings"
        subtitle="Configure the time slice the optimizer sees. Smaller windows solve faster; full-year captures seasonality."
      />
      <div className="flex flex-col gap-5 px-8 py-5 overflow-y-auto flex-1 min-h-0 text-sm">

      {/* ── 1. Status — StatCard strip ───────────────────────── */}
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

      {/* ── 2. Mode toggle (the single decision point) ───────── */}
      <section className="border border-border rounded-[10px] bg-bg p-3.5 shadow-[0_1px_0_rgba(10,14,20,0.04)]">
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={isMultiPeriod}
            disabled={!cfg || toggleMultiPeriod.isPending}
            onChange={e => toggleMultiPeriod.mutate(e.target.checked)}
            className="accent-accent mt-0.5"
          />
          <div>
            <div className="font-semibold text-text">Multi-investment periods</div>
            <p className="text-[11px] text-muted mt-0.5 leading-relaxed">
              Enables PyPSA's multi-horizon LP. The optimiser sizes new capacity
              for several investment years (e.g. <code>2030/2040/2050</code>),
              each with its own operational profile. Leave OFF for a single
              operational range with capacity decided once.
            </p>
          </div>
        </label>
      </section>

      {/* ── 3a. Multi-period config (only when toggle ON) ────── */}
      {isMultiPeriod && (
        <section>
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2.5">Multi-period planning</h3>

          {/* Investment years */}
          <div className="border border-border rounded mb-3">
            <div className="px-2.5 py-1.5 border-b border-border bg-bg-2 text-[9px] font-bold uppercase tracking-[0.14em] text-muted">
              Investment years
            </div>
            <div className="p-2.5">
              <p className="text-[11px] text-muted mb-2 leading-relaxed">
                Each year becomes one optimisation period. Capacity decisions are
                made independently per period; assets must have <code>build_year</code>
                ≤ period to be available, and are retired at
                <code> build_year + lifetime</code>.
              </p>
              <div className="flex flex-wrap items-center gap-1">
                {periods.map(y => (
                  <span
                    key={y}
                    className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-border bg-bg text-[11px] font-mono"
                  >
                    {y}
                    <button
                      onClick={() => removePeriod(y)}
                      className="text-muted hover:text-danger"
                      title={`Remove ${y}`}
                    ><X size={10} /></button>
                  </span>
                ))}
                <input
                  type="number"
                  value={newPeriod}
                  onChange={e => setNewPeriod(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') addPeriod() }}
                  placeholder="add year"
                  className="w-20 px-1.5 py-0.5 border border-border rounded text-[11px] font-mono bg-bg"
                />
                <button
                  onClick={addPeriod}
                  disabled={!newPeriod.trim() || applyPeriods.isPending}
                  className="px-1.5 py-0.5 border border-border rounded text-[11px] text-accent hover:border-accent disabled:opacity-40"
                ><Plus size={10} /></button>
              </div>
              {periods.length === 0 && (
                <p className="text-[10px] text-warn mt-2">
                  No years yet — add at least one before solving.
                </p>
              )}
            </div>
          </div>

          {/* Period weightings (years + objective per period) */}
          {periods.length > 0 && (
            <div className="border border-border rounded mb-3">
              <div className="px-2.5 py-1.5 border-b border-border bg-bg-2 text-[9px] font-bold uppercase tracking-[0.14em] text-muted flex items-center justify-between">
                <span>Period weightings</span>
                <span className="text-[10px] text-muted/70 normal-case">years × objective per period</span>
              </div>
              <div className="p-2.5 flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    step="0.1"
                    min={0}
                    placeholder="Apply years to all (e.g. 10)"
                    value={bulkYears}
                    onChange={e => setBulkYears(e.target.value)}
                    className="flex-1 px-2 py-1 border border-border rounded text-[11px] bg-bg font-mono"
                  />
                  <button
                    disabled={!bulkYears || applyBulkPeriodYears.isPending}
                    onClick={() => {
                      const v = parseFloat(bulkYears)
                      if (!Number.isFinite(v) || v < 0) {
                        toast.error('Enter a non-negative number')
                        return
                      }
                      applyBulkPeriodYears.mutate(v)
                      setBulkYears('')
                    }}
                    className="px-2 py-1 bg-accent/80 text-white rounded text-[11px] font-medium hover:bg-accent disabled:opacity-40"
                  >Apply years</button>
                </div>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    step="0.01"
                    min={0}
                    placeholder="Apply objective weight to all (e.g. 0.6)"
                    value={bulkObjective}
                    onChange={e => setBulkObjective(e.target.value)}
                    className="flex-1 px-2 py-1 border border-border rounded text-[11px] bg-bg font-mono"
                  />
                  <button
                    disabled={!bulkObjective || applyBulkPeriodObjective.isPending}
                    onClick={() => {
                      const v = parseFloat(bulkObjective)
                      if (!Number.isFinite(v) || v < 0) {
                        toast.error('Enter a non-negative number')
                        return
                      }
                      applyBulkPeriodObjective.mutate(v)
                      setBulkObjective('')
                    }}
                    className="px-2 py-1 bg-accent/80 text-white rounded text-[11px] font-medium hover:bg-accent disabled:opacity-40"
                  >Apply objective</button>
                </div>

                {/* Auto-discount: the ONLY automated path to ipw.objective.
                    Writes PV × years per period at LP build time using the
                    solver settings' discount_rate and inflation_rate, and
                    reverts in restore() so the on-disk network keeps whatever
                    the user typed. The PV column in the table below previews
                    exactly what it will write. Manual edits to the objective
                    cell still work and are what to use for one-off factors. */}
                <label className="flex items-center gap-2 mt-1 pt-2 border-t border-border/60 text-[10px] text-muted">
                  <input
                    type="checkbox"
                    checked={Boolean(cfg?.auto_discount_periods)}
                    onChange={e => updateAutoDiscount.mutate(e.target.checked)}
                    className="cursor-pointer"
                  />
                  <span className="select-none">
                    Auto-discount: overwrite <code>objective</code> per period at solve time using
                    <code> discount_rate</code> from solver settings. Stops the LP from front-loading
                    all CAPEX into period 1.
                  </span>
                </label>

                {/* Per-period inline editor */}
                <div className="border border-border rounded overflow-auto max-h-64 mt-1">
                  {/* 400 → 480: gained the PV × preview column. Same +80
                      bump the weightings table below took (480 → 560) for
                      its new Period column. */}
                  <table className="w-full text-[11px] border-collapse" style={{ minWidth: 480 }}>
                    <thead className="sticky top-0 bg-bg-2 z-10">
                      <tr className="border-b border-border">
                        <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Period</th>
                        <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Years</th>
                        <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Objective</th>
                        <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase"
                            title="What Auto-discount will write into `objective` at solve time: (1+real rate)^-(period-first period) x years. Preview only — nothing is written until you solve.">
                          PV ×<br />preview
                        </th>
                        {networkLoadCarriers.map(carrier => (
                          <th key={`hdr-load-${carrier}`}
                              className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase"
                              title={`Per-period load multiplier for carrier "${carrier}". 1.00 = unchanged, 1.10 = +10% growth. Applied to every load whose carrier canonicalises to "${carrier}".`}>
                            Load ×<br />{loadCarrierLabel(carrier)}
                          </th>
                        ))}
                        <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase"
                          title="Per-period upper bound on overnight CAPEX (€). LP enforces Σ overnight_cost × Δp_nom ≤ budget for all extendable assets with build_year=P. Leave 0 (or empty) for unconstrained.">Budget M€</th>
                      </tr>
                    </thead>
                    <tbody>
                      {periodWeightings.map((row, i) => {
                        const period = Number(row.period ?? row.name ?? periods[i] ?? 0)
                        const years = Number(row.years ?? 1)
                        const objective = Number(row.objective ?? 1)
                        // Resolve scaler per carrier: prefer the new
                        // `load_scalers_by_carrier[carrier][period]`, fall
                        // back to legacy `load_scalers[period]` (applied to
                        // every carrier when there's no per-carrier entry).
                        const byCarrier = cfg?.load_scalers_by_carrier ?? {}
                        const legacyScaler = Number(cfg?.load_scalers?.[String(period)] ?? 1)
                        const resolvedScaler = (carrier: string): number => {
                          const v = byCarrier[carrier]?.[String(period)]
                          return v != null && Number.isFinite(v) ? Number(v) : legacyScaler
                        }
                        const budgetEur = Number(cfg?.capex_budget_per_period?.[String(period)] ?? 0)
                        const budgetMeur = budgetEur > 0 ? budgetEur / 1e6 : 0
                        return (
                          <tr key={period} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                            <td className="px-2 py-1 font-mono text-[11px]">{period}</td>
                            <td className="px-2 py-1 text-right">
                              <input
                                key={`y-${period}-${years}`}
                                type="number"
                                step="0.1"
                                min={0}
                                defaultValue={years.toFixed(2)}
                                // Disable while a sibling mutation is in
                                // flight — without this, a fast double-blur
                                // (user clicks away, immediately clicks back
                                // in & blurs again) fires two PUTs racing
                                // each other; the second one's response can
                                // overwrite the first's success state.
                                disabled={updateOnePeriodCol.isPending}
                                onBlur={e => {
                                  const v = parseFloat(e.target.value)
                                  if (!Number.isFinite(v) || v < 0) {
                                    e.target.value = String(years)
                                    return
                                  }
                                  if (v !== years) {
                                    updateOnePeriodCol.mutate({ period, col: 'years', value: v })
                                  }
                                }}
                                className="w-20 px-1 py-0.5 border border-border rounded text-[11px] font-mono bg-bg focus:outline-none focus:border-accent text-right disabled:opacity-50 disabled:cursor-wait"
                              />
                            </td>
                            <td className="px-2 py-1 text-right">
                              <input
                                key={`o-${period}-${objective}`}
                                type="number"
                                step="0.01"
                                min={0}
                                defaultValue={objective.toFixed(4)}
                                disabled={updateOnePeriodCol.isPending}
                                onBlur={e => {
                                  const v = parseFloat(e.target.value)
                                  if (!Number.isFinite(v) || v < 0) {
                                    e.target.value = String(objective)
                                    return
                                  }
                                  if (v !== objective) {
                                    updateOnePeriodCol.mutate({ period, col: 'objective', value: v })
                                  }
                                }}
                                className="w-24 px-1 py-0.5 border border-border rounded text-[11px] font-mono bg-bg focus:outline-none focus:border-accent text-right disabled:opacity-50 disabled:cursor-wait"
                              />
                            </td>
                            <td className={`px-2 py-1 text-right font-mono text-[11px] ${autoDiscountOn ? 'text-text' : 'text-muted/40'}`}
                                title={autoDiscountOn
                                  ? `Auto-discount will set objective = ${pvFactor({ period, refPeriod, years, discountRate: cfg?.discount_rate ?? 0, inflationRate: cfg?.inflation_rate ?? 0 }).toFixed(4)} at solve time, overriding the value on the left.`
                                  : 'Auto-discount is off — the objective value on the left is what the LP uses.'}>
                              {pvFactor({
                                period,
                                refPeriod,
                                years,
                                discountRate: cfg?.discount_rate ?? 0,
                                inflationRate: cfg?.inflation_rate ?? 0,
                              }).toFixed(4)}
                            </td>
                            {networkLoadCarriers.map(carrier => {
                              const v = resolvedScaler(carrier)
                              return (
                                <td key={`ls-${period}-${carrier}`} className="px-2 py-1 text-right">
                                  <input
                                    key={`ls-${period}-${carrier}-${v}`}
                                    type="number"
                                    step="0.01"
                                    min={0.01}
                                    defaultValue={v.toFixed(2)}
                                    title={`Load multiplier for "${carrier}" in period ${period} (1.00 = unchanged, 1.10 = +10% growth)`}
                                    disabled={updateLoadScalersByCarrier.isPending}
                                    onBlur={e => {
                                      const nv = parseFloat(e.target.value)
                                      if (!Number.isFinite(nv) || nv <= 0) {
                                        e.target.value = v.toFixed(2)
                                        return
                                      }
                                      if (nv === v) return
                                      // Wholesale PUT: clone the nested map,
                                      // ensure the per-carrier slot exists,
                                      // write the new value. 1.0 entries are
                                      // dropped so the server-side map stays
                                      // compact (anything missing falls back
                                      // to legacy load_scalers or 1.0 anyway).
                                      const next: Record<string, Record<string, number>> = {}
                                      for (const [c, m] of Object.entries(byCarrier ?? {})) {
                                        next[c] = { ...m }
                                      }
                                      const slot = next[carrier] ?? {}
                                      if (nv === 1) delete slot[String(period)]
                                      else slot[String(period)] = nv
                                      if (Object.keys(slot).length === 0) delete next[carrier]
                                      else next[carrier] = slot
                                      updateLoadScalersByCarrier.mutate(next)
                                    }}
                                    className="w-20 px-1 py-0.5 border border-border rounded text-[11px] font-mono bg-bg focus:outline-none focus:border-accent text-right disabled:opacity-50 disabled:cursor-wait"
                                  />
                                </td>
                              )
                            })}
                            <td className="px-2 py-1 text-right">
                              <input
                                key={`b-${period}-${budgetMeur}`}
                                type="number"
                                step="10"
                                min={0}
                                defaultValue={budgetMeur > 0 ? budgetMeur.toFixed(0) : ''}
                                placeholder="—"
                                title="CAPEX budget for this period in millions of EUR. 0 / empty = unconstrained"
                                disabled={updateCapexBudget.isPending}
                                onBlur={e => {
                                  const raw = e.target.value.trim()
                                  const next: Record<string, number> = { ...(cfg?.capex_budget_per_period ?? {}) }
                                  if (!raw) {
                                    delete next[String(period)]
                                  } else {
                                    const v = parseFloat(raw)
                                    if (!Number.isFinite(v) || v < 0) {
                                      e.target.value = budgetMeur > 0 ? budgetMeur.toFixed(0) : ''
                                      return
                                    }
                                    if (v === 0) {
                                      delete next[String(period)]
                                    } else {
                                      next[String(period)] = v * 1e6  // M€ → €
                                    }
                                  }
                                  // Only send the PUT if the stored value actually changed.
                                  if ((next[String(period)] ?? 0) !== budgetEur) {
                                    updateCapexBudget.mutate(next)
                                  }
                                }}
                                className="w-20 px-1 py-0.5 border border-border rounded text-[11px] font-mono bg-bg focus:outline-none focus:border-accent text-right disabled:opacity-50 disabled:cursor-wait"
                              />
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>

                <p className="text-[10px] text-muted leading-relaxed">
                  <code>years</code> = calendar years the period stands in for
                  (period 2030 with <code>years=10</code> → 2030–2039).
                  <code> objective</code> = LP-objective weight. When
                  Auto-discount is ON the <code>PV × preview</code> column is
                  what actually reaches the LP; the value you type here is
                  overridden at solve time and restored afterwards. With
                  Auto-discount OFF, what you type is what the LP uses.
                  <code>Load × · {'{carrier}'}</code> =
                  per-carrier load growth multiplier — loads of that carrier
                  are scaled independently per period at solve time
                  (<code>1.00</code> = unchanged, <code>1.10</code> = +10 %).
                  Each carrier present in the network gets its own column;
                  set Hydrogen ×1.5 in 2030 without affecting Electrical.
                  <code> Budget M€</code> = upper bound on total NEW overnight
                  CAPEX for assets built in this period; blank = unconstrained.
                  Defaults: 1.
                </p>
              </div>
            </div>
          )}

          {/* Snapshot constructor (MultiIndex) */}
          {periods.length > 0 && (
            <div className="border border-border rounded">
              <div className="px-2.5 py-1.5 border-b border-border bg-bg-2 text-[9px] font-bold uppercase tracking-[0.14em] text-muted flex items-center justify-between">
                <span>Snapshot constructor (MultiIndex)</span>
                <span className="text-[10px] text-muted/70 normal-case">builds (period × timestep) snapshots</span>
              </div>
              <div className="p-2.5 flex flex-col gap-2.5">
                <div className="flex items-center gap-3 text-[11px]">
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="radio"
                      name="mp-mode"
                      checked={mpMode === 'same'}
                      onChange={() => setMpMode('same')}
                      className="accent-accent"
                    />
                    Same year per period
                  </label>
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="radio"
                      name="mp-mode"
                      checked={mpMode === 'per_period'}
                      onChange={() => setMpMode('per_period')}
                      className="accent-accent"
                    />
                    Different year per period
                  </label>
                </div>

                {mpMode === 'same' ? (
                  <div className="flex flex-col gap-2">
                    <p className="text-[10px] text-muted leading-relaxed">
                      One operational (start, end, freq) range replicated under
                      every investment period. Canonical workflow when you have
                      one year of weather/load data and want to use it as a
                      representative profile for every decade.
                    </p>
                    <div className="grid grid-cols-3 gap-2">
                      <label className="flex flex-col gap-0.5">
                        <span className="text-[10px] text-muted">Start</span>
                        <input
                          type="datetime-local" lang="en-US"
                          value={mpStart}
                          onChange={e => setMpStart(e.target.value)}
                          className="px-2 py-1 border border-border rounded text-[11px] bg-bg"
                        />
                      </label>
                      <label className="flex flex-col gap-0.5">
                        <span className="text-[10px] text-muted">End</span>
                        <input
                          type="datetime-local" lang="en-US"
                          value={mpEnd}
                          onChange={e => setMpEnd(e.target.value)}
                          className="px-2 py-1 border border-border rounded text-[11px] bg-bg"
                        />
                      </label>
                      <label className="flex flex-col gap-0.5">
                        <span className="text-[10px] text-muted">Resolution</span>
                        <select
                          value={mpFreq}
                          onChange={e => setMpFreq(e.target.value)}
                          className="px-2 py-1 border border-border rounded text-[11px] bg-bg"
                        >
                          {FREQ_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                        </select>
                      </label>
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-col gap-2">
                    <p className="text-[10px] text-muted leading-relaxed">
                      One (start, end, freq) per investment period. Use when you
                      have multi-year weather data and want each decade to see a
                      different operational year.
                    </p>
                    <div className="border border-border rounded overflow-auto max-h-64">
                      <table className="w-full text-[11px] border-collapse" style={{ minWidth: 480 }}>
                        <thead className="sticky top-0 bg-bg-2 z-10">
                          <tr className="border-b border-border">
                            <th className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Period</th>
                            <th className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Start</th>
                            <th className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">End</th>
                            <th className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Freq</th>
                          </tr>
                        </thead>
                        <tbody>
                          {periods.map((p, i) => {
                            const row = mpPerPeriod[i] ?? { start: '', end: '', freq: 'h' }
                            const updateRow = (patch: Partial<typeof row>) => {
                              setMpPerPeriod(prev => prev.map((r, j) => j === i ? { ...r, ...patch } : r))
                            }
                            return (
                              <tr key={p} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                                <td className="px-2 py-1 font-mono text-[11px]">{p}</td>
                                <td className="px-2 py-1">
                                  <input
                                    type="datetime-local" lang="en-US"
                                    value={row.start}
                                    onChange={e => updateRow({ start: e.target.value })}
                                    className="w-full px-1 py-0.5 border border-border rounded text-[11px] bg-bg"
                                  />
                                </td>
                                <td className="px-2 py-1">
                                  <input
                                    type="datetime-local" lang="en-US"
                                    value={row.end}
                                    onChange={e => updateRow({ end: e.target.value })}
                                    className="w-full px-1 py-0.5 border border-border rounded text-[11px] bg-bg"
                                  />
                                </td>
                                <td className="px-2 py-1">
                                  <select
                                    value={row.freq}
                                    onChange={e => updateRow({ freq: e.target.value })}
                                    className="w-full px-1 py-0.5 border border-border rounded text-[11px] bg-bg"
                                  >
                                    {FREQ_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.value}</option>)}
                                  </select>
                                </td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                <button
                  onClick={onApplyMultiPeriod}
                  disabled={applyMultiPeriodSnapshots.isPending}
                  className="w-full px-3 py-1.5 bg-accent text-white rounded text-xs font-medium hover:bg-accent/90 disabled:opacity-40"
                >
                  {applyMultiPeriodSnapshots.isPending ? 'Building…' : 'Build MultiIndex snapshots'}
                </button>
                <p className="text-[10px] text-muted leading-relaxed">
                  This replaces the snapshot index with a 2-level
                  (period, timestep) MultiIndex. Uploaded time-series profiles
                  are re-aligned by their operational timestamp — a 1-year upload
                  becomes the profile under every period.
                </p>
              </div>
            </div>
          )}
        </section>
      )}

      {/* ── 3b. Single-period config (only when toggle OFF) ──── */}
      {!isMultiPeriod && (
        <section>
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2.5">Snapshot range</h3>
          <p className="text-[11px] text-muted mb-2 leading-relaxed">
            Defines the single operational window the LP spans. The index is
            built as <code>pd.date_range(start, end, freq)</code>. Time-series
            uploads are re-aligned by datetime intersection.
          </p>
          <div className="flex flex-col gap-2">
            <label className="flex flex-col gap-1">
              <span className="text-[11px] text-muted">Start</span>
              <input
                type="datetime-local" lang="en-US" value={start}
                onChange={e => { rangeTouchedRef.current = true; setStart(e.target.value) }}
                className="px-2 py-1 border border-border rounded text-xs bg-bg"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[11px] text-muted">End</span>
              <input
                type="datetime-local" lang="en-US" value={end}
                onChange={e => { rangeTouchedRef.current = true; setEnd(e.target.value) }}
                className="px-2 py-1 border border-border rounded text-xs bg-bg"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[11px] text-muted">Resolution</span>
              <select
                value={freq} onChange={e => setFreq(e.target.value)}
                className="px-2 py-1 border border-border rounded text-xs bg-bg"
              >
                {FREQ_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
          </div>
          {/* Snap back to the uploaded time-series extent — the data range the
              user actually uploaded. Only shown when flat profiles exist. */}
          {snap?.ts_start && snap?.ts_end && (
            <button
              onClick={() => {
                setStart(toLocal(snap.ts_start!))
                setEnd(toLocal(snap.ts_end!))
                rangeTouchedRef.current = false
              }}
              className="mt-2 text-[10.5px] text-accent hover:underline"
            >↻ Reset to uploaded data range ({toLocal(snap.ts_start).slice(0, 10)} → {toLocal(snap.ts_end).slice(0, 10)})</button>
          )}
          <button
            onClick={onApplySnapshots}
            disabled={applySnapshots.isPending}
            className="mt-3 w-full px-3 py-1.5 bg-accent text-white rounded text-xs font-medium hover:bg-accent/90 disabled:opacity-40"
          >{applySnapshots.isPending ? 'Applying…' : 'Apply snapshots'}</button>
        </section>
      )}

      {/* ── 3c. Representative weeks (works in both modes) ───── */}
      {/* Samples random ISO weeks from an uploaded full-year hourly profile.
          Adapts automatically: flat networks get a flat sampled index,
          multi-period networks get the sample replicated under every period.
          Gated on snap.can_sample_weeks — the backend reports whether a
          full-year hourly profile is actually available. */}
      <section>
        <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2.5">Representative weeks</h3>
        <p className="text-[11px] text-muted mb-2 leading-relaxed">
          Sample <code>N</code> random ISO calendar weeks (Mon–Sun) per month
          from an uploaded full-year hourly profile — e.g. 1 week/month →
          12 × 168 = <span className="font-mono">2016</span> snapshots instead
          of 8760. Each sampled hour is weighted by{' '}
          <code>days-in-month / (weeks × 7)</code> so dispatch, cost and
          emissions still aggregate to a full year.{' '}
          {!snap?.can_sample_weeks && (
            <span className="text-warn">
              Disabled — upload a full-year hourly profile on the Time Series
              page first.
            </span>
          )}
        </p>
        <div className="flex items-end gap-2">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-muted">Weeks / month</span>
            <input
              type="number" min={1} max={5} step={1} value={sampleNWeeks}
              onChange={e => setSampleNWeeks(e.target.value)}
              className="w-24 px-2 py-1 border border-border rounded text-xs bg-bg font-mono"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-muted">Seed (optional)</span>
            <input
              type="number" placeholder="random" value={sampleSeed}
              onChange={e => setSampleSeed(e.target.value)}
              title="Fix the RNG seed for reproducible sampling — leave blank for a fresh random draw"
              className="w-28 px-2 py-1 border border-border rounded text-xs bg-bg font-mono"
            />
          </label>
          <button
            onClick={onSampleWeeks}
            disabled={!snap?.can_sample_weeks || sampleWeeks.isPending}
            className="px-3 py-1.5 bg-accent text-white rounded text-xs font-medium hover:bg-accent/90 disabled:opacity-40"
          >
            {sampleWeeks.isPending
              ? 'Sampling…'
              : sampledWeeks.length > 0 ? 'Re-sample' : 'Sample weeks'}
          </button>
        </div>
        {sampledWeeks.length > 0 && (
          <div className="border border-border rounded overflow-auto max-h-48 mt-2">
            <table className="w-full text-[11px] border-collapse" style={{ minWidth: 340 }}>
              <thead className="sticky top-0 bg-bg-2 z-10">
                <tr className="border-b border-border">
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Month</th>
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">ISO week</th>
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Range</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Weight</th>
                </tr>
              </thead>
              <tbody>
                {sampledWeeks.map((w, i) => (
                  <tr key={`${w.month}-${w.iso_week}-${i}`} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                    <td className="px-2 py-1 font-mono">
                      {new Date(2000, w.month - 1, 1).toLocaleString('en-US', { month: 'short' })}
                    </td>
                    <td className="px-2 py-1 font-mono">W{w.iso_week}</td>
                    <td className="px-2 py-1 font-mono text-muted">
                      {w.start.slice(0, 10)} → {w.end.slice(0, 10)}
                    </td>
                    <td className="px-2 py-1 font-mono text-right">{w.weight.toFixed(2)}×</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ── 4. Snapshot weightings (always shown) ────────────── */}
      {snap && snap.weightings && snap.weightings.length > 0 && (
        <section>
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2.5">Snapshot weightings</h3>
          <p className="text-[11px] text-muted mb-2 leading-relaxed">
            Per-snapshot weight in the LP objective and storage SoC equations.
            Default <code>1</code>. For representative-day workflows set
            <code> all</code> to the number of days each snapshot represents
            (e.g. <code>30</code> for one typical day × 30 days). Use CSV for
            hourly horizons where row-by-row editing isn't practical.
          </p>

          {/* Apply-to-all row */}
          <div className="flex items-center gap-2 mb-2">
            <input
              type="number"
              step="0.1"
              min={0}
              placeholder="Apply to all (e.g. 30)"
              value={bulkWeight}
              onChange={e => setBulkWeight(e.target.value)}
              className="flex-1 px-2 py-1 border border-border rounded text-xs bg-bg font-mono"
            />
            <button
              disabled={!bulkWeight || applyBulkWeight.isPending}
              onClick={() => {
                const v = parseFloat(bulkWeight)
                if (!Number.isFinite(v) || v < 0) {
                  toast.error('Enter a non-negative number')
                  return
                }
                confirmToast(
                  `Set every snapshot weight to ${v}? This overwrites all per-row edits.`,
                  () => applyBulkWeight.mutate(v),
                  { confirmLabel: 'Apply' },
                )
              }}
              className="px-2 py-1 bg-accent text-white rounded text-xs font-medium hover:bg-accent/90 disabled:opacity-40"
            >
              {applyBulkWeight.isPending ? 'Applying…' : 'Apply to all'}
            </button>
          </div>

          {/* CSV import/export row */}
          <div className="flex items-center gap-2 mb-2">
            <button
              onClick={onDownloadCsv}
              className="flex items-center gap-1 px-2 py-1 border border-border rounded text-[11px] hover:border-accent hover:text-accent"
              title="Download all snapshot weightings as CSV"
            ><Download size={11} /> Download CSV</button>
            <button
              onClick={() => csvUploadRef.current?.click()}
              className="flex items-center gap-1 px-2 py-1 border border-border rounded text-[11px] hover:border-accent hover:text-accent"
              title="Upload edited CSV to overwrite weights"
            ><Upload size={11} /> Upload CSV</button>
            <input
              ref={csvUploadRef}
              type="file"
              accept=".csv,text/csv"
              className="hidden"
              onChange={e => {
                onUploadCsv(e.target.files?.[0])
                if (csvUploadRef.current) csvUploadRef.current.value = ''
              }}
            />
            <span className="text-[10px] text-muted">
              Columns: <code>snapshot</code>, <code>objective</code>, <code>generators</code>, <code>stores</code>
            </span>
          </div>

          {/* Per-row table — paginated so 8760-hour horizons are reachable */}
          <div className="border border-border rounded overflow-auto max-h-64">
            <table className="w-full text-xs border-collapse" style={{ minWidth: snapshotsAreMulti ? 560 : 480 }}>
              <thead className="sticky top-0 bg-bg-2 z-10">
                <tr className="border-b border-border">
                  {snapshotsAreMulti && (
                    <th className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Period</th>
                  )}
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Snapshot</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Objective</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Generators</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Stores</th>
                </tr>
              </thead>
              <tbody>
                {weightingRows.map((row, i) => (
                  <tr key={row.key} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                    {snapshotsAreMulti && (
                      <td className="px-2 py-1 font-mono text-[11px]">{row.period}</td>
                    )}
                    <td className="px-2 py-1 font-mono text-[11px] whitespace-nowrap">{row.iso}</td>
                    <td className="px-2 py-1 text-right">
                      <input
                        key={`sw-${row.key}-${row.objective}`}
                        type="number"
                        step="0.1"
                        min={0}
                        defaultValue={row.objective.toFixed(2)}
                        // Disabled while the shared per-snapshot mutation is
                        // in flight — same double-blur race protection as
                        // the per-period table above. Cosmetic only.
                        disabled={updateOneWeight.isPending}
                        onBlur={e => {
                          const v = parseFloat(e.target.value)
                          if (!Number.isFinite(v) || v < 0) {
                            e.target.value = row.objective.toFixed(2)
                            return
                          }
                          if (v !== row.objective) {
                            updateOneWeight.mutate({ key: row.key, objective: v })
                          }
                        }}
                        className="w-20 px-1 py-0.5 border border-border rounded text-[11px] font-mono bg-bg focus:outline-none focus:border-accent text-right disabled:opacity-50 disabled:cursor-wait"
                      />
                    </td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right text-muted">
                      {row.generators.toFixed(2)}
                    </td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right text-muted">
                      {row.stores.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination footer — only shown when there's more than one page */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between mt-1.5 text-[10px] text-muted">
              <span>
                Rows {pageStart + 1}–{pageEnd} of {totalWeightings}
              </span>
              <div className="flex items-center gap-2">
                <button
                  disabled={page === 0}
                  onClick={() => setPage(p => Math.max(0, p - 1))}
                  className="px-1.5 py-0.5 border border-border rounded hover:border-accent hover:text-accent disabled:opacity-30"
                >← Prev</button>
                <span>Page {page + 1} / {totalPages}</span>
                <button
                  disabled={page >= totalPages - 1}
                  onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
                  className="px-1.5 py-0.5 border border-border rounded hover:border-accent hover:text-accent disabled:opacity-30"
                >Next →</button>
              </div>
            </div>
          )}
        </section>
      )}
      </div>
    </div>
  )
}

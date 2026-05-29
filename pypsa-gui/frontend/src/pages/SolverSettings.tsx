import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Plus, Trash2, AlertCircle, Leaf, ArrowRight } from 'lucide-react'
import { simulationApi } from '../api/simulation'
import { networkApi } from '../api/network'
import type { SolverConfig } from '../api/types'
import { useUIStore } from '../store/uiStore'
import { PageHeader } from '../components/PageKit'
import CarrierSelect from '../components/CarrierSelect'

// Two-tier UI: a "common" form per-solver covering ~6 parameters most users
// touch, and an "advanced" key:value table that flows straight into
// solver_options (a free-form dict the backend forwards verbatim to
// linopy/PyPSA). Adding a niche parameter never needs a code change — the user
// just types the option name into the Advanced table.

type Solver = 'highs' | 'gurobi' | 'glpk' | 'cplex' | 'scip' | 'cbc'

interface ParamSpec {
  key: string
  label: string
  kind: 'number' | 'select' | 'string'
  options?: string[]   // for select
  step?: number        // for number
  min?: number
  hint?: string
}

// HiGHS option keys: lowercase_with_underscores. Gurobi: PascalCase. Both
// follow each project's documented convention so paste-from-docs works.
const COMMON_PARAMS: Record<Solver, ParamSpec[]> = {
  highs: [
    { key: 'time_limit',   label: 'Time limit (s)',     kind: 'number', step: 1, min: 0,
      hint: 'Wall-clock cap; 0 disables.' },
    { key: 'mip_rel_gap',  label: 'MIP rel. gap',       kind: 'number', step: 0.001, min: 0,
      hint: 'Relative optimality gap for MIPs (0.01 = 1%).' },
    { key: 'threads',      label: 'Threads',            kind: 'number', step: 1, min: 0,
      hint: '0 = use all cores.' },
    { key: 'parallel',     label: 'Parallel',           kind: 'select',
      options: ['choose', 'on', 'off'] },
    { key: 'presolve',     label: 'Presolve',           kind: 'select',
      options: ['choose', 'on', 'off'] },
    { key: 'solver',       label: 'LP/MIP solver',      kind: 'select',
      options: ['choose', 'simplex', 'ipm', 'pdlp'] },
    // HiGHS's native objective-scaling option. Takes a base-10 EXPONENT
    // integer (default 0 = no scaling, -6 = scale costs by 1e-6).
    // Distinct from the GUI's "Objective scaling" section above, which
    // multiplies linopy's objective by a float factor at the PyPSA layer.
    // HiGHS prints a "Consider … user_objective_scale option to -N"
    // suggestion when its preprocessor detects a wide coefficient range.
    { key: 'user_objective_scale', label: 'HiGHS user_objective_scale', kind: 'number', step: 1,
      hint: 'INTEGER base-10 exponent (default 0 = no scaling). HiGHS multiplies costs by 10^N internally. ' +
            'Set e.g. -6 when HiGHS emits "Problem has some excessively large costs" warnings. ' +
            'Distinct from the GUI\'s float-multiplier "Objective scaling" above — both are valid; use one OR the other.' },
    { key: 'output_flag',  label: 'Verbose log',        kind: 'select',
      options: ['true', 'false'],
      hint: 'Stream HiGHS internal log lines into the bottom panel.' },
  ],
  gurobi: [
    { key: 'TimeLimit',    label: 'Time limit (s)',     kind: 'number', step: 1, min: 0 },
    { key: 'MIPGap',       label: 'MIP rel. gap',       kind: 'number', step: 0.001, min: 0 },
    { key: 'Threads',      label: 'Threads',            kind: 'number', step: 1, min: 0,
      hint: '0 = use all cores.' },
    { key: 'Method',       label: 'LP method',          kind: 'select',
      options: ['-1', '0', '1', '2', '3', '4'],
      hint: '−1 auto · 0 primal · 1 dual · 2 barrier · 3 concurrent · 4 deterministic concurrent' },
    { key: 'Presolve',     label: 'Presolve',           kind: 'select',
      options: ['-1', '0', '1', '2'],
      hint: '−1 auto · 0 off · 1 conservative · 2 aggressive' },
    { key: 'Crossover',    label: 'Crossover',          kind: 'select',
      options: ['-1', '0', '1', '2', '3', '4'],
      hint: '−1 auto · 0 disable · 1–4 strategies (after barrier).' },
    { key: 'NumericFocus', label: 'Numeric focus',      kind: 'select',
      options: ['0', '1', '2', '3'],
      hint: 'Higher = more conservative numerics, slower but more stable.' },
  ],
  glpk: [
    { key: 'tmlim',  label: 'Time limit (s)', kind: 'number', step: 1, min: 0 },
    { key: 'msglev', label: 'Log level',      kind: 'select', options: ['0', '1', '2', '3'] },
  ],
  cplex: [
    { key: 'timelimit',         label: 'Time limit (s)', kind: 'number', step: 1, min: 0 },
    { key: 'mip.tolerances.mipgap', label: 'MIP rel. gap', kind: 'number', step: 0.001, min: 0 },
    { key: 'threads',           label: 'Threads',        kind: 'number', step: 1, min: 0 },
  ],
  scip: [
    { key: 'limits/time', label: 'Time limit (s)', kind: 'number', step: 1, min: 0 },
    { key: 'limits/gap',  label: 'MIP rel. gap',   kind: 'number', step: 0.001, min: 0 },
  ],
  cbc: [
    { key: 'seconds',  label: 'Time limit (s)', kind: 'number', step: 1, min: 0 },
    { key: 'ratioGap', label: 'MIP rel. gap',   kind: 'number', step: 0.001, min: 0 },
  ],
}

// MODE_DESCRIPTIONS used to surface the "lopf vs pf" picker in the UI. The
// `pf` standalone mode was removed: AC power flow is now exclusively a Stage 2
// chain after LOPF (see the General tab → AC PF section). The backend still
// accepts mode='pf' for compatibility but the frontend coerces any legacy
// 'pf' value to 'lopf' on draft hydration.

// Coerce form text → typed value. We keep the input free-text but persist
// numbers as numbers / booleans as booleans so the backend doesn't have to
// guess. Empty string means "unset" — drop the key entirely.
function coerceValue(raw: string, kind: ParamSpec['kind']): number | string | boolean | null {
  if (raw === '' || raw === undefined) return null
  if (kind === 'number') {
    const n = Number(raw)
    return Number.isFinite(n) ? n : null
  }
  if (raw === 'true') return true
  if (raw === 'false') return false
  return raw
}

export default function SolverSettings() {
  const qc = useQueryClient()
  const { data: cfg } = useQuery({ queryKey: ['solverConfig'], queryFn: simulationApi.getSolverConfig })
  const { data: solvers } = useQuery({ queryKey: ['checkSolvers'], queryFn: simulationApi.checkSolvers })
  const [draft, setDraft] = useState<SolverConfig | null>(null)
  // Snapshot of `cfg` at the moment `draft` was hydrated. Used at Save
  // time to compute a DIFF — we send only the fields the user actually
  // changed in this form, never the unchanged ones. Without this, Save
  // ships the entire `draft` (which is stale for any field edited via a
  // sibling page like ModelHorizon's per-period load scalers / CAPEX
  // budget / auto-discount toggle): the backend's PUT then overwrites
  // those sibling-page edits with the stale draft values, silently
  // reverting them.
  const baselineRef = useRef<SolverConfig | null>(null)

  // Hydrate the form from the backend once. We don't useEffect on every cfg
  // refetch because that would clobber the user's in-progress edits if a
  // background poll happens to land mid-typing.
  // Legacy `pf` mode (standalone AC PF run) was removed from the UI — coerce
  // to `lopf` on hydration so projects saved with the old value still load.
  // Backend still accepts mode='pf' for backwards compat (see solver_service)
  // but the user can no longer reach it from the form.
  useEffect(() => {
    if (cfg && draft === null) {
      const next = { ...cfg } as SolverConfig
      if ((next.mode as string) === 'pf') next.mode = 'lopf'
      setDraft(next)
      // Capture baseline alongside draft so the Save diff has a stable
      // reference point. Cloned via JSON to detach nested objects
      // (solver_options, load_scalers_by_carrier, capex_budget_per_period)
      // from the cfg cache — otherwise mutations on draft's nested fields
      // would also mutate baselineRef and the diff would always be empty.
      baselineRef.current = JSON.parse(JSON.stringify(next))
    }
  }, [cfg, draft])

  const save = useMutation({
    mutationFn: async (next: SolverConfig) => {
      const baseline = baselineRef.current
      if (!baseline) {
        // No baseline (shouldn't happen post-hydration) — fall back to
        // full-payload save. Preserves old behaviour.
        await simulationApi.updateSolverConfig(next)
        return { savedNext: next, noOp: false }
      }
      // Diff: include only fields the user actually changed in this form.
      // JSON-equality handles nested objects (dicts of dicts, dicts of
      // floats) consistently because we cloned via JSON at hydration, so
      // key insertion order is preserved across the comparison.
      //
      // CAUTION: nested-object fields (`solver_options`,
      // `load_scalers_by_carrier`, `capex_budget_per_period`) are
      // diffed at the top level — if ANY sub-key changes here, the
      // entire object is sent and replaces whatever was on the
      // backend. Today this is safe because each nested object has a
      // single editor (solver_options only here, the others only in
      // ModelHorizon), so there's no overlap. If a future feature
      // lets two pages edit sub-keys of the SAME nested object, this
      // whole-replace semantics will silently wipe one side's edits.
      const diff: Partial<SolverConfig> = {}
      const nextAsRec = next as unknown as Record<string, unknown>
      const baselineAsRec = baseline as unknown as Record<string, unknown>
      const diffAsRec = diff as unknown as Record<string, unknown>
      for (const key of Object.keys(next)) {
        const a = nextAsRec[key]
        const b = baselineAsRec[key]
        if (JSON.stringify(a) !== JSON.stringify(b)) {
          diffAsRec[key] = a
        }
      }
      // Nothing changed in this form — skip the PUT entirely. The
      // onSuccess handler renders a distinct "no changes" toast so
      // the user isn't misled into thinking unmodified fields were
      // re-persisted.
      if (Object.keys(diff).length === 0) {
        return { savedNext: next, noOp: true }
      }
      await simulationApi.updateSolverConfig(diff)
      return { savedNext: next, noOp: false }
    },
    onSuccess: ({ savedNext, noOp }) => {
      // Advance baseline so the next Save's diff is computed against
      // what we just persisted (or, on no-op, the same snapshot).
      baselineRef.current = JSON.parse(JSON.stringify(savedNext))
      qc.invalidateQueries({ queryKey: ['solverConfig'] })
      if (noOp) {
        toast('No changes to save', { icon: 'ℹ️' })
      } else {
        toast.success('Solver settings saved')
      }
    },
    onError: () => toast.error('Could not save solver settings'),
  })

  // Pre-run validation lives in the dedicated Issues panel — opened via
  // the sidebar's "Issues" entry or the CTA at the bottom of this tab.
  // Keeping a single source-of-truth panel avoids two parallel "Validate"
  // buttons that could disagree about freshness.
  const setSlidePanel = useUIStore(s => s.setSlidePanel)

  const solver = (draft?.solver_name ?? 'highs') as Solver
  const commonSpec = COMMON_PARAMS[solver] ?? []
  const opts = (draft?.solver_options ?? {}) as Record<string, unknown>

  // Advanced keys = options NOT covered by the common form. Stored as parallel
  // arrays so React keeps focus while the user types into a key field.
  const commonKeys = useMemo(() => new Set(commonSpec.map(s => s.key)), [commonSpec])
  const advancedEntries = useMemo<Array<[string, unknown]>>(
    () => Object.entries(opts).filter(([k]) => !commonKeys.has(k)),
    [opts, commonKeys],
  )

  // Tab state must live ABOVE the early-return guard, otherwise React sees
  // a different hook count on the loading → loaded transition and throws
  // "Rendered more hooks than during the previous render". All hooks first,
  // then the loading check.
  // Tab grouping reflects the user-facing mental model:
  //   • General  — solve strategy, AC PF chaining, clustering, and a CTA
  //                opening the Issues panel for pre-run validation.
  //   • Solver   — solver-specific knobs: pick a solver, tune its common
  //                options, presolve, raw solver_options table.
  //   • Dispatch — operational choices: Unit Commitment + economic
  //                assumptions (discount, lifetime, CO2 price).
  //   • Network  — physical-network choices: transmission losses, VOLL load
  //                shedding, N-1 SCLOPF.
  //   • Add. Constraints — global LP constraints (CO2 cap, etc).
  type Tab = 'general' | 'solver' | 'dispatch' | 'network' | 'constraints'
  const [tab, setTab] = useState<Tab>('general')

  if (!draft) return <div className="p-6 text-muted text-xs">Loading…</div>

  function patch(partial: Partial<SolverConfig>) {
    setDraft(prev => prev ? { ...prev, ...partial } : prev)
  }
  function setOption(key: string, value: number | string | boolean | null) {
    setDraft(prev => {
      if (!prev) return prev
      const next = { ...(prev.solver_options ?? {}) } as Record<string, unknown>
      if (value === null) delete next[key]
      else next[key] = value
      return { ...prev, solver_options: next }
    })
  }
  function renameAdvancedKey(oldKey: string, newKey: string) {
    setDraft(prev => {
      if (!prev) return prev
      const next = { ...(prev.solver_options ?? {}) } as Record<string, unknown>
      const v = next[oldKey]
      delete next[oldKey]
      if (newKey) next[newKey] = v
      return { ...prev, solver_options: next }
    })
  }

  // Save stays outside the tab body so the user never has to scroll to find
  // it, regardless of which topic they're currently editing. (Tab state is
  // declared above the early return — required by React's hook rules.)
  const tabs: { id: Tab; label: string }[] = [
    { id: 'general',     label: 'General' },
    { id: 'solver',      label: 'Solver' },
    { id: 'dispatch',    label: 'Dispatch' },
    { id: 'network',     label: 'Network' },
    { id: 'constraints', label: 'Add. Constraints' },
  ]

  return (
    <div className="flex flex-col h-full text-sm">
      <PageHeader
        eyebrow="SIMULATION · SOLVER"
        title="Solver &amp; mode"
        subtitle="Pick how PyPSA builds and solves the optimization model — backend, optimization mode, physics, and forwarded solver options."
      />
      {/* Sticky tab bar — keeps the section selector visible while the user
          scrolls within a tall section like Constraints or SCLOPF. */}
      <div className="flex border-b border-border bg-bg sticky top-0 z-10">
        {tabs.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`px-3 py-2 text-xs font-medium border-b-2 transition-colors ${
              tab === t.id
                ? 'border-accent text-accent'
                : 'border-transparent text-muted hover:text-text'
            }`}>
            {t.label}
          </button>
        ))}
      </div>

      <div className="flex flex-col gap-5 px-8 py-5 overflow-y-auto flex-1">
      {tab === 'general' && <>
      <p className="text-[11px] text-muted -mb-2 leading-relaxed">
        Solve strategy, AC PF chaining, and clustering. Pre-run validation
        moved to the <span className="font-medium text-text">Issues</span> panel.
        Solver-specific knobs (HiGHS / Gurobi options, presolve, raw <code>solver_options</code>)
        live under the <span className="font-medium text-text">Solver</span> tab.
      </p>

      {/* ── Clustering ─────────────────────────────────────── */}
      <ClusteringSection />

      {/* ── Solve strategy: full vs rolling-horizon ──────────────────── */}
      {/* PyPSA's `optimize_with_rolling_horizon` splits the snapshot index into
          windows of `rolling_horizon` snapshots, each LP solved sequentially
          with `rolling_overlap` snapshots of overlap to carry SoC across.
          Recommended for problems where the full LP is too large for memory
          (typically > ~1000 snapshots × > ~100 buses).
          Incompatible with SCLOPF and auto-chained AC PF — backend validation
          rejects those combinations. */}
      <section>
        <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2.5">Solve strategy</h3>
        <div className="space-y-1">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="solve_strategy"
              value="full"
              checked={(draft.solve_strategy ?? 'full') === 'full'}
              onChange={() => patch({ solve_strategy: 'full' })}
              className="accent-accent"
            />
            <span className="text-xs text-text">Full LP (one optimisation over all snapshots)</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="solve_strategy"
              value="rolling"
              checked={draft.solve_strategy === 'rolling'}
              onChange={() => patch({ solve_strategy: 'rolling' })}
              className="accent-accent"
            />
            <span className="text-xs text-text">Rolling horizon (chunked)</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="solve_strategy"
              value="myopic"
              checked={draft.solve_strategy === 'myopic'}
              onChange={() => patch({ solve_strategy: 'myopic' })}
              className="accent-accent"
              disabled={!draft.multi_investment_periods}
            />
            <span className={`text-xs ${draft.multi_investment_periods ? 'text-text' : 'text-muted'}`}>
              Myopic foresight (per-period capacity expansion)
              {!draft.multi_investment_periods && (
                <span className="ml-2 text-[10px] text-muted">— requires multi-investment periods</span>
              )}
            </span>
          </label>
        </div>
        {draft.solve_strategy === 'rolling' && (
          <div className="mt-2 pl-5 space-y-2">
            <div className="flex items-center gap-2">
              <label className="text-[11px] text-muted w-20">Horizon</label>
              <input
                type="number"
                min={1}
                step={1}
                value={draft.rolling_horizon ?? 168}
                onChange={e => patch({ rolling_horizon: Math.max(1, parseInt(e.target.value || '168', 10) || 168) })}
                className="bg-bg border border-border rounded px-2 py-1 text-xs font-mono text-text w-24 focus:outline-none focus:border-accent"
              />
              <span className="text-[10px] text-muted">snapshots per window</span>
            </div>
            <div className="flex items-center gap-2">
              <label className="text-[11px] text-muted w-20">Overlap</label>
              <input
                type="number"
                min={0}
                step={1}
                value={draft.rolling_overlap ?? 24}
                onChange={e => patch({ rolling_overlap: Math.max(0, parseInt(e.target.value || '24', 10) || 0) })}
                className="bg-bg border border-border rounded px-2 py-1 text-xs font-mono text-text w-24 focus:outline-none focus:border-accent"
              />
              <span className="text-[10px] text-muted">snapshots of SoC carry between windows</span>
            </div>
            {/* Surface the SCLOPF / AC PF incompatibility BEFORE the user
                clicks Run. The backend will also reject these — this is a
                UX courtesy so they don't have to read the failure log. */}
            {(draft.sclopf || draft.run_ac_pf_after_lopf) && (
              <p className="text-[10px] text-warn bg-warn/10 border border-warn/30 rounded px-2 py-1">
                Rolling horizon is not compatible with{' '}
                {draft.sclopf && <code>SCLOPF</code>}
                {draft.sclopf && draft.run_ac_pf_after_lopf && ' or '}
                {draft.run_ac_pf_after_lopf && <code>auto-chained AC PF</code>}
                . Disable one to run.
              </p>
            )}
            <p className="text-[10px] text-muted">
              Recommended when the full LP would exceed memory or take impractical wall time.
              Each window solves independently; <code>overlap</code> ≥ 24 helps storage SoC
              continuity in hourly models.{' '}
              <span className="text-warn">Note: the reported objective reflects only the last
              window — per-window costs are not summed by PyPSA. Use the LoadFlow / Costs
              tabs for accurate totals across the full horizon.</span>
            </p>
          </div>
        )}
        {draft.solve_strategy === 'myopic' && (
          <div className="mt-2 pl-5 space-y-2">
            <p className="text-[10px] text-muted leading-relaxed">
              Solves each investment period sequentially at full hourly detail.
              Capacities decided in one period are frozen
              (<code>extendable=False</code>, <code>p_nom = p_nom_opt</code>)
              before the next period is optimised, so each period sees the
              earlier ones as fixed existing capacity. Matches the way real
              planners commit to investments — pure myopic has no forward
              visibility across periods. Per-period vintage bounds (set on
              extendable assets via the Properties panel) are still honoured.
            </p>
            <label className="flex items-center gap-2 cursor-pointer pt-1">
              <input
                type="checkbox"
                checked={draft.lf_aggregate_future ?? false}
                onChange={e => patch({ lf_aggregate_future: e.target.checked })}
                className="accent-accent"
              />
              <span className="text-xs text-text">Limited foresight: see aggregated future periods</span>
            </label>
            {draft.lf_aggregate_future && (
              <div className="ml-5 space-y-2 border-l border-border pl-3">
                <p className="text-[10px] text-muted leading-relaxed">
                  Each iteration of the myopic loop also sees representative
                  blocks for every period after the current one — clustered
                  via <code>tsam</code> on the load + renewable profiles.
                  Gives the solver forward visibility so capacity is sized
                  with future demand in mind, at a fraction of the full LP's
                  solve cost.
                </p>
                <div className="flex items-center gap-2">
                  <label className="text-[11px] text-muted w-32">Representative blocks</label>
                  <input
                    type="number"
                    min={1}
                    step={1}
                    value={draft.lf_k_periods ?? 8}
                    onChange={e => patch({ lf_k_periods: Math.max(1, parseInt(e.target.value || '8', 10) || 8) })}
                    className="bg-bg border border-border rounded px-2 py-1 text-xs font-mono text-text w-20 focus:outline-none focus:border-accent"
                  />
                  <span className="text-[10px] text-muted">per future period (≥ 8 keeps cost gap &lt; 5 %)</span>
                </div>
                <div className="flex items-center gap-2">
                  <label className="text-[11px] text-muted w-32">Block length</label>
                  <input
                    type="number"
                    min={1}
                    step={1}
                    value={draft.lf_period_length_h ?? 168}
                    onChange={e => patch({ lf_period_length_h: Math.max(1, parseInt(e.target.value || '168', 10) || 168) })}
                    className="bg-bg border border-border rounded px-2 py-1 text-xs font-mono text-text w-20 focus:outline-none focus:border-accent"
                  />
                  <span className="text-[10px] text-muted">hours (168 = weekly, 24 = daily)</span>
                </div>
                <div className="flex items-center gap-2">
                  <label className="text-[11px] text-muted w-32">Clustering</label>
                  <select
                    value={draft.lf_cluster_method ?? 'hierarchical'}
                    onChange={e => patch({ lf_cluster_method: e.target.value })}
                    className="bg-bg border border-border rounded px-2 py-1 text-xs text-text focus:outline-none focus:border-accent"
                  >
                    <option value="hierarchical">hierarchical (default)</option>
                    <option value="k_means">k-means</option>
                    <option value="k_medoids">k-medoids</option>
                  </select>
                </div>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={draft.lf_include_extreme ?? true}
                    onChange={e => patch({ lf_include_extreme: e.target.checked })}
                    className="accent-accent"
                  />
                  <span className="text-[11px] text-text">Include extreme blocks (peak-load + renewable-drought)</span>
                </label>
              </div>
            )}
            {draft.run_ac_pf_after_lopf && (
              <p className="text-[10px] text-warn bg-warn/10 border border-warn/30 rounded px-2 py-1">
                Myopic foresight is not yet compatible with{' '}
                <code>auto-chained AC PF</code>. Disable one to run.
              </p>
            )}
            {draft.sclopf && (
              <p className="text-[10px] text-muted bg-panel border border-border rounded px-2 py-1">
                SCLOPF is enabled — contingency constraints are applied per
                myopic iteration. Choose <code>Contingency scope</code> below
                (Horizon / Current period).
              </p>
            )}
          </div>
        )}
      </section>

      {/* ── Stage 2: post-solve AC Power Flow ──────────────── */}
      {/* Folded into the General tab (rather than its own tab) because LOPF
          is now the only run mode — AC PF is exclusively a chain after the
          LP. Lives next to Solve strategy / Validate so the user configures
          the full solve pipeline in one place. */}
      <div className="border-t border-border pt-4">
        <Stage2Panel draft={draft} patch={patch} />
      </div>

      {/* ── Pre-run validation CTA ──────────────────────────────
          Detailed validation lives in the dedicated Issues panel. Surfacing
          it via a CTA here keeps the General tab focused on solve-strategy
          configuration while preserving one-click access from the place
          users land before clicking Run. */}
      <div className="border-t border-border pt-4">
        <button
          onClick={() => setSlidePanel('issues')}
          className="w-full flex items-center justify-between gap-3 px-3 py-2.5 border border-border rounded-md hover:border-accent hover:bg-accent/5 transition-colors group"
        >
          <span className="flex items-center gap-2 text-left">
            <AlertCircle size={14} className="text-accent shrink-0" />
            <span className="flex flex-col">
              <span className="text-[12px] font-medium text-text">Pre-run validation</span>
              <span className="text-[10.5px] text-muted">
                Errors block <code>Run</code>; warnings only inform. Open the Issues panel to view findings and jump to offending components.
              </span>
            </span>
          </span>
          <ArrowRight size={14} className="text-muted group-hover:text-accent shrink-0" />
        </button>
      </div>
      </>}

      {tab === 'solver' && <>
      {/* ── Solver selection ─────────────────────────────────── */}
      <section>
        <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2.5">Solver</h3>
        <select
          value={draft.solver_name}
          onChange={e => patch({ solver_name: e.target.value })}
          className="w-full px-2 py-1.5 border border-border rounded text-xs bg-bg"
        >
          {(['highs', 'gurobi', 'glpk', 'cplex', 'scip', 'cbc'] as Solver[]).map(s => {
            const available = solvers?.[s] ?? false
            return (
              <option key={s} value={s} disabled={!available}>
                {s.toUpperCase()}{!available ? ' — not installed' : ''}
              </option>
            )
          })}
        </select>
        <p className="text-[11px] text-muted mt-1">
          HiGHS ships with the pixi env. Gurobi requires a license; GLPK/CBC are bundled.
        </p>
      </section>

      {/* ── Common solver options ────────────────────────────── */}
      <section>
        <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2.5">{solver.toUpperCase()} options — common</h3>
        <div className="grid grid-cols-1 gap-2">
          {commonSpec.map(spec => {
            const v = opts[spec.key]
            const display = v === undefined || v === null ? '' : String(v)
            return (
              <div key={spec.key} className="flex flex-col gap-0.5">
                <label className="text-[11px] text-muted">
                  {spec.label}
                  <span className="ml-1 font-mono text-[10px] text-muted/70">({spec.key})</span>
                </label>
                {spec.kind === 'select' ? (
                  <select
                    value={display}
                    onChange={e => setOption(spec.key, coerceValue(e.target.value, spec.kind))}
                    className="px-2 py-1 border border-border rounded text-xs bg-bg"
                  >
                    <option value="">— default —</option>
                    {spec.options?.map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                ) : (
                  <input
                    type={spec.kind === 'number' ? 'number' : 'text'}
                    step={spec.step} min={spec.min}
                    value={display}
                    onChange={e => setOption(spec.key, coerceValue(e.target.value, spec.kind))}
                    placeholder="default"
                    className="px-2 py-1 border border-border rounded text-xs bg-bg"
                  />
                )}
                {spec.hint && <span className="text-[10px] text-muted">{spec.hint}</span>}
              </div>
            )
          })}
        </div>
      </section>

      {/* ── Presolve toggle ──────────────────────────────────── */}
      {/* Solver-presolve eliminates redundant rows/cols before simplex, typically
          cutting solve time 2-10×. The backend maps this single boolean to the
          right solver-specific key (HiGHS: presolve, Gurobi: Presolve, …).
          Turn off only when debugging infeasibility — presolve frequently
          rewrites the culprit row before the solver can report it. */}
      <section>
        <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2.5">Presolve</h3>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={draft.presolve_enabled !== false}
            onChange={e => patch({ presolve_enabled: e.target.checked })}
            className="accent-accent"
          />
          <span className="text-xs text-text">Enable presolve (recommended)</span>
        </label>
        <p className="text-[10px] text-muted mt-1">
          Disable only when investigating infeasibility — presolve often eliminates
          the row that triggered the failure before the solver can report it.
        </p>
      </section>

      {/* ── Objective scaling ────────────────────────────────── */}
      {/* Numerical-conditioning multiplier applied to the LP objective.
          Doesn't change the optimum (LP-theory invariant) — only affects
          how the solver internally sees the problem. Helpful when CAPEX (€M)
          and marginal_cost (€/MWh) span many orders of magnitude and HiGHS /
          Gurobi report conditioning warnings.

          Quick-pick buttons are listed in increasing magnitude reduction
          (closer to 1.0 = less aggressive). The slider/input below is the
          authoritative control — covers values the buttons don't. */}
      <section>
        <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2.5">
          Objective scaling
          <span className="ml-2 font-mono text-[10px] font-normal text-muted">(user_objective_scale)</span>
        </h3>
        <p className="text-[11px] text-muted mb-2 leading-relaxed">
          Multiplies the LP objective by a positive constant for solver
          numerical conditioning. The optimal solution <em>doesn't change</em>
          {' '}(an LP invariant) — only the solver's internal magnitudes shift.
          Reported <code>n.objective</code> and LP duals are divided back
          by this factor post-solve so user-facing € stays unchanged.
        </p>
        {solver === 'highs' && (
          <p className="text-[10.5px] text-muted mb-2 leading-relaxed bg-bg-2 border border-border rounded px-2 py-1.5">
            <span className="font-medium text-text">Two same-named options exist.</span>
            {' '}This GUI field (a <em>float multiplier</em>) acts at the linopy layer
            before HiGHS sees the problem. The native HiGHS{' '}
            <code className="text-accent">user_objective_scale</code> option in the
            common params below is a <em>base-10 exponent integer</em> applied by HiGHS
            internally. Use whichever fits — they do the same job. Don't set both,
            since the effects compound.
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2 mb-1.5">
          {[
            { value: 1e-6, label: '×10⁻⁶' },
            { value: 1e-3, label: '×10⁻³' },
            { value: 1,    label: '×1 (default)' },
            { value: 1e3,  label: '×10³' },
            { value: 1e6,  label: '×10⁶' },
          ].map(opt => {
            const active = Math.abs((draft.user_objective_scale ?? 1) - opt.value) < 1e-12
            return (
              <button
                key={opt.value}
                onClick={() => patch({ user_objective_scale: opt.value })}
                className={`px-2 py-1 rounded text-[11px] border transition-colors ${
                  active
                    ? 'border-accent bg-accent/10 text-accent font-medium'
                    : 'border-border text-muted hover:text-text hover:border-text/30'
                }`}
              >
                {opt.label}
              </button>
            )
          })}
        </div>
        <div className="flex items-center gap-2 mt-2">
          <label className="text-[11px] text-muted">Custom</label>
          <input
            type="number"
            step="any"
            min={0}
            value={draft.user_objective_scale ?? 1}
            onChange={e => {
              const v = parseFloat(e.target.value)
              patch({ user_objective_scale: Number.isFinite(v) && v > 0 ? v : 1 })
            }}
            className="px-2 py-1 border border-border rounded text-xs bg-bg font-mono w-32"
          />
          {(draft.user_objective_scale ?? 1) !== 1 && (
            <span className="text-[10px] text-warn font-mono">
              non-identity scaling active
            </span>
          )}
        </div>
        <details className="mt-2 text-[10.5px] text-muted">
          <summary className="cursor-pointer hover:text-text">When to use this</summary>
          <ul className="list-disc pl-5 mt-1.5 space-y-1 leading-relaxed">
            <li>
              <span className="font-medium text-text">Solver reports "objective range too large"</span>
              {' '}or "numerical-conditioning" warnings — try a scale of <code>1e-3</code> or
              <code> 1e-6</code> to convert € → k€ → M€.
            </li>
            <li>
              <span className="font-medium text-text">Tiny LPs with sub-€/MWh costs</span>
              {' '}— a scale of <code>1e3</code> can recover bits the solver would otherwise lose.
            </li>
            <li>
              <span className="font-medium text-text">Default (1.0)</span> is correct for almost every
              practical case. Touch this only if HiGHS / Gurobi explicitly complain.
            </li>
            <li>
              Invalid values (non-positive, non-finite) silently fall back to 1.0 — see the
              <code> [OBJ-SCALE]</code> log line during the next solve to confirm what scale was applied.
            </li>
          </ul>
        </details>
      </section>

      {/* ── Advanced free-form ───────────────────────────────── */}
      <section>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Advanced — raw solver_options</h3>
          <button
            type="button"
            onClick={() => setOption(`option_${advancedEntries.length + 1}`, '')}
            className="flex items-center gap-1 text-[11px] text-muted hover:text-accent"
          ><Plus size={11} /> Add</button>
        </div>
        <p className="text-[10px] text-muted mb-2">
          Anything here is passed verbatim to the solver. Useful for niche tuning that isn't
          in the common form.
        </p>
        <div className="flex flex-col gap-1">
          {advancedEntries.length === 0 && (
            <div className="text-[11px] text-muted italic">No extra options.</div>
          )}
          {advancedEntries.map(([k, v], idx) => (
            <div key={`${idx}-${k}`} className="flex gap-1">
              <input
                type="text" value={k}
                onChange={e => renameAdvancedKey(k, e.target.value)}
                placeholder="key"
                className="flex-1 px-2 py-1 border border-border rounded text-xs bg-bg font-mono"
              />
              <input
                type="text" value={v === null || v === undefined ? '' : String(v)}
                onChange={e => setOption(k, coerceValue(e.target.value, 'string'))}
                placeholder="value"
                className="flex-1 px-2 py-1 border border-border rounded text-xs bg-bg font-mono"
              />
              <button
                type="button" onClick={() => setOption(k, null)}
                className="px-1 text-muted hover:text-danger"
                title="Remove"
              ><Trash2 size={12} /></button>
            </div>
          ))}
        </div>
      </section>
      </>}

      {tab === 'dispatch' && <>
      {/* ── Unit Commitment (MIP) ────────────────────────────── */}
      {/* Active only when at least one generator has committable=True. With
          UC the LP becomes a MILP and these knobs control how hard the
          solver works to close the gap between the best integer solution
          and the LP relaxation lower bound. */}
      <section>
        <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2.5">Unit Commitment (MIP)</h3>
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <label className="text-[11px] text-muted w-28">MIP gap</label>
            <input
              type="number"
              step="0.001"
              min={0}
              max={1}
              value={draft.mip_gap ?? 0.01}
              onChange={e => patch({ mip_gap: Math.max(0, Math.min(1, parseFloat(e.target.value || '0.01') || 0.01)) })}
              className="bg-bg border border-border rounded px-2 py-1 text-xs font-mono text-text w-24 focus:outline-none focus:border-accent"
            />
            <span className="text-[10px] text-muted">relative tolerance (0.01 = 1 % of optimal)</span>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-[11px] text-muted w-28">Time limit</label>
            <input
              type="number"
              step={60}
              min={0}
              value={draft.mip_time_limit_s ?? 0}
              onChange={e => patch({ mip_time_limit_s: Math.max(0, parseFloat(e.target.value || '0') || 0) })}
              className="bg-bg border border-border rounded px-2 py-1 text-xs font-mono text-text w-24 focus:outline-none focus:border-accent"
            />
            <span className="text-[10px] text-muted">seconds (0 = unlimited)</span>
          </div>
        </div>
        <p className="text-[10px] text-muted mt-1.5">
          Active only when at least one generator has <code>committable=True</code>.
          Tightening the gap can multiply solve time; loosening can leave dispatched
          units off that should be on. <code>0.01</code> is a sensible default.
        </p>
      </section>

      {/* ── Economic assumptions ─────────────────────────────── */}
      {/* Discount rate, default lifetime, CO2 price. Applied transiently at
          solve time and reverted after the solve — the network on disk
          stays unchanged. */}
      <EconomicAssumptions draft={draft} patch={patch} />
      </>}

      {tab === 'network' && <>
      {/* ── Transmission losses ──────────────────────────────── */}
      <section>
        <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2.5">Transmission losses</h3>
        {/* Losses are ignored when SCLOPF is on — PyPSA's
            optimize_security_constrained doesn't accept the kwarg and the
            LODF formulation assumes a lossless DC network. Grey out so the
            user doesn't expect a no-op to take effect. */}
        <label className={`flex items-center justify-between p-2 border border-border rounded ${draft.sclopf ? 'opacity-50 cursor-not-allowed' : ''}`}>
          <span className="text-xs">
            Enable transmission losses
            <span className="block text-[11px] text-muted">
              {draft.sclopf
                ? 'Disabled — SCLOPF cannot model losses (LODF assumes a lossless DC network).'
                : 'Iterative LOPF that approximates AC line losses.'}
            </span>
          </span>
          <input
            type="checkbox" checked={draft.transmission_losses && !draft.sclopf}
            disabled={draft.sclopf}
            onChange={e => patch({ transmission_losses: e.target.checked })}
          />
        </label>
      </section>

      {/* ── Value of Lost Load (VOLL) ────────────────────────── */}
      <ReliabilityAssumptions draft={draft} patch={patch} />

      {/* ── N-1 Security-Constrained LOPF ────────────────────── */}
      <SclopfPanel draft={draft} patch={patch} />
      </>}

      {tab === 'constraints' && <>
      {/* ── Global constraints ───────────────────────────────── */}
      <GlobalConstraints />
      </>}
      </div>

      {/* ── Save ─────────────────────────────────────────────── */}
      {/* Outside the tab body so it's always reachable. Pinned to the
          bottom edge of the panel with a top border for separation. */}
      <div className="flex gap-2 p-3 border-t border-border bg-bg">
        <button
          onClick={() => draft && save.mutate(draft)}
          disabled={save.isPending}
          className="flex-1 px-3 py-1.5 bg-accent text-white rounded text-xs font-medium hover:bg-accent/90 disabled:opacity-40"
        >{save.isPending ? 'Saving…' : 'Save settings'}</button>
        <button
          onClick={() => {
            if (!cfg) return
            // Reset BOTH the draft and the baseline to the live cfg.
            // Without resetting baselineRef, a subsequent Save would
            // diff the freshly-cfg-derived draft against the stale
            // baseline — re-PUTting any field that drifted since the
            // initial hydration (i.e. sibling-page edits) and
            // re-introducing exactly the stale-draft bug this whole
            // mutation flow was rewritten to avoid.
            setDraft(cfg)
            baselineRef.current = JSON.parse(JSON.stringify(cfg))
          }}
          className="px-3 py-1.5 border border-border rounded text-xs text-muted hover:text-text"
        >Revert</button>
      </div>
    </div>
  )
}

// ── Global constraints sub-section ──────────────────────────────────────────
// Network-wide policy constraints stored in `n.global_constraints`. The five
// canonical PyPSA types each surface a different set of contextual fields:
//
//   primary_energy                  — carrier_attribute + sense + constant
//                                     (CO2 cap, fuel-use cap, water consumption)
//   transmission_volume_expansion_limit — sense + constant (MW·km)
//   transmission_expansion_cost_limit   — sense + constant (€)
//   tech_capacity_expansion_limit   — carrier + sense + constant (MW)
//   operational_limit               — carrier + sense + constant (MWh)
//
// "Quick-add CO2 cap" button is the most common entry point — pre-populates
// the form with carrier_attribute='co2_emissions' and sense='<='.

interface GlobalConstraint {
  name: string
  type: string
  sense: string
  constant: number
  carrier_attribute?: string | null
  carrier?: string | null
  investment_period?: number | null
}

const CONSTRAINT_TYPES: Array<{ value: string; label: string; needs: ('carrier_attribute' | 'carrier')[]; unit: string; hint: string }> = [
  { value: 'primary_energy',                       label: 'Primary energy (e.g. CO2 cap)',
    needs: ['carrier_attribute'], unit: 't or unit-of-attribute',
    hint: 'Caps Σ(carrier_attribute) over horizon. CO2 → carrier_attribute=co2_emissions.' },
  { value: 'tech_capacity_expansion_limit',        label: 'Tech capacity expansion',
    needs: ['carrier'], unit: 'MW',
    hint: 'Per-carrier cap on new generation/storage capacity (e.g. ≤100 GW new wind).' },
  { value: 'operational_limit',                    label: 'Operational limit',
    needs: ['carrier'], unit: 'MWh',
    hint: 'Per-carrier cap on dispatch over horizon (e.g. coal ≤ X TWh).' },
  { value: 'transmission_volume_expansion_limit',  label: 'Transmission volume',
    needs: [], unit: 'MW·km',
    hint: 'Cap on total ΣMW·km of newly built transmission.' },
  { value: 'transmission_expansion_cost_limit',    label: 'Transmission cost',
    needs: [], unit: '€',
    hint: 'Cap on total annualised CAPEX of new transmission.' },
]

function GlobalConstraints() {
  const qc = useQueryClient()
  const { data: rawRows = [] } = useQuery({
    queryKey: ['global_constraints'],
    queryFn: networkApi.getGlobalConstraints,
  })
  const rows = rawRows as unknown as GlobalConstraint[]
  // Investment periods drive the "Period" dropdown — empty list ⇒ flat
  // network, where the dropdown collapses to "Horizon-wide" only.
  const { data: invPeriods } = useQuery({
    queryKey: ['investmentPeriods'],
    queryFn: networkApi.getInvestmentPeriods,
  })
  const periods = useMemo<number[]>(() => {
    const arr = (invPeriods?.periods ?? []) as number[]
    return [...arr].sort((a, b) => a - b)
  }, [invPeriods])

  const [form, setForm] = useState<GlobalConstraint>({
    name: '', type: 'primary_energy', sense: '<=', constant: 0,
    carrier_attribute: 'co2_emissions',
    investment_period: null,  // horizon-wide by default
  })
  const spec = CONSTRAINT_TYPES.find(t => t.value === form.type) ?? CONSTRAINT_TYPES[0]

  const createMut = useMutation({
    mutationFn: (body: GlobalConstraint) => networkApi.createGlobalConstraint({
      name: body.name,
      type: body.type,
      sense: body.sense,
      constant: Number(body.constant) || 0,
      carrier_attribute: body.carrier_attribute || undefined,
      carrier: body.carrier || undefined,
      investment_period: body.investment_period ?? undefined,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['global_constraints'] })
      toast.success('Constraint added')
      // Reset form keeping the type so user can quickly add another of the same kind.
      setForm(f => ({ ...f, name: '', constant: 0 }))
    },
    onError: (e: { response?: { data?: { detail?: unknown } } }) => {
      // FastAPI returns string `detail` for HTTPException but array of
      // {type, loc, msg, input} for Pydantic validation errors. Coerce
      // before display so react-hot-toast doesn't crash trying to render
      // raw objects as JSX (same fix as the Carriers tab handler).
      const raw = e?.response?.data?.detail
      const msg = typeof raw === 'string'
        ? raw
        : Array.isArray(raw)
          ? raw.map((d) => {
              const r = d as { loc?: unknown[]; msg?: string }
              const field = Array.isArray(r.loc) ? r.loc.slice(1).join('.') : ''
              return field ? `${field}: ${r.msg ?? 'invalid'}` : (r.msg ?? 'invalid')
            }).join(' · ')
          : 'Failed to add constraint'
      toast.error(msg)
    },
  })

  const deleteMut = useMutation({
    mutationFn: (name: string) => networkApi.deleteGlobalConstraint(name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['global_constraints'] })
      toast.success('Constraint removed')
    },
    onError: () => toast.error('Failed to remove constraint'),
  })

  const presetCO2 = () => setForm({
    name: form.name || 'co2_cap',
    type: 'primary_energy', sense: '<=', constant: form.constant || 100_000_000,
    carrier_attribute: 'co2_emissions',
  })

  const onSubmit = () => {
    if (!form.name.trim()) { toast.error('Pick a name'); return }
    createMut.mutate(form)
  }

  return (
    <section className="border-t border-border pt-4">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Global constraints</h3>
        <button
          onClick={presetCO2}
          className="flex items-center gap-1 text-[11px] text-muted hover:text-success"
          title="Quick-add a CO2 emissions cap"
        ><Leaf size={11} /> CO2 cap preset</button>
      </div>
      <p className="text-[11px] text-muted mb-2">
        Network-wide policy constraints applied during the LOPF solve. Stored in
        <code className="mx-1">n.global_constraints</code>.
      </p>

      {/* Existing constraints */}
      {rows.length > 0 && (
        <div className="border border-border rounded mb-3 overflow-hidden">
          <table className="w-full text-xs">
            <thead className="bg-panel">
              <tr className="border-b border-border">
                <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Name</th>
                <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Type</th>
                <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Carrier</th>
                <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="Empty = horizon-wide (sums across every period). A year value = applies only within that investment period.">Period</th>
                <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Sense</th>
                <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Constant</th>
                <th className="px-2 py-1.5"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const periodVal = r.investment_period
                // PyPSA stores the no-period sentinel as NaN OR -1 depending
                // on version. Treat anything outside a reasonable year range
                // (1900-2200) as horizon-wide.
                const isHorizon = periodVal == null
                  || !Number.isFinite(periodVal as number)
                  || (periodVal as number) < 1900 || (periodVal as number) > 2200
                return (
                  <tr key={r.name} className="border-b border-border/40">
                    <td className="px-2 py-1 font-mono text-[11px]">{r.name}</td>
                    <td className="px-2 py-1 text-[11px] text-muted">{r.type}</td>
                    <td className="px-2 py-1 text-[11px]">
                      {r.carrier_attribute || r.carrier || '—'}
                    </td>
                    <td className="px-2 py-1 text-[11px]">
                      {isHorizon
                        ? <span className="text-muted italic">horizon</span>
                        : <span className="font-mono text-accent">{periodVal}</span>}
                    </td>
                    <td className="px-2 py-1 text-[11px] font-mono">{r.sense}</td>
                    <td className="px-2 py-1 text-[11px] font-mono text-right">{Number(r.constant).toLocaleString()}</td>
                    <td className="px-2 py-1 text-right">
                      <button onClick={() => deleteMut.mutate(r.name)}
                        className="text-muted hover:text-danger" title="Delete">
                        <Trash2 size={11} />
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Add new */}
      <div className="border border-border rounded p-2.5 space-y-2 bg-panel/40">
        <div className="text-[10px] text-muted uppercase tracking-wide">Add constraint</div>
        <div className="grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-0.5 col-span-2">
            <span className="text-[10px] text-muted">Type</span>
            <select
              value={form.type}
              onChange={e => setForm(f => ({ ...f, type: e.target.value }))}
              className="px-2 py-1 border border-border rounded text-xs bg-bg"
            >
              {CONSTRAINT_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
            <span className="text-[10px] text-muted">{spec.hint}</span>
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[10px] text-muted">Name</span>
            <input
              type="text" value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              placeholder="e.g. co2_cap"
              className="px-2 py-1 border border-border rounded text-xs bg-bg font-mono"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[10px] text-muted">Sense</span>
            <select
              value={form.sense}
              onChange={e => setForm(f => ({ ...f, sense: e.target.value }))}
              className="px-2 py-1 border border-border rounded text-xs bg-bg"
            >
              <option value="<=">≤</option>
              <option value="==">=</option>
              <option value=">=">≥</option>
            </select>
          </label>
          {spec.needs.includes('carrier_attribute') && (
            <label className="flex flex-col gap-0.5 col-span-2">
              <span className="text-[10px] text-muted">Carrier attribute (e.g. co2_emissions)</span>
              <input
                type="text" value={form.carrier_attribute ?? ''}
                onChange={e => setForm(f => ({ ...f, carrier_attribute: e.target.value }))}
                placeholder="co2_emissions"
                className="px-2 py-1 border border-border rounded text-xs bg-bg font-mono"
              />
            </label>
          )}
          {spec.needs.includes('carrier') && (
            <div className="col-span-2">
              <CarrierSelect
                label="Carrier"
                value={form.carrier ?? ''}
                onChange={v => setForm(f => ({ ...f, carrier: v }))}
                allowEmpty
                placeholder="— pick a carrier —"
                title="Operational-limit and capacity-expansion constraints are scoped by carrier. List comes from the project's n.carriers + the PyPSA-Eur catalog."
                wrapperClassName="flex flex-col gap-0.5"
              />
              <span className="text-[10px] text-muted block mt-0.5">
                Carriers come from <span className="font-medium text-text">n.carriers</span> +
                the curated PyPSA-Eur catalog. Add new carriers via the
                {' '}<span className="font-medium text-text">Carriers</span> tab at the bottom.
              </span>
            </div>
          )}

          {/* Investment-period scope. Default "Horizon-wide" applies the
              constraint across ALL periods (Σ over the horizon, one shadow
              price). Selecting a year scopes it to that single period —
              useful for declining-budget CO2 pathways like 100 Mt in 2025,
              80 Mt in 2030, etc. Hidden on flat (single-period) networks
              since the constraint scope is always horizon = period there. */}
          {periods.length > 0 && (
            <label className="flex flex-col gap-0.5 col-span-2">
              <span className="text-[10px] text-muted">
                Investment period scope
                <span className="ml-1 text-muted/70">— horizon-wide unless picked</span>
              </span>
              <select
                value={form.investment_period == null ? '' : String(form.investment_period)}
                onChange={e => {
                  const v = e.target.value
                  setForm(f => ({
                    ...f,
                    investment_period: v === '' ? null : Number(v),
                  }))
                }}
                className="px-2 py-1 border border-border rounded text-xs bg-bg"
              >
                <option value="">Horizon-wide (Σ across every period)</option>
                {periods.map(p => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
              <span className="text-[10px] text-muted">
                Per-period scope is supported by <code>primary_energy</code>,
                {' '}<code>operational_limit</code> and{' '}
                <code>tech_capacity_expansion_limit</code>. The first two
                surface a separate shadow price per period in the Emissions /
                Prices tabs.
              </span>
            </label>
          )}

          <label className="flex flex-col gap-0.5 col-span-2">
            <span className="text-[10px] text-muted">Constant ({spec.unit})</span>
            <input
              type="number" value={form.constant}
              onChange={e => setForm(f => ({ ...f, constant: Number(e.target.value) }))}
              className="px-2 py-1 border border-border rounded text-xs bg-bg font-mono"
            />
          </label>
        </div>
        <button
          onClick={onSubmit}
          disabled={createMut.isPending}
          className="w-full flex items-center justify-center gap-1 px-2 py-1.5 bg-success/90 text-white rounded text-[11px] font-medium hover:bg-success disabled:opacity-40"
        ><Plus size={11} /> {createMut.isPending ? 'Adding…' : 'Add constraint'}</button>
      </div>

      {/* Fuel-limit-per-period shortcut. Translates a (carrier, period, MWh)
          triple into one `operational_limit` global constraint per row —
          saves clicking through the generic form for every (carrier, period)
          combination. */}
      <FuelLimitsPerPeriod existingRows={rows} />
    </section>
  )
}

// ── Fuel-limit-per-period shortcut ────────────────────────────────────────
// A matrix-style editor that surfaces every (carrier, period) cell and lets
// the user type a MWh cap. Saves one `operational_limit` global constraint
// per non-empty cell. Reads back the same set on every render so the table
// always reflects what's actually applied to the LP.
function FuelLimitsPerPeriod({ existingRows }: { existingRows: GlobalConstraint[] }) {
  const qc = useQueryClient()
  const { data: carriers = [] } = useQuery({
    queryKey: ['carriers'],
    queryFn: networkApi.getCarriers,
  })
  const { data: invPeriods } = useQuery({
    queryKey: ['investmentPeriods'],
    queryFn: networkApi.getInvestmentPeriods,
  })

  // Derive the period list. Empty for single-period (flat) networks —
  // we still show a "horizon-wide" pseudo-period so users can cap
  // annual fuel use without enabling multi-period.
  type PeriodKey = number | 'horizon'
  const periods = useMemo<PeriodKey[]>(() => {
    const arr = (invPeriods?.periods ?? []) as number[]
    if (!arr || arr.length === 0) return ['horizon']
    return [...arr].sort((a, b) => a - b)
  }, [invPeriods])

  // Build a fast lookup of (carrier|period) → existing constraint row, so
  // when the user types a number into a cell we know whether to update an
  // existing constraint or POST a new one. Naming convention:
  //     fuel_limit_<carrier>_<period>
  // Pre-existing operational_limit rows the user created via the generic
  // form are matched by carrier+period regardless of name.
  type CellKey = string
  const cellKey = (carrier: string, period: PeriodKey) =>
    `${carrier}|${period}`
  const byCell = useMemo(() => {
    const map = new Map<CellKey, GlobalConstraint>()
    for (const r of existingRows) {
      if (r.type !== 'operational_limit') continue
      const p: PeriodKey = r.investment_period != null
        ? Number(r.investment_period) : 'horizon'
      if (r.carrier) map.set(cellKey(r.carrier, p), r)
    }
    return map
  }, [existingRows])

  // Local edit buffer — keyed by cellKey, value is the typed string.
  const [drafts, setDrafts] = useState<Record<CellKey, string>>({})

  const upsertMut = useMutation({
    mutationFn: async ({ carrier, period, mwh }: { carrier: string; period: PeriodKey; mwh: number | null }) => {
      const key = cellKey(carrier, period)
      const existing = byCell.get(key)
      const name = existing?.name ?? `fuel_limit_${carrier}_${period}`
      if (mwh == null) {
        // Empty cell ⇒ delete the constraint if one exists.
        if (existing) await networkApi.deleteGlobalConstraint(existing.name)
        return
      }
      const body = {
        name,
        type: 'operational_limit',
        sense: '<=',
        constant: mwh,
        carrier,
        investment_period: period === 'horizon' ? undefined : (period as number),
      }
      if (existing) await networkApi.updateGlobalConstraint(existing.name, body)
      else          await networkApi.createGlobalConstraint(body)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['global_constraints'] })
    },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      toast.error(e.response?.data?.detail ?? 'Failed to save fuel limit'),
  })

  const onBlur = (carrier: string, period: PeriodKey) => {
    const k = cellKey(carrier, period)
    const raw = drafts[k]
    if (raw === undefined) return  // user didn't touch this cell
    const trimmed = raw.trim()
    let mwh: number | null
    if (trimmed === '') mwh = null
    else {
      const v = Number(trimmed)
      if (!Number.isFinite(v) || v < 0) { toast.error('Must be a non-negative number'); return }
      mwh = v
    }
    upsertMut.mutate({ carrier, period, mwh })
    setDrafts(d => { const c = { ...d }; delete c[k]; return c })
  }

  // Hide carriers that wouldn't normally have a fuel limit — buses' own
  // carrier (AC/DC), purely electrical labels, etc. Keep the
  // "could-be-fuel" carriers visible: anything that has co2_emissions > 0,
  // OR is in the typical fuel-fossil keyword list.
  const FUEL_KEYWORDS = ['coal', 'lignite', 'gas', 'ocgt', 'ccgt', 'oil',
                          'biomass', 'biogas', 'nuclear', 'uranium',
                          'h2', 'hydrogen']
  const fuelCarriers = useMemo(() => {
    return (carriers as Array<{ name: string; co2_emissions?: number }>)
      .filter(c => {
        const n = (c.name ?? '').toLowerCase()
        if (c.co2_emissions && c.co2_emissions > 0) return true
        return FUEL_KEYWORDS.some(k => n.includes(k))
      })
      .map(c => c.name)
      .sort()
  }, [carriers])

  if (fuelCarriers.length === 0) {
    return (
      <div className="mt-3 border-t border-border pt-3">
        <div className="text-[10px] text-muted uppercase tracking-wide mb-1.5">
          Fuel limits per period
        </div>
        <p className="text-[11px] text-muted">
          No fuel-bearing carriers detected. Add carriers with{' '}
          <code>co2_emissions &gt; 0</code> (or named like
          <code> coal / gas / oil / biomass / nuclear / h2</code>) to use
          this shortcut. Visit the <span className="font-medium text-text">Carriers</span> tab
          (bottom panel) to set CO₂ intensities.
        </p>
      </div>
    )
  }

  return (
    <div className="mt-3 border-t border-border pt-3">
      <div className="flex items-center justify-between mb-1.5">
        <div className="text-[10px] text-muted uppercase tracking-wide">
          Fuel limits per period
          <span className="ml-2 lowercase normal-case text-muted/80">
            (operational_limit, MWh)
          </span>
        </div>
        <span className="text-[10px] text-muted">
          {periods[0] === 'horizon' ? 'Horizon-wide' : `${periods.length} period(s)`}
        </span>
      </div>
      <p className="text-[11px] text-muted mb-2 leading-relaxed">
        Cap the total dispatch (MWh) of each fuel-bearing carrier per
        investment period. Blank cell = no cap. Each non-empty cell is
        saved as one <code>operational_limit</code> global constraint
        named <code>fuel_limit_&lt;carrier&gt;_&lt;period&gt;</code>.
      </p>
      <div className="border border-border rounded overflow-auto max-h-72">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-panel z-10">
            <tr className="border-b border-border">
              <th className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Carrier</th>
              {periods.map(p => (
                <th key={String(p)} className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase whitespace-nowrap">
                  {p === 'horizon' ? 'Horizon' : p}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {fuelCarriers.map(carrier => (
              <tr key={carrier} className="border-b border-border/40">
                <td className="px-2 py-1 font-mono text-[11px]">{carrier}</td>
                {periods.map(p => {
                  const k = cellKey(carrier, p)
                  const existing = byCell.get(k)
                  const displayValue = drafts[k] !== undefined
                    ? drafts[k]
                    : (existing?.constant != null ? String(existing.constant) : '')
                  return (
                    <td key={String(p)} className="px-1 py-0.5 text-right">
                      <input
                        // `key` includes the existing value so React remounts
                        // when the cached constant changes — without this,
                        // the uncontrolled `defaultValue` would never refresh
                        // after a successful save (documented footgun).
                        key={`${k}-${existing?.constant ?? ''}`}
                        type="number"
                        step="any"
                        min={0}
                        value={displayValue}
                        onChange={e => setDrafts(d => ({ ...d, [k]: e.target.value }))}
                        onBlur={() => onBlur(carrier, p)}
                        onKeyDown={e => {
                          if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
                          if (e.key === 'Escape') {
                            setDrafts(d => { const c = { ...d }; delete c[k]; return c })
                            ;(e.target as HTMLInputElement).blur()
                          }
                        }}
                        placeholder="—"
                        className="w-full px-1.5 py-0.5 border border-transparent rounded text-[11px] font-mono text-right bg-transparent focus:bg-bg focus:border-accent hover:border-border"
                      />
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] text-muted mt-1.5">
        Blank cell ⇒ no cap (deletes the underlying constraint).
        Tip: hit <kbd className="px-1 border border-border rounded text-[9px] font-mono">Enter</kbd>
        {' '}to save, <kbd className="px-1 border border-border rounded text-[9px] font-mono">Esc</kbd>
        {' '}to revert an in-progress edit.
      </p>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Modelling Assumptions section — two sub-cards driving the transient
// solve-time knobs (discount rate / lifetime / CO2 price / VOLL). Lives
// between the Advanced solver options and the Global Constraints section so
// the user reads: "how to solve → what modelling assumptions → what
// constraints" top-to-bottom. Multi-period planning (toggle + years +
// per-period weightings) lives under Model Horizon.
// ─────────────────────────────────────────────────────────────────────────────

// EconomicAssumptions lives under the Dispatch tab. Three transient-at-solve
// fields (discount rate, default lifetime, CO2 price) that the backend applies
// before n.optimize() and reverts after — the network on disk stays unchanged.
function EconomicAssumptions({
  draft, patch,
}: {
  draft: SolverConfig
  patch: (p: Partial<SolverConfig>) => void
}) {
  return (
    <section className="border border-border rounded">
      <div className="px-3 py-1.5 border-b border-border bg-panel text-[10px] font-semibold uppercase tracking-wide text-muted">
        Economic assumptions
      </div>
      <div className="grid grid-cols-4 gap-3 p-3">
        <NumberField
          label="Discount rate"
          unit="%"
          // Strip FP rounding noise from the rate↔percent conversion
          // (e.g. 0.07 × 100 = 7.000000000000001 in IEEE-754). toFixed(2)
          // gives at most two decimal places; the step is 0.1 so two
          // decimals comfortably exceeds any value the user can input.
          // `?? 0` defends against legacy SolverConfig payloads missing
          // `discount_rate` entirely — without it, `undefined * 100`
          // becomes NaN, `(NaN).toFixed(2)` becomes "NaN", `Number("NaN")`
          // becomes NaN, and the `<input value={NaN}>` renders blank
          // with no controlled fallback (React warns).
          value={Number(((draft.discount_rate ?? 0) * 100).toFixed(2))}
          step={0.1}
          onChange={v => patch({ discount_rate: v / 100 })}
          hint="Used to annualise CAPEX via the annuity factor r(1+r)^L / ((1+r)^L−1). Applied to extendable assets whose overnight_cost is set. Typical: 5–10 %. If you enter a NOMINAL rate here, set the Inflation rate alongside so the cross-period PV factor uses the real rate; if your costs are already in real €, leave Inflation = 0."
        />
        <NumberField
          label="Inflation rate"
          unit="%"
          value={Number(((draft.inflation_rate ?? 0) * 100).toFixed(2))}
          step={0.1}
          onChange={v => patch({ inflation_rate: v / 100 })}
          hint="Expected inflation. Combined with the discount rate via the Fisher relation real = (1+nominal)/(1+inflation)−1 to derive the real discount used in the cross-period PV factor (when 'Auto-discount periods' is enabled under Model Horizon). Default 0 % preserves the previous behaviour where the discount rate is treated as real. Typical: 1.5–3 %."
        />
        <NumberField
          label="Default lifetime"
          unit="yr"
          // Same NaN-defence: missing `default_lifetime` on legacy
          // payloads would render the input as blank/uncontrolled.
          value={draft.default_lifetime ?? 25}
          step={1}
          onChange={v => patch({ default_lifetime: v })}
          hint="Fallback lifetime when an asset's own `lifetime` is empty or infinite. Combined with the discount rate, defines the annuity factor used to compute capital_cost from overnight_cost."
        />
        <NumberField
          label="CO₂ price (default)"
          unit="€/tCO₂"
          value={draft.co2_price ?? 0}
          step={1}
          onChange={v => patch({ co2_price: v })}
          hint="Default surcharge added to fossil generators' marginal_cost as co2_emissions × price / efficiency. Used at every snapshot UNLESS a per-period override is set below (multi-period only)."
        />
      </div>

      {/* ── Per-period CO₂ price (multi-period only) ─────────────────── */}
      <Co2PricePerPeriod draft={draft} patch={patch} />

      <p className="text-[10px] text-muted px-3 pb-3">
        Applied transiently at solve time and reverted after. The network state
        on disk (and exported .nc) stays unchanged — these are LP transforms.
      </p>
    </section>
  )
}

// ── Per-period CO2 price table ────────────────────────────────────────────
// Reads investment periods from /api/network/investment_periods. Each row
// is a period; the input value is €/tCO2 for that period. Blank cell
// falls back to the scalar `co2_price` (which is shown as the placeholder).
// On single-period (flat) networks this section renders a hint pointing
// users at the scalar field above.
function Co2PricePerPeriod({
  draft, patch,
}: {
  draft: SolverConfig
  patch: (p: Partial<SolverConfig>) => void
}) {
  const { data: invPeriods } = useQuery({
    queryKey: ['investmentPeriods'],
    queryFn: networkApi.getInvestmentPeriods,
  })
  const periods = useMemo<number[]>(() => {
    const arr = (invPeriods?.periods ?? []) as number[]
    return [...arr].sort((a, b) => a - b)
  }, [invPeriods])

  // Local edit buffer so we don't fire a patch on every keystroke.
  const [drafts, setDrafts] = useState<Record<string, string>>({})

  if (!draft.multi_investment_periods || periods.length === 0) {
    return (
      <div className="px-3 pb-3 text-[10px] text-muted">
        Per-period CO₂ price table appears when multi-period planning is enabled
        and at least one investment period is defined.{' '}
        {draft.multi_investment_periods
          ? <>Add periods under <span className="font-medium text-text">Model Horizon</span>.</>
          : <>Toggle <span className="font-medium text-text">Multi-investment periods</span> in General.</>}
      </div>
    )
  }

  const current = draft.co2_price_per_period ?? {}
  const onBlur = (period: number) => {
    const k = String(period)
    if (drafts[k] === undefined) return
    const raw = drafts[k].trim()
    const next = { ...current }
    if (raw === '') {
      delete next[k]
    } else {
      const v = Number(raw)
      if (!Number.isFinite(v) || v < 0) {
        toast.error('CO₂ price must be a non-negative number')
        return
      }
      next[k] = v
    }
    patch({ co2_price_per_period: next })
    setDrafts(d => { const c = { ...d }; delete c[k]; return c })
  }

  return (
    <div className="px-3 pb-3">
      <div className="flex items-center justify-between mb-1.5">
        <div className="text-[10px] text-muted uppercase tracking-wide">
          CO₂ price per investment period
          <span className="ml-2 lowercase normal-case text-muted/80">(€/tCO₂)</span>
        </div>
        <span className="text-[10px] text-muted">
          {periods.length} period(s) · default {draft.co2_price.toFixed(1)}
        </span>
      </div>
      <p className="text-[11px] text-muted mb-2 leading-relaxed">
        Override the default CO₂ price per period — e.g. 20 €/tCO₂ in 2026,
        25 €/tCO₂ in 2027. Empty cell falls back to the default above.
        Backend applies these via the time-varying{' '}
        <code>generators_t.marginal_cost</code> at solve time, so the LP sees
        the right surcharge at every snapshot.
      </p>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5">
        {periods.map(p => {
          const key = String(p)
          const draftVal = drafts[key]
          const persisted = current[key]
          const displayValue = draftVal !== undefined
            ? draftVal
            : (persisted != null ? String(persisted) : '')
          return (
            <label key={p}
                   className="flex flex-col gap-0.5 border border-border rounded px-2 py-1 bg-bg">
              <span className="text-[10px] text-muted font-mono">{p}</span>
              <input
                // Force remount when persisted value changes so the field
                // re-syncs after a save (uncontrolled-input footgun).
                key={`${p}-${persisted ?? ''}`}
                type="number"
                step="any"
                min={0}
                value={displayValue}
                onChange={e => setDrafts(d => ({ ...d, [key]: e.target.value }))}
                onBlur={() => onBlur(p)}
                onKeyDown={e => {
                  if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
                  if (e.key === 'Escape') {
                    setDrafts(d => { const c = { ...d }; delete c[key]; return c })
                    ;(e.target as HTMLInputElement).blur()
                  }
                }}
                placeholder={draft.co2_price ? draft.co2_price.toFixed(1) : '0'}
                className="px-1.5 py-0.5 border border-transparent rounded text-[11px] font-mono bg-bg
                           focus:border-accent hover:border-border"
              />
            </label>
          )
        })}
      </div>
    </div>
  )
}

// ReliabilityAssumptions lives under the Network tab. Single field (VOLL) —
// when > 0 the backend injects a slack 'load_shedding' generator on every bus,
// so the LP can drop demand instead of failing when supply is tight.
function ReliabilityAssumptions({
  draft, patch,
}: {
  draft: SolverConfig
  patch: (p: Partial<SolverConfig>) => void
}) {
  return (
    <section className="border border-border rounded">
      <div className="px-3 py-1.5 border-b border-border bg-panel text-[10px] font-semibold uppercase tracking-wide text-muted">
        Reliability — Value of Lost Load
      </div>
      <div className="p-3">
        <NumberField
          label="Value of Lost Load (VOLL)"
          unit="€/MWh"
          // `?? 0` for the same legacy-config defence as discount_rate
          // above — missing `voll` would render the field as
          // uncontrolled with React's warning.
          value={draft.voll ?? 0}
          step={100}
          onChange={v => patch({ voll: v })}
          hint="When > 0, a slack 'load_shedding' generator is added on every bus at this marginal_cost. Lets the LP drop demand instead of failing when supply is tight. Typical 3 000–10 000 €/MWh."
        />
        <p className="text-[10px] text-muted mt-2">
          Lost-load energy + cost surface in the Results → LoadFlow tab when
          this is set and the LP actually sheds.
        </p>
      </div>
    </section>
  )
}

// N-1 Security-Constrained LOPF panel. When `sclopf` is on, the run routes through PyPSA's
// `optimize_security_constrained()`; the branch_outages list is built
// server-side from this card's four selectors:
//   1) include-all-lines checkbox
//   2) include-all-transformers checkbox
//   3) voltage threshold (kV) — every line/transformer whose higher-voltage
//      bus is ≥ threshold is auto-selected
//   4) two explicit pick lists (extra lines, extra transformers)
// The four selectors UNION at solve time, so the user can layer them.
function SclopfPanel({
  draft, patch,
}: {
  draft: SolverConfig
  patch: (p: Partial<SolverConfig>) => void
}) {
  const { data: lines = [] }        = useQuery({ queryKey: ['lines'],        queryFn: networkApi.getLines })
  const { data: transformers = [] } = useQuery({ queryKey: ['transformers'], queryFn: networkApi.getTransformers })
  const { data: buses = [] }        = useQuery({ queryKey: ['buses'],        queryFn: networkApi.getBuses })
  const [searchLines, setSearchLines]   = useState('')
  const [searchTrafos, setSearchTrafos] = useState('')

  // Resolve max v_nom per branch via its connected buses. Cached as a Map
  // so the filters below stay O(1) per row even on big networks.
  const busVnom = useMemo(() => {
    const m = new Map<string, number>()
    for (const b of buses as Array<{ name: string; v_nom: number }>) {
      m.set(b.name, Number(b.v_nom) || 0)
    }
    return m
  }, [buses])
  const lineMaxV = (l: { bus0: string; bus1: string }) =>
    Math.max(busVnom.get(l.bus0) ?? 0, busVnom.get(l.bus1) ?? 0)

  const thr = draft.sclopf_voltage_threshold_kv ?? 0

  // Auto-selected sets from the (a) all-flags and (b) threshold rules — the
  // backend resolver does this union too, but we mirror it here so the user
  // can see which rows are already covered. Greyed-out checkboxes mean
  // "already included via another rule".
  const autoLines = useMemo(() => {
    const s = new Set<string>()
    const ls = lines as Array<{ name: string; bus0: string; bus1: string }>
    if (draft.sclopf_include_all_lines) ls.forEach(l => s.add(l.name))
    if (thr > 0) for (const l of ls) if (lineMaxV(l) >= thr) s.add(l.name)
    return s
  }, [lines, draft.sclopf_include_all_lines, thr, busVnom])
  const autoTrafos = useMemo(() => {
    const s = new Set<string>()
    const ts = transformers as Array<{ name: string; bus0: string; bus1: string }>
    if (draft.sclopf_include_all_transformers) ts.forEach(t => s.add(t.name))
    if (thr > 0) for (const t of ts) if (lineMaxV(t) >= thr) s.add(t.name)
    return s
  }, [transformers, draft.sclopf_include_all_transformers, thr, busVnom])

  const filteredLines  = useMemo(() =>
    (lines as Array<{ name: string; bus0: string; bus1: string }>)
      .filter(l => !searchLines || l.name.toLowerCase().includes(searchLines.toLowerCase())),
    [lines, searchLines])
  const filteredTrafos = useMemo(() =>
    (transformers as Array<{ name: string; bus0: string; bus1: string }>)
      .filter(t => !searchTrafos || t.name.toLowerCase().includes(searchTrafos.toLowerCase())),
    [transformers, searchTrafos])

  const totalResolved = autoLines.size + autoTrafos.size
    + (draft.sclopf_extra_lines ?? []).filter(n => !autoLines.has(n)).length
    + (draft.sclopf_extra_transformers ?? []).filter(n => !autoTrafos.has(n)).length

  const toggleExtraLine = (name: string) => {
    const cur = draft.sclopf_extra_lines ?? []
    patch({ sclopf_extra_lines: cur.includes(name)
      ? cur.filter(n => n !== name)
      : [...cur, name] })
  }
  const toggleExtraTrafo = (name: string) => {
    const cur = draft.sclopf_extra_transformers ?? []
    patch({ sclopf_extra_transformers: cur.includes(name)
      ? cur.filter(n => n !== name)
      : [...cur, name] })
  }

  return (
    <section className="border-t border-border pt-4">
      <div className="flex items-center gap-2 mb-1">
        <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">N-1 Security-Constrained LOPF</h3>
        <span className="text-[10px] text-muted">(SCLOPF — preventive)</span>
      </div>
      <p className="text-[11px] text-muted mb-3">
        Adds one constraint per (contingency × monitored branch) so no branch is
        overloaded even if a selected branch is out. Uses PyPSA's
        <code> n.optimize.optimize_security_constrained</code>. Only fires when
        <code> mode == "lopf"</code>. Each extra contingency adds rows per
        snapshot — keep the set focused for big networks.
      </p>

      <label className="flex items-center gap-2 cursor-pointer mb-3">
        <input type="checkbox" checked={draft.sclopf}
          onChange={e => patch({ sclopf: e.target.checked })}
          className="accent-accent" />
        <span className="text-xs font-medium text-text">Enable SCLOPF</span>
      </label>

      <div className={`flex flex-col gap-3 ${draft.sclopf ? '' : 'opacity-50 pointer-events-none'}`}>
        {/* ── Coarse selectors ──────────────────────────────────── */}
        <div className="border border-border rounded">
          <div className="px-3 py-1.5 border-b border-border bg-panel text-[10px] font-semibold uppercase tracking-wide text-muted">
            Default outage set
          </div>
          <div className="grid grid-cols-3 gap-3 p-3 items-start">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={draft.sclopf_include_all_lines}
                onChange={e => patch({ sclopf_include_all_lines: e.target.checked })}
                className="accent-accent" />
              <span className="text-xs text-text">Include all lines
                <span className="block text-[10px] text-muted">{(lines as unknown[]).length} on this network</span>
              </span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={draft.sclopf_include_all_transformers}
                onChange={e => patch({ sclopf_include_all_transformers: e.target.checked })}
                className="accent-accent" />
              <span className="text-xs text-text">Include all transformers
                <span className="block text-[10px] text-muted">{(transformers as unknown[]).length} on this network</span>
              </span>
            </label>
            <NumberField
              label="Voltage threshold"
              unit="kV"
              value={draft.sclopf_voltage_threshold_kv ?? 0}
              step={1}
              onChange={v => patch({ sclopf_voltage_threshold_kv: v })}
              hint="Lines / transformers whose higher-voltage bus is ≥ this value are auto-included as contingencies. 0 disables this rule. Typical: only stress-test the EHV / HV grid (e.g. 220 kV)."
            />
          </div>
        </div>

        {/* ── Resolved-set summary ──────────────────────────────── */}
        <div className="px-3 py-2 bg-panel border border-border rounded text-[11px] text-muted">
          <span className="font-medium text-text">Resolved contingencies: {totalResolved}</span>
          {totalResolved === 0 && draft.sclopf && (
            <span className="ml-2 text-danger">— pick at least one source above (otherwise this falls back to plain LOPF).</span>
          )}
          {totalResolved > 200 && (
            <span className="ml-2 text-warn">— large set, the LP may be slow.</span>
          )}
        </div>

        {/* ── Contingency scope (myopic-only) ───────────────────── */}
        {/* This dropdown only affects runs where solve_strategy == "myopic".
            On the full-horizon SCLOPF path there's only one snapshot window
            anyway, so the choice is irrelevant. We still render it whenever
            SCLOPF is enabled, with a hint saying it kicks in under myopic. */}
        <div className="border border-border rounded">
          <div className="px-3 py-1.5 border-b border-border bg-panel text-[10px] font-semibold uppercase tracking-wide text-muted">
            Contingency scope (myopic strategy)
          </div>
          <div className="p-3 flex flex-col gap-2">
            <label className="text-[11px] text-muted">
              When myopic foresight is active, choose how SCLOPF constraints
              apply across each iteration's snapshot window:
            </label>
            <select
              value={draft.sclopf_scope ?? 'horizon'}
              onChange={e => patch({ sclopf_scope: e.target.value as 'horizon' | 'current_period' })}
              className="bg-bg border border-border rounded px-2 py-1.5 text-[12px] text-text w-full"
            >
              <option value="horizon">
                Horizon — N-1 on every snapshot (current + future representatives)
              </option>
              <option value="current_period">
                Current period — N-1 only on current-period hourly snapshots
              </option>
            </select>
            <p className="text-[10px] text-muted leading-snug">
              <span className="font-medium text-text">Horizon</span> (default,
              safer): contingency constraints apply to current-period hourly
              snapshots AND any future-period representative slice from
              limited foresight. Matches the TSO "N-1 must always hold"
              convention. The LP grows with the iteration's full snapshot
              count.
              <br />
              <span className="font-medium text-text">Current period</span>:
              the SCLOPF LP for each iteration drops future-period snapshots
              entirely — only current-period hourly snapshots get N-1
              constraints. Faster, but limited foresight has no effect during
              SCLOPF iterations.
            </p>
            {draft.sclopf_scope === 'current_period' && draft.solve_strategy === 'myopic' && (
              <div className="text-[10px] text-warn bg-warn/10 border border-warn/30 rounded px-2 py-1">
                ⚠ Future-period representative snapshots are dropped from each
                iteration's contingency LP. Capacity decisions in each myopic
                iteration see ONLY that period's actual hourly load — no
                forward-looking demand growth from limited foresight.
              </div>
            )}
            {draft.solve_strategy !== 'myopic' && (
              <div className="text-[10px] text-muted italic">
                Applies only when <code>solve_strategy = "myopic"</code> is set
                above. Ignored on the full-horizon SCLOPF path.
              </div>
            )}
          </div>
        </div>

        {/* ── Explicit picks ───────────────────────────────────── */}
        <div className="grid grid-cols-2 gap-3">
          <div className="border border-border rounded min-w-0">
            <div className="px-3 py-1.5 border-b border-border bg-panel text-[10px] font-semibold uppercase tracking-wide text-muted flex items-center gap-2">
              <span className="flex-1 min-w-0 truncate">Extra lines</span>
              <input value={searchLines} placeholder="search…"
                onChange={e => setSearchLines(e.target.value)}
                className="bg-bg border border-border rounded px-1.5 py-0.5 text-[10px] w-20 min-w-0 shrink" />
            </div>
            <div className="max-h-64 overflow-y-auto p-2">
              {filteredLines.length === 0 ? (
                <p className="text-[10px] text-muted italic px-1">No lines.</p>
              ) : filteredLines.map(l => {
                const inAuto = autoLines.has(l.name)
                const inExtra = (draft.sclopf_extra_lines ?? []).includes(l.name)
                return (
                  <label key={l.name}
                    className={`flex items-center gap-1.5 py-0.5 px-1 text-[11px] cursor-pointer hover:bg-panel rounded ${inAuto ? 'opacity-60' : ''}`}
                    title={inAuto ? 'Already included via the rules above' : undefined}>
                    <input type="checkbox" checked={inAuto || inExtra}
                      disabled={inAuto}
                      onChange={() => toggleExtraLine(l.name)}
                      className="accent-accent" />
                    <span className="font-mono truncate flex-1">{l.name}</span>
                    <span className="text-[9px] text-muted shrink-0">{lineMaxV(l).toFixed(0)} kV</span>
                  </label>
                )
              })}
            </div>
          </div>

          <div className="border border-border rounded min-w-0">
            <div className="px-3 py-1.5 border-b border-border bg-panel text-[10px] font-semibold uppercase tracking-wide text-muted flex items-center gap-2">
              <span className="flex-1 min-w-0 truncate">Extra transformers</span>
              <input value={searchTrafos} placeholder="search…"
                onChange={e => setSearchTrafos(e.target.value)}
                className="bg-bg border border-border rounded px-1.5 py-0.5 text-[10px] w-20 min-w-0 shrink" />
            </div>
            <div className="max-h-64 overflow-y-auto p-2">
              {filteredTrafos.length === 0 ? (
                <p className="text-[10px] text-muted italic px-1">No transformers.</p>
              ) : filteredTrafos.map(t => {
                const inAuto = autoTrafos.has(t.name)
                const inExtra = (draft.sclopf_extra_transformers ?? []).includes(t.name)
                return (
                  <label key={t.name}
                    className={`flex items-center gap-1.5 py-0.5 px-1 text-[11px] cursor-pointer hover:bg-panel rounded ${inAuto ? 'opacity-60' : ''}`}
                    title={inAuto ? 'Already included via the rules above' : undefined}>
                    <input type="checkbox" checked={inAuto || inExtra}
                      disabled={inAuto}
                      onChange={() => toggleExtraTrafo(t.name)}
                      className="accent-accent" />
                    <span className="font-mono truncate flex-1">{t.name}</span>
                    <span className="text-[9px] text-muted shrink-0">{lineMaxV(t).toFixed(0)} kV</span>
                  </label>
                )
              })}
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

// Stage 2 — post-solve AC Power Flow panel. Controls the auto-chain toggle,
// the slack-bus override, the Newton-Raphson tolerance, and the standalone
// "Run AC Power Flow on current solution" trigger. Mirrors SclopfPanel's
// layout (single-card section with subdued sub-options when the master
// toggle is off) so the Solver Settings page reads consistently across
// tabs.
//
// Trigger semantics:
//   • Auto-chain checkbox flips `run_ac_pf_after_lopf`. On the next "Run
//     Optimization" the backend chains Stage 2 inside the same worker.
//   • Standalone button calls POST /api/simulation/run_ac_pf, which is a
//     separate worker thread. Requires that LOPF has solved and dispatch
//     is populated. Disabled when no solved network is loaded.
//   • On successful standalone trigger the resultSource auto-flips to
//     'ac_pf' (per the design Q: "auto-flip to AC PF view"). The user can
//     flip back via the LOPF/AC PF toggle in LoadFlow / TopologyCanvas.
function Stage2Panel({
  draft, patch,
}: {
  draft: SolverConfig
  patch: (p: Partial<SolverConfig>) => void
}) {
  const { data: buses = [] } = useQuery({ queryKey: ['buses'], queryFn: networkApi.getBuses })
  const { data: simStatus } = useQuery({
    // Use the SAME key as every other status consumer (App, AppHeader,
    // SnapshotPicker, Results, OverviewPanel). The previous 'simulation_status'
    // was a separate cache entry, so a standalone AC-PF trigger from here
    // refreshed only this panel and left the picker/overlay status stale.
    queryKey: ['simulationStatus'],
    queryFn: simulationApi.getStatus,
    refetchInterval: 2000,
  })
  const setResultSource = useUIStore(s => s.setResultSource)
  const qc = useQueryClient()

  // Treat "completed/optimal" as "has a solved network we can fix dispatch
  // from". An idle network that was loaded from a project save also counts —
  // /api/simulation/run_ac_pf will 400 if the network isn't actually solved.
  const isRunning = simStatus?.running === true
  const canRunAcPf = !isRunning

  // Refresh result queries when a standalone AC-PF run finishes. The success
  // handler flips resultSource→'ac_pf' (which refetches the ac_pf-keyed
  // queries), but the lopf-keyed result queries wouldn't refetch if the user
  // flips back to 'lopf'. Watch the running→idle edge and invalidate ['results']
  // (+ status) so every view reflects the post-AC-PF state. prevRunningRef
  // starts false so a fresh mount with no run in flight never fires.
  const prevRunningRef = useRef(false)
  useEffect(() => {
    if (prevRunningRef.current && !isRunning) {
      qc.invalidateQueries({ queryKey: ['results'] })
      qc.invalidateQueries({ queryKey: ['simulationStatus'] })
    }
    prevRunningRef.current = isRunning
  }, [isRunning, qc])

  const runAcPf = useMutation({
    mutationFn: () => simulationApi.runAcPf(),
    onSuccess: () => {
      toast.success('Stage 2: AC Power Flow started — see the Log tab for progress.')
      // Auto-flip the result-source toggle to AC PF so the LoadFlow tab and
      // the canvas overlay show the new view as soon as it finishes.
      setResultSource('ac_pf')
      // Invalidate the simulation status query so the polling cycle picks up
      // the new "running" state immediately rather than waiting 2s.
      qc.invalidateQueries({ queryKey: ['simulationStatus'] })
    },
    onError: (e: unknown) => {
      const code = (e as { response?: { status?: number } })?.response?.status
      if (code === 409) {
        toast(
          (t) => (
            <div className="flex items-center gap-3">
              <span>Previous simulation still running.</span>
              <button
                className="px-2 py-0.5 rounded bg-accent text-white text-xs hover:opacity-90"
                onClick={async () => {
                  toast.dismiss(t.id)
                  try {
                    await simulationApi.forceReset()
                    runAcPf.mutate()
                  } catch { /* ignore */ }
                }}
              >
                Force restart
              </button>
            </div>
          ),
          { duration: 10000 },
        )
        return
      }
      const msg = e instanceof Error ? e.message : 'Stage 2 start failed'
      toast.error(`AC PF: ${msg}`)
    },
  })

  // Slack-bus options. Empty value ⇒ backend auto-picks (largest gen).
  const busOptions = useMemo(
    () => (buses as Array<{ name: string }>).map(b => b.name).sort(),
    [buses],
  )

  return (
    <section>
      <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2.5">Stage 2: AC Power Flow</h3>
      <p className="text-xs text-muted mb-3">
        After the LOPF / SCLOPF solve, fix the optimal dispatch and run a
        non-linear Newton-Raphson AC power flow per snapshot to recover real
        voltages, reactive power, and physical line losses. Each snapshot
        solves independently; non-converged snapshots are flagged in the
        LoadFlow tab and on the canvas.
      </p>

      <label className="flex items-center justify-between mb-3 cursor-pointer">
        <span className="text-xs">
          Auto-run AC PF after optimisation
          <span className="block text-[11px] text-muted">
            Chains Stage 2 in the same "Run" click. Leave off to trigger
            Stage 2 manually below.
          </span>
        </span>
        <input
          type="checkbox"
          checked={!!draft.run_ac_pf_after_lopf}
          onChange={e => patch({ run_ac_pf_after_lopf: e.target.checked })}
        />
      </label>

      <div className="grid grid-cols-2 gap-3 mb-3">
        <div className="flex flex-col gap-0.5">
          <label className="text-[11px] text-muted">
            Slack bus
            <span className="ml-1 text-[10px] text-muted/70">(auto-pick if blank)</span>
          </label>
          <select
            value={draft.ac_pf_slack_bus ?? ''}
            onChange={e => patch({ ac_pf_slack_bus: e.target.value })}
            className="px-2 py-1 border border-border rounded text-xs bg-bg"
          >
            <option value="">— Auto (largest gen) —</option>
            {busOptions.map(b => <option key={b} value={b}>{b}</option>)}
          </select>
          <span className="text-[10px] text-muted">
            Empty ⇒ backend picks the bus with the largest aggregate p_nom.
          </span>
        </div>
        <div className="flex flex-col gap-0.5">
          <label className="text-[11px] text-muted">
            Tolerance (x_tol)
          </label>
          <input
            type="number"
            step="any"
            min={0}
            value={draft.ac_pf_x_tol ?? 1e-6}
            onChange={e => {
              const v = parseFloat(e.target.value)
              patch({ ac_pf_x_tol: Number.isFinite(v) && v > 0 ? v : 1e-6 })
            }}
            className="px-2 py-1 border border-border rounded text-xs bg-bg font-mono"
          />
          <span className="text-[10px] text-muted">
            Newton-Raphson convergence threshold. Default 1e-6.
          </span>
        </div>
      </div>

      <div className="border-t border-border pt-3">
        <button
          onClick={() => runAcPf.mutate()}
          disabled={!canRunAcPf || runAcPf.isPending}
          className="w-full py-2 bg-accent text-white rounded text-xs font-semibold hover:bg-accent/90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {runAcPf.isPending ? 'Starting Stage 2…' : 'Run AC Power Flow on current solution'}
        </button>
        <p className="text-[10px] text-muted mt-1.5">
          Standalone trigger — requires that a LOPF has solved at least once
          since the network was loaded.
          {isRunning && ' Disabled while a solve is in progress.'}
        </p>
      </div>
    </section>
  )
}


// Small numeric input with label + unit + (?) tooltip. Local to this file
// because it has slightly different sizing than the generic NumInput used
// in the right Properties panel — denser, full-width, two-line layout.
function NumberField({
  label, unit, value, step, onChange, hint,
}: {
  label: string
  unit?: string
  value: number
  step?: number
  onChange: (v: number) => void
  hint?: string
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] text-muted">
        {hint ? (
          <span className="inline-flex items-baseline gap-1" title={hint}>
            {label}{unit ? ` (${unit})` : ''}
            <span className="text-[9px] text-muted/60 cursor-help">ⓘ</span>
          </span>
        ) : (
          <>{label}{unit ? ` (${unit})` : ''}</>
        )}
      </span>
      <input
        type="number"
        value={value}
        step={step ?? 0.01}
        onChange={e => {
          const v = parseFloat(e.target.value)
          onChange(Number.isFinite(v) ? v : 0)
        }}
        className="px-2 py-1 border border-border rounded text-xs font-mono bg-bg focus:outline-none focus:border-accent"
      />
    </label>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Clustering section. Lives in the General tab. Lets the user pre-process the
// in-memory network by mapping buses to clusters BEFORE the LP. Four modes:
//   • nodal   — no clustering (network is left alone)
//   • zone    — one cluster per `country` value on n.buses (zonal market view)
//   • region  — one cluster per electrical sub-network (connected component
//               on the passive-branch graph: lines + transformers)
//   • custom  — pick a PyPSA algorithm and tune its parameters
//
// Clicking "Apply clustering" calls POST /api/network/cluster which mutates
// the in-memory network and triggers full cache invalidation so the canvas
// and tables refresh to the reduced topology.
// ─────────────────────────────────────────────────────────────────────────────

type ClusterMode = 'nodal' | 'zone' | 'region' | 'custom'
type ClusterAlgorithm = 'kmeans' | 'hac' | 'greedy_modularity' | 'stubs'

interface KmeansOpts {
  n_init: number
  max_iter: number
  tol: number
  random_state: number
}
interface HacOpts {
  affinity: 'euclidean' | 'l1' | 'l2' | 'manhattan' | 'cosine'
  linkage: 'ward' | 'complete' | 'average' | 'single'
  feature_source: 'none' | 'renewable_cf'
}
interface StubsOpts {
  matching_attrs: string[]
}
interface ClusterRequest {
  mode: ClusterMode
  algorithm?: ClusterAlgorithm
  n_clusters?: number
  weighting?: 'uniform' | 'load'
  kmeans?: KmeansOpts
  hac?: HacOpts
  stubs?: StubsOpts
}

const DEFAULT_CLUSTER_REQ: ClusterRequest = {
  mode: 'nodal',
  algorithm: 'kmeans',
  n_clusters: 50,
  weighting: 'load',
  kmeans: { n_init: 10, max_iter: 300, tol: 1e-4, random_state: 0 },
  hac:    { affinity: 'euclidean', linkage: 'ward', feature_source: 'none' },
  stubs:  { matching_attrs: [] },
}

function ClusteringSection() {
  const qc = useQueryClient()
  const { data: buses = [] } = useQuery({ queryKey: ['buses'], queryFn: networkApi.getBuses })
  const [req, setReq] = useState<ClusterRequest>(DEFAULT_CLUSTER_REQ)

  // Context counts surfaced above the form: tells the user what they're
  // working with before they pick a mode. Counts come from /buses (already
  // cached for the canvas), so this is free.
  const busArr = buses as Array<{ name: string; country?: string; sub_network?: string }>
  const nBuses = busArr.length
  const nCountries = new Set(busArr.map(b => b.country || '—')).size
  // Exclude empty labels so the count reflects only manually-assigned regions.
  // Without this filter, the catch-all "—" bucket inflates the count by 1
  // whenever any bus is blank (typical case on a freshly-imported network).
  const nSubNets = new Set(
    busArr.map(b => (b.sub_network ?? '').trim()).filter(v => v !== '')
  ).size

  const apply = useMutation({
    mutationFn: () => networkApi.applyClustering(req),
    onSuccess: (res: { bus_count: number; line_count: number; message: string }) => {
      // Full invalidation — every component table is stale after a cluster.
      qc.invalidateQueries()
      toast.success(res.message ?? `Clustered to ${res.bus_count} buses`)
    },
    onError: (e: { response?: { data?: { detail?: string } } } | Error) => {
      const msg = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail
                ?? (e as Error).message
      toast.error(`Clustering failed: ${msg}`)
    },
  })

  const setMode = (m: ClusterMode) => setReq(r => ({ ...r, mode: m }))
  const setAlgorithm = (a: ClusterAlgorithm) => setReq(r => ({ ...r, algorithm: a }))
  const patchK = (p: Partial<KmeansOpts>) => setReq(r => ({ ...r, kmeans: { ...r.kmeans!, ...p } }))
  const patchH = (p: Partial<HacOpts>) => setReq(r => ({ ...r, hac:    { ...r.hac!,    ...p } }))
  const patchS = (p: Partial<StubsOpts>) => setReq(r => ({ ...r, stubs:  { ...r.stubs!,  ...p } }))

  const modeDesc: Record<ClusterMode, string> = {
    nodal:  `Keep the network as-is (${nBuses} buses).`,
    zone:   `One cluster per country (${nCountries} distinct, requires bus.country to be set).`,
    region: `One cluster per sub-network. Buses with the same bus.sub_network value collapse together; blank values auto-fill from electrical connectivity. ${nSubNets === 0 ? 'No manual labels yet — full auto-determination.' : `${nSubNets} manual label${nSubNets === 1 ? '' : 's'}, rest auto-determined.`}`,
    custom: 'Pick a PyPSA clustering algorithm and target cluster count.',
  }

  const showCustom = req.mode === 'custom'
  const algoNeedsN = showCustom && req.algorithm !== 'stubs'

  return (
    <section className="border border-border rounded">
      <div className="px-3 py-1.5 border-b border-border bg-panel">
        <div className="text-xs font-semibold text-text">Clustering</div>
        <div className="text-[10px] text-muted">
          Pre-LP topology reduction · current: {nBuses} buses · {nSubNets} manual sub-net label{nSubNets === 1 ? '' : 's'} · {nCountries} {nCountries === 1 ? 'country' : 'countries'}
        </div>
      </div>
      <div className="p-3 flex flex-col gap-3">
        {/* Mode picker */}
        <div className="flex flex-col gap-1.5">
          {(['nodal', 'zone', 'region', 'custom'] as const).map(m => (
            <label key={m} className={`flex items-start gap-2 cursor-pointer p-2 border rounded ${
              req.mode === m ? 'border-accent bg-accent/5' : 'border-border hover:bg-panel'
            }`}>
              <input
                type="radio" name="cluster_mode" value={m} checked={req.mode === m}
                onChange={() => setMode(m)}
                className="mt-0.5 accent-accent"
              />
              <div className="min-w-0">
                <div className="text-xs font-medium text-text capitalize">{m}</div>
                <div className="text-[10px] text-muted">{modeDesc[m]}</div>
              </div>
            </label>
          ))}
        </div>

        {/* Custom-mode: algorithm picker + per-algo params */}
        {showCustom && (
          <div className="border-t border-border pt-3 flex flex-col gap-3">
            <div className="flex items-center gap-2">
              <label className="text-[11px] text-muted w-20">Algorithm</label>
              <select
                value={req.algorithm}
                onChange={e => setAlgorithm(e.target.value as ClusterAlgorithm)}
                className="flex-1 px-2 py-1 border border-border rounded text-xs bg-bg"
              >
                <option value="kmeans">k-means — spatial, ignores topology</option>
                <option value="hac">HAC — topology-constrained agglomerative</option>
                <option value="greedy_modularity">Greedy modularity — community detection</option>
                <option value="stubs">Stubs — fold dead-ends (no target count)</option>
              </select>
            </div>

            {algoNeedsN && (
              <div className="flex items-center gap-2">
                <label className="text-[11px] text-muted w-20">Target N</label>
                <input
                  type="number" min={1} max={Math.max(1, nBuses)} step={1}
                  value={req.n_clusters ?? 50}
                  onChange={e => setReq(r => ({ ...r, n_clusters: Math.max(1, Math.min(nBuses || 1, parseInt(e.target.value || '50', 10) || 50)) }))}
                  className="bg-bg border border-border rounded px-2 py-1 text-xs font-mono w-24"
                />
                <span className="text-[10px] text-muted">final cluster count (≤ {nBuses})</span>
              </div>
            )}

            {/* Bus weighting — applies to k-means; HAC/modularity ignore it. */}
            {req.algorithm === 'kmeans' && (
              <div className="flex items-center gap-2">
                <label className="text-[11px] text-muted w-20">Weighting</label>
                <select
                  value={req.weighting ?? 'load'}
                  onChange={e => setReq(r => ({ ...r, weighting: e.target.value as 'uniform' | 'load' }))}
                  className="flex-1 px-2 py-1 border border-border rounded text-xs bg-bg"
                >
                  <option value="load">By peak load (Σ p_set on each bus)</option>
                  <option value="uniform">Uniform (every bus weighted 1)</option>
                </select>
              </div>
            )}

            {/* k-means params */}
            {req.algorithm === 'kmeans' && (
              <div className="grid grid-cols-2 gap-2">
                <label className="flex flex-col gap-0.5">
                  <span className="text-[11px] text-muted">n_init</span>
                  <input type="number" min={1} step={1}
                    value={req.kmeans?.n_init ?? 10}
                    onChange={e => patchK({ n_init: Math.max(1, parseInt(e.target.value || '10', 10) || 10) })}
                    className="px-2 py-1 border border-border rounded text-xs font-mono bg-bg" />
                </label>
                <label className="flex flex-col gap-0.5">
                  <span className="text-[11px] text-muted">max_iter</span>
                  <input type="number" min={1} step={1}
                    value={req.kmeans?.max_iter ?? 300}
                    onChange={e => patchK({ max_iter: Math.max(1, parseInt(e.target.value || '300', 10) || 300) })}
                    className="px-2 py-1 border border-border rounded text-xs font-mono bg-bg" />
                </label>
                <label className="flex flex-col gap-0.5">
                  <span className="text-[11px] text-muted">tol</span>
                  <input type="number" min={0} step="any"
                    value={req.kmeans?.tol ?? 1e-4}
                    onChange={e => patchK({ tol: Math.max(0, parseFloat(e.target.value || '1e-4') || 1e-4) })}
                    className="px-2 py-1 border border-border rounded text-xs font-mono bg-bg" />
                </label>
                <label className="flex flex-col gap-0.5">
                  <span className="text-[11px] text-muted">random_state</span>
                  <input type="number" step={1}
                    value={req.kmeans?.random_state ?? 0}
                    onChange={e => patchK({ random_state: parseInt(e.target.value || '0', 10) || 0 })}
                    className="px-2 py-1 border border-border rounded text-xs font-mono bg-bg" />
                </label>
              </div>
            )}

            {/* HAC params */}
            {req.algorithm === 'hac' && (
              <div className="grid grid-cols-2 gap-2">
                <label className="flex flex-col gap-0.5">
                  <span className="text-[11px] text-muted">affinity</span>
                  <select
                    value={req.hac?.affinity ?? 'euclidean'}
                    onChange={e => patchH({ affinity: e.target.value as HacOpts['affinity'] })}
                    className="px-2 py-1 border border-border rounded text-xs bg-bg"
                  >
                    {(['euclidean', 'l1', 'l2', 'manhattan', 'cosine'] as const).map(o =>
                      <option key={o} value={o}>{o}</option>)}
                  </select>
                </label>
                <label className="flex flex-col gap-0.5">
                  <span className="text-[11px] text-muted">linkage</span>
                  <select
                    value={req.hac?.linkage ?? 'ward'}
                    onChange={e => patchH({ linkage: e.target.value as HacOpts['linkage'] })}
                    className="px-2 py-1 border border-border rounded text-xs bg-bg"
                  >
                    {(['ward', 'complete', 'average', 'single'] as const).map(o =>
                      <option key={o} value={o}>{o}</option>)}
                  </select>
                </label>
                <label className="flex flex-col gap-0.5 col-span-2">
                  <span className="text-[11px] text-muted">feature source</span>
                  <select
                    value={req.hac?.feature_source ?? 'none'}
                    onChange={e => patchH({ feature_source: e.target.value as HacOpts['feature_source'] })}
                    className="px-2 py-1 border border-border rounded text-xs bg-bg"
                  >
                    <option value="none">None — equal similarity (uniform feature)</option>
                    <option value="renewable_cf">Renewable CF — wind/solar p_max_pu time-series</option>
                  </select>
                  <span className="text-[10px] text-muted">
                    With <code>ward</code> linkage only <code>euclidean</code> affinity is accepted.
                  </span>
                </label>
              </div>
            )}

            {/* Stubs params */}
            {req.algorithm === 'stubs' && (
              <div className="flex flex-col gap-1">
                <span className="text-[11px] text-muted">Match attributes (only fold a stub into its neighbour if they agree on every selected attribute)</span>
                <div className="flex flex-wrap gap-2">
                  {(['v_nom', 'carrier', 'country', 'sub_network'] as const).map(attr => {
                    const on = (req.stubs?.matching_attrs ?? []).includes(attr)
                    return (
                      <label key={attr} className={`flex items-center gap-1 px-2 py-1 border rounded text-[11px] cursor-pointer ${
                        on ? 'bg-accent/10 border-accent/40 text-accent' : 'border-border text-muted hover:text-text'
                      }`}>
                        <input
                          type="checkbox" checked={on}
                          onChange={() => patchS({
                            matching_attrs: on
                              ? (req.stubs?.matching_attrs ?? []).filter(a => a !== attr)
                              : [...(req.stubs?.matching_attrs ?? []), attr],
                          })}
                          className="accent-accent"
                        />
                        <code>{attr}</code>
                      </label>
                    )
                  })}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Apply — separate from "Save settings" because clustering mutates the
            in-memory network (not the solver config). Disabled in nodal mode
            (would be a no-op). */}
        <div className="border-t border-border pt-3 flex items-center gap-2">
          <button
            onClick={() => apply.mutate()}
            disabled={apply.isPending || req.mode === 'nodal'}
            className="flex-1 px-3 py-1.5 bg-accent text-white rounded text-xs font-medium hover:bg-accent/90 disabled:opacity-40"
          >
            {apply.isPending ? 'Clustering…' : 'Apply clustering'}
          </button>
          <button
            onClick={() => setReq(DEFAULT_CLUSTER_REQ)}
            className="px-3 py-1.5 border border-border rounded text-xs text-muted hover:text-text"
          >
            Reset
          </button>
        </div>
        <p className="text-[10px] text-muted">
          Clustering replaces the in-memory network with the reduced topology.
          Save the project (header → Save) afterwards to persist; the operation
          can't be undone in-session beyond ⌘Z range, so save first if uncertain.
        </p>
      </div>
    </section>
  )
}

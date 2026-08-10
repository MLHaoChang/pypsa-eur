import React, { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery, useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query'
import {
  X, Plus, Zap, Battery, Database, GitBranch,
  Pencil, Trash2, Flame, Wind, BatteryCharging, ChevronRight, PlugZap, TrendingUp,
  HelpCircle, ExternalLink,
} from 'lucide-react'
import { H2Icon } from '../components/AssetIcons'
import { AreaChart, Area, ResponsiveContainer, Tooltip } from 'recharts'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { isRenewableCarrier } from '../utils/carriers'
import { ingestRescale } from '../utils/rescaleActions'
import { networkApi } from '../api/network'
import { simulationApi } from '../api/simulation'
import type { Bus, Carrier, Generator, Line, Link, LinkProfileMeta, Load, StorageUnit, Store, Transformer, LoadProfileMeta, GeneratorProfileMeta } from '../api/types'
import { safeMax } from '../utils/numeric'
import { isPlaced } from '../utils/geo'
import toast from 'react-hot-toast'
import CreationForm from './CreationForm'
import { tip as docTip } from '../utils/propertyDocs'
import { CARRIER_CATALOG_NAMES } from '../utils/carrierCatalog'
import { vintageCloneToast } from '../utils/toasts'
import VintagePeriodBoundsModal from '../components/VintagePeriodBoundsModal'

import {
  TOOLTIP_VIEWPORT_MARGIN,
  InfoTip,
  Row,
  Section,
  ExpandedItem,
  ExpandedProps,
  DetailFooter,
  VintageCloneButton,
  FS,
  toFS,
  nf,
  ni,
  no,
  SetFS,
  NumInput,
  useGlobalDiscountRate,
  useGlobalDefaultLifetime,
  useBuildYearOptions,
  BuildYearSelect,
  defaultLifetime,
  DiscountRateInput,
  discountRatePct,
  TxtInput,
  CoordPairInput,
  ChkInput,
  autofillNomMin,
  SelInput,
  categorizeCarriers,
  CarrierGroupSelect,
  SectionHdr,
  BusSelect,
  MiniProfileChart,
  CardShell,
  EditShell,
} from "./properties/cardKit"

// ── Delete undo toast ──────────────────────────────────────────────────────────
const ALL_NETWORK_KEYS = ['buses', 'lines', 'links', 'generators', 'loads', 'storage_units', 'stores', 'transformers', 'carriers', 'snapshots', 'undoInfo']

function showDeleteUndoToast(label: string, qc: QueryClient) {
  toast.custom(
    (t) => (
      <div
        style={{ opacity: t.visible ? 1 : 0, transition: 'opacity 0.15s' }}
        className="flex items-center gap-3 bg-panel border border-border rounded-lg shadow-lg px-4 py-2.5 text-sm"
      >
        <Trash2 size={12} className="text-danger shrink-0" />
        <span className="text-text">Deleted <span className="font-semibold">{label}</span></span>
        <button
          onClick={() => {
            toast.dismiss(t.id)
            networkApi.undo().then(() => {
              const proj = useUIStore.getState().currentProject
              ALL_NETWORK_KEYS.forEach(k => qc.invalidateQueries({ queryKey: nk(proj, k) }))
              toast.success('Undone')
            }).catch((e: Error) => toast.error(e.message))
          }}
          className="ml-1 text-accent font-semibold text-xs hover:underline whitespace-nowrap"
        >
          Undo
        </button>
        <button onClick={() => toast.dismiss(t.id)} aria-label="Dismiss notification" className="p-0.5 text-muted hover:text-text transition-colors">
          <X size={11} />
        </button>
      </div>
    ),
    { duration: 6000 },
  )
}

// ── Generator card ─────────────────────────────────────────────────────────────
// Exported so the card can be rendered in isolation by
// PropertiesPanel.save.test.tsx. Props and behaviour are unchanged.
export function GeneratorCard({ gen, onRename, mode = 'card', title }: {
  gen: Generator
  onRename?: (newName: string) => void
  mode?: 'card' | 'detail'
  // Optional override for the Section title in detail mode. Used by
  // AssetGroupPanel to show the asset name as the heading so each card in a
  // multi-card list is identifiable. Single-asset panels rely on the default.
  title?: string
}) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [showChart, setShowChart] = useState(false)
  const [vintageOpen, setVintageOpen] = useState(false)
  const [form, setForm] = useState<FS>({})

  const currentProject = useUIStore(s => s.currentProject)
  const { data: genProfiles = {} } = useQuery({
    queryKey: nk(currentProject, 'generator_profiles'),
    queryFn: networkApi.getGeneratorProfiles,
    staleTime: 30_000,
  })
  const genMeta: GeneratorProfileMeta | undefined = genProfiles[gen.name]
  const hasProfile = genMeta?.has_profile === true

  const { data: carriers = [] } = useQuery({ queryKey: nk(currentProject, 'carriers'), queryFn: networkApi.getCarriers, staleTime: 60_000 })
  const { data: buses = [] } = useQuery({ queryKey: nk(currentProject, 'buses'), queryFn: networkApi.getBuses, staleTime: 30_000 })
  const globalLt = useGlobalDefaultLifetime()

  const delMut = useMutation({
    mutationFn: () => networkApi.deleteGenerator(gen.name),
    onSuccess: () => { qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'generators') }); showDeleteUndoToast(gen.name, qc) },
    onError: (e: Error) => toast.error(e.message),
  })

  const saveMut = useMutation({
    mutationFn: () => {
      const newName = form.name?.trim() || gen.name
      const newBus = (form.bus as string | undefined)?.trim() || gen.bus
      // Read latest cached generator (race-safe vs stale closure) and
      // spread it so fields the form doesn't surface are preserved
      // through the backend's remove+add cycle. Mirrors the pattern
      // applied to Load/Link/Transformer cards in patch B1.
      const cachedGens = qc.getQueryData<Generator[]>(nk(useUIStore.getState().currentProject, 'generators')) ?? []
      const current = cachedGens.find(g => g.name === gen.name) ?? gen
      const payload: Partial<Generator> = {
        ...current,
        name: newName, bus: newBus,
        carrier: form.carrier || current.carrier,
        // AC control mode (PQ/PV/Slack). Drives Stage 2 AC PF; falls back to
        // the existing value if the dropdown wasn't touched. Coerce to the
        // narrow string-union the type demands so the payload is type-safe.
        control: ((form.control || current.control || 'PQ') as 'PQ' | 'PV' | 'Slack'),
        p_nom: nf(form, 'p_nom', current.p_nom),
        p_nom_extendable: form.p_nom_extendable === 'true',
        p_nom_min: nf(form, 'p_nom_min', current.p_nom_min ?? 0),
        p_min_pu: nf(form, 'p_min_pu', current.p_min_pu),
        p_max_pu: nf(form, 'p_max_pu', current.p_max_pu),
        // Cost fields: blank input ⇒ save 0, not the old value. nf()'s
        // existing-value fallback was hiding deliberate clears (typical
        // case: user sets overnight_cost and wipes capital_cost so the
        // solver re-derives it from overnight × annuity on the next run).
        marginal_cost: nf(form, 'marginal_cost', 0),
        capital_cost: nf(form, 'capital_cost', 0),
        fom_cost: nf(form, 'fom_cost', 0),
        curtailment_cost: nf(form, 'curtailment_cost', 0),
        efficiency: nf(form, 'efficiency', current.efficiency),
        committable: form.committable === 'true',
        build_year: ni(form, 'build_year', current.build_year ?? 2025),
        start_up_cost: nf(form, 'start_up_cost', current.start_up_cost ?? 0),
        shut_down_cost: nf(form, 'shut_down_cost', current.shut_down_cost ?? 0),
        min_up_time: ni(form, 'min_up_time', current.min_up_time ?? 0),
        min_down_time: ni(form, 'min_down_time', current.min_down_time ?? 0),
      }
      // Optional bounds + per-asset cost overrides. Assign UNCONDITIONALLY
      // so blanking a field actually clears it on the backend — the
      // `...current` spread above re-injects the previous value, and the
      // old `if (X !== null) payload.X = X` gate skipped the assignment
      // for blanks, making it impossible for the user to un-set a bound
      // once entered. `null` in the payload routes through Pydantic's
      // `Optional[float] = None` → PyPSA's sentinel (-/+inf for bounds,
      // NaN for discount_rate/overnight_cost) which is "unbounded / use
      // global default". Same semantic as never having typed a value.
      payload.p_nom_max = no(form, 'p_nom_max')
      payload.lifetime = no(form, 'lifetime')
      payload.ramp_limit_up = no(form, 'ramp_limit_up')
      payload.ramp_limit_down = no(form, 'ramp_limit_down')
      // e_sum_min / e_sum_max: PyPSA's annual energy bounds. Same
      // clearing semantic as p_nom_max / lifetime above.
      payload.e_sum_min = no(form, 'e_sum_min')
      payload.e_sum_max = no(form, 'e_sum_max')
      payload.overnight_cost = no(form, 'overnight_cost')
      // Per-asset discount rate. UI carries percent (7), backend wants decimal (0.07).
      // null ⇒ NaN on the asset ⇒ solver fills with global at solve time.
      const drPct = no(form, 'discount_rate_pct')
      payload.discount_rate = drPct === null ? null : drPct / 100
      return networkApi.updateGenerator(gen.name, payload)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'generators') })
      setOpen(false)
      toast.success('Generator saved')
      const newName = form.name?.trim() || gen.name
      if (newName !== gen.name) onRename?.(newName)
    },
    onError: (e: Error) => toast.error(`Save failed: ${e.message}`),
  })

  const startEdit = () => {
    const base = toFS(gen, ['name', 'bus', 'carrier', 'p_nom', 'p_nom_extendable', 'p_nom_min', 'p_nom_max',
      'p_min_pu', 'p_max_pu', 'marginal_cost', 'capital_cost', 'fom_cost', 'overnight_cost',
      'curtailment_cost',
      'efficiency',
      'committable', 'ramp_limit_up', 'ramp_limit_down', 'start_up_cost', 'shut_down_cost',
      'min_up_time', 'min_down_time', 'e_sum_min', 'e_sum_max', 'build_year', 'lifetime'])
    // Pre-fill lifetime from solver-config global when blank/inf — matches
    // user expectation that "all assets default to the global lifetime".
    base.lifetime = defaultLifetime(gen.lifetime, globalLt)
    setForm({
      ...base,
      discount_rate_pct: discountRatePct(gen.discount_rate),
      // Control mode: blank if unset (treat as default 'PQ' on save).
      control: gen.control || 'PQ',
    })
    setOpen(true)
  }

  const isExt = form.p_nom_extendable === 'true'
  const isCommit = form.committable === 'true'

  // Items shared between the card-list view (CardShell + ExpandedProps) and the
  // single-asset detail view (Section + Rows). Keeping them in one place avoids
  // drift between the two layouts.
  const items: ExpandedItem[] = [
    // AC control mode (PQ / PV / Slack). Always shown so Stage 2 users can
    // see at a glance which generator is currently their slack source.
    { label: 'Control',       value: gen.control || 'PQ',                    tip: 'AC PF control type. PQ = fixed P,Q; PV = fixed P with voltage held; Slack = absorbs system imbalance.' },
    { label: 'p_nom',         value: gen.p_nom,             unit: ' MW',     tip: docTip('generator.p_nom') },
    { label: 'Extendable',    value: gen.p_nom_extendable,                   tip: docTip('generator.p_nom_extendable') },
    { label: 'p_nom_min',     value: gen.p_nom_extendable ? gen.p_nom_min : null,    unit: ' MW', tip: docTip('generator.p_nom_min') },
    { label: 'p_nom_max',     value: gen.p_nom_extendable ? gen.p_nom_max ?? null : null, unit: ' MW', tip: docTip('generator.p_nom_max') },
    { label: 'p_min_pu',      value: gen.p_min_pu,                           tip: docTip('generator.p_min_pu') },
    { label: 'p_max_pu',      value: gen.p_max_pu,                           tip: docTip('generator.p_max_pu') },
    { label: 'Efficiency',    value: gen.efficiency,                         tip: docTip('generator.efficiency') },
    { label: 'Marginal cost', value: gen.marginal_cost,     unit: ' €/MWh',  tip: docTip('generator.marginal_cost') },
    { label: 'Capital cost',  value: gen.capital_cost,      unit: ' €/MW',   tip: docTip('generator.capital_cost') },
    { label: 'FOM cost',      value: gen.fom_cost ?? null,  unit: ' €/MW/yr', tip: docTip('generator.fom_cost') },
    { label: 'Overnight cost', value: gen.overnight_cost ?? null, unit: ' €/MW', tip: docTip('generator.overnight_cost') },
    // Per-asset discount rate (decimal in PyPSA, displayed as percent here).
    // Null/NaN means "use solver-config global at solve time", which we
    // render as empty so the row simply disappears from the read view.
    { label: 'Discount rate',
      value: gen.discount_rate != null && Number.isFinite(gen.discount_rate)
        ? Number((gen.discount_rate * 100).toFixed(3)) : null,
      unit: ' %',
      tip: 'Per-asset discount rate used by PyPSA\'s native annuity. Blank means the global rate from Solver Settings is used.' },
    // Curtailment cost row only renders for renewable carriers — visibleItems
    // filters falsy/null/0 so non-renewables won't see the row even though
    // the attribute exists.
    { label: 'Curtailment cost', value: isRenewableCarrier(gen.carrier ?? '') ? gen.curtailment_cost ?? null : null,
      unit: ' €/MWh', tip: docTip('generator.curtailment_cost') },
    { label: 'Build year',    value: gen.build_year,                         tip: docTip('generator.build_year') },
    { label: 'Lifetime',      value: gen.lifetime ?? null,  unit: ' yr',     tip: docTip('generator.lifetime') },
    { label: 'Committable',   value: gen.committable,                        tip: docTip('generator.committable') },
    { label: 'Ramp up',       value: gen.committable ? gen.ramp_limit_up ?? null : null, unit: ' pu/h', tip: docTip('generator.ramp_limit_up') },
    { label: 'Ramp down',     value: gen.committable ? gen.ramp_limit_down ?? null : null, unit: ' pu/h', tip: docTip('generator.ramp_limit_down') },
    { label: 'Start cost',    value: gen.committable ? gen.start_up_cost : null, unit: ' €', tip: docTip('generator.start_up_cost') },
    { label: 'Shut cost',     value: gen.committable ? gen.shut_down_cost : null, unit: ' €', tip: docTip('generator.shut_down_cost') },
    { label: 'Min up',        value: gen.committable ? gen.min_up_time : null,    unit: ' h', tip: docTip('generator.min_up_time') },
    { label: 'Min down',      value: gen.committable ? gen.min_down_time : null,  unit: ' h', tip: docTip('generator.min_down_time') },
  ]
  const visibleItems = items.filter(i => i.value != null && i.value !== '' && i.value !== false)

  if (!open && mode === 'detail') {
    return (
      <>
        <Section title={title ?? 'Generator Properties'}>
          <Row label="Carrier" value={gen.carrier} tip={docTip('generator.carrier')} />
          {visibleItems.map(i => (
            <Row key={i.label} label={i.label} value={i.value} unit={i.unit} tip={i.tip} />
          ))}
        </Section>
        {showChart && hasProfile && (
          <MiniProfileChart component="generators" attribute="p_max_pu" name={gen.name} unit="pu" />
        )}
        <DetailFooter
          editLabel="Edit Generator"
          assetName={gen.name}
          onEdit={startEdit}
          onDelete={() => delMut.mutate()}
          deletePending={delMut.isPending}
          hasProfile={hasProfile}
          showChart={showChart}
          onToggleChart={() => setShowChart(s => !s)}
        />
        <VintageCloneButton
          assetName={gen.name}
          defaultYear={(gen.build_year || new Date().getFullYear()) + 10}
          defaultCost={gen.overnight_cost}
          onClone={async (year, overnightCost) => {
            const payload: Partial<Generator> = {
              ...gen, name: `${gen.name}_${year}`, build_year: year,
            }
            if (overnightCost !== null) payload.overnight_cost = overnightCost
            await networkApi.createGenerator(payload)
            qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'generators') })
            toast.success(`Cloned ${gen.name} → ${payload.name}`)
          }}
        />
      </>
    )
  }

  if (!open) {
    return (
      <CardShell
        title={gen.name} badge={gen.carrier}
        meta={`${gen.p_nom?.toFixed(0) ?? '—'} MW${gen.marginal_cost > 0 ? ` · MC ${gen.marginal_cost} €/MWh` : ''}${gen.p_nom_extendable ? ' · extendable' : ''}`}
        onEdit={startEdit}
        onDelete={() => delMut.mutate()}
        delPending={delMut.isPending}
        onToggleChart={hasProfile ? () => setShowChart(s => !s) : undefined}
        chartActive={showChart}
        extraBadge={
          hasProfile
            ? <span className="text-[9px] font-medium px-1.5 py-0.5 rounded bg-success/15 text-success border border-success/30">✓ profile</span>
            : <span className="text-[9px] px-1.5 py-0.5 rounded bg-border/40 text-muted">static</span>
        }
      >
        <ExpandedProps items={items} />
        {showChart && hasProfile && (
          <MiniProfileChart component="generators" attribute="p_max_pu" name={gen.name} unit="pu" />
        )}
      </CardShell>
    )
  }

  return (
    <EditShell title={gen.name} onSave={() => saveMut.mutate()} onCancel={() => setOpen(false)} saving={saveMut.isPending}>
      <TxtInput label="Name" k="name" fs={form} set={setForm} tip={docTip('generator.name')} />
      <CarrierGroupSelect k="carrier" fs={form} set={setForm} carriers={carriers as Carrier[]} tip={docTip('generator.carrier')} />
      <SectionHdr title="Topology" />
      <BusSelect k="bus" fs={form} set={setForm} buses={buses as Bus[]} tip={docTip('generator.bus')} />
      {/* AC PF control type. Only consumed by Stage 2 (post-solve AC Power
          Flow); irrelevant for LOPF. PQ is the safe default — generator
          dispatches at its solved P with q_set unchanged. PV holds the bus
          voltage. Slack absorbs system imbalance and must be set on exactly
          one generator (or one bus) per electrical island. */}
      <label className="flex flex-col gap-0.5 mb-1.5">
        <span className="text-[10px] text-muted">
          <InfoTip text="AC PF control type. Consumed by Stage 2 (AC Power Flow) only. PQ = fixed P,Q; PV = fixed P with voltage held; Slack = absorbs system imbalance.">
            Control
          </InfoTip>
        </span>
        <select className="bg-bg border border-border rounded px-2 py-1 text-xs text-text focus:outline-none focus:border-accent"
          value={form.control ?? gen.control ?? 'PQ'}
          onChange={e => setForm(p => ({ ...p, control: e.target.value }))}
        >
          <option value="PQ">PQ — Fixed P,Q (default)</option>
          <option value="PV">PV — Fixed P, voltage held</option>
          <option value="Slack">Slack — Absorbs imbalance</option>
        </select>
      </label>
      <SectionHdr title="Capacity" />
      <NumInput label="p_nom" k="p_nom" fs={form} set={setForm} unit="MW" tip={docTip('generator.p_nom')} />
      <ChkInput label="Extendable" k="p_nom_extendable" fs={form} set={setForm} tip={docTip('generator.p_nom_extendable')} onCheck={autofillNomMin('p_nom', 'p_nom_min')} />
      {/* Min/max bounds are only meaningful for extendable assets — PyPSA
          ignores p_nom_min/p_nom_max when p_nom_extendable=False. Hiding them
          keeps the form focused on what actually affects the optimiser. */}
      {isExt && <NumInput label="p_nom_min" k="p_nom_min" fs={form} set={setForm} unit="MW" tip={docTip('generator.p_nom_min')} />}
      {isExt && <NumInput label="p_nom_max" k="p_nom_max" fs={form} set={setForm} unit="MW" tip={docTip('generator.p_nom_max')} />}
      {isExt && (
        <div className="col-span-2 -mt-0.5">
          <button type="button" onClick={() => setVintageOpen(true)}
            className="text-[11px] text-accent hover:underline">
            Per-period bounds…
          </button>
          <span className="ml-2 text-[10px] text-muted">override Min/Max per investment period</span>
        </div>
      )}
      {vintageOpen && (
        <VintagePeriodBoundsModal componentClass="Generator" name={gen.name} onClose={() => setVintageOpen(false)} />
      )}
      <SectionHdr title="Operation" />
      <NumInput label="p_max_pu" k="p_max_pu" fs={form} set={setForm} tip={docTip('generator.p_max_pu')} />
      <NumInput label="Efficiency" k="efficiency" fs={form} set={setForm} tip={docTip('generator.efficiency')} />
      <SectionHdr title="Costs" />
      <NumInput label="Marginal cost" k="marginal_cost" fs={form} set={setForm} unit="€/MWh" tip={docTip('generator.marginal_cost')} />
      <NumInput label="Capital cost" k="capital_cost" fs={form} set={setForm} unit="€/MW" tip={docTip('generator.capital_cost')} />
      <NumInput label="FOM cost" k="fom_cost" fs={form} set={setForm} unit="€/MW/yr" tip={docTip('generator.fom_cost')} />
      <NumInput label="Overnight cost" k="overnight_cost" fs={form} set={setForm} unit="€/MW" tip={docTip('generator.overnight_cost')} />
      {/* Per-asset discount rate. Leave blank to use the global rate from
          Solver Settings. PyPSA's annuity uses this when overnight_cost is set. */}
      <DiscountRateInput fs={form} set={setForm} tip="Discount rate used to annuitize overnight_cost for this asset. Leave blank to use the global rate from Solver Settings." />
      {/* Curtailment cost surfaces only for renewable carriers — it's a
          custom non-PyPSA attribute that solver_service reads at solve time
          and injects as an extra_functionality LP term. For thermals the
          field doesn't apply (no curtailment concept) so we keep the form
          clean. The value still saves on every save, so toggling carrier to
          renewable later doesn't lose the typed value. */}
      {isRenewableCarrier(form.carrier || gen.carrier || '') && (
        <NumInput label="Curtailment cost" k="curtailment_cost" fs={form} set={setForm} unit="€/MWh" tip={docTip('generator.curtailment_cost')} />
      )}
      <SectionHdr title="Lifecycle" />
      <BuildYearSelect fs={form} set={setForm} tip={docTip('generator.build_year')} />
      <NumInput label="Lifetime" k="lifetime" fs={form} set={setForm} unit="yr" tip={docTip('generator.lifetime')} />
      <SectionHdr title="Unit Commitment" />
      <ChkInput label="Committable" k="committable" fs={form} set={setForm} tip={docTip('generator.committable')} />
      {/* p_min_pu (minimum stable load fraction) is a Unit Commitment concept:
          it forces a thermal plant to dispatch at least p_min_pu × p_nom when
          on. PyPSA only enforces the floor when `committable` is true; outside
          UC it's just a constant fraction that's almost always 0. Surfacing
          it here keeps the form focused. */}
      {isCommit && <>
        <NumInput label="p_min_pu (min load)" k="p_min_pu" fs={form} set={setForm} tip={docTip('generator.p_min_pu')} />
        <NumInput label="Ramp up" k="ramp_limit_up" fs={form} set={setForm} unit="pu/h" tip={docTip('generator.ramp_limit_up')} />
        <NumInput label="Ramp down" k="ramp_limit_down" fs={form} set={setForm} unit="pu/h" tip={docTip('generator.ramp_limit_down')} />
        <NumInput label="Start cost" k="start_up_cost" fs={form} set={setForm} unit="€" tip={docTip('generator.start_up_cost')} />
        <NumInput label="Shut cost" k="shut_down_cost" fs={form} set={setForm} unit="€" tip={docTip('generator.shut_down_cost')} />
        <NumInput label="Min up" k="min_up_time" fs={form} set={setForm} unit="h" tip={docTip('generator.min_up_time')} />
        <NumInput label="Min down" k="min_down_time" fs={form} set={setForm} unit="h" tip={docTip('generator.min_down_time')} />
      </>}
      {/* Annual energy bounds — PyPSA-native. Blank ⇒ unbounded (PyPSA's
          -inf / +inf defaults). Use for hydro reservoir inflow budgets,
          take-or-pay PPAs, fuel-tank caps. Unit: MWh over the full snapshot
          horizon (weighted by snapshot_weightings). */}
      <NumInput label="E sum min" k="e_sum_min" fs={form} set={setForm} unit="MWh"
                tip="Minimum total energy produced over the snapshot horizon. PyPSA: e_sum_min ≤ Σ_t (p_t × weight_t). Blank = no lower bound." />
      <NumInput label="E sum max" k="e_sum_max" fs={form} set={setForm} unit="MWh"
                tip="Maximum total energy produced over the snapshot horizon. PyPSA: Σ_t (p_t × weight_t) ≤ e_sum_max. Blank = no upper bound." />
    </EditShell>
  )
}

// ── Storage unit card ──────────────────────────────────────────────────────────
function StorageUnitCard({ su, onRename, mode = 'card', title }: {
  su: StorageUnit
  onRename?: (newName: string) => void
  mode?: 'card' | 'detail'
  title?: string
}) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [vintageOpen, setVintageOpen] = useState(false)
  const [form, setForm] = useState<FS>({})

  const currentProject = useUIStore(s => s.currentProject)
  const { data: carriers = [] } = useQuery({ queryKey: nk(currentProject, 'carriers'), queryFn: networkApi.getCarriers, staleTime: 60_000 })
  const { data: buses = [] } = useQuery({ queryKey: nk(currentProject, 'buses'), queryFn: networkApi.getBuses, staleTime: 30_000 })
  const globalLt = useGlobalDefaultLifetime()

  const delMut = useMutation({
    mutationFn: () => networkApi.deleteStorageUnit(su.name),
    onSuccess: () => { qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'storage_units') }); showDeleteUndoToast(su.name, qc) },
    onError: (e: Error) => toast.error(e.message),
  })

  const saveMut = useMutation({
    mutationFn: () => {
      const newName = form.name?.trim() || su.name
      const newBus = (form.bus as string | undefined)?.trim() || su.bus
      const cachedSUs = qc.getQueryData<StorageUnit[]>(nk(useUIStore.getState().currentProject, 'storage_units')) ?? []
      const current = cachedSUs.find(s => s.name === su.name) ?? su
      const payload: Partial<StorageUnit> = {
        ...current,
        name: newName, bus: newBus,
        carrier: form.carrier || current.carrier,
        p_nom: nf(form, 'p_nom', current.p_nom),
        p_nom_extendable: form.p_nom_extendable === 'true',
        p_nom_min: nf(form, 'p_nom_min', current.p_nom_min ?? 0),
        max_hours: nf(form, 'max_hours', current.max_hours),
        efficiency_store: nf(form, 'efficiency_store', current.efficiency_store),
        efficiency_dispatch: nf(form, 'efficiency_dispatch', current.efficiency_dispatch),
        standing_loss: nf(form, 'standing_loss', current.standing_loss),
        cyclic_state_of_charge: form.cyclic_state_of_charge === 'true',
        state_of_charge_initial: nf(form, 'state_of_charge_initial', current.state_of_charge_initial),
        // Inflow (MW) — exogenous energy into the SoC equation. 0 = battery.
        // Time-varying inflows go through the /timeseries upload path; this
        // is only the scalar constant override.
        inflow: nf(form, 'inflow', current.inflow ?? 0),
        // Cost fields: blank ⇒ save 0 (deliberate clear), not the old value.
        marginal_cost: nf(form, 'marginal_cost', 0),
        capital_cost: nf(form, 'capital_cost', 0),
        fom_cost: nf(form, 'fom_cost', 0),
        build_year: ni(form, 'build_year', current.build_year ?? 2025),
      }
      // Optional bounds + per-asset overrides — assign unconditionally so
      // blanking the field clears it on the backend (see GeneratorCard
      // for the cleared-to-null rationale). The `...current` spread above
      // re-injects the previous value; an explicit null override is the
      // only way to un-set.
      payload.p_nom_max = no(form, 'p_nom_max')
      payload.overnight_cost = no(form, 'overnight_cost')
      payload.lifetime = no(form, 'lifetime')
      const drPct = no(form, 'discount_rate_pct')
      payload.discount_rate = drPct === null ? null : drPct / 100
      return networkApi.updateStorageUnit(su.name, payload)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'storage_units') })
      setOpen(false)
      toast.success('Storage unit saved')
      const newName = form.name?.trim() || su.name
      if (newName !== su.name) onRename?.(newName)
    },
    onError: (e: Error) => toast.error(`Save failed: ${e.message}`),
  })

  const startEdit = () => {
    const base = toFS(su, ['name', 'bus', 'carrier', 'p_nom', 'p_nom_extendable',
      'p_nom_min', 'p_nom_max', 'max_hours',
      'efficiency_store', 'efficiency_dispatch', 'standing_loss',
      'cyclic_state_of_charge', 'state_of_charge_initial',
      'inflow',
      'marginal_cost', 'capital_cost', 'fom_cost', 'overnight_cost', 'build_year'])
    base.lifetime = defaultLifetime(su.lifetime, globalLt)
    setForm({
      ...base,
      discount_rate_pct: discountRatePct(su.discount_rate),
    })
    setOpen(true)
  }

  const isExt = form.p_nom_extendable === 'true'

  const items: ExpandedItem[] = [
    { label: 'p_nom',          value: su.p_nom,                    unit: ' MW',    tip: docTip('storage_unit.p_nom') },
    { label: 'Max hours',      value: su.max_hours,                unit: ' h',     tip: docTip('storage_unit.max_hours') },
    { label: 'Extendable',     value: su.p_nom_extendable,                         tip: docTip('storage_unit.p_nom_extendable') },
    { label: 'p_nom_min',      value: su.p_nom_extendable ? su.p_nom_min ?? null : null,             unit: ' MW', tip: docTip('storage_unit.p_nom_min') },
    { label: 'p_nom_max',      value: su.p_nom_extendable ? su.p_nom_max ?? null : null,             unit: ' MW', tip: docTip('storage_unit.p_nom_max') },
    { label: 'Store η',        value: su.efficiency_store,                         tip: docTip('storage_unit.efficiency_store') },
    { label: 'Dispatch η',     value: su.efficiency_dispatch,                      tip: docTip('storage_unit.efficiency_dispatch') },
    { label: 'Standing loss',  value: su.standing_loss,            unit: ' /h',    tip: docTip('storage_unit.standing_loss') },
    { label: 'Cyclic SoC',     value: su.cyclic_state_of_charge,                   tip: docTip('storage_unit.cyclic_state_of_charge') },
    { label: 'Initial SoC',    value: su.cyclic_state_of_charge ? null : su.state_of_charge_initial, tip: docTip('storage_unit.state_of_charge_initial') },
    { label: 'Marginal cost',  value: su.marginal_cost,            unit: ' €/MWh', tip: docTip('storage_unit.marginal_cost') },
    { label: 'Capital cost',   value: su.capital_cost,             unit: ' €/MW',  tip: docTip('storage_unit.capital_cost') },
    { label: 'FOM cost',       value: su.fom_cost ?? null,         unit: ' €/MW/yr', tip: docTip('storage_unit.fom_cost') },
    { label: 'Overnight cost', value: su.overnight_cost ?? null,   unit: ' €/MW',    tip: docTip('storage_unit.overnight_cost') },
    { label: 'Discount rate',
      value: su.discount_rate != null && Number.isFinite(su.discount_rate)
        ? Number((su.discount_rate * 100).toFixed(3)) : null,
      unit: ' %',
      tip: 'Per-asset discount rate. Blank means the global rate from Solver Settings is used.' },
    { label: 'Build year',     value: su.build_year,                               tip: docTip('storage_unit.build_year') },
  ]
  const visibleItems = items.filter(i => i.value != null && i.value !== '' && i.value !== false)

  if (!open && mode === 'detail') {
    return (
      <>
        <Section title={title ?? 'Storage Properties'}>
          <Row label="Carrier" value={su.carrier} tip={docTip('storage_unit.carrier')} />
          {visibleItems.map(i => (
            <Row key={i.label} label={i.label} value={i.value} unit={i.unit} tip={i.tip} />
          ))}
        </Section>
        <DetailFooter
          editLabel="Edit Storage"
          assetName={su.name}
          onEdit={startEdit}
          onDelete={() => delMut.mutate()}
          deletePending={delMut.isPending}
        />
        <VintageCloneButton
          assetName={su.name}
          defaultYear={(su.build_year || new Date().getFullYear()) + 10}
          defaultCost={su.overnight_cost}
          onClone={async (year, overnightCost) => {
            const payload: Partial<StorageUnit> = {
              ...su, name: `${su.name}_${year}`, build_year: year,
            }
            if (overnightCost !== null) payload.overnight_cost = overnightCost
            await networkApi.createStorageUnit(payload)
            qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'storage_units') })
            toast.success(`Cloned ${su.name} → ${payload.name}`)
          }}
        />
      </>
    )
  }

  if (!open) {
    return (
      <CardShell
        title={su.name} badge={su.carrier}
        meta={`${su.p_nom?.toFixed(0) ?? '—'} MW · ${su.max_hours}h · η ${((su.efficiency_dispatch ?? 1) * 100).toFixed(0)}%`}
        onEdit={startEdit}
        onDelete={() => delMut.mutate()}
        delPending={delMut.isPending}
      >
        <ExpandedProps items={items} />
      </CardShell>
    )
  }

  return (
    <EditShell title={su.name} onSave={() => saveMut.mutate()} onCancel={() => setOpen(false)} saving={saveMut.isPending}>
      <TxtInput label="Name" k="name" fs={form} set={setForm} tip={docTip('storage_unit.name')} />
      <CarrierGroupSelect k="carrier" fs={form} set={setForm} carriers={carriers as Carrier[]} tip={docTip('storage_unit.carrier')} />
      <SectionHdr title="Topology" />
      <BusSelect k="bus" fs={form} set={setForm} buses={buses as Bus[]} tip={docTip('storage_unit.bus')} />
      <SectionHdr title="Capacity" />
      <NumInput label="p_nom" k="p_nom" fs={form} set={setForm} unit="MW" tip={docTip('storage_unit.p_nom')} />
      <NumInput label="Max hours" k="max_hours" fs={form} set={setForm} unit="h" tip={docTip('storage_unit.max_hours')} />
      <ChkInput label="Extendable" k="p_nom_extendable" fs={form} set={setForm} tip={docTip('storage_unit.p_nom_extendable')} onCheck={autofillNomMin('p_nom', 'p_nom_min')} />
      {isExt && <NumInput label="p_nom_min" k="p_nom_min" fs={form} set={setForm} unit="MW" tip={docTip('storage_unit.p_nom_min')} />}
      {isExt && <NumInput label="p_nom_max" k="p_nom_max" fs={form} set={setForm} unit="MW" tip={docTip('storage_unit.p_nom_max')} />}
      {isExt && (
        <div className="col-span-2 -mt-0.5">
          <button type="button" onClick={() => setVintageOpen(true)}
            className="text-[11px] text-accent hover:underline">
            Per-period bounds…
          </button>
          <span className="ml-2 text-[10px] text-muted">override Min/Max per investment period</span>
        </div>
      )}
      {vintageOpen && (
        <VintagePeriodBoundsModal componentClass="StorageUnit" name={su.name} onClose={() => setVintageOpen(false)} />
      )}
      <SectionHdr title="Efficiency" />
      <NumInput label="Store η" k="efficiency_store" fs={form} set={setForm} tip={docTip('storage_unit.efficiency_store')} />
      <NumInput label="Dispatch η" k="efficiency_dispatch" fs={form} set={setForm} tip={docTip('storage_unit.efficiency_dispatch')} />
      <NumInput label="Standing loss" k="standing_loss" fs={form} set={setForm} unit="/h" tip={docTip('storage_unit.standing_loss')} />
      <SectionHdr title="State of Charge" />
      <ChkInput label="Cyclic SoC" k="cyclic_state_of_charge" fs={form} set={setForm} tip={docTip('storage_unit.cyclic_state_of_charge')} />
      <NumInput label="Initial SoC" k="state_of_charge_initial" fs={form} set={setForm} tip={docTip('storage_unit.state_of_charge_initial')} />
      {/* Inflow — PyPSA's exogenous input to the SoC equation. Required for
          hydro reservoir modelling (without it a reservoir is just a battery
          with no fuel). Constant value here; time-varying inflow profiles
          are uploaded via the TimeSeries tab (component=storage_units,
          attribute=inflow). */}
      <NumInput label="Inflow" k="inflow" fs={form} set={setForm} unit="MW"
                tip="Exogenous energy into the SoC equation (e.g. river-flow rate × turbine head efficiency for hydro). Constant value here; for hourly profiles upload via the TimeSeries tab as storage_units/inflow." />
      <SectionHdr title="Costs" />
      <NumInput label="Marginal cost" k="marginal_cost" fs={form} set={setForm} unit="€/MWh" tip={docTip('storage_unit.marginal_cost')} />
      <NumInput label="Capital cost" k="capital_cost" fs={form} set={setForm} unit="€/MW" tip={docTip('storage_unit.capital_cost')} />
      <NumInput label="FOM cost" k="fom_cost" fs={form} set={setForm} unit="€/MW/yr" tip={docTip('storage_unit.fom_cost')} />
      <NumInput label="Overnight cost" k="overnight_cost" fs={form} set={setForm} unit="€/MW" tip={docTip('storage_unit.overnight_cost')} />
      <DiscountRateInput fs={form} set={setForm} tip="Discount rate used to annuitize overnight_cost for this asset. Leave blank to use the global rate from Solver Settings." />
      <SectionHdr title="Lifecycle" />
      <BuildYearSelect fs={form} set={setForm} tip={docTip('storage_unit.build_year')} />
      <NumInput label="Lifetime" k="lifetime" fs={form} set={setForm} unit="yr" tip="Asset lifetime in years. Defaults to the global default_lifetime from Solver Settings." />
    </EditShell>
  )
}

// ── Store card ─────────────────────────────────────────────────────────────────
function StoreCard({ store, onRename, mode = 'card', title }: {
  store: Store
  onRename?: (newName: string) => void
  mode?: 'card' | 'detail'
  title?: string
}) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [vintageOpen, setVintageOpen] = useState(false)
  const [form, setForm] = useState<FS>({})

  const currentProject = useUIStore(s => s.currentProject)
  const { data: carriers = [] } = useQuery({ queryKey: nk(currentProject, 'carriers'), queryFn: networkApi.getCarriers, staleTime: 60_000 })
  const { data: buses = [] } = useQuery({ queryKey: nk(currentProject, 'buses'), queryFn: networkApi.getBuses, staleTime: 30_000 })
  const globalLt = useGlobalDefaultLifetime()

  const delMut = useMutation({
    mutationFn: () => networkApi.deleteStore(store.name),
    onSuccess: () => { qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'stores') }); showDeleteUndoToast(store.name, qc) },
    onError: (e: Error) => toast.error(e.message),
  })

  const saveMut = useMutation({
    mutationFn: () => {
      const newName = form.name?.trim() || store.name
      const newBus = (form.bus as string | undefined)?.trim() || store.bus
      const cachedStores = qc.getQueryData<Store[]>(nk(useUIStore.getState().currentProject, 'stores')) ?? []
      const current = cachedStores.find(s => s.name === store.name) ?? store
      const payload: Partial<Store> = {
        ...current,
        name: newName, bus: newBus,
        carrier: form.carrier || current.carrier,
        e_nom: nf(form, 'e_nom', current.e_nom),
        e_nom_extendable: form.e_nom_extendable === 'true',
        e_nom_min: nf(form, 'e_nom_min', current.e_nom_min ?? 0),
        e_min_pu: nf(form, 'e_min_pu', current.e_min_pu),
        e_max_pu: nf(form, 'e_max_pu', current.e_max_pu),
        e_initial: nf(form, 'e_initial', current.e_initial),
        e_cyclic: form.e_cyclic === 'true',
        // Cost fields: blank ⇒ save 0 (deliberate clear), not the old value.
        capital_cost: nf(form, 'capital_cost', 0),
        marginal_cost: nf(form, 'marginal_cost', 0),
        fom_cost: nf(form, 'fom_cost', 0),
        standing_loss: nf(form, 'standing_loss', current.standing_loss),
      }
      // Optional bounds + per-asset overrides — assign unconditionally so
      // blanking clears the field on the backend (see GeneratorCard).
      payload.e_nom_max = no(form, 'e_nom_max')
      payload.overnight_cost = no(form, 'overnight_cost')
      const drPct = no(form, 'discount_rate_pct')
      payload.discount_rate = drPct === null ? null : drPct / 100
      // Multi-period gates. build_year=0 ⇒ "exists from the start" (PyPSA
      // default). lifetime=null ⇒ infinite (never retires); user can leave
      // blank to fall back to the solver's default_lifetime at solve time.
      payload.build_year = ni(form, 'build_year', current.build_year ?? 0)
      payload.lifetime = no(form, 'lifetime')
      return networkApi.updateStore(store.name, payload)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'stores') })
      setOpen(false)
      toast.success('Store saved')
      const newName = form.name?.trim() || store.name
      if (newName !== store.name) onRename?.(newName)
    },
    onError: (e: Error) => toast.error(`Save failed: ${e.message}`),
  })

  const startEdit = () => {
    const base = toFS(store, ['name', 'bus', 'carrier', 'e_nom', 'e_nom_extendable', 'e_nom_min', 'e_nom_max',
      'e_min_pu', 'e_max_pu', 'e_initial', 'e_cyclic', 'capital_cost', 'marginal_cost', 'fom_cost',
      'overnight_cost', 'standing_loss', 'build_year'])
    base.lifetime = defaultLifetime(store.lifetime, globalLt)
    setForm({ ...base, discount_rate_pct: discountRatePct(store.discount_rate) })
    setOpen(true)
  }

  const isH2Store = isH2Carrier(store.carrier)
  const isExt = form.e_nom_extendable === 'true'

  const items: ExpandedItem[] = [
    { label: 'e_nom',         value: store.e_nom,                              unit: ' MWh',   tip: docTip('store.e_nom') },
    { label: 'Extendable',    value: store.e_nom_extendable,                                  tip: docTip('store.e_nom_extendable') },
    { label: 'e_nom_min',     value: store.e_nom_extendable ? store.e_nom_min : null,        unit: ' MWh', tip: docTip('store.e_nom_min') },
    { label: 'e_nom_max',     value: store.e_nom_extendable ? store.e_nom_max ?? null : null, unit: ' MWh', tip: docTip('store.e_nom_max') },
    { label: 'e_min_pu',      value: store.e_min_pu,                                         tip: docTip('store.e_min_pu') },
    { label: 'e_max_pu',      value: store.e_max_pu,                                         tip: docTip('store.e_max_pu') },
    { label: 'e_initial',     value: store.e_cyclic ? null : store.e_initial,  unit: ' MWh', tip: docTip('store.e_initial') },
    { label: 'Cyclic SoE',    value: store.e_cyclic,                                         tip: docTip('store.e_cyclic') },
    { label: 'Capital cost',  value: store.capital_cost,                       unit: ' €/MWh', tip: docTip('store.capital_cost') },
    { label: 'Marginal cost', value: store.marginal_cost,                      unit: ' €/MWh', tip: docTip('store.marginal_cost') },
    { label: 'FOM cost',      value: store.fom_cost ?? null,                   unit: ' €/MWh/yr', tip: docTip('store.fom_cost') },
    { label: 'Overnight cost', value: store.overnight_cost ?? null,            unit: ' €/MWh',    tip: docTip('store.overnight_cost') },
    { label: 'Discount rate',
      value: store.discount_rate != null && Number.isFinite(store.discount_rate)
        ? Number((store.discount_rate * 100).toFixed(3)) : null,
      unit: ' %',
      tip: 'Per-asset discount rate. Blank means the global rate from Solver Settings is used.' },
    { label: 'Standing loss', value: store.standing_loss,                      unit: ' /h',   tip: docTip('store.standing_loss') },
    { label: 'Build year',    value: store.build_year,                                       tip: docTip('store.build_year') },
    { label: 'Lifetime',      value: store.lifetime ?? null,                   unit: ' yr',  tip: docTip('store.lifetime') },
  ]
  const visibleItems = items.filter(i => i.value != null && i.value !== '' && i.value !== false)

  if (!open && mode === 'detail') {
    return (
      <>
        <Section title={title ?? 'Store Properties'}>
          <Row label="Carrier" value={store.carrier} tip={docTip('store.carrier')} />
          {visibleItems.map(i => (
            <Row key={i.label} label={i.label} value={i.value} unit={i.unit} tip={i.tip} />
          ))}
        </Section>
        <DetailFooter
          editLabel="Edit Store"
          assetName={store.name}
          onEdit={startEdit}
          onDelete={() => delMut.mutate()}
          deletePending={delMut.isPending}
        />
      </>
    )
  }

  if (!open) {
    return (
      <CardShell
        title={store.name} badge={store.carrier}
        meta={`${store.e_nom?.toFixed(0) ?? '—'} MWh${store.e_nom_extendable ? ' · extendable' : ''}${isH2Store ? ` ≈ ${((store.e_nom ?? 0) / 0.0336).toFixed(0)} kg H₂` : ''}`}
        onEdit={startEdit}
        onDelete={() => delMut.mutate()}
        delPending={delMut.isPending}
      >
        <ExpandedProps items={items} />
      </CardShell>
    )
  }

  return (
    <EditShell title={store.name} onSave={() => saveMut.mutate()} onCancel={() => setOpen(false)} saving={saveMut.isPending}>
      <TxtInput label="Name" k="name" fs={form} set={setForm} tip={docTip('store.name')} />
      <CarrierGroupSelect k="carrier" fs={form} set={setForm} carriers={carriers as Carrier[]} tip={docTip('store.carrier')} />
      <SectionHdr title="Topology" />
      <BusSelect k="bus" fs={form} set={setForm} buses={buses as Bus[]} tip={docTip('store.bus')} />
      <div className="col-span-2 text-[9px] text-muted bg-border/20 rounded px-2 py-1.5 leading-tight">
        {isH2Store
          ? <>A <strong>Store</strong> is a pure energy vessel — power throughput is unlimited and bounded only by connected Links (electrolyzers/fuel cells). Set <code>e_nom</code> for tank capacity. 1 MWh ≈ 29.8 kg H₂ (LHV).</>
          : <>A <strong>Store</strong> has no intrinsic power limit — charge/discharge rate is set by connected components. Only energy capacity (<code>e_nom</code>) constrains storage.</>}
      </div>
      <SectionHdr title="Energy Capacity" />
      <NumInput label="e_nom (nominal)" k="e_nom" fs={form} set={setForm} unit="MWh" tip={docTip('store.e_nom')} />
      <ChkInput label="Extendable" k="e_nom_extendable" fs={form} set={setForm} tip={docTip('store.e_nom_extendable')} onCheck={autofillNomMin('e_nom', 'e_nom_min')} />
      {isExt && <NumInput label="e_nom_min" k="e_nom_min" fs={form} set={setForm} unit="MWh" tip={docTip('store.e_nom_min')} />}
      {isExt && <NumInput label="e_nom_max" k="e_nom_max" fs={form} set={setForm} unit="MWh" tip={docTip('store.e_nom_max')} />}
      {isExt && (
        <div className="col-span-2 -mt-0.5">
          <button type="button" onClick={() => setVintageOpen(true)}
            className="text-[11px] text-accent hover:underline">
            Per-period bounds…
          </button>
          <span className="ml-2 text-[10px] text-muted">override Min/Max per investment period</span>
        </div>
      )}
      {vintageOpen && (
        <VintagePeriodBoundsModal componentClass="Store" name={store.name} onClose={() => setVintageOpen(false)} />
      )}
      <SectionHdr title="State of Energy Limits" />
      <NumInput label="e_min_pu (SoE min)" k="e_min_pu" fs={form} set={setForm} tip={docTip('store.e_min_pu')} />
      <NumInput label="e_max_pu (SoE max)" k="e_max_pu" fs={form} set={setForm} tip={docTip('store.e_max_pu')} />
      <NumInput label="e_initial" k="e_initial" fs={form} set={setForm} tip={docTip('store.e_initial')} />
      <ChkInput label="Cyclic SoE" k="e_cyclic" fs={form} set={setForm} tip={docTip('store.e_cyclic')} />
      <SectionHdr title="Costs" />
      <NumInput label="Capital cost" k="capital_cost" fs={form} set={setForm} unit="€/MWh" tip={docTip('store.capital_cost')} />
      <NumInput label="Marginal cost" k="marginal_cost" fs={form} set={setForm} unit="€/MWh" tip={docTip('store.marginal_cost')} />
      <NumInput label="FOM cost" k="fom_cost" fs={form} set={setForm} unit="€/MWh/yr" tip={docTip('store.fom_cost')} />
      <NumInput label="Overnight cost" k="overnight_cost" fs={form} set={setForm} unit="€/MWh" tip={docTip('store.overnight_cost')} />
      <DiscountRateInput fs={form} set={setForm} tip="Discount rate used to annuitize overnight_cost for this asset. Leave blank to use the global rate from Solver Settings." />
      <NumInput label="Standing loss" k="standing_loss" fs={form} set={setForm} unit="/h" tip={docTip('store.standing_loss')} />
      <SectionHdr title="Lifecycle (multi-period)" />
      <BuildYearSelect fs={form} set={setForm} tip={docTip('store.build_year')} />
      <NumInput label="Lifetime" k="lifetime" fs={form} set={setForm} unit="yr" tip="Asset lifetime in years. Defaults to the global default_lifetime from Solver Settings." />
    </EditShell>
  )
}

// ── Load card ──────────────────────────────────────────────────────────────────
function LoadCard({ load, onRename, mode = 'card', title }: {
  load: Load
  onRename?: (newName: string) => void
  mode?: 'card' | 'detail'
  title?: string
}) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [showChart, setShowChart] = useState(false)
  const [form, setForm] = useState<FS>({})

  const currentProject = useUIStore(s => s.currentProject)
  const { data: profiles = {} } = useQuery({
    queryKey: nk(currentProject, 'load_profiles'),
    queryFn: networkApi.getLoadProfiles,
    staleTime: 30_000,
  })
  const profileMeta: LoadProfileMeta | undefined = profiles[load.name]
  const hasProfile = profileMeta?.has_profile === true

  const { data: carriers = [] } = useQuery({ queryKey: nk(currentProject, 'carriers'), queryFn: networkApi.getCarriers, staleTime: 60_000 })
  // Needed for the bus selector — populated when the edit form is open. Reused
  // by the Generator / Storage / Store cards via the same pattern.
  const { data: buses = [] } = useQuery({ queryKey: nk(currentProject, 'buses'), queryFn: networkApi.getBuses, staleTime: 30_000 })

  const delMut = useMutation({
    mutationFn: () => networkApi.deleteLoad(load.name),
    onSuccess: () => { qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'loads') }); showDeleteUndoToast(load.name, qc) },
    onError: (e: Error) => toast.error(e.message),
  })

  const saveMut = useMutation({
    mutationFn: () => {
      const newName = form.name?.trim() || load.name
      // `form.bus` carries the user's pick from the bus selector. Fall back
      // to the existing load.bus when the form value is missing (e.g. legacy
      // open before this field existed). The selector renders the current
      // bus pre-selected so a user who doesn't touch it keeps the same bus.
      const newBus = (form.bus as string | undefined)?.trim() || load.bus
      // PUT payload MUST spread the full cached load before overriding —
      // backend's `_update_component` does remove + add, so any field NOT
      // in the payload resets to Pydantic schema defaults. Without the
      // spread, fields the form doesn't expose (e.g. `active`, `type`,
      // future schema additions) silently revert to defaults on every
      // Apply. Read from React Query cache to pick up edits made in
      // other tabs / panels while this form was open.
      const cachedLoads = qc.getQueryData<Load[]>(nk(useUIStore.getState().currentProject, 'loads')) ?? []
      const current = cachedLoads.find(l => l.name === load.name) ?? load
      return networkApi.updateLoad(load.name, {
        ...current,
        name: newName, bus: newBus,
        carrier: form.carrier || current.carrier,
        p_set: nf(form, 'p_set', current.p_set),
        q_set: nf(form, 'q_set', current.q_set ?? 0),
        sign: nf(form, 'sign', current.sign ?? -1),
      } as Partial<Load>)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'loads') })
      setOpen(false)
      toast.success('Load saved')
      const newName = form.name?.trim() || load.name
      if (newName !== load.name) onRename?.(newName)
    },
    onError: (e: Error) => toast.error(`Save failed: ${e.message}`),
  })

  const startEdit = () => {
    setForm(toFS(load, ['name', 'bus', 'carrier', 'p_set', 'q_set', 'sign']))
    setOpen(true)
  }

  const items: ExpandedItem[] = [
    { label: 'p_set (static)', value: load.p_set,        unit: ' MW',   tip: docTip('load.p_set') },
    { label: 'q_set',          value: load.q_set,        unit: ' MVAr', tip: docTip('load.q_set') },
    { label: 'Sign',           value: load.sign,                        tip: docTip('load.sign') },
  ]
  const visibleItems = items.filter(i => i.value != null && i.value !== '' && i.value !== false)

  if (!open && mode === 'detail') {
    return (
      <>
        <Section title={title ?? 'Load Properties'}>
          <Row label="Carrier" value={load.carrier} tip={docTip('load.carrier')} />
          {visibleItems.map(i => (
            <Row key={i.label} label={i.label} value={i.value} unit={i.unit} tip={i.tip} />
          ))}
          <Row
            label="Profile"
            value={hasProfile ? `Loaded · ${profileMeta.has_profile ? profileMeta.rows : 0} pts` : 'Static p_set'}
          />
        </Section>
        {showChart && hasProfile && (
          <MiniProfileChart component="loads" attribute="p_set" name={load.name} unit="MW" />
        )}
        <DetailFooter
          editLabel="Edit Load"
          assetName={load.name}
          onEdit={startEdit}
          onDelete={() => delMut.mutate()}
          deletePending={delMut.isPending}
          hasProfile={hasProfile}
          showChart={showChart}
          onToggleChart={() => setShowChart(s => !s)}
        />
      </>
    )
  }

  if (!open) {
    return (
      <CardShell
        title={load.name} badge={load.carrier}
        meta={`P = ${load.p_set?.toFixed(1) ?? '—'} MW${load.q_set ? ` · Q = ${load.q_set.toFixed(1)} MVAr` : ''}`}
        onEdit={startEdit}
        onDelete={() => delMut.mutate()}
        delPending={delMut.isPending}
        onToggleChart={hasProfile ? () => setShowChart(s => !s) : undefined}
        chartActive={showChart}
        extraBadge={
          hasProfile
            ? <span className="text-[9px] font-medium px-1.5 py-0.5 rounded bg-success/15 text-success border border-success/30">✓ profile</span>
            : <span className="text-[9px] font-medium px-1.5 py-0.5 rounded bg-muted/10 text-muted border border-border">static</span>
        }
      >
        <ExpandedProps items={items} />
        {showChart && hasProfile && (
          <MiniProfileChart component="loads" attribute="p_set" name={load.name} unit="MW" />
        )}
      </CardShell>
    )
  }

  return (
    <EditShell title={load.name} onSave={() => saveMut.mutate()} onCancel={() => setOpen(false)} saving={saveMut.isPending}>
      <TxtInput label="Name" k="name" fs={form} set={setForm} tip={docTip('load.name')} />
      <CarrierGroupSelect k="carrier" fs={form} set={setForm} carriers={carriers as Carrier[]} tip={docTip('load.carrier')} />
      <SectionHdr title="Topology" />
      <BusSelect k="bus" fs={form} set={setForm} buses={buses as Bus[]} tip={docTip('load.bus')} />
      <SectionHdr title="Set Points" />
      <NumInput label="p_set (static)" k="p_set" fs={form} set={setForm} unit="MW" tip={docTip('load.p_set')} />
      <NumInput label="q_set" k="q_set" fs={form} set={setForm} unit="MVAr" tip={docTip('load.q_set')} />
      <NumInput label="Sign" k="sign" fs={form} set={setForm} tip={docTip('load.sign')} />
      <SectionHdr title="Time Series" />
      <div className="flex items-center gap-2 py-1">
        <div className={`w-3 h-3 rounded-sm border flex items-center justify-center shrink-0 ${hasProfile ? 'bg-success border-success' : 'bg-bg border-border'}`}>
          {hasProfile && <span className="text-white text-[8px] leading-none">✓</span>}
        </div>
        <span className="text-xs text-muted">
          {hasProfile && profileMeta.has_profile
            ? `Profile loaded · ${profileMeta.rows} pts · peak ${profileMeta.peak?.toFixed(1)} MW`
            : 'No profile — static p_set used'}
        </span>
      </div>
    </EditShell>
  )
}

// ── Asset group panel ──────────────────────────────────────────────────────────
type AssetCategory = 'Thermal' | 'Renewables' | 'Storage' | 'Load' | 'Links'

// isRenewableCarrier imported from utils/carriers (single null-safe source).
const H2_BUS_KEYWORDS = ['h2', 'hydrogen', 'h₂']
function isH2Carrier(carrier: string): boolean {
  const c = carrier.toLowerCase()
  return H2_BUS_KEYWORDS.some(k => c === k || c.startsWith(k + ' ') || c.startsWith(k + '_'))
}

// Pictograms here mirror the left-sidebar palette so the user sees the same
// glyph for an asset whether they're adding it or inspecting it.
const CAT_ICONS: Record<AssetCategory, React.FC<{ size: number; style?: React.CSSProperties }>> = {
  Thermal:    ({ size, style }) => <Flame size={size} style={style} />,
  Renewables: ({ size, style }) => <Wind size={size} style={style} />,
  Storage:    ({ size, style }) => <BatteryCharging size={size} style={style} />,
  Load:       ({ size, style }) => <Zap size={size} style={style} />,
  Links:      ({ size, style }) => <PlugZap size={size} style={style} />,
}
// Override when the category groups assets attached to an H₂ bus — same H₂
// glyph as the left sidebar's Hydrogen Storage / Hydrogen Demand entries.
const H2_CAT_ICONS: Partial<Record<AssetCategory, React.FC<{ size: number; style?: React.CSSProperties }>>> = {
  Storage: ({ size, style }) => <H2Icon size={size} style={style} />,
  Load:    ({ size, style }) => <H2Icon size={size} style={style} />,
}
const CAT_COLORS: Record<AssetCategory, string> = {
  Thermal: '#dc2626', Renewables: '#16a34a', Storage: '#7c3aed', Load: '#d97706', Links: '#0891b2',
}
const H2_CAT_COLORS: Partial<Record<AssetCategory, string>> = {
  Storage: '#7c3aed', Load: '#6d28d9',
}
const CAT_DISPLAY_LABELS: Record<AssetCategory, string> = {
  Thermal: 'Conventional', Renewables: 'Renewables', Storage: 'Storage', Load: 'Load', Links: 'Links',
}
const H2_CAT_DISPLAY_LABELS: Partial<Record<AssetCategory, string>> = {
  Storage: 'H₂ Storage', Load: 'H₂ Demand',
}

// ── Link card ──────────────────────────────────────────────────────────────────
function LinkCard({ link, onRename, mode = 'card', title }: {
  link: Link
  onRename?: (newName: string) => void
  mode?: 'card' | 'detail'
  title?: string
}) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [showChart, setShowChart] = useState(false)
  const [vintageOpen, setVintageOpen] = useState(false)
  const [form, setForm] = useState<FS>({})

  const currentProject = useUIStore(s => s.currentProject)
  const { data: linkProfiles = {} } = useQuery({
    queryKey: nk(currentProject, 'link_profiles'),
    queryFn: networkApi.getLinkProfiles,
    staleTime: 30_000,
  })
  const linkMeta = linkProfiles[link.name]
  const hasProfile = linkMeta?.has_profile === true
  const category = linkMeta?.category ?? 'other'

  const categoryLabel = category === 'electrolyzer' ? 'Electrolyzer' : category === 'fuel_cell' ? 'Fuel Cell' : undefined

  const { data: carriers = [] } = useQuery({ queryKey: nk(currentProject, 'carriers'), queryFn: networkApi.getCarriers, staleTime: 60_000 })
  const { data: buses = [] }    = useQuery({ queryKey: nk(currentProject, 'buses'),    queryFn: networkApi.getBuses,    staleTime: 30_000 })
  const globalLt = useGlobalDefaultLifetime()

  const delMut = useMutation({
    mutationFn: () => networkApi.deleteLink(link.name),
    onSuccess: () => { qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'links') }); showDeleteUndoToast(link.name, qc) },
    onError: (e: Error) => toast.error(e.message),
  })

  const saveMut = useMutation({
    mutationFn: () => {
      const newName = form.name?.trim() || link.name
      // Spread the cached link before overriding — backend's
      // `_update_component` does remove + add, so any field NOT in the
      // payload resets to Pydantic schema defaults. The form only
      // surfaces a curated subset (efficiency, p_nom, costs, …);
      // without the spread, Link-specific fields like `efficiency2`,
      // `efficiency3`, `bus2`, `bus3`, `bus4`, `committable`,
      // `terrain_factor`, `marginal_cost_storage`, `ramp_limit_*` all
      // silently reset every time the user clicks Apply.
      const cachedLinks = qc.getQueryData<Link[]>(nk(useUIStore.getState().currentProject, 'links')) ?? []
      const current = cachedLinks.find(l => l.name === link.name) ?? link
      const payload: Partial<Link> = {
        ...current,
        name: newName,
        bus0: form.bus0 || current.bus0,
        bus1: form.bus1 || current.bus1,
        carrier: form.carrier || current.carrier,
        p_nom: nf(form, 'p_nom', current.p_nom),
        p_nom_extendable: form.p_nom_extendable === 'true',
        p_nom_min: nf(form, 'p_nom_min', current.p_nom_min ?? 0),
        p_min_pu: nf(form, 'p_min_pu', current.p_min_pu ?? 0),
        p_max_pu: nf(form, 'p_max_pu', current.p_max_pu ?? 1),
        efficiency: nf(form, 'efficiency', current.efficiency),
        // Cost fields: blank ⇒ save 0 (deliberate clear), not the old value.
        marginal_cost: nf(form, 'marginal_cost', 0),
        capital_cost: nf(form, 'capital_cost', 0),
        fom_cost: nf(form, 'fom_cost', 0),
        build_year: ni(form, 'build_year', current.build_year ?? 2025),
      }
      // Optional bounds + per-asset overrides — assign unconditionally so
      // blanking clears the field on the backend (see GeneratorCard).
      payload.p_nom_max = no(form, 'p_nom_max')
      payload.lifetime = no(form, 'lifetime')
      payload.overnight_cost = no(form, 'overnight_cost')
      const drPct = no(form, 'discount_rate_pct')
      payload.discount_rate = drPct === null ? null : drPct / 100
      return networkApi.updateLink(link.name, payload)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'links') })
      setOpen(false)
      toast.success('Link saved')
      const newName = form.name?.trim() || link.name
      if (newName !== link.name) onRename?.(newName)
    },
    onError: (e: Error) => toast.error(`Save failed: ${e.message}`),
  })

  const startEdit = () => {
    const base = toFS(link, ['name', 'carrier', 'bus0', 'bus1', 'p_nom', 'p_nom_extendable', 'p_nom_min', 'p_nom_max',
      'p_min_pu', 'p_max_pu', 'efficiency', 'marginal_cost', 'capital_cost', 'fom_cost',
      'overnight_cost', 'build_year', 'lifetime'])
    base.lifetime = defaultLifetime(link.lifetime, globalLt)
    setForm({
      ...base,
      discount_rate_pct: discountRatePct(link.discount_rate),
    })
    setOpen(true)
  }

  const isExt = form.p_nom_extendable === 'true'

  const bus0Obj = (buses as Bus[]).find(b => b.name === link.bus0)
  const bus1Obj = (buses as Bus[]).find(b => b.name === link.bus1)
  const flowLabel = category === 'electrolyzer'
    ? `${bus0Obj?.carrier ?? 'AC'} → ${bus1Obj?.carrier ?? 'H₂'}`
    : category === 'fuel_cell'
    ? `${bus0Obj?.carrier ?? 'H₂'} → ${bus1Obj?.carrier ?? 'AC'}`
    : `${link.bus0} → ${link.bus1}`

  const items: ExpandedItem[] = [
    { label: 'From bus (bus₀)', value: link.bus0,                                 tip: docTip('link.bus0') },
    { label: 'To bus (bus₁)',   value: link.bus1,                                 tip: docTip('link.bus1') },
    { label: 'p_nom',           value: link.p_nom,             unit: ' MW',       tip: docTip('link.p_nom') },
    { label: 'Extendable',      value: link.p_nom_extendable,                     tip: docTip('link.p_nom_extendable') },
    { label: 'p_nom_min',       value: link.p_nom_extendable ? link.p_nom_min : null, unit: ' MW', tip: docTip('link.p_nom_min') },
    { label: 'p_nom_max',       value: link.p_nom_extendable ? link.p_nom_max ?? null : null, unit: ' MW', tip: docTip('link.p_nom_max') },
    { label: 'Efficiency',      value: link.efficiency,                            tip: docTip('link.efficiency') },
    { label: 'p_min_pu',        value: link.p_min_pu,                              tip: docTip('link.p_min_pu') },
    { label: 'p_max_pu',        value: link.p_max_pu,                              tip: docTip('link.p_max_pu') },
    { label: 'Marginal cost',   value: link.marginal_cost,     unit: ' €/MWh',     tip: docTip('link.marginal_cost') },
    { label: 'Capital cost',    value: link.capital_cost,      unit: ' €/MW',      tip: docTip('link.capital_cost') },
    { label: 'FOM cost',        value: link.fom_cost ?? null,  unit: ' €/MW/yr',   tip: docTip('link.fom_cost') },
    { label: 'Overnight cost',  value: link.overnight_cost ?? null, unit: ' €/MW',  tip: docTip('link.overnight_cost') },
    { label: 'Discount rate',
      value: link.discount_rate != null && Number.isFinite(link.discount_rate)
        ? Number((link.discount_rate * 100).toFixed(3)) : null,
      unit: ' %',
      tip: 'Per-asset discount rate. Blank means the global rate from Solver Settings is used.' },
    { label: 'Build year',      value: link.build_year,                            tip: docTip('link.build_year') },
    { label: 'Lifetime',        value: link.lifetime ?? null,  unit: ' yr',        tip: docTip('link.lifetime') },
  ]
  const visibleItems = items.filter(i => i.value != null && i.value !== '' && i.value !== false)

  if (!open && mode === 'detail') {
    return (
      <>
        <Section title={title ?? 'Link Properties'}>
          <Row label="Carrier" value={link.carrier} tip={docTip('link.carrier')} />
          {categoryLabel && <Row label="Category" value={categoryLabel} />}
          {visibleItems.map(i => (
            <Row key={i.label} label={i.label} value={i.value} unit={i.unit} tip={i.tip} />
          ))}
        </Section>
        {showChart && hasProfile && (
          <MiniProfileChart component="links" attribute="p_max_pu" name={link.name} unit="pu" />
        )}
        <DetailFooter
          editLabel="Edit Link"
          assetName={link.name}
          onEdit={startEdit}
          // Link delete is exposed in the panel header (canDelete === true)
          // so we omit a duplicate Delete button here.
          hasProfile={hasProfile}
          showChart={showChart}
          onToggleChart={() => setShowChart(s => !s)}
        />
        <VintageCloneButton
          assetName={link.name}
          defaultYear={(link.build_year || new Date().getFullYear()) + 10}
          defaultCost={link.overnight_cost}
          onClone={async (year, overnightCost) => {
            const payload: Partial<Link> = {
              ...link, name: `${link.name}_${year}`, build_year: year,
            }
            if (overnightCost !== null) payload.overnight_cost = overnightCost
            await networkApi.createLink(payload)
            qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'links') })
            toast.success(`Cloned ${link.name} → ${payload.name}`)
          }}
        />
      </>
    )
  }

  if (!open) {
    return (
      <CardShell
        title={link.name} badge={link.carrier}
        meta={`${link.p_nom?.toFixed(0) ?? '—'} MW · η ${((link.efficiency ?? 1) * 100).toFixed(0)}% · ${flowLabel}`}
        onEdit={startEdit}
        onDelete={() => delMut.mutate()}
        delPending={delMut.isPending}
        onToggleChart={hasProfile ? () => setShowChart(s => !s) : undefined}
        chartActive={showChart}
        extraBadge={
          <span className="flex items-center gap-1">
            {categoryLabel && (
              <span className={`text-[9px] px-1.5 py-0.5 rounded font-medium ${
                category === 'electrolyzer' ? 'bg-violet-100 text-violet-700' :
                category === 'fuel_cell'    ? 'bg-blue-100 text-blue-700' :
                'bg-border/40 text-muted'
              }`}>{categoryLabel}</span>
            )}
            {hasProfile
              ? <span className="text-[9px] font-medium px-1.5 py-0.5 rounded bg-success/15 text-success border border-success/30">✓ profile</span>
              : <span className="text-[9px] px-1.5 py-0.5 rounded bg-border/40 text-muted">static</span>
            }
          </span>
        }
      >
        <ExpandedProps items={items} />
        {showChart && hasProfile && (
          <MiniProfileChart component="links" attribute="p_max_pu" name={link.name} unit="pu" />
        )}
      </CardShell>
    )
  }

  return (
    <EditShell title={link.name} onSave={() => saveMut.mutate()} onCancel={() => setOpen(false)} saving={saveMut.isPending}>
      <TxtInput label="Name" k="name" fs={form} set={setForm} tip={docTip('link.name')} />
      <CarrierGroupSelect k="carrier" fs={form} set={setForm} carriers={carriers as Carrier[]} tip={docTip('link.carrier')} />
      {(category === 'electrolyzer' || category === 'fuel_cell') && (
        <div className="col-span-2 text-[9px] text-muted bg-border/20 rounded px-2 py-1.5 leading-tight">
          {category === 'electrolyzer'
            ? <>An <strong>electrolyzer</strong> consumes electricity (bus₀) and produces H₂ (bus₁). Efficiency = H₂ MW out / electricity MW in. To model a fuel cell (H₂→AC), create a separate Link with bus₀ as the H₂ bus and bus₁ as the AC bus.</>
            : <>A <strong>fuel cell</strong> consumes H₂ (bus₀) and produces electricity (bus₁). Efficiency = AC MW out / H₂ MW in. Electrolyzer and fuel cell must always be modelled as separate Links.</>}
        </div>
      )}
      <SectionHdr title="Topology" />
      <label className="col-span-2 flex flex-col gap-0.5">
        <span className="text-[9px] text-muted">
          <InfoTip text={docTip('link.bus0')}>From bus (bus₀) — input / consumption</InfoTip>
        </span>
        <select value={form['bus0'] ?? ''} onChange={e => setForm(p => ({ ...p, bus0: e.target.value }))}
          className="bg-bg border border-border rounded px-1.5 py-0.5 text-[10px] focus:outline-none focus:border-accent w-full">
          {(buses as Bus[]).map(b => (
            <option key={b.name} value={b.name}>{b.name}{b.carrier ? ` (${b.carrier})` : ''}</option>
          ))}
        </select>
      </label>
      <label className="col-span-2 flex flex-col gap-0.5">
        <span className="text-[9px] text-muted">
          <InfoTip text={docTip('link.bus1')}>To bus (bus₁) — output / production</InfoTip>
        </span>
        <select value={form['bus1'] ?? ''} onChange={e => setForm(p => ({ ...p, bus1: e.target.value }))}
          className="bg-bg border border-border rounded px-1.5 py-0.5 text-[10px] focus:outline-none focus:border-accent w-full">
          {(buses as Bus[]).map(b => (
            <option key={b.name} value={b.name}>{b.name}{b.carrier ? ` (${b.carrier})` : ''}</option>
          ))}
        </select>
      </label>
      <SectionHdr title="Capacity" />
      <NumInput label="p_nom" k="p_nom" fs={form} set={setForm} unit="MW" tip={docTip('link.p_nom')} />
      <ChkInput label="Extendable" k="p_nom_extendable" fs={form} set={setForm} tip={docTip('link.p_nom_extendable')} onCheck={autofillNomMin('p_nom', 'p_nom_min')} />
      {isExt && <NumInput label="p_nom_min" k="p_nom_min" fs={form} set={setForm} unit="MW" tip={docTip('link.p_nom_min')} />}
      {isExt && <NumInput label="p_nom_max" k="p_nom_max" fs={form} set={setForm} unit="MW" tip={docTip('link.p_nom_max')} />}
      {isExt && (
        <div className="col-span-2 -mt-0.5">
          <button type="button" onClick={() => setVintageOpen(true)}
            className="text-[11px] text-accent hover:underline">
            Per-period bounds…
          </button>
          <span className="ml-2 text-[10px] text-muted">override Min/Max per investment period</span>
        </div>
      )}
      {vintageOpen && (
        <VintagePeriodBoundsModal componentClass="Link" name={link.name} onClose={() => setVintageOpen(false)} />
      )}
      <SectionHdr title="Operation" />
      <NumInput label="Efficiency" k="efficiency" fs={form} set={setForm} tip={docTip('link.efficiency')} />
      <NumInput label="p_min_pu" k="p_min_pu" fs={form} set={setForm} tip={docTip('link.p_min_pu')} />
      <NumInput label="p_max_pu (static)" k="p_max_pu" fs={form} set={setForm} tip={docTip('link.p_max_pu')} />
      <SectionHdr title="Costs" />
      <NumInput label="Marginal cost" k="marginal_cost" fs={form} set={setForm} unit="€/MWh" tip={docTip('link.marginal_cost')} />
      <NumInput label="Capital cost" k="capital_cost" fs={form} set={setForm} unit="€/MW" tip={docTip('link.capital_cost')} />
      <NumInput label="FOM cost" k="fom_cost" fs={form} set={setForm} unit="€/MW/yr" tip={docTip('link.fom_cost')} />
      <NumInput label="Overnight cost" k="overnight_cost" fs={form} set={setForm} unit="€/MW" tip={docTip('link.overnight_cost')} />
      <DiscountRateInput fs={form} set={setForm} tip="Discount rate used to annuitize overnight_cost for this asset. Leave blank to use the global rate from Solver Settings." />
      <SectionHdr title="Lifecycle" />
      <BuildYearSelect fs={form} set={setForm} tip={docTip('link.build_year')} />
      <NumInput label="Lifetime" k="lifetime" fs={form} set={setForm} unit="yr" tip={docTip('link.lifetime')} />
    </EditShell>
  )
}

function AssetGroupPanel({ name }: { name: string }) {
  const parts = name.split('::')
  const busName = parts[0]
  const category = parts[1] as AssetCategory
  const currentProject = useUIStore(s => s.currentProject)

  const { data: buses = [] }      = useQuery({ queryKey: nk(currentProject, 'buses'),         queryFn: networkApi.getBuses })
  const { data: generators = [] } = useQuery({ queryKey: nk(currentProject, 'generators'),    queryFn: networkApi.getGenerators })
  const { data: loads = [] }      = useQuery({ queryKey: nk(currentProject, 'loads'),         queryFn: networkApi.getLoads })
  const { data: sus = [] }        = useQuery({ queryKey: nk(currentProject, 'storage_units'), queryFn: networkApi.getStorageUnits })
  const { data: stores = [] }     = useQuery({ queryKey: nk(currentProject, 'stores'),        queryFn: networkApi.getStores })
  const { data: links = [] }      = useQuery({ queryKey: nk(currentProject, 'links'),         queryFn: networkApi.getLinks })

  const bus       = (buses as Bus[]).find(b => b.name === busName)
  const busIsH2   = isH2Carrier(bus?.carrier ?? '')

  const busGens   = (generators as Generator[]).filter(g => g.bus === busName)
  const busLoads  = (loads as Load[]).filter(l => l.bus === busName)
  const busSus    = (sus as StorageUnit[]).filter(s => s.bus === busName)
  const busStores = (stores as Store[]).filter(s => s.bus === busName)
  const busLinks  = (links as Link[]).filter(l => l.bus0 === busName)

  const thermalGens   = busGens.filter(g => !isRenewableCarrier(g.carrier))
  const renewableGens = busGens.filter(g => isRenewableCarrier(g.carrier))

  const useH2Icons = busIsH2 && (category === 'Storage' || category === 'Load')
  const CatIcon  = (useH2Icons ? H2_CAT_ICONS[category] : undefined) ?? CAT_ICONS[category]
  const catColor = (useH2Icons ? H2_CAT_COLORS[category] : undefined) ?? CAT_COLORS[category]
  const catLabel = (useH2Icons ? H2_CAT_DISPLAY_LABELS[category] : undefined) ?? CAT_DISPLAY_LABELS[category] ?? category

  const renderGenerators = (gens: Generator[]) => {
    if (gens.length === 0) return (
      <p className="text-xs text-muted py-2 text-center">No {catLabel.toLowerCase()} generators on this bus</p>
    )
    // mode='detail' so each asset renders the same Section + Row layout used
    // by single-asset panels. The asset name is the section title so multiple
    // stacked cards remain identifiable.
    return gens.map(g => <GeneratorCard key={g.name} gen={g} mode="detail" title={g.name} />)
  }

  return (
    <div className="p-3">
      {/* Category header */}
      <div className="flex items-center gap-2 mb-3 pb-2 border-b border-border">
        <div style={{
          width: 28, height: 28, borderRadius: 6,
          background: catColor + '15', border: `1.5px solid ${catColor}`,
          display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
        }}>
          <CatIcon size={14} style={{ color: catColor }} />
        </div>
        <div>
          <p className="text-xs font-bold text-text">{catLabel}</p>
          <p className="text-[10px] text-muted">{busName}{bus?.carrier ? ` · ${bus.carrier}` : ''}</p>
        </div>
      </div>

      {(category === 'Thermal') && renderGenerators(thermalGens)}
      {(category === 'Renewables') && renderGenerators(renewableGens)}

      {category === 'Storage' && (
        <>
          {busSus.length === 0 && busStores.length === 0 && (
            <p className="text-xs text-muted py-2 text-center">No storage on this bus</p>
          )}
          {busSus.length > 0 && (
            <>
              <p className="text-[9px] font-bold text-muted uppercase tracking-wider mb-1">Storage Units</p>
              {busSus.map(s => <StorageUnitCard key={s.name} su={s} mode="detail" title={s.name} />)}
            </>
          )}
          {busStores.length > 0 && (
            <>
              <p className="text-[9px] font-bold text-muted uppercase tracking-wider mb-1 mt-2">Stores</p>
              {busStores.map(s => <StoreCard key={s.name} store={s} mode="detail" title={s.name} />)}
            </>
          )}
        </>
      )}

      {category === 'Load' && (
        <>
          {busLoads.length === 0
            ? <p className="text-xs text-muted py-2 text-center">No loads on this bus</p>
            : busLoads.map(l => <LoadCard key={l.name} load={l} mode="detail" title={l.name} />)
          }
        </>
      )}

      {category === 'Links' && (
        <>
          {busLinks.length === 0
            ? <p className="text-xs text-muted py-2 text-center">No links from this bus</p>
            : busLinks.map(l => <LinkCard key={l.name} link={l} mode="detail" title={l.name} />)
          }
        </>
      )}
    </div>
  )
}

// ── Add component mini-form ────────────────────────────────────────────────────
type AddType = 'Generator' | 'Load' | 'StorageUnit' | 'Store' | 'Line' | null

function AddForm({ bus, type, onClose }: { bus: string; type: AddType; onClose: () => void }) {
  const qc = useQueryClient()
  const currentProject = useUIStore(s => s.currentProject)
  const [fields, setFields] = useState<Record<string, string>>({})
  const { data: buses = [] }    = useQuery({ queryKey: nk(currentProject, 'buses'),    queryFn: networkApi.getBuses })
  const { data: carriers = [] } = useQuery({ queryKey: nk(currentProject, 'carriers'), queryFn: networkApi.getCarriers })
  const f = (k: string) => fields[k] ?? ''
  const set = (k: string, v: string) => setFields(p => ({ ...p, [k]: v }))

  // Default carrier to the bus's own carrier so H₂ buses get H₂ by default
  const busObj = (buses as Bus[]).find(b => b.name === bus)
  const busCarrier = busObj?.carrier || 'AC'
  const carrierValue = fields['carrier'] !== undefined ? fields['carrier'] : busCarrier
  const carrierOpts = (carriers as Carrier[]).map(c => c.name)
  const allCarrierOpts = carrierValue && !carrierOpts.includes(carrierValue)
    ? [carrierValue, ...carrierOpts] : carrierOpts

  const mut = useMutation({
    mutationFn: async () => {
      const name = f('name')
      if (!name) throw new Error('Name required')
      const proj = useUIStore.getState().currentProject
      if (type === 'Generator') {
        await networkApi.createGenerator({ name, bus, carrier: carrierValue, p_nom: parseFloat(f('p_nom')) || 0, marginal_cost: parseFloat(f('mc')) || 0, capital_cost: parseFloat(f('cc')) || 0 } as Partial<Generator>)
        return qc.invalidateQueries({ queryKey: nk(proj, 'generators') })
      }
      if (type === 'Load') {
        await networkApi.createLoad({ name, bus, carrier: carrierValue, p_set: parseFloat(f('p_set')) || 0 } as Partial<Load>)
        return qc.invalidateQueries({ queryKey: nk(proj, 'loads') })
      }
      if (type === 'StorageUnit') {
        await networkApi.createStorageUnit({ name, bus, carrier: carrierValue, p_nom: parseFloat(f('p_nom')) || 0, max_hours: parseFloat(f('max_hours')) || 6 } as Partial<StorageUnit>)
        return qc.invalidateQueries({ queryKey: nk(proj, 'storage_units') })
      }
      if (type === 'Store') {
        await networkApi.createStore({ name, bus, carrier: carrierValue, e_nom: parseFloat(f('e_nom')) || 0 } as Partial<Store>)
        return qc.invalidateQueries({ queryKey: nk(proj, 'stores') })
      }
      if (type === 'Line') {
        const bus1 = f('bus1')
        if (!bus1) throw new Error('Target bus required')
        await networkApi.createLine({ name, bus0: bus, bus1, s_nom: parseFloat(f('s_nom')) || 100, length: parseFloat(f('length')) || 1, r: parseFloat(f('r')) || 0.01, x: parseFloat(f('x')) || 0.1 } as Partial<Line>)
        return qc.invalidateQueries({ queryKey: nk(proj, 'lines') })
      }
    },
    onSuccess: () => { toast.success(`${type} added`); onClose() },
    onError: (e: Error) => toast.error(e.message),
  })

  const inp = (label: string, key: string, placeholder?: string, type_?: string) => (
    <label className="flex flex-col gap-0.5 mb-1.5">
      <span className="text-[10px] text-muted">{label}</span>
      <input
        className="bg-bg border border-border rounded px-2 py-1 text-xs text-text focus:outline-none focus:border-accent"
        placeholder={placeholder} type={type_ ?? 'text'}
        value={f(key)} onChange={e => set(key, e.target.value)}
      />
    </label>
  )

  return (
    <div className="bg-bg border border-accent/30 rounded-lg p-3 mb-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold text-accent">Add {type}</span>
        <button onClick={onClose} aria-label="Close add form" className="text-muted hover:text-text"><X size={12} /></button>
      </div>
      {inp('Name *', 'name')}
      <label className="flex flex-col gap-0.5 mb-1.5">
        <span className="text-[10px] text-muted">Carrier</span>
        <select
          className="bg-bg border border-border rounded px-2 py-1 text-xs text-text focus:outline-none focus:border-accent"
          value={carrierValue}
          onChange={e => set('carrier', e.target.value)}
        >
          {allCarrierOpts.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
      </label>
      {(type === 'Generator' || type === 'StorageUnit') && inp('p_nom (MW)', 'p_nom', '0', 'number')}
      {type === 'Generator' && inp('Marginal cost (€/MWh)', 'mc', '0', 'number')}
      {type === 'Generator' && inp('Capital cost (€/MW)', 'cc', '0', 'number')}
      {type === 'Load' && inp('p_set (MW)', 'p_set', '0', 'number')}
      {type === 'StorageUnit' && inp('Max hours (h)', 'max_hours', '6', 'number')}
      {type === 'Store' && inp('e_nom (MWh)', 'e_nom', '0', 'number')}
      {type === 'Line' && (
        <>
          <label className="flex flex-col gap-0.5 mb-1.5">
            <span className="text-[10px] text-muted">Target bus *</span>
            <select className="bg-bg border border-border rounded px-2 py-1 text-xs text-text focus:outline-none focus:border-accent" value={f('bus1')} onChange={e => set('bus1', e.target.value)}>
              <option value="">Select…</option>
              {(buses as Bus[]).filter((b: Bus) => b.name !== bus).map((b: Bus) => <option key={b.name} value={b.name}>{b.name}</option>)}
            </select>
          </label>
          {inp('s_nom (MW)', 's_nom', '100', 'number')}
          {inp('Length (km)', 'length', '1', 'number')}
          {inp('R (pu)', 'r', '0.01', 'number')}
          {inp('X (pu)', 'x', '0.1', 'number')}
        </>
      )}
      <button onClick={() => mut.mutate()} disabled={mut.isPending}
        className="w-full mt-1 py-1.5 bg-accent text-white rounded text-xs font-semibold hover:bg-accent/90 transition-colors disabled:opacity-50">
        {mut.isPending ? 'Adding…' : 'Add'}
      </button>
    </div>
  )
}

// ── Bus panel ──────────────────────────────────────────────────────────────────
function BusPanel({ name }: { name: string }) {
  const [addType, setAddType] = useState<AddType>(null)
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState<FS>({})
  const { setSelectedComponent } = useUIStore()
  const currentProject = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const { data: buses = [] }    = useQuery({ queryKey: nk(currentProject, 'buses'),         queryFn: networkApi.getBuses })
  const { data: carriers = [] } = useQuery({ queryKey: nk(currentProject, 'carriers'),      queryFn: networkApi.getCarriers })
  const { data: gens = [] }     = useQuery({ queryKey: nk(currentProject, 'generators'),    queryFn: networkApi.getGenerators })
  const { data: loads = [] }    = useQuery({ queryKey: nk(currentProject, 'loads'),         queryFn: networkApi.getLoads })
  const { data: sus = [] }      = useQuery({ queryKey: nk(currentProject, 'storage_units'), queryFn: networkApi.getStorageUnits })
  const { data: stores = [] }   = useQuery({ queryKey: nk(currentProject, 'stores'),        queryFn: networkApi.getStores })
  const { data: links = [] }    = useQuery({ queryKey: nk(currentProject, 'links'),         queryFn: networkApi.getLinks })

  const bus       = (buses as Bus[]).find((b: Bus) => b.name === name)
  const busGens   = (gens as Generator[]).filter((g: Generator) => g.bus === name)
  const busLoads  = (loads as Load[]).filter((l: Load) => l.bus === name)
  const busSus    = (sus as StorageUnit[]).filter((s: StorageUnit) => s.bus === name)
  const busStores = (stores as Store[]).filter((s: Store) => s.bus === name)
  const busLinks  = (links as Link[]).filter((l: Link) => l.bus0 === name)

  const totalGen  = busGens.reduce((s: number, g: Generator) => s + (g.p_nom ?? 0), 0)
  const totalLoad = busLoads.reduce((s: number, l: Load) => s + Math.abs(l.p_set ?? 0), 0)

  const thermalGens   = busGens.filter(g => !isRenewableCarrier(g.carrier))
  const renewableGens = busGens.filter(g => isRenewableCarrier(g.carrier))

  const startEdit = () => {
    if (!bus) return
    setForm(toFS(bus, ['name', 'v_nom', 'carrier', 'control', 'country', 'sub_network', 'x', 'y']))
    setEditing(true)
  }

  const updateMut = useMutation({
    mutationFn: () => {
      if (!bus) throw new Error('Bus not found')
      const newName = form.name?.trim() || bus.name
      const cachedBuses = qc.getQueryData<Bus[]>(nk(useUIStore.getState().currentProject, 'buses')) ?? []
      const current = cachedBuses.find(b => b.name === bus.name) ?? bus
      return networkApi.updateBus(bus.name, {
        ...current,
        name: newName,
        v_nom: nf(form, 'v_nom', current.v_nom),
        carrier: form.carrier || current.carrier,
        control: form.control || current.control,
        country: form.country ?? current.country,
        // sub_network drives the "region" cluster mode: buses sharing the same
        // user-set label collapse to one node. Empty string means "auto-determine"
        // and falls through to PyPSA's `determine_network_topology` in the
        // /api/network/cluster handler.
        sub_network: form.sub_network ?? current.sub_network,
        x: nf(form, 'x', current.x),
        y: nf(form, 'y', current.y),
      })
    },
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'buses') })
      const newName = form.name?.trim() || name
      if (newName !== name) setSelectedComponent({ type: 'Bus', name: newName })
      setEditing(false)
      toast.success('Bus updated')
      // update_bus previews a per-km-preserving impedance rescale for any
      // connected line whose length changed as a result of this edit (a
      // Latitude/Longitude change here recomputes connected line lengths on
      // the backend). Feed it into the shared queue — see
      // utils/rescaleActions.ts — instead of discarding it; this is the exact
      // path Finding 1 (2026-07-31 review) flagged as silently losing the
      // preview.
      ingestRescale(qc, data.rescale)
    },
    onError: (e: Error) => toast.error(`Update failed: ${e.message}`),
  })

  const CATEGORY_GROUPS: { cat: AssetCategory; count: number }[] = (
    [
      { cat: 'Thermal' as AssetCategory,    count: thermalGens.length },
      { cat: 'Renewables' as AssetCategory, count: renewableGens.length },
      { cat: 'Storage' as AssetCategory,    count: busSus.length + busStores.length },
      { cat: 'Load' as AssetCategory,       count: busLoads.length },
      { cat: 'Links' as AssetCategory,      count: busLinks.length },
    ] as { cat: AssetCategory; count: number }[]
  ).filter(g => g.count > 0)

  // Icons here mirror the left-sidebar palette (Load = Electrical-Demand
  // glyph, etc.) so the quick-add buttons feel like compact aliases for the
  // matching palette item.
  const ADD_BUTTONS = [
    { type: 'Line' as AddType, icon: GitBranch, label: 'Line' },
    { type: 'Generator' as AddType, icon: Flame, label: 'Generator' },
    { type: 'Load' as AddType, icon: Zap, label: 'Load' },
    { type: 'StorageUnit' as AddType, icon: Battery, label: 'Storage' },
    { type: 'Store' as AddType, icon: Database, label: 'Store' },
  ]

  return (
    <div className="p-3 space-y-3">
      {bus && !editing && (
        <Section title="Bus Properties">
          <Row label="Voltage"     value={bus.v_nom}    unit=" kV" tip={docTip('bus.v_nom')} />
          <Row label="Carrier"     value={bus.carrier}  tip={docTip('bus.carrier')} />
          <Row label="Control"     value={bus.control}  tip={docTip('bus.control')} />
          <Row label="Country"     value={bus.country}  tip={docTip('bus.country')} />
          <Row label="Sub-network" value={bus.sub_network || '—'} tip="Manual region label for the 'region' clustering mode. Buses sharing this value collapse to one node. Empty = auto-determine from electrical topology." />
          {/* isPlaced (utils/geo, D4-compliant single import) — not a bare
              `bus.x != null` check. PyPSA's Bus.x/y default to 0.0, so an
              unplaced bus always has non-null x/y and the old check rendered
              "0.000, 0.000" as if it were a real position: this is exactly
              the surface the empty-state panel sends the user to ("paste a
              lat, lng pair into its properties"), so showing Null Island
              here undermines that guidance. */}
          <Row label="Coordinates" value={isPlaced(bus) ? `${bus.x?.toFixed(3)}, ${bus.y?.toFixed(3)}` : 'not set'} tip={docTip('bus.coordinates')} />
          <button onClick={startEdit}
            className="w-full mt-2 py-1.5 border border-border rounded text-xs text-muted hover:border-accent hover:text-accent transition-colors flex items-center justify-center gap-1.5">
            <Pencil size={11} /> Edit Bus
          </button>
        </Section>
      )}
      {bus && editing && (
        <EditShell title={bus.name} onSave={() => updateMut.mutate()} onCancel={() => setEditing(false)} saving={updateMut.isPending}>
          <TxtInput label="Name" k="name" fs={form} set={setForm} tip={docTip('bus.name')} />
          <NumInput label="Voltage" k="v_nom" fs={form} set={setForm} unit="kV" tip={docTip('bus.v_nom')} />
          <SelInput label="Control" k="control" fs={form} set={setForm} options={['PQ', 'PV', 'Slack']} tip={docTip('bus.control')} />
          <CarrierGroupSelect k="carrier" fs={form} set={setForm} carriers={carriers as Carrier[]} tip={docTip('bus.carrier')} />
          <TxtInput label="Country" k="country" fs={form} set={setForm} tip={docTip('bus.country')} />
          <TxtInput label="Sub-network" k="sub_network" fs={form} set={setForm} tip="Manual region label for the 'region' clustering mode. Buses sharing this value collapse to one node. Empty = auto-determine from electrical topology." />
          <CoordPairInput fs={form} set={setForm} tip={docTip('bus.coordinates')} />
          <NumInput label="Longitude (x)" k="x" fs={form} set={setForm} tip={docTip('bus.x')} />
          <NumInput label="Latitude (y)" k="y" fs={form} set={setForm} tip={docTip('bus.y')} />
        </EditShell>
      )}

      <Section title="Capacity Summary">
        <Row label="Total generation" value={totalGen}  unit=" MW" />
        <Row label="Total load"       value={totalLoad} unit=" MW" />
        <Row label="Generators"       value={busGens.length} />
        <Row label="Loads"            value={busLoads.length} />
        <Row label="Storage units"    value={busSus.length} />
        <Row label="Stores"           value={busStores.length} />
        <Row label="Links (from)"     value={busLinks.length} />
      </Section>

      {CATEGORY_GROUPS.length > 0 && (
        <Section title="Connected Assets">
          <p className="text-[10px] text-muted mb-1.5">Click a category to view &amp; edit assets</p>
          <div className="grid grid-cols-2 gap-1.5">
            {CATEGORY_GROUPS.map(({ cat, count }) => {
              const CatIcon = CAT_ICONS[cat]
              const color = CAT_COLORS[cat]
              return (
                <button
                  key={cat}
                  onClick={() => setSelectedComponent({ type: 'AssetGroup', name: `${name}::${cat}` })}
                  className="flex items-center gap-2 px-2.5 py-2 rounded-lg border transition-all text-left hover:shadow-sm"
                  style={{ borderColor: color + '60', background: color + '08' }}
                >
                  <div style={{
                    width: 22, height: 22, borderRadius: cat === 'Storage' ? 5 : '50%',
                    background: color + '20', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
                  }}>
                    <CatIcon size={11} style={{ color }} />
                  </div>
                  <div>
                    <p className="text-[10px] font-semibold" style={{ color }}>{CAT_DISPLAY_LABELS[cat] ?? cat}</p>
                    <p className="text-[9px] text-muted">{count} asset{count !== 1 ? 's' : ''}</p>
                  </div>
                </button>
              )
            })}
          </div>
        </Section>
      )}

      <Section title="Add to this Bus">
        <div className="flex flex-wrap gap-1 mb-2">
          {ADD_BUTTONS.map(({ type, icon: Icon, label }) => (
            <button key={type} onClick={() => setAddType(addType === type ? null : type)}
              className={`flex items-center gap-1 px-2 py-1 rounded border text-xs transition-colors ${
                addType === type ? 'border-accent text-accent bg-accent/10' : 'border-border text-muted hover:text-accent hover:border-accent'
              }`}>
              <Icon size={10} /><Plus size={8} />{label}
            </button>
          ))}
        </div>
        {addType && <AddForm bus={name} type={addType} onClose={() => setAddType(null)} />}
      </Section>
    </div>
  )
}

// ── Line panel ─────────────────────────────────────────────────────────────────
function LinePanel({ name }: { name: string }) {
  const qc = useQueryClient()
  const { setSelectedComponent } = useUIStore()
  const currentProject = useUIStore(s => s.currentProject)
  const { data: lines = [] } = useQuery({ queryKey: nk(currentProject, 'lines'), queryFn: networkApi.getLines })
  const line = (lines as Line[]).find(l => l.name === name)
  const [editing, setEditing] = useState(false)
  const [vintageOpen, setVintageOpen] = useState(false)
  const [form, setForm] = useState<Record<string, string>>({})
  // Solver-config global default_lifetime used to pre-fill the lifetime
  // input when the asset's own lifetime is blank/inf — same behaviour as
  // generators / storage / stores / links.
  const globalLt = useGlobalDefaultLifetime()

  const numField = (key: string, fallback: number | null | undefined): number => {
    const v = parseFloat(form[key]); return isNaN(v) ? (fallback ?? 0) : v
  }

  const updateMut = useMutation({
    mutationFn: () => {
      if (!line) throw new Error('Line not found')
      const newName = form.name?.trim() || name
      const isExt = form.s_nom_extendable === 'true'
      // Read latest cached line (race-safe vs stale closure) and spread it
      // so fields the form doesn't surface (`terrain_factor`, `num_parallel`,
      // `v_ang_min/max`, `committable`, …) survive the backend's remove+add
      // cycle. Mirror of the E6 pattern applied to Gen/Storage/Store/Bus.
      const cachedLines = qc.getQueryData<Line[]>(nk(useUIStore.getState().currentProject, 'lines')) ?? []
      const current = cachedLines.find(l => l.name === name) ?? line
      // r/x/b are entered per-km in the UI; PyPSA stores absolute Ohm / Siemens.
      // Multiply by length on save. Per-km value of 0 (or empty) ⇒ absolute 0.
      const lengthOut = numField('length', current.length)
      const payload: Partial<Line> = {
        ...current,
        name: newName, bus0: current.bus0, bus1: current.bus1, carrier: current.carrier || 'AC',
        s_nom: numField('s_nom', current.s_nom), length: lengthOut,
        r: numField('r_per_km', 0) * lengthOut,
        x: numField('x_per_km', 0) * lengthOut,
        b: numField('b_per_km', 0) * lengthOut,
        // Cost fields: blank ⇒ save 0 (deliberate clear), not the old value.
        capital_cost: numField('capital_cost', 0),
        fom_cost: numField('fom_cost', 0),
        // Only send overnight_cost when the user typed something. The
        // helpers above coerce empty strings → 0, but we want None (NaN
        // server-side) to signal "unset" so the discount-rate annuity
        // recompute leaves capital_cost alone. The empty-string check
        // here matches our `no()` helper used elsewhere.
        // Assign overnight_cost unconditionally so blanking clears it on
        // the backend — the `...current` spread above re-injects the
        // previous value, and the old conditional spread skipped the
        // assignment for blanks. null routes through Pydantic's
        // `Optional[float] = None` to NaN on the asset.
        overnight_cost: (() => {
          const raw = (form.overnight_cost ?? '').trim()
          if (!raw) return null
          const v = parseFloat(raw)
          return Number.isFinite(v) ? v : null
        })(),
        s_nom_extendable: isExt,
        s_nom_min: isExt ? numField('s_nom_min', current.s_nom_min ?? 0) : 0,
      }
      // Optional bounds + per-asset overrides — assign unconditionally so
      // blanking clears the field on the backend (see GeneratorCard).
      // s_nom_max only applies when extendable; otherwise PyPSA ignores it.
      if (isExt) {
        const maxRaw = form.s_nom_max?.trim()
        payload.s_nom_max = maxRaw ? parseFloat(maxRaw) : null
      }
      const drRaw = (form.discount_rate_pct ?? '').trim()
      if (drRaw) {
        const v = parseFloat(drRaw)
        payload.discount_rate = Number.isFinite(v) ? v / 100 : null
      } else {
        payload.discount_rate = null
      }
      // Vintage attributes — same pattern as generators / storage / links.
      payload.build_year = ni(form, 'build_year', current.build_year ?? 0)
      const ltRaw = (form.lifetime ?? '').trim()
      if (ltRaw) {
        const v = parseFloat(ltRaw)
        payload.lifetime = Number.isFinite(v) ? v : null
      } else {
        payload.lifetime = null
      }
      return networkApi.updateLine(name, payload)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'lines') })
      setEditing(false)
      toast.success('Line updated')
      const newName = form.name?.trim() || name
      if (newName !== name) setSelectedComponent({ type: 'Line', name: newName })
    },
    onError: (e: Error) => toast.error(`Update failed: ${e.message}`),
  })

  if (!line) return <p className="p-3 text-xs text-muted">Loading…</p>

  const startEdit = () => {
    // Derive per-km values from the stored absolute Ohm / Siemens via the
    // current length. Length=0 ⇒ leave per-km blank (avoid division by zero;
    // the user will set length first, then per-km values).
    const L = Number.isFinite(line.length) && line.length > 0 ? line.length : 0
    const perKm = (v: number | null | undefined) =>
      L > 0 && v != null && Number.isFinite(v) ? String(v / L) : ''
    setForm({
      name: line.name,
      s_nom: String(line.s_nom ?? ''), length: String(line.length ?? ''),
      r_per_km: perKm(line.r), x_per_km: perKm(line.x), b_per_km: perKm(line.b),
      capital_cost: String(line.capital_cost ?? ''),
      fom_cost: String(line.fom_cost ?? 0),
      // Render NaN / null overnight_cost as empty so the input is blank
      // rather than showing "0" (which would confuse "actually zero" with
      // "unset" on every save).
      overnight_cost: line.overnight_cost == null || !Number.isFinite(line.overnight_cost) ? '' : String(line.overnight_cost),
      s_nom_extendable: line.s_nom_extendable ? 'true' : 'false',
      s_nom_min: String(line.s_nom_min ?? 0),
      // Hide PyPSA's float('inf') sentinel — render an empty input so the
      // user can leave "no upper bound" as the default.
      s_nom_max: line.s_nom_max == null || !Number.isFinite(line.s_nom_max) ? '' : String(line.s_nom_max),
      discount_rate_pct: discountRatePct(line.discount_rate),
      // Vintage fields — same defaulting story as generators / storage:
      // build_year falls back to the line's stored value (or 0 if unset),
      // lifetime pre-fills from the solver-config global when the line
      // doesn't already specify one.
      build_year: String(line.build_year ?? 0),
      lifetime: defaultLifetime(line.lifetime, globalLt),
    })
    setEditing(true)
  }

  const numInp = (label: string, key: string, unit?: string, lineTip?: string) => (
    <label key={key} className="flex flex-col gap-0.5 mb-1.5">
      <span className="text-[10px] text-muted">
        <InfoTip text={lineTip}>{label}{unit ? ` (${unit})` : ''}</InfoTip>
      </span>
      <input className="bg-bg border border-border rounded px-2 py-1 text-xs font-mono text-text focus:outline-none focus:border-accent"
        type="number" step="any" value={form[key] ?? ''}
        onChange={e => setForm(p => ({ ...p, [key]: e.target.value }))} />
    </label>
  )

  const txtInp = (label: string, key: string, lineTip?: string) => (
    <label key={key} className="flex flex-col gap-0.5 mb-1.5">
      <span className="text-[10px] text-muted">
        <InfoTip text={lineTip}>{label}</InfoTip>
      </span>
      <input className="bg-bg border border-border rounded px-2 py-1 text-xs text-text focus:outline-none focus:border-accent"
        type="text" value={form[key] ?? ''}
        onChange={e => setForm(p => ({ ...p, [key]: e.target.value }))} />
    </label>
  )

  return (
    <div className="p-3">
      <Section title="Topology">
        <Row label="From bus" value={line.bus0} tip={docTip('line.bus0')} />
        <Row label="To bus"   value={line.bus1} tip={docTip('line.bus1')} />
        <Row label="Carrier"  value={line.carrier} tip={docTip('line.carrier')} />
      </Section>
      {editing ? (
        <Section title="Edit Parameters">
          {txtInp('Name', 'name', docTip('line.name'))}
          {numInp('s_nom', 's_nom', 'MVA', docTip('line.s_nom'))}
          {numInp('Length', 'length', 'km', docTip('line.length'))}
          {/* Per-km input + read-only computed total. PyPSA stores absolute
              Ohm / Siemens, but the natural physical quantity is per-km — so
              the user enters r/x/b per km and we multiply by length on save.
              Total is shown live so the user can sanity-check what PyPSA
              actually receives. Length=0 falls back to "—" for the total. */}
          {(() => {
            const L = numField('length', line.length)
            const fmtTotal = (k: string, unit: string) => {
              const perKm = parseFloat(form[k] ?? '')
              if (!Number.isFinite(perKm) || L <= 0) return `— ${unit}`
              const tot = perKm * L
              return `= ${tot.toLocaleString(undefined, { maximumSignificantDigits: 6 })} ${unit}`
            }
            return (
              <>
                <label className="flex flex-col gap-0.5 mb-1.5">
                  <span className="text-[10px] text-muted">
                    <InfoTip text={docTip('line.r')}>Resistance r (Ω/km)</InfoTip>
                  </span>
                  <input className="bg-bg border border-border rounded px-2 py-1 text-xs font-mono text-text focus:outline-none focus:border-accent"
                    type="number" step="any" value={form.r_per_km ?? ''}
                    onChange={e => setForm(p => ({ ...p, r_per_km: e.target.value }))} />
                  <span className="text-[10px] text-muted font-mono">{fmtTotal('r_per_km', 'Ω')}</span>
                </label>
                <label className="flex flex-col gap-0.5 mb-1.5">
                  <span className="text-[10px] text-muted">
                    <InfoTip text={docTip('line.x')}>Reactance x (Ω/km)</InfoTip>
                  </span>
                  <input className="bg-bg border border-border rounded px-2 py-1 text-xs font-mono text-text focus:outline-none focus:border-accent"
                    type="number" step="any" value={form.x_per_km ?? ''}
                    onChange={e => setForm(p => ({ ...p, x_per_km: e.target.value }))} />
                  <span className="text-[10px] text-muted font-mono">{fmtTotal('x_per_km', 'Ω')}</span>
                </label>
                <label className="flex flex-col gap-0.5 mb-1.5">
                  <span className="text-[10px] text-muted">
                    <InfoTip text={docTip('line.b')}>Susceptance b (S/km)</InfoTip>
                  </span>
                  <input className="bg-bg border border-border rounded px-2 py-1 text-xs font-mono text-text focus:outline-none focus:border-accent"
                    type="number" step="any" value={form.b_per_km ?? ''}
                    onChange={e => setForm(p => ({ ...p, b_per_km: e.target.value }))} />
                  <span className="text-[10px] text-muted font-mono">{fmtTotal('b_per_km', 'S')}</span>
                </label>
              </>
            )
          })()}
          {/* Extendable toggle + conditional min/max — mirrors the
              generator/storage/store/link pattern so all expandable assets
              expose the same controls. capital_cost stays visible regardless,
              since PyPSA still reports it via n.statistics() even when not
              extendable (annualised cost basis). */}
          <label className="flex items-center gap-1.5 cursor-pointer py-0.5 mb-1.5">
            <input type="checkbox" checked={form.s_nom_extendable === 'true'}
              onChange={e => {
                const checked = e.target.checked
                setForm(p => {
                  const next = { ...p, s_nom_extendable: checked ? 'true' : 'false' }
                  return autofillNomMin('s_nom', 's_nom_min')(checked, next)
                })
              }}
              className="accent-accent" />
            <span className="text-xs text-text">
              <InfoTip text={docTip('line.s_nom_extendable')}>Extendable</InfoTip>
            </span>
          </label>
          {form.s_nom_extendable === 'true' && (
            <>
              {numInp('s_nom_min', 's_nom_min', 'MVA', docTip('line.s_nom_min'))}
              {numInp('s_nom_max', 's_nom_max', 'MVA', docTip('line.s_nom_max'))}
              <div className="my-1">
                <button type="button" onClick={() => setVintageOpen(true)}
                  className="text-[11px] text-accent hover:underline">
                  Per-period bounds…
                </button>
                <span className="ml-2 text-[10px] text-muted">override Min/Max per investment period</span>
              </div>
            </>
          )}
          {vintageOpen && line && (
            <VintagePeriodBoundsModal componentClass="Line" name={line.name} onClose={() => setVintageOpen(false)} />
          )}
          {numInp('Capital cost', 'capital_cost', '€/MVA', docTip('line.capital_cost'))}
          {numInp('FOM cost', 'fom_cost', '€/MVA/yr', docTip('line.fom_cost'))}
          {numInp('Overnight cost', 'overnight_cost', '€/MVA', docTip('line.overnight_cost'))}
          <div className="mb-1.5">
            <DiscountRateInput fs={form} set={setForm} tip="Discount rate used to annuitize overnight_cost. Leave blank to use the global rate from Solver Settings." />
          </div>
          {/* Vintage attributes — same controls as Generator / Link / Storage
              so the multi-period validator has the same set of knobs across
              every component class. Build year defaults to the first
              investment period when blank; lifetime falls back to the global
              default_lifetime from Solver Settings. */}
          <BuildYearSelect fs={form} set={setForm} tip={docTip('line.build_year')} />
          <NumInput label="Lifetime" k="lifetime" fs={form} set={setForm} unit="yr"
            tip={docTip('line.lifetime')} />
          <div className="flex gap-2 mt-2">
            <button onClick={() => updateMut.mutate()} disabled={updateMut.isPending}
              className="flex-1 py-1.5 bg-accent text-white rounded text-xs font-semibold hover:bg-accent/90 transition-colors disabled:opacity-50">
              {updateMut.isPending ? 'Saving…' : 'Save'}
            </button>
            <button onClick={() => setEditing(false)}
              className="px-3 py-1.5 border border-border rounded text-xs text-muted hover:text-text transition-colors">
              Cancel
            </button>
          </div>
        </Section>
      ) : (
        <>
          <Section title="Parameters">
            <Row label="Length"      value={line.length}       unit=" km"   tip={docTip('line.length')} />
            <Row label="s_nom"       value={line.s_nom}        unit=" MVA"  tip={docTip('line.s_nom')} />
            {/* Per-km + total pairs. PyPSA stores absolute Ohm / Siemens, but
                the natural physical quantity is per-km — show both so the user
                can sanity-check what's actually in the LP. */}
            {(() => {
              const L = Number.isFinite(line.length) && line.length > 0 ? line.length : 0
              const perKm = (v: number | null | undefined) =>
                L > 0 && v != null && Number.isFinite(v) ? v / L : null
              return (
                <>
                  <Row label="r (per km)"   value={perKm(line.r)} unit=" Ω/km" tip={docTip('line.r')} />
                  <Row label="r (total)"    value={line.r ?? null} unit=" Ω" />
                  <Row label="x (per km)"   value={perKm(line.x)} unit=" Ω/km" tip={docTip('line.x')} />
                  <Row label="x (total)"    value={line.x ?? null} unit=" Ω" />
                  <Row label="b (per km)"   value={perKm(line.b)} unit=" S/km" tip={docTip('line.b')} />
                  <Row label="b (total)"    value={line.b ?? null} unit=" S" />
                </>
              )
            })()}
            <Row label="Extendable"  value={line.s_nom_extendable}          tip={docTip('line.s_nom_extendable')} />
            {line.s_nom_extendable && <Row label="s_nom_min" value={line.s_nom_min} unit=" MVA" tip={docTip('line.s_nom_min')} />}
            {line.s_nom_extendable && <Row label="s_nom_max" value={line.s_nom_max ?? null} unit=" MVA" tip={docTip('line.s_nom_max')} />}
            <Row label="Capital cost" value={line.capital_cost} unit=" €/MVA" tip={docTip('line.capital_cost')} />
            <Row label="FOM cost"     value={line.fom_cost ?? null} unit=" €/MVA/yr" tip={docTip('line.fom_cost')} />
            <Row label="Overnight cost" value={line.overnight_cost ?? null} unit=" €/MVA" tip={docTip('line.overnight_cost')} />
            <Row label="Discount rate"
              value={line.discount_rate != null && Number.isFinite(line.discount_rate)
                ? Number((line.discount_rate * 100).toFixed(3)) : null}
              unit=" %"
              tip="Per-asset discount rate. Blank means the global rate from Solver Settings is used." />
            <Row label="Build year"  value={line.build_year}                         tip={docTip('line.build_year')} />
            <Row label="Lifetime"    value={line.lifetime ?? null}  unit=" yr"       tip={docTip('line.lifetime')} />
          </Section>
          <button onClick={startEdit}
            className="w-full py-1.5 border border-border rounded text-xs text-muted hover:border-accent hover:text-accent transition-colors">
            Edit Parameters
          </button>
        </>
      )}
    </div>
  )
}

// ── Transformer panel ──────────────────────────────────────────────────────────
function TransformerPanel({ name }: { name: string }) {
  const qc = useQueryClient()
  const { setSelectedComponent } = useUIStore()
  const currentProject = useUIStore(s => s.currentProject)
  const { data: transformers = [] } = useQuery({
    queryKey: nk(currentProject, 'transformers'), queryFn: networkApi.getTransformers,
  })
  const tr = (transformers as Transformer[]).find(t => t.name === name)
  const [editing, setEditing] = useState(false)
  const [vintageOpen, setVintageOpen] = useState(false)
  const [form, setForm] = useState<Record<string, string>>({})
  // Solver-config global default_lifetime — used to pre-fill the lifetime
  // input when the transformer's own lifetime is blank/inf.
  const globalLt = useGlobalDefaultLifetime()

  const numField = (key: string, fallback: number | null | undefined): number => {
    const v = parseFloat(form[key]); return isNaN(v) ? (fallback ?? 0) : v
  }

  const updateMut = useMutation({
    mutationFn: () => {
      if (!tr) throw new Error('Transformer not found')
      const newName = form.name?.trim() || name
      const isExt = form.s_nom_extendable === 'true'
      // Spread the cached transformer before overriding — backend's
      // `_update_component` does remove + add, so any field NOT in the
      // payload resets to Pydantic schema defaults. The form only
      // surfaces a curated subset (r, x, s_nom, costs, …); without
      // the spread, Transformer-specific fields like `tap_position`,
      // `tap_side`, `num_parallel`, `model`, `g`, `b`, `active`,
      // `s_max_pu` silently revert to defaults on every Apply.
      const cachedTrs = qc.getQueryData<Transformer[]>(nk(useUIStore.getState().currentProject, 'transformers')) ?? []
      const current = cachedTrs.find(t => t.name === name) ?? tr
      // Forward v_nom_0 / v_nom_1 so the backend re-validates against the
      // connected buses on every save (catches voltage edits made elsewhere).
      const payload: Partial<Transformer> = {
        ...current,
        name: newName, bus0: current.bus0, bus1: current.bus1, type: current.type ?? '',
        s_nom: numField('s_nom', current.s_nom),
        r: numField('r', current.r),
        x: numField('x', current.x),
        tap_ratio: numField('tap_ratio', current.tap_ratio),
        phase_shift: numField('phase_shift', current.phase_shift ?? 0),
        // Cost fields: blank ⇒ save 0 (deliberate clear), not the old value.
        capital_cost: numField('capital_cost', 0),
        fom_cost: numField('fom_cost', 0),
        // overnight_cost: assign unconditionally so blanking clears it on
        // the backend (see GeneratorCard for the cleared-to-null rationale).
        overnight_cost: (() => {
          const raw = (form.overnight_cost ?? '').trim()
          if (!raw) return null
          const v = parseFloat(raw)
          return Number.isFinite(v) ? v : null
        })(),
        v_nom_0: numField('v_nom_0', current.v_nom_0 ?? 0),
        v_nom_1: numField('v_nom_1', current.v_nom_1 ?? 0),
        s_nom_extendable: isExt,
        s_nom_min: isExt ? numField('s_nom_min', current.s_nom_min ?? 0) : 0,
      }
      // Optional bounds + per-asset overrides — assign unconditionally.
      if (isExt) {
        const maxRaw = form.s_nom_max?.trim()
        payload.s_nom_max = maxRaw ? parseFloat(maxRaw) : null
      }
      const drRaw = (form.discount_rate_pct ?? '').trim()
      if (drRaw) {
        const v = parseFloat(drRaw)
        payload.discount_rate = Number.isFinite(v) ? v / 100 : null
      } else {
        payload.discount_rate = null
      }
      payload.build_year = ni(form, 'build_year', current.build_year ?? 0)
      const ltRaw = (form.lifetime ?? '').trim()
      if (ltRaw) {
        const v = parseFloat(ltRaw)
        payload.lifetime = Number.isFinite(v) ? v : null
      } else {
        payload.lifetime = null
      }
      return networkApi.updateTransformer(name, payload)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'transformers') })
      setEditing(false)
      toast.success('Transformer updated')
      const newName = form.name?.trim() || name
      if (newName !== name) setSelectedComponent({ type: 'Transformer', name: newName })
    },
    onError: (e: Error) => toast.error(`Update failed: ${e.message}`),
  })

  if (!tr) return <p className="p-3 text-xs text-muted">Loading…</p>

  const startEdit = () => {
    setForm({
      name: tr.name,
      s_nom: String(tr.s_nom ?? ''),
      r: String(tr.r ?? ''),
      x: String(tr.x ?? ''),
      tap_ratio: String(tr.tap_ratio ?? '1'),
      phase_shift: String(tr.phase_shift ?? '0'),
      capital_cost: String(tr.capital_cost ?? '0'),
      fom_cost: String(tr.fom_cost ?? '0'),
      overnight_cost: tr.overnight_cost == null || !Number.isFinite(tr.overnight_cost) ? '' : String(tr.overnight_cost),
      v_nom_0: String(tr.v_nom_0 ?? '0'),
      v_nom_1: String(tr.v_nom_1 ?? '0'),
      s_nom_extendable: tr.s_nom_extendable ? 'true' : 'false',
      s_nom_min: String(tr.s_nom_min ?? 0),
      s_nom_max: tr.s_nom_max == null || !Number.isFinite(tr.s_nom_max) ? '' : String(tr.s_nom_max),
      discount_rate_pct: discountRatePct(tr.discount_rate),
      // Vintage fields — same defaulting story as the rest of the panels.
      build_year: String(tr.build_year ?? 0),
      lifetime: defaultLifetime(tr.lifetime, globalLt),
    })
    setEditing(true)
  }

  const numInp = (label: string, key: string, unit?: string) => (
    <label key={key} className="flex flex-col gap-0.5 mb-1.5">
      <span className="text-[10px] text-muted">{label}{unit ? ` (${unit})` : ''}</span>
      <input className="bg-bg border border-border rounded px-2 py-1 text-xs font-mono text-text focus:outline-none focus:border-accent"
        type="number" step="any" value={form[key] ?? ''}
        onChange={e => setForm(p => ({ ...p, [key]: e.target.value }))} />
    </label>
  )

  const txtInp = (label: string, key: string) => (
    <label key={key} className="flex flex-col gap-0.5 mb-1.5">
      <span className="text-[10px] text-muted">{label}</span>
      <input className="bg-bg border border-border rounded px-2 py-1 text-xs text-text focus:outline-none focus:border-accent"
        type="text" value={form[key] ?? ''}
        onChange={e => setForm(p => ({ ...p, [key]: e.target.value }))} />
    </label>
  )

  return (
    <div className="p-3">
      <Section title="Topology">
        <Row label="HV bus (bus0)" value={tr.bus0} />
        <Row label="LV bus (bus1)" value={tr.bus1} />
        {tr.type && <Row label="Type" value={tr.type} />}
      </Section>
      <Section title="Voltage step">
        <Row label="v_nom₀ (HV)" value={tr.v_nom_0 ?? null} unit=" kV" />
        <Row label="v_nom₁ (LV)" value={tr.v_nom_1 ?? null} unit=" kV" />
      </Section>
      {editing ? (
        <Section title="Edit Parameters">
          {txtInp('Name', 'name')}
          {numInp('Declared v_nom₀', 'v_nom_0', 'kV')}
          {numInp('Declared v_nom₁', 'v_nom_1', 'kV')}
          {numInp('s_nom', 's_nom', 'MVA')}
          {numInp('Resistance R', 'r', 'pu')}
          {numInp('Reactance X', 'x', 'pu')}
          {numInp('Tap ratio', 'tap_ratio')}
          {numInp('Phase shift', 'phase_shift', '°')}
          <label className="flex items-center gap-1.5 cursor-pointer py-0.5 mb-1.5">
            <input type="checkbox" checked={form.s_nom_extendable === 'true'}
              onChange={e => {
                const checked = e.target.checked
                setForm(p => {
                  const next = { ...p, s_nom_extendable: checked ? 'true' : 'false' }
                  return autofillNomMin('s_nom', 's_nom_min')(checked, next)
                })
              }}
              className="accent-accent" />
            <span className="text-xs text-text">Extendable</span>
          </label>
          {form.s_nom_extendable === 'true' && (
            <>
              {numInp('s_nom_min', 's_nom_min', 'MVA')}
              {numInp('s_nom_max', 's_nom_max', 'MVA')}
              <div className="my-1">
                <button type="button" onClick={() => setVintageOpen(true)}
                  className="text-[11px] text-accent hover:underline">
                  Per-period bounds…
                </button>
                <span className="ml-2 text-[10px] text-muted">override Min/Max per investment period</span>
              </div>
            </>
          )}
          {vintageOpen && tr && (
            <VintagePeriodBoundsModal componentClass="Transformer" name={tr.name} onClose={() => setVintageOpen(false)} />
          )}
          {numInp('Capital cost', 'capital_cost', '€/MVA')}
          {numInp('FOM cost', 'fom_cost', '€/MVA/yr')}
          {numInp('Overnight cost', 'overnight_cost', '€/MVA')}
          <div className="mb-1.5">
            <DiscountRateInput fs={form} set={setForm} tip="Discount rate used to annuitize overnight_cost. Leave blank to use the global rate from Solver Settings." />
          </div>
          {/* Vintage attributes — same controls as Line / Generator / Link /
              Storage so multi-period validators have the same set of knobs
              across every component class. */}
          <BuildYearSelect fs={form} set={setForm} tip={docTip('transformer.build_year')} />
          <NumInput label="Lifetime" k="lifetime" fs={form} set={setForm} unit="yr"
            tip={docTip('transformer.lifetime')} />
          <div className="flex gap-2 mt-2">
            <button onClick={() => updateMut.mutate()} disabled={updateMut.isPending}
              className="flex-1 py-1.5 bg-accent text-white rounded text-xs font-semibold hover:bg-accent/90 transition-colors disabled:opacity-50">
              {updateMut.isPending ? 'Saving…' : 'Save'}
            </button>
            <button onClick={() => setEditing(false)}
              className="px-3 py-1.5 border border-border rounded text-xs text-muted hover:text-text transition-colors">
              Cancel
            </button>
          </div>
        </Section>
      ) : (
        <>
          <Section title="Parameters">
            <Row label="s_nom"       value={tr.s_nom}             unit=" MVA" />
            <Row label="Resistance R" value={tr.r ?? null}        unit=" pu" />
            <Row label="Reactance X" value={tr.x}                 unit=" pu" />
            <Row label="Tap ratio"   value={tr.tap_ratio} />
            <Row label="Phase shift" value={tr.phase_shift ?? 0}  unit="°" />
            <Row label="Extendable"  value={tr.s_nom_extendable} />
            <Row label="Capital cost" value={tr.capital_cost}     unit=" €/MVA" />
            <Row label="FOM cost"     value={tr.fom_cost ?? null} unit=" €/MVA/yr" />
            <Row label="Overnight cost" value={tr.overnight_cost ?? null} unit=" €/MVA" />
            <Row label="Discount rate"
              value={tr.discount_rate != null && Number.isFinite(tr.discount_rate)
                ? Number((tr.discount_rate * 100).toFixed(3)) : null}
              unit=" %"
              tip="Per-asset discount rate. Blank means the global rate from Solver Settings is used." />
            <Row label="Build year"  value={tr.build_year}                       tip={docTip('transformer.build_year')} />
            <Row label="Lifetime"    value={tr.lifetime ?? null}  unit=" yr"     tip={docTip('transformer.lifetime')} />
          </Section>
          <button onClick={startEdit}
            className="w-full py-1.5 border border-border rounded text-xs text-muted hover:border-accent hover:text-accent transition-colors">
            Edit Parameters
          </button>
        </>
      )}
    </div>
  )
}

// ── Link panel ─────────────────────────────────────────────────────────────────
function LinkPanel({ name }: { name: string }) {
  const { setSelectedComponent } = useUIStore()
  const currentProject = useUIStore(s => s.currentProject)
  const { data: links = [] } = useQuery({ queryKey: nk(currentProject, 'links'), queryFn: networkApi.getLinks })
  const link = (links as Link[]).find((l: Link) => l.name === name)
  if (!link) return <p className="p-3 text-xs text-muted">Loading…</p>
  return (
    <div className="p-3">
      <Section title="Topology">
        <Row label="From bus" value={link.bus0} tip={docTip('link.bus0')} />
        <Row label="To bus"   value={link.bus1} tip={docTip('link.bus1')} />
      </Section>
      <LinkCard key={link.name} link={link} mode="detail" onRename={n => setSelectedComponent({ type: 'Link', name: n })} />
    </div>
  )
}

// ── Direct asset panels (selected from bottom table or search) ─────────────────
function GeneratorPanel({ name }: { name: string }) {
  const { setSelectedComponent } = useUIStore()
  const currentProject = useUIStore(s => s.currentProject)
  const { data: generators = [] } = useQuery({ queryKey: nk(currentProject, 'generators'), queryFn: networkApi.getGenerators })
  const gen = (generators as Generator[]).find(g => g.name === name)
  if (!gen) return <p className="p-3 text-xs text-muted">Loading…</p>
  return (
    <div className="p-3">
      <Section title="Connected to">
        <Row label="Bus" value={gen.bus} tip={docTip('generator.bus')} />
      </Section>
      <GeneratorCard key={gen.name} gen={gen} mode="detail" onRename={n => setSelectedComponent({ type: 'Generator', name: n })} />
    </div>
  )
}

function StorageUnitPanel({ name }: { name: string }) {
  const { setSelectedComponent } = useUIStore()
  const currentProject = useUIStore(s => s.currentProject)
  const { data: sus = [] } = useQuery({ queryKey: nk(currentProject, 'storage_units'), queryFn: networkApi.getStorageUnits })
  const su = (sus as StorageUnit[]).find(s => s.name === name)
  if (!su) return <p className="p-3 text-xs text-muted">Loading…</p>
  return (
    <div className="p-3">
      <Section title="Connected to">
        <Row label="Bus" value={su.bus} tip={docTip('storage_unit.bus')} />
      </Section>
      <StorageUnitCard key={su.name} su={su} mode="detail" onRename={n => setSelectedComponent({ type: 'StorageUnit', name: n })} />
    </div>
  )
}

// Wrapper for the right-panel dispatch table. Mirrors StorageUnitPanel exactly
// — the only differences are the data source (n.stores) and the rename target
// type. Selecting a Store from the bottom table (or anywhere else) routes
// here via selectedComponent.type === 'Store'.
function StorePanel({ name }: { name: string }) {
  const { setSelectedComponent } = useUIStore()
  const currentProject = useUIStore(s => s.currentProject)
  const { data: stores = [] } = useQuery({ queryKey: nk(currentProject, 'stores'), queryFn: networkApi.getStores })
  const store = (stores as Store[]).find(s => s.name === name)
  if (!store) return <p className="p-3 text-xs text-muted">Loading…</p>
  return (
    <div className="p-3">
      <Section title="Connected to">
        <Row label="Bus" value={store.bus} tip={docTip('store.bus')} />
      </Section>
      <StoreCard key={store.name} store={store} mode="detail" onRename={n => setSelectedComponent({ type: 'Store', name: n })} />
    </div>
  )
}

function LoadPanel({ name }: { name: string }) {
  const { setSelectedComponent } = useUIStore()
  const currentProject = useUIStore(s => s.currentProject)
  const { data: loads = [] } = useQuery({ queryKey: nk(currentProject, 'loads'), queryFn: networkApi.getLoads })
  const load = (loads as Load[]).find(l => l.name === name)
  if (!load) return <p className="p-3 text-xs text-muted">Loading…</p>
  return (
    <div className="p-3">
      <Section title="Connected to">
        <Row label="Bus" value={load.bus} tip={docTip('load.bus')} />
      </Section>
      <LoadCard key={load.name} load={load} mode="detail" onRename={n => setSelectedComponent({ type: 'Load', name: n })} />
    </div>
  )
}

// ── Main ───────────────────────────────────────────────────────────────────────
export default function PropertiesPanel() {
  const { selectedComponent, setSelectedComponent, creationItem, rightPanelOpen, toggleRightPanel } = useUIStore()
  const qc = useQueryClient()

  const deleteMut = useMutation({
    mutationFn: async () => {
      if (!selectedComponent) return
      const proj = useUIStore.getState().currentProject
      if (selectedComponent.type === 'Bus') {
        await networkApi.deleteBusCascade(selectedComponent.name)
        const keys = ['buses', 'lines', 'links', 'generators', 'loads', 'storage_units', 'stores']
        keys.forEach(k => qc.invalidateQueries({ queryKey: nk(proj, k) }))
      } else if (selectedComponent.type === 'Line') {
        await networkApi.deleteLine(selectedComponent.name)
        qc.invalidateQueries({ queryKey: nk(proj, 'lines') })
      } else if (selectedComponent.type === 'Link') {
        await networkApi.deleteLink(selectedComponent.name)
        qc.invalidateQueries({ queryKey: nk(proj, 'links') })
      } else if (selectedComponent.type === 'Transformer') {
        await networkApi.deleteTransformer(selectedComponent.name)
        qc.invalidateQueries({ queryKey: nk(proj, 'transformers') })
      }
    },
    onSuccess: () => {
      const name = selectedComponent?.name ?? ''
      const label = selectedComponent?.type === 'Bus' ? `Bus "${name}" + connected` : name
      showDeleteUndoToast(label, qc)
      setSelectedComponent(null)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const handleDelete = () => {
    if (!selectedComponent) return
    deleteMut.mutate()
  }

  // ── Collapsed strip ─────────────────────────────────────────────────────────
  if (!rightPanelOpen) {
    return (
      <div className="flex flex-col items-center shrink-0 border-l border-border bg-panel" style={{ width: 22 }}>
        <button
          onClick={toggleRightPanel}
          className="mt-2 p-1 text-muted hover:text-accent transition-colors"
          title="Expand properties panel"
        >
          <ChevronRight size={13} />
        </button>
        {/* Rotated label */}
        <span
          className="text-[9px] font-bold text-muted uppercase tracking-widest mt-3 select-none"
          style={{ writingMode: 'vertical-lr', transform: 'rotate(180deg)' }}
        >
          Properties
        </span>
      </div>
    )
  }

  // ── Creation form ───────────────────────────────────────────────────────────
  // The form opens as a 300px slide-in panel on the right for both entry
  // paths (click in AssetPalette or drag-and-drop onto the canvas). When the
  // drop path is taken, creationItem.dropPosition carries the canvas-space
  // coordinates so the form can pre-populate x/y (for Bus) and so the canvas
  // can re-position the new node via posCache afterwards.
  if (creationItem) {
    return (
      <aside className="shrink-0 bg-bg border-l border-border flex flex-col overflow-hidden shadow-lg" style={{ width: 300 }}>
        <CreationForm item={creationItem} />
      </aside>
    )
  }

  // ── Empty state (panel open, nothing selected) ──────────────────────────────
  if (!selectedComponent) {
    return (
      <aside className="shrink-0 bg-bg border-l border-border flex flex-col overflow-hidden" style={{ width: 300 }}>
        <div className="flex items-center justify-between px-3 h-10 border-b border-border bg-panel shrink-0">
          <span className="text-[10px] font-bold text-muted uppercase tracking-wider">Properties</span>
          <button onClick={toggleRightPanel} className="p-1 text-muted hover:text-text transition-colors" title="Hide panel">
            <X size={13} />
          </button>
        </div>
        <div className="flex-1 flex flex-col items-center justify-center gap-2 p-4">
          <div className="w-8 h-8 rounded-full bg-border/40 flex items-center justify-center">
            <Database size={14} className="text-muted" />
          </div>
          <p className="text-xs text-muted text-center">Click a bus, line, or asset on the canvas to inspect it</p>
        </div>
      </aside>
    )
  }

  // ── Component detail ────────────────────────────────────────────────────────
  const TYPE_LABELS: Record<string, string> = {
    Bus: 'Bus', Line: 'Line', Link: 'Link / HVDC', Transformer: 'Transformer',
    AssetGroup: 'Assets', Generator: 'Generator', StorageUnit: 'Storage Unit',
    Store: 'Store', Load: 'Load',
  }

  const displayName = selectedComponent.type === 'AssetGroup'
    ? selectedComponent.name.split('::')[0]
    : selectedComponent.name

  const canDelete = selectedComponent.type === 'Bus' || selectedComponent.type === 'Line' || selectedComponent.type === 'Link' || selectedComponent.type === 'Transformer'
  // AssetGroup is a synthetic bus::category grouping, not a real network
  // component — it has no entry in Asset Detail's results registry.
  const canViewResults = selectedComponent.type !== 'AssetGroup'

  return (
    <aside className="shrink-0 bg-bg border-l border-border flex flex-col overflow-hidden shadow-lg" style={{ width: 300 }}>
      <div className="flex items-center justify-between px-3 h-10 border-b border-border bg-panel shrink-0">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-accent" />
          <span className="text-xs font-bold text-text uppercase tracking-wider">
            {TYPE_LABELS[selectedComponent.type] ?? selectedComponent.type}
          </span>
        </div>
        <div className="flex items-center gap-0.5">
          {canDelete && (
            <button
              onClick={handleDelete}
              disabled={deleteMut.isPending}
              className="p-1 text-muted hover:text-danger transition-colors disabled:opacity-40"
              title={`Delete ${selectedComponent.type}`}
            >
              <Trash2 size={13} />
            </button>
          )}
          <button onClick={() => setSelectedComponent(null)} className="p-1 text-muted hover:text-text transition-colors" title="Deselect">
            <X size={14} />
          </button>
        </div>
      </div>
      <div className="px-3 py-2 border-b border-border bg-accent/5 shrink-0 flex items-center justify-between gap-2">
        <p className="text-xs font-mono font-semibold text-accent truncate">{displayName}</p>
        {canViewResults && (
          <button
            onClick={() => useUIStore.getState().requestAssetDetail({
              componentClass: selectedComponent.type, name: selectedComponent.name })}
            title="Open this asset's results in the Asset Detail tab"
            className="flex items-center gap-1 text-[11px] text-muted hover:text-accent shrink-0"
          ><ExternalLink size={11} /> View results</button>
        )}
      </div>
      <div className="flex-1 overflow-y-auto">
        {/* `key={...}` forces a remount when switching between two assets of the
            same type, resetting any in-flight edit/form state to a clean view. */}
        {selectedComponent.type === 'Bus'         && <BusPanel         key={selectedComponent.name} name={selectedComponent.name} />}
        {selectedComponent.type === 'Line'        && <LinePanel        key={selectedComponent.name} name={selectedComponent.name} />}
        {selectedComponent.type === 'Link'        && <LinkPanel        key={selectedComponent.name} name={selectedComponent.name} />}
        {selectedComponent.type === 'Generator'   && <GeneratorPanel   key={selectedComponent.name} name={selectedComponent.name} />}
        {selectedComponent.type === 'StorageUnit' && <StorageUnitPanel key={selectedComponent.name} name={selectedComponent.name} />}
        {selectedComponent.type === 'Store'       && <StorePanel       key={selectedComponent.name} name={selectedComponent.name} />}
        {selectedComponent.type === 'Load'        && <LoadPanel        key={selectedComponent.name} name={selectedComponent.name} />}
        {selectedComponent.type === 'AssetGroup'  && <AssetGroupPanel  key={selectedComponent.name} name={selectedComponent.name} />}
        {selectedComponent.type === 'Transformer' && <TransformerPanel key={selectedComponent.name} name={selectedComponent.name} />}
      </div>
    </aside>
  )
}

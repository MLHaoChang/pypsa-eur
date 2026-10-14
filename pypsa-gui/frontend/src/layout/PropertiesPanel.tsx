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
import { editScope, loadExtras } from '../utils/extrasStore'
import { nk } from '../utils/queryKeys'
import { updateAsset } from '../utils/assetWrite'
// A1-FE: a refused edit (`study_in_flight`, quiet in the interceptor) toasts
// the backend's sentence, not axios' "Request failed with status code 409".
import { blockerMessage } from '../utils/blockerMessage'
import { isRenewableCarrier } from '../utils/carriers'
import { ingestRescale } from '../utils/rescaleActions'
import type { RescalePreview } from '../utils/rescale'
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
import { useAssetSchema } from '../hooks/useAssetSchema'
import { useAssetProvenance } from '../hooks/useAssetProvenance'
import { buildPatch, seedForm } from '../utils/assetForm'
import { AssetFieldsEdit, assetReadoutItems, fieldHelp, schemaFields } from './properties/AssetFields'
import { GuideButton } from '../components/GuidedTour'
import CarrierPanel from './properties/CarrierPanel'

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
  no,
  SetFS,
  NumInput,
  EhBusInputs,
  EhLinkInputs,
  ehBusFS,
  ehBusPayload,
  ehLinkPayload,
  useGlobalDiscountRate,
  useGlobalDefaultLifetime,
  useBuildYearOptions,
  defaultLifetime,
  TxtInput,
  CoordPairInput,
  autofillNomMin,
  categorizeCarriers,
  CarrierGroupSelect,
  SectionHdr,
  MiniProfileChart,
  CardShell,
  EditShell,
  ExtrasSection,
  extrasPatch,
  seedExtras,
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
            }).catch((e: Error) => toast.error(blockerMessage(e)))
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
  // S1b: the fields come from the asset schema (services/asset_schema/fields.py).
  // Investment is typed as the overnight cost; the annualised capital cost is
  // derived (overnight × annuity + fixed O&M) and shown read-only.
  const { data: schema } = useAssetSchema('Generator')
  // U3: each field's provenance (default from the pack, customised, measured).
  const provenance = useAssetProvenance('Generator', gen.name)
  const specs = schemaFields(schema)
  const asset = gen as unknown as Record<string, unknown>
  // Curtailment cost prices spilled wind and solar; a thermal unit has none to spill.
  const hidden: ReadonlySet<string> = isRenewableCarrier((form.carrier || gen.carrier) ?? '')
    ? new Set() : new Set(['curtailment_cost'])

  const delMut = useMutation({
    mutationFn: () => networkApi.deleteGenerator(gen.name),
    onSuccess: () => { qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'generators') }); showDeleteUndoToast(gen.name, qc) },
    onError: (e: Error) => toast.error(blockerMessage(e)),
  })

  const saveMut = useMutation({
    // The Asset-write chokepoint (utils/assetWrite.ts) owns fetch, spread,
    // PUT and invalidation; this builder owns only the form→patch mapping.
    // `current` arrives from the chokepoint — fresh-fetched on a cold cache
    // (ruling 3), so the old `?? gen` stale-prop fallback is gone.
    mutationFn: () => updateAsset<Generator>(
      qc, useUIStore.getState().currentProject, 'generators', gen.name,
      (current) => {
      const payload = buildPatch(specs, form, current as unknown as Record<string, unknown>) as Partial<Generator>
      payload.name = form.name?.trim() || gen.name
      payload.bus = (form.bus as string | undefined)?.trim() || gen.bus
      payload.carrier = form.carrier || current.carrier
      // Extras last: a value the user just typed in the extras picker wins.
      Object.assign(payload, extrasPatch(form, loadExtras(editScope('Generator'))))
      return payload
    }),
    onSuccess: () => {
      // Invalidation is the chokepoint's job now.
      setOpen(false)
      toast.success('Generator saved')
      const newName = form.name?.trim() || gen.name
      if (newName !== gen.name) onRename?.(newName)
    },
    onError: (e: Error) => toast.error(`Save failed: ${blockerMessage(e)}`),
  })

  const startEdit = () => {
    const base = seedForm(specs, asset)
    // Pre-fill lifetime from solver-config global when blank/inf — matches
    // user expectation that "all assets default to the global lifetime".
    base.lifetime = defaultLifetime(gen.lifetime, globalLt)
    if (!base.control) base.control = 'PQ'
    setForm(seedExtras(gen, base, loadExtras(editScope('Generator'))))
    setOpen(true)
  }

  // The schematic's asset-node double-click (plan 1 A2) asks for this
  // editor; only the selected asset's detail view takes it (the LinkCard rule).
  const editRequest = useUIStore(s => s.propertiesEditRequest)
  useEffect(() => {
    if (editRequest?.type !== 'Generator' || editRequest.name !== gen.name || mode !== 'detail') return
    if (!open) startEdit()
    useUIStore.getState().clearPropertiesEditRequest()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editRequest, mode, gen.name])

  const items: ExpandedItem[] = assetReadoutItems(schema, asset, hidden, provenance)
  const visibleItems = items.filter(i => i.value != null && i.value !== '' && i.value !== false)

  if (!open && mode === 'detail') {
    return (
      <>
        <Section title={title ?? 'Generator Properties'}>
          <Row label="Carrier" value={gen.carrier} tip={fieldHelp(schema, 'carrier')} />
          {visibleItems.map(i => (
            <Row key={i.label} label={i.label} value={i.value} unit={i.unit} tip={i.tip} badge={i.badge} />
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
      {schema ? (
        <AssetFieldsEdit
          schema={schema} fs={form} set={setForm} current={asset}
          carriers={carriers as Carrier[]} buses={buses as Bus[]} hidden={hidden} provenance={provenance}
          onCheck={{ p_nom_extendable: autofillNomMin('p_nom', 'p_nom_min') }}
          after={{
            p_nom_max: (
              <div className="col-span-2 -mt-0.5">
                <button type="button" onClick={() => setVintageOpen(true)}
                  className="text-[11px] text-accent hover:underline">
                  Per-period bounds…
                </button>
                <span className="ml-2 text-[10px] text-muted">override Min/Max per investment period</span>
              </div>
            ),
          }}
        />
      ) : <p className="col-span-2 text-[10px] text-muted">Loading fields…</p>}
      {vintageOpen && (
        <VintagePeriodBoundsModal componentClass="Generator" name={gen.name} onClose={() => setVintageOpen(false)} />
      )}
      <ExtrasSection
        componentClass="Generator"
        fs={form}
        set={setForm}
        curated={Object.keys(form)}
      />
    </EditShell>
  )
}

// ── Storage unit card ──────────────────────────────────────────────────────────
export function StorageUnitCard({ su, onRename, mode = 'card', title }: {
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
  // S1b: the fields come from the asset schema (services/asset_schema/fields.py).
  // A battery is typed as its two overnight parts; capital cost, lifetime and
  // fixed O&M are derived from them and shown read-only.
  const { data: schema } = useAssetSchema('StorageUnit')
  // U3: each field's provenance (default from the pack, customised, measured).
  const provenance = useAssetProvenance('StorageUnit', su.name)
  const specs = schemaFields(schema)
  const asset = su as unknown as Record<string, unknown>

  const delMut = useMutation({
    mutationFn: () => networkApi.deleteStorageUnit(su.name),
    onSuccess: () => { qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'storage_units') }); showDeleteUndoToast(su.name, qc) },
    onError: (e: Error) => toast.error(blockerMessage(e)),
  })

  const saveMut = useMutation({
    // Chokepoint (utils/assetWrite.ts) owns fetch/spread/PUT/invalidate;
    // the schema's descriptors own the form→patch mapping.
    mutationFn: () => updateAsset<StorageUnit>(
      qc, useUIStore.getState().currentProject, 'storage_units', su.name,
      (current) => {
      const payload = buildPatch(specs, form, current as unknown as Record<string, unknown>) as Partial<StorageUnit>
      payload.name = form.name?.trim() || su.name
      payload.bus = (form.bus as string | undefined)?.trim() || su.bus
      payload.carrier = form.carrier || current.carrier
      // Extras last: a value the user just typed in the extras picker wins.
      Object.assign(payload, extrasPatch(form, loadExtras(editScope('StorageUnit'))))
      return payload
    }),
    onSuccess: () => {
      setOpen(false)
      toast.success('Storage unit saved')
      const newName = form.name?.trim() || su.name
      if (newName !== su.name) onRename?.(newName)
    },
    onError: (e: Error) => toast.error(`Save failed: ${blockerMessage(e)}`),
  })

  const startEdit = () => {
    setForm(seedExtras(su, seedForm(specs, asset), loadExtras(editScope('StorageUnit'))))
    setOpen(true)
  }

  // Asset-node double-click (plan 1 A2), as on the Generator card.
  const editRequest = useUIStore(s => s.propertiesEditRequest)
  useEffect(() => {
    if (editRequest?.type !== 'StorageUnit' || editRequest.name !== su.name || mode !== 'detail') return
    if (!open) startEdit()
    useUIStore.getState().clearPropertiesEditRequest()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editRequest, mode, su.name])

  const items: ExpandedItem[] = assetReadoutItems(schema, asset, undefined, provenance)
  // A unit priced the old way (one overnight cost, no parts) still has that
  // cost; the card says so, since its parts are what the form types now.
  const legacyOvernight = su.overnight_cost != null && Number.isFinite(su.overnight_cost)
    && !(Number.isFinite(asset.inv_power_overnight as number) || Number.isFinite(asset.inv_energy_overnight as number))
  const visibleItems = items.filter(i => i.value != null && i.value !== '' && i.value !== false)

  if (!open && mode === 'detail') {
    return (
      <>
        <Section title={title ?? 'Storage Properties'}>
          <Row label="Carrier" value={su.carrier} tip={fieldHelp(schema, 'carrier')} />
          {visibleItems.map(i => (
            <Row key={i.label} label={i.label} value={i.value} unit={i.unit} tip={i.tip} badge={i.badge} />
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
          defaultCost={legacyOvernight ? su.overnight_cost : null}
          onClone={async (year, overnightCost) => {
            // `...su` carries the investment parts (inv_*) with the rest, so
            // a clone of a battery keeps both parts and their lifetimes.
            const payload: Partial<StorageUnit> = {
              ...su, name: `${su.name}_${year}`, build_year: year,
            }
            if (overnightCost !== null && legacyOvernight) payload.overnight_cost = overnightCost
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
      {schema ? (
        <AssetFieldsEdit
          schema={schema} fs={form} set={setForm} current={asset}
          carriers={carriers as Carrier[]} buses={buses as Bus[]} provenance={provenance}
          onCheck={{ p_nom_extendable: autofillNomMin('p_nom', 'p_nom_min') }}
          after={{
            p_nom_max: (
              <div className="col-span-2 -mt-0.5">
                <button type="button" onClick={() => setVintageOpen(true)}
                  className="text-[11px] text-accent hover:underline">
                  Per-period bounds…
                </button>
                <span className="ml-2 text-[10px] text-muted">override Min/Max per investment period</span>
              </div>
            ),
            inv_energy_lifetime: legacyOvernight ? (
              <p className="col-span-2 text-[10px] text-muted" data-testid="legacy-overnight-note">
                Priced the old way: one overnight cost of {(su.overnight_cost! / 1000).toLocaleString()} €/kW.
                Enter the two parts to replace it.
              </p>
            ) : undefined,
          }}
        />
      ) : <p className="col-span-2 text-[10px] text-muted">Loading fields…</p>}
      {vintageOpen && (
        <VintagePeriodBoundsModal componentClass="StorageUnit" name={su.name} onClose={() => setVintageOpen(false)} />
      )}
      <ExtrasSection
        componentClass="StorageUnit"
        fs={form}
        set={setForm}
        curated={Object.keys(form)}
      />
    </EditShell>
  )
}

// ── Store card ─────────────────────────────────────────────────────────────────
export function StoreCard({ store, onRename, mode = 'card', title }: {
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
  // S1c: the fields come from the asset schema (services/asset_schema/fields.py).
  // Investment is typed as the overnight cost per kWh; the annualised capital
  // cost is derived and shown read-only.
  const { data: schema } = useAssetSchema('Store')
  // U3: each field's provenance (default from the pack, customised, measured).
  const provenance = useAssetProvenance('Store', store.name)
  const specs = schemaFields(schema)
  const asset = store as unknown as Record<string, unknown>

  const delMut = useMutation({
    mutationFn: () => networkApi.deleteStore(store.name),
    onSuccess: () => { qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'stores') }); showDeleteUndoToast(store.name, qc) },
    onError: (e: Error) => toast.error(blockerMessage(e)),
  })

  const saveMut = useMutation({
    // Chokepoint (utils/assetWrite.ts) owns fetch/spread/PUT/invalidate;
    // the schema's descriptors own the form→patch mapping.
    mutationFn: () => updateAsset<Store>(
      qc, useUIStore.getState().currentProject, 'stores', store.name,
      (current) => {
      const payload = buildPatch(specs, form, current as unknown as Record<string, unknown>) as Partial<Store>
      payload.name = form.name?.trim() || store.name
      payload.bus = (form.bus as string | undefined)?.trim() || store.bus
      payload.carrier = form.carrier || current.carrier
      // Extras last: a value the user just typed in the extras picker wins.
      Object.assign(payload, extrasPatch(form, loadExtras(editScope('Store'))))
      return payload
    }),
    onSuccess: () => {
      setOpen(false)
      toast.success('Store saved')
      const newName = form.name?.trim() || store.name
      if (newName !== store.name) onRename?.(newName)
    },
    onError: (e: Error) => toast.error(`Save failed: ${blockerMessage(e)}`),
  })

  const startEdit = () => {
    const base = seedForm(specs, asset)
    base.lifetime = defaultLifetime(store.lifetime, globalLt)
    setForm(seedExtras(store, base, loadExtras(editScope('Store'))))
    setOpen(true)
  }

  const isH2Store = isH2Carrier(store.carrier)
  // Asset-node double-click (plan 1 A2), as on the Generator card.
  const editRequest = useUIStore(s => s.propertiesEditRequest)
  useEffect(() => {
    if (editRequest?.type !== 'Store' || editRequest.name !== store.name || mode !== 'detail') return
    if (!open) startEdit()
    useUIStore.getState().clearPropertiesEditRequest()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editRequest, mode, store.name])

  const items: ExpandedItem[] = assetReadoutItems(schema, asset, undefined, provenance)
  const visibleItems = items.filter(i => i.value != null && i.value !== '' && i.value !== false)

  if (!open && mode === 'detail') {
    return (
      <>
        <Section title={title ?? 'Store Properties'}>
          <Row label="Carrier" value={store.carrier} tip={fieldHelp(schema, 'carrier')} />
          {visibleItems.map(i => (
            <Row key={i.label} label={i.label} value={i.value} unit={i.unit} tip={i.tip} badge={i.badge} />
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
      <div className="col-span-2 text-[9px] text-muted bg-border/20 rounded px-2 py-1.5 leading-tight">
        {isH2Store
          ? <>A <strong>Store</strong> is a pure energy vessel — power throughput is unlimited and bounded only by connected Links (electrolyzers/fuel cells). Set the energy capacity for the tank. 1 MWh ≈ 29.8 kg H₂ (LHV).</>
          : <>A <strong>Store</strong> has no intrinsic power limit — charge/discharge rate is set by connected components. Only the energy capacity constrains storage.</>}
      </div>
      {schema ? (
        <AssetFieldsEdit
          schema={schema} fs={form} set={setForm} current={asset} provenance={provenance}
          carriers={carriers as Carrier[]} buses={buses as Bus[]}
          onCheck={{ e_nom_extendable: autofillNomMin('e_nom', 'e_nom_min') }}
          after={{
            e_nom_max: (
              <div className="col-span-2 -mt-0.5">
                <button type="button" onClick={() => setVintageOpen(true)}
                  className="text-[11px] text-accent hover:underline">
                  Per-period bounds…
                </button>
                <span className="ml-2 text-[10px] text-muted">override Min/Max per investment period</span>
              </div>
            ),
          }}
        />
      ) : <p className="col-span-2 text-[10px] text-muted">Loading fields…</p>}
      {vintageOpen && (
        <VintagePeriodBoundsModal componentClass="Store" name={store.name} onClose={() => setVintageOpen(false)} />
      )}
      <ExtrasSection
        componentClass="Store"
        fs={form}
        set={setForm}
        curated={Object.keys(form)}
      />
    </EditShell>
  )
}

// ── Load card ──────────────────────────────────────────────────────────────────
export function LoadCard({ load, onRename, mode = 'card', title }: {
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
    onError: (e: Error) => toast.error(blockerMessage(e)),
  })

  // S1c: the fields come from the asset schema (services/asset_schema/fields.py).
  const { data: schema } = useAssetSchema('Load')
  // U3: each field's provenance (default from the pack, customised, measured).
  const provenance = useAssetProvenance('Load', load.name)
  const specs = schemaFields(schema)
  const asset = load as unknown as Record<string, unknown>

  const saveMut = useMutation({
    // Chokepoint (utils/assetWrite.ts) owns fetch/spread/PUT/invalidate;
    // the schema's descriptors own the form→patch mapping.
    mutationFn: () => updateAsset<Load>(
      qc, useUIStore.getState().currentProject, 'loads', load.name,
      (current) => {
        const payload = buildPatch(specs, form, current as unknown as Record<string, unknown>) as Partial<Load>
        payload.name = form.name?.trim() || load.name
        payload.bus = (form.bus as string | undefined)?.trim() || load.bus
        payload.carrier = form.carrier || current.carrier
        Object.assign(payload, extrasPatch(form, loadExtras(editScope('Load'))))
        return payload
      }),
    onSuccess: () => {
      setOpen(false)
      toast.success('Load saved')
      const newName = form.name?.trim() || load.name
      if (newName !== load.name) onRename?.(newName)
    },
    onError: (e: Error) => toast.error(`Save failed: ${blockerMessage(e)}`),
  })

  const startEdit = () => {
    setForm(seedExtras(load, seedForm(specs, asset), loadExtras(editScope('Load'))))
    setOpen(true)
  }

  // Asset-node double-click (plan 1 A2), as on the Generator card.
  const editRequest = useUIStore(s => s.propertiesEditRequest)
  useEffect(() => {
    if (editRequest?.type !== 'Load' || editRequest.name !== load.name || mode !== 'detail') return
    if (!open) startEdit()
    useUIStore.getState().clearPropertiesEditRequest()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editRequest, mode, load.name])

  const items: ExpandedItem[] = assetReadoutItems(schema, asset, undefined, provenance)
  const visibleItems = items.filter(i => i.value != null && i.value !== '' && i.value !== false)

  if (!open && mode === 'detail') {
    return (
      <>
        <Section title={title ?? 'Load Properties'}>
          <Row label="Carrier" value={load.carrier} tip={fieldHelp(schema, 'carrier')} />
          {visibleItems.map(i => (
            <Row key={i.label} label={i.label} value={i.value} unit={i.unit} tip={i.tip} badge={i.badge} />
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
      {schema ? (
        <AssetFieldsEdit
          schema={schema} fs={form} set={setForm} current={asset} provenance={provenance}
          carriers={carriers as Carrier[]} buses={buses as Bus[]}
        />
      ) : <p className="col-span-2 text-[10px] text-muted">Loading fields…</p>}
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
      <ExtrasSection
        componentClass="Load"
        fs={form}
        set={setForm}
        curated={Object.keys(form)}
      />
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
export function LinkCard({ link, onRename, mode = 'card', title }: {
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
  // S1c: the fields come from the asset schema (services/asset_schema/fields.py).
  // Investment is typed as the overnight cost per kW of input; the annualised
  // capital cost is derived and shown read-only.
  const { data: schema } = useAssetSchema('Link')
  // U3: each field's provenance (default from the pack, customised, measured).
  const provenance = useAssetProvenance('Link', link.name)
  const specs = schemaFields(schema)
  const asset = link as unknown as Record<string, unknown>

  const delMut = useMutation({
    mutationFn: () => networkApi.deleteLink(link.name),
    onSuccess: () => { qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'links') }); showDeleteUndoToast(link.name, qc) },
    onError: (e: Error) => toast.error(blockerMessage(e)),
  })

  const saveMut = useMutation({
    // Chokepoint (utils/assetWrite.ts) owns fetch/spread/PUT/invalidate —
    // the spread is what keeps un-surfaced Link fields (`efficiency2`,
    // `bus2..bus4`, `committable`, `terrain_factor`, …) alive through the
    // backend's remove+add cycle. This builder is only the form→patch map.
    mutationFn: () => updateAsset<Link>(
      qc, useUIStore.getState().currentProject, 'links', link.name,
      (current) => {
      const payload = buildPatch(specs, form, current as unknown as Record<string, unknown>) as Partial<Link>
      payload.name = form.name?.trim() || link.name
      payload.bus0 = form.bus0 || current.bus0
      payload.bus1 = form.bus1 || current.bus1
      payload.carrier = form.carrier || current.carrier
      Object.assign(payload, ehLinkPayload(form, current))
      // Extras last: a value the user just typed in the extras picker wins.
      Object.assign(payload, extrasPatch(form, loadExtras(editScope('Link'))))
      return payload
    }),
    onSuccess: () => {
      setOpen(false)
      toast.success('Link saved')
      const newName = form.name?.trim() || link.name
      if (newName !== link.name) onRename?.(newName)
    },
    onError: (e: Error) => toast.error(`Save failed: ${blockerMessage(e)}`),
  })

  const startEdit = () => {
    const base = seedForm(specs, asset)
    base.lifetime = defaultLifetime(link.lifetime, globalLt)
    setForm(seedExtras(link, { ...base, eh_role: link.eh_role ?? '' }, loadExtras(editScope('Link'))))
    setOpen(true)
  }

  // Same request as the Bus card; only the selected Link's detail view takes
  // it (card mode stacks several Links in one asset group).
  const editRequest = useUIStore(s => s.propertiesEditRequest)
  useEffect(() => {
    if (editRequest?.type !== 'Link' || editRequest.name !== link.name || mode !== 'detail') return
    if (!open) startEdit()
    useUIStore.getState().clearPropertiesEditRequest()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editRequest, mode, link.name])

  const bus0Obj = (buses as Bus[]).find(b => b.name === link.bus0)
  const bus1Obj = (buses as Bus[]).find(b => b.name === link.bus1)
  const flowLabel = category === 'electrolyzer'
    ? `${bus0Obj?.carrier ?? 'AC'} → ${bus1Obj?.carrier ?? 'H₂'}`
    : category === 'fuel_cell'
    ? `${bus0Obj?.carrier ?? 'H₂'} → ${bus1Obj?.carrier ?? 'AC'}`
    : `${link.bus0} → ${link.bus1}`

  // Both ends lead the read-out: the schema's identity group is not in it.
  const items: ExpandedItem[] = [
    { label: 'From bus', value: link.bus0, tip: fieldHelp(schema, 'bus0') },
    { label: 'To bus', value: link.bus1, tip: fieldHelp(schema, 'bus1') },
    ...assetReadoutItems(schema, asset, undefined, provenance),
  ]
  const visibleItems = items.filter(i => i.value != null && i.value !== '' && i.value !== false)

  if (!open && mode === 'detail') {
    return (
      <>
        <Section title={title ?? 'Link Properties'}>
          <Row label="Carrier" value={link.carrier} tip={fieldHelp(schema, 'carrier')} />
          {categoryLabel && <Row label="Category" value={categoryLabel} />}
          {visibleItems.map(i => (
            <Row key={i.label} label={i.label} value={i.value} unit={i.unit} tip={i.tip} badge={i.badge} />
          ))}
        </Section>
        {showChart && hasProfile && (
          <MiniProfileChart component="links" attribute="p_max_pu" name={link.name} unit="pu" />
        )}
        <DetailFooter
          editLabel="Edit Link"
          testId="props-edit-link"
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
      {(category === 'electrolyzer' || category === 'fuel_cell') && (
        <div className="col-span-2 text-[9px] text-muted bg-border/20 rounded px-2 py-1.5 leading-tight">
          {category === 'electrolyzer'
            ? <>An <strong>electrolyzer</strong> consumes electricity (bus₀) and produces H₂ (bus₁). Efficiency = H₂ MW out / electricity MW in. To model a fuel cell (H₂→AC), create a separate Link with bus₀ as the H₂ bus and bus₁ as the AC bus.</>
            : <>A <strong>fuel cell</strong> consumes H₂ (bus₀) and produces electricity (bus₁). Efficiency = AC MW out / H₂ MW in. Electrolyzer and fuel cell must always be modelled as separate Links.</>}
        </div>
      )}
      {schema ? (
        <AssetFieldsEdit
          schema={schema} fs={form} set={setForm} current={asset} provenance={provenance}
          carriers={carriers as Carrier[]} buses={buses as Bus[]}
          onCheck={{ p_nom_extendable: autofillNomMin('p_nom', 'p_nom_min') }}
          after={{
            p_nom_max: (
              <div className="col-span-2 -mt-0.5">
                <button type="button" onClick={() => setVintageOpen(true)}
                  className="text-[11px] text-accent hover:underline">
                  Per-period bounds…
                </button>
                <span className="ml-2 text-[10px] text-muted">override Min/Max per investment period</span>
              </div>
            ),
          }}
        />
      ) : <p className="col-span-2 text-[10px] text-muted">Loading fields…</p>}
      {vintageOpen && (
        <VintagePeriodBoundsModal componentClass="Link" name={link.name} onClose={() => setVintageOpen(false)} />
      )}
      <SectionHdr title="Energy Hub" />
      <EhLinkInputs fs={form} set={setForm} />
      <ExtrasSection
        componentClass="Link"
        fs={form}
        set={setForm}
        curated={Object.keys(form)}
      />
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
    onError: (e: Error) => toast.error(blockerMessage(e)),
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
      {type === 'Generator' && inp('Capital cost (€/MW/yr)', 'cc', '0', 'number')}
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
  // `p_set_peak` covers loads whose demand lives in `loads_t.p_set` — summing
  // the static p_set alone showed "0 MW" on a time-series-loaded bus.
  const totalLoad = busLoads.reduce((s: number, l: Load) => s + Math.abs(l.p_set_peak ?? l.p_set ?? 0), 0)

  const thermalGens   = busGens.filter(g => !isRenewableCarrier(g.carrier))
  const renewableGens = busGens.filter(g => isRenewableCarrier(g.carrier))

  // M5: the fields come from the asset schema (services/asset_schema/fields.py), as on the asset cards; the
  // coordinate pair and the Energy Hub tags stay hand-written around them.
  const { data: schema } = useAssetSchema('Bus')
  const provenance = useAssetProvenance('Bus', name)
  const specs = schemaFields(schema)

  const startEdit = () => {
    if (!bus) return
    setForm(seedExtras(bus, {
      ...seedForm(specs, bus as unknown as Record<string, unknown>),
      ...ehBusFS(bus),
    }, loadExtras(editScope('Bus'))))
    setEditing(true)
  }

  // The EH tagging tour (prepareTaggingTour / the `props-edit-bus` reveal)
  // asks for Edit before the bus row may have loaded: wait for it, then
  // consume the request.
  const editRequest = useUIStore(s => s.propertiesEditRequest)
  useEffect(() => {
    if (editRequest?.type !== 'Bus' || !bus || editRequest.name !== bus.name) return
    if (!editing) startEdit()
    useUIStore.getState().clearPropertiesEditRequest()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editRequest, bus])

  const updateMut = useMutation({
    // Chokepoint (utils/assetWrite.ts) owns fetch/spread/PUT/invalidate.
    // The PUT's response passes through it — the rescale preview below
    // depends on that (Finding 1, 2026-07-31 review).
    mutationFn: async () => {
      if (!bus) throw new Error('Bus not found')
      const newName = form.name?.trim() || bus.name
      const resp = await updateAsset<Bus>(
        qc, useUIStore.getState().currentProject, 'buses', bus.name,
        (current) => {
          // The descriptors own the typed fields' patch; a blank sub-network saves "" (the
          // "region" clustering mode then determines it from the topology).
          const payload = buildPatch(specs, form, current as unknown as Record<string, unknown>) as Partial<Bus>
          payload.name = newName
          payload.carrier = form.carrier || current.carrier
          Object.assign(payload, ehBusPayload(form, current))
          Object.assign(payload, extrasPatch(form, loadExtras(editScope('Bus'))))
          return payload
        })
      // updateBus's typed response — the chokepoint is row-agnostic, so the
      // shape is reasserted where it is consumed.
      return resp as { name: string; rescale: RescalePreview[] }
    },
    onSuccess: (data) => {
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
    onError: (e: Error) => toast.error(`Update failed: ${blockerMessage(e)}`),
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
          {/* The schema's non-identity fields (the nominal voltage), with their provenance chips. */}
          {assetReadoutItems(schema, bus as unknown as Record<string, unknown>, undefined, provenance).map(i => (
            <Row key={i.label} label={i.label} value={i.value} unit={i.unit} tip={i.tip} badge={i.badge} />
          ))}
          <Row label="Carrier"     value={bus.carrier}  tip={fieldHelp(schema, 'carrier')} />
          <Row label="Control"     value={bus.control}  tip={fieldHelp(schema, 'control')} />
          <Row label="Country"     value={bus.country}  tip={fieldHelp(schema, 'country')} />
          <Row label="Sub-network" value={bus.sub_network || '—'} tip={fieldHelp(schema, 'sub_network')} />
          {/* isPlaced (utils/geo, D4-compliant single import) — not a bare
              `bus.x != null` check. PyPSA's Bus.x/y default to 0.0, so an
              unplaced bus always has non-null x/y and the old check rendered
              "0.000, 0.000" as if it were a real position: this is exactly
              the surface the empty-state panel sends the user to ("paste a
              lat, lng pair into its properties"), so showing Null Island
              here undermines that guidance. */}
          <Row label="Coordinates" value={isPlaced(bus) ? `${bus.x?.toFixed(3)}, ${bus.y?.toFixed(3)}` : 'not set'} tip={docTip('bus.coordinates')} />
          <button onClick={startEdit} data-testid="props-edit-bus"
            className="w-full mt-2 py-1.5 border border-border rounded text-xs text-muted hover:border-accent hover:text-accent transition-colors flex items-center justify-center gap-1.5">
            <Pencil size={11} /> Edit Bus
          </button>
        </Section>
      )}
      {bus && editing && (
        <EditShell title={bus.name} onSave={() => updateMut.mutate()} onCancel={() => setEditing(false)} saving={updateMut.isPending}>
          {schema ? (
            <AssetFieldsEdit
              schema={schema} fs={form} set={setForm} current={bus as unknown as Record<string, unknown>}
              provenance={provenance} carriers={carriers as Carrier[]} buses={[]}
              // A "lat, lng" pair fills both coordinates at once, before the two fields.
              after={{ sub_network: <CoordPairInput fs={form} set={setForm} tip={docTip('bus.coordinates')} /> }}
            />
          ) : <p className="col-span-2 text-[10px] text-muted">Loading fields…</p>}
          <div className="col-span-2 flex items-end justify-between gap-2">
            <div className="flex-1"><SectionHdr title="Energy Hub" /></div>
            <GuideButton tourId="eh_tagging" testId="eh-bus-guide-button" label="How to tag" />
          </div>
          <EhBusInputs fs={form} set={setForm} />
          <ExtrasSection
            componentClass="Bus"
            fs={form}
            set={setForm}
            curated={Object.keys(form)}
          />
        </EditShell>
      )}

      <Section title="Capacity Summary">
        <Row label="Total generation" value={totalGen}  unit=" MW" />
        <Row label="Peak load"        value={totalLoad} unit=" MW" tip="Largest hourly load across the horizon (time-series aware)" />
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
export function LinePanel({ name }: { name: string }) {
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
  // S1c-2: the fields come from the asset schema (services/asset_schema/fields.py).
  // Investment is typed per MVA and km; the backend derives the annualised
  // capital cost and fixed O&M from it times the length. The topology stays
  // read-only here, and r/x/b keep their own per-km block (they scale with
  // the length too, but are stored as totals).
  const { data: schema } = useAssetSchema('Line')
  // U3: each field's provenance (default from the pack, customised, measured).
  const provenance = useAssetProvenance('Line', name)
  const specs = schemaFields(schema)
  const asset = (line ?? {}) as unknown as Record<string, unknown>

  const numField = (key: string, fallback: number | null | undefined): number => {
    const v = parseFloat(form[key]); return isNaN(v) ? (fallback ?? 0) : v
  }

  const updateMut = useMutation({
    // Chokepoint (utils/assetWrite.ts) owns fetch/spread/PUT/invalidate —
    // the spread keeps un-surfaced fields (`terrain_factor`, `num_parallel`,
    // `v_ang_min/max`, `committable`, …) alive through remove+add; the
    // schema's descriptors own the form→patch mapping.
    mutationFn: () => updateAsset<Line>(
      qc, useUIStore.getState().currentProject, 'lines', name,
      (current) => {
      if (!line) throw new Error('Line not found')
      const payload = buildPatch(specs, form, current as unknown as Record<string, unknown>) as Partial<Line>
      payload.name = form.name?.trim() || name
      // r/x/b are entered per km; PyPSA stores absolute Ohm / Siemens.
      // Multiply by the length being saved. Per-km 0 (or empty) ⇒ absolute 0.
      const lengthOut = payload.length ?? current.length
      payload.r = numField('r_per_km', 0) * lengthOut
      payload.x = numField('x_per_km', 0) * lengthOut
      payload.b = numField('b_per_km', 0) * lengthOut
      // Extras last: a value the user just typed in the extras picker wins.
      Object.assign(payload, extrasPatch(form, loadExtras(editScope('Line'))))
      return payload
    }),
    onSuccess: () => {
      setEditing(false)
      toast.success('Line updated')
      const newName = form.name?.trim() || name
      if (newName !== name) setSelectedComponent({ type: 'Line', name: newName })
    },
    onError: (e: Error) => toast.error(`Update failed: ${blockerMessage(e)}`),
  })

  if (!line) return <p className="p-3 text-xs text-muted">Loading…</p>

  // Derive per-km values from the stored absolute Ohm / Siemens via the
  // current length. Length=0 ⇒ leave per-km blank (avoid division by zero;
  // the user will set length first, then per-km values).
  const storedLength = Number.isFinite(line.length) && line.length > 0 ? line.length : 0
  const perKm = (v: number | null | undefined) =>
    storedLength > 0 && v != null && Number.isFinite(v) ? v / storedLength : null

  const startEdit = () => {
    const base = seedForm(specs, asset)
    base.lifetime = defaultLifetime(line.lifetime, globalLt)
    const km = (v: number | null | undefined) => { const p = perKm(v); return p === null ? '' : String(p) }
    setForm(seedExtras(line, { ...base, r_per_km: km(line.r), x_per_km: km(line.x), b_per_km: km(line.b) },
      loadExtras(editScope('Line'))))
    setEditing(true)
  }

  // A line priced the old way (one overnight cost per MVA, no per-km cost)
  // keeps that cost; the form says so, since the per-km cost replaces it.
  const legacyOvernight = line.overnight_cost != null && Number.isFinite(line.overnight_cost)
    && !(line.inv_per_km_overnight != null && Number.isFinite(line.inv_per_km_overnight))

  // r/x/b: per-km input + read-only computed total. PyPSA stores absolute
  // Ohm / Siemens, but the natural physical quantity is per km — so the
  // user enters r/x/b per km and we multiply by length on save. The total
  // is shown live so the user can sanity-check what PyPSA actually
  // receives. Length=0 falls back to "—" for the total.
  const impedanceInputs = () => {
    const L = numField('length', line.length)
    const fmtTotal = (k: string, unit: string) => {
      const v = parseFloat(form[k] ?? '')
      if (!Number.isFinite(v) || L <= 0) return `— ${unit}`
      return `= ${(v * L).toLocaleString(undefined, { maximumSignificantDigits: 6 })} ${unit}`
    }
    const row = (k: string, label: string, unit: string, total: string, tip?: string) => (
      <label key={k} className="col-span-2 flex flex-col gap-0.5">
        <span className="text-[10px] text-muted"><InfoTip text={tip}>{label} ({unit}/km)</InfoTip></span>
        <input className="bg-bg border border-border rounded px-2 py-1 text-xs font-mono text-text focus:outline-none focus:border-accent"
          type="number" step="any" value={form[k] ?? ''} aria-label={`${label} (${unit}/km)`}
          onChange={e => setForm(p => ({ ...p, [k]: e.target.value }))} />
        <span className="text-[10px] text-muted font-mono">{fmtTotal(k, total)}</span>
      </label>
    )
    return (
      <>
        {row('r_per_km', 'Resistance r', 'Ω', 'Ω', docTip('line.r'))}
        {row('x_per_km', 'Reactance x', 'Ω', 'Ω', docTip('line.x'))}
        {row('b_per_km', 'Susceptance b', 'S', 'S', docTip('line.b'))}
      </>
    )
  }

  const items = assetReadoutItems(schema, asset, undefined, provenance)

  return (
    <div className="p-3">
      <Section title="Topology">
        <Row label="From bus" value={line.bus0} tip={fieldHelp(schema, 'bus0')} />
        <Row label="To bus"   value={line.bus1} tip={fieldHelp(schema, 'bus1')} />
        <Row label="Carrier"  value={line.carrier} tip={fieldHelp(schema, 'carrier')} />
      </Section>
      {editing ? (
        <Section title="Edit Parameters">
          <div className="grid grid-cols-2 gap-x-2 gap-y-1.5 mb-2">
            {schema ? (
              <AssetFieldsEdit
                schema={schema} fs={form} set={setForm} current={asset} carriers={[]} buses={[]} provenance={provenance}
                hidden={LINE_TOPOLOGY}
                onCheck={{ s_nom_extendable: autofillNomMin('s_nom', 's_nom_min') }}
                after={{
                  length: impedanceInputs(),
                  s_nom_max: (
                    <div className="col-span-2 -mt-0.5">
                      <button type="button" onClick={() => setVintageOpen(true)}
                        className="text-[11px] text-accent hover:underline">
                        Per-period bounds…
                      </button>
                      <span className="ml-2 text-[10px] text-muted">override Min/Max per investment period</span>
                    </div>
                  ),
                  inv_per_km_overnight: legacyOvernight ? (
                    <p className="col-span-2 text-[10px] text-muted" data-testid="legacy-overnight-note">
                      Priced the old way: one overnight cost of {line.overnight_cost!.toLocaleString()} €/MVA.
                      Enter the cost per km to replace it.
                    </p>
                  ) : undefined,
                }}
              />
            ) : <p className="col-span-2 text-[10px] text-muted">Loading fields…</p>}
          </div>
          {vintageOpen && (
            <VintagePeriodBoundsModal componentClass="Line" name={line.name} onClose={() => setVintageOpen(false)} />
          )}
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
          <ExtrasSection
            componentClass="Line"
            fs={form}
            set={setForm}
            curated={Object.keys(form)}
          />
        </Section>
      ) : (
        <>
          <Section title="Parameters">
            {items.map(i => (
              <Row key={i.label} label={i.label} value={i.value} unit={i.unit} tip={i.tip} badge={i.badge} />
            ))}
            {/* Per-km + total pairs. PyPSA stores absolute Ohm / Siemens, but
                the natural physical quantity is per-km — show both so the user
                can sanity-check what's actually in the LP. */}
            <Row label="r (per km)"   value={perKm(line.r)} unit=" Ω/km" tip={docTip('line.r')} />
            <Row label="r (total)"    value={line.r ?? null} unit=" Ω" />
            <Row label="x (per km)"   value={perKm(line.x)} unit=" Ω/km" tip={docTip('line.x')} />
            <Row label="x (total)"    value={line.x ?? null} unit=" Ω" />
            <Row label="b (per km)"   value={perKm(line.b)} unit=" S/km" tip={docTip('line.b')} />
            <Row label="b (total)"    value={line.b ?? null} unit=" S" />
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

// The Line panel shows its ends and carrier read-only in "Topology".
const LINE_TOPOLOGY: ReadonlySet<string> = new Set(['bus0', 'bus1', 'carrier'])

// ── Transformer panel ──────────────────────────────────────────────────────────
export function TransformerPanel({ name }: { name: string }) {
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
  // S1c-2: the fields come from the asset schema (services/asset_schema/fields.py).
  // Investment is typed as the overnight cost per MVA; the annualised capital
  // cost is derived and shown read-only. The windings stay read-only here.
  const { data: schema } = useAssetSchema('Transformer')
  // U3: each field's provenance (default from the pack, customised, measured).
  const provenance = useAssetProvenance('Transformer', name)
  const specs = schemaFields(schema)
  const asset = (tr ?? {}) as unknown as Record<string, unknown>

  const updateMut = useMutation({
    // Chokepoint (utils/assetWrite.ts) owns fetch/spread/PUT/invalidate —
    // the spread keeps un-surfaced Transformer fields (`tap_position`,
    // `tap_side`, `num_parallel`, `model`, `g`, `b`, `active`, `s_max_pu`)
    // alive through the backend's remove+add cycle; the schema's descriptors
    // own the form→patch mapping. v_nom_0 / v_nom_1 always go back, so the
    // backend re-validates them against the connected buses on every save.
    mutationFn: () => updateAsset<Transformer>(
      qc, useUIStore.getState().currentProject, 'transformers', name,
      (current) => {
      if (!tr) throw new Error('Transformer not found')
      const payload = buildPatch(specs, form, current as unknown as Record<string, unknown>) as Partial<Transformer>
      payload.name = form.name?.trim() || name
      // Extras last: a value the user just typed in the extras picker wins.
      Object.assign(payload, extrasPatch(form, loadExtras(editScope('Transformer'))))
      return payload
    }),
    onSuccess: () => {
      setEditing(false)
      toast.success('Transformer updated')
      const newName = form.name?.trim() || name
      if (newName !== name) setSelectedComponent({ type: 'Transformer', name: newName })
    },
    onError: (e: Error) => toast.error(`Update failed: ${blockerMessage(e)}`),
  })

  if (!tr) return <p className="p-3 text-xs text-muted">Loading…</p>

  const startEdit = () => {
    const base = seedForm(specs, asset)
    base.lifetime = defaultLifetime(tr.lifetime, globalLt)
    setForm(seedExtras(tr, base, loadExtras(editScope('Transformer'))))
    setEditing(true)
  }

  const items = assetReadoutItems(schema, asset, TRANSFORMER_VOLTAGES, provenance)

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
          <div className="grid grid-cols-2 gap-x-2 gap-y-1.5 mb-2">
            {schema ? (
              <AssetFieldsEdit
                schema={schema} fs={form} set={setForm} current={asset} carriers={[]} buses={[]} provenance={provenance}
                hidden={TRANSFORMER_TOPOLOGY}
                onCheck={{ s_nom_extendable: autofillNomMin('s_nom', 's_nom_min') }}
                after={{
                  s_nom_max: (
                    <div className="col-span-2 -mt-0.5">
                      <button type="button" onClick={() => setVintageOpen(true)}
                        className="text-[11px] text-accent hover:underline">
                        Per-period bounds…
                      </button>
                      <span className="ml-2 text-[10px] text-muted">override Min/Max per investment period</span>
                    </div>
                  ),
                }}
              />
            ) : <p className="col-span-2 text-[10px] text-muted">Loading fields…</p>}
          </div>
          {vintageOpen && (
            <VintagePeriodBoundsModal componentClass="Transformer" name={tr.name} onClose={() => setVintageOpen(false)} />
          )}
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
          <ExtrasSection
            componentClass="Transformer"
            fs={form}
            set={setForm}
            curated={Object.keys(form)}
          />
        </Section>
      ) : (
        <>
          <Section title="Parameters">
            {items.map(i => (
              <Row key={i.label} label={i.label} value={i.value} unit={i.unit} tip={i.tip} badge={i.badge} />
            ))}
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

// The Transformer panel shows its windings' buses read-only in "Topology",
// and their declared voltages in "Voltage step" (the read-out skips them).
const TRANSFORMER_TOPOLOGY: ReadonlySet<string> = new Set(['bus0', 'bus1'])
const TRANSFORMER_VOLTAGES: ReadonlySet<string> = new Set(['v_nom_0', 'v_nom_1'])

// ── Link panel ─────────────────────────────────────────────────────────────────
function LinkPanel({ name }: { name: string }) {
  const { setSelectedComponent } = useUIStore()
  const currentProject = useUIStore(s => s.currentProject)
  const { data: links = [] } = useQuery({ queryKey: nk(currentProject, 'links'), queryFn: networkApi.getLinks })
  const link = (links as Link[]).find((l: Link) => l.name === name)
  const { data: schema } = useAssetSchema('Link')
  if (!link) return <p className="p-3 text-xs text-muted">Loading…</p>
  return (
    <div className="p-3">
      <Section title="Topology">
        <Row label="From bus" value={link.bus0} tip={fieldHelp(schema, 'bus0')} />
        <Row label="To bus"   value={link.bus1} tip={fieldHelp(schema, 'bus1')} />
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
  const { data: schema } = useAssetSchema('Generator')
  if (!gen) return <p className="p-3 text-xs text-muted">Loading…</p>
  return (
    <div className="p-3">
      <Section title="Connected to">
        <Row label="Bus" value={gen.bus} tip={fieldHelp(schema, 'bus')} />
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
  const { data: schema } = useAssetSchema('StorageUnit')
  if (!su) return <p className="p-3 text-xs text-muted">Loading…</p>
  return (
    <div className="p-3">
      <Section title="Connected to">
        <Row label="Bus" value={su.bus} tip={fieldHelp(schema, 'bus')} />
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
  const { data: schema } = useAssetSchema('Store')
  if (!store) return <p className="p-3 text-xs text-muted">Loading…</p>
  return (
    <div className="p-3">
      <Section title="Connected to">
        <Row label="Bus" value={store.bus} tip={fieldHelp(schema, 'bus')} />
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
  const { data: schema } = useAssetSchema('Load')
  if (!load) return <p className="p-3 text-xs text-muted">Loading…</p>
  return (
    <div className="p-3">
      <Section title="Connected to">
        <Row label="Bus" value={load.bus} tip={fieldHelp(schema, 'bus')} />
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
    onError: (e: Error) => toast.error(blockerMessage(e)),
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
    Store: 'Store', Load: 'Load', Carrier: 'Carrier',
  }

  const displayName = selectedComponent.type === 'AssetGroup'
    ? selectedComponent.name.split('::')[0]
    : selectedComponent.name

  const canDelete = selectedComponent.type === 'Bus' || selectedComponent.type === 'Line' || selectedComponent.type === 'Link' || selectedComponent.type === 'Transformer'
  // AssetGroup is a synthetic bus::category grouping, not a real network
  // component — it has no entry in Asset Detail's results registry.
  // A Carrier is not an asset either (M5b).
  const canViewResults = selectedComponent.type !== 'AssetGroup' && selectedComponent.type !== 'Carrier'

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
        {selectedComponent.type === 'Carrier'     && <CarrierPanel     key={selectedComponent.name} name={selectedComponent.name} />}
      </div>
    </aside>
  )
}

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { DataGrid } from '../components/DataGrid'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { updateAsset } from '../utils/assetWrite'
import type { Generator, StorageUnit, Store } from '../api/types'
import toast from 'react-hot-toast'

const TABS = ['Generators', 'Storage Units', 'Stores'] as const
type Tab = typeof TABS[number]

// ── Carrier chips ─────────────────────────────────────────────────────────────
function CarrierChips({ data }: { data: { carrier: string; p_nom?: number }[] }) {
  const grouped: Record<string, { count: number; total: number }> = {}
  data.forEach(d => {
    if (!grouped[d.carrier]) grouped[d.carrier] = { count: 0, total: 0 }
    grouped[d.carrier].count++
    grouped[d.carrier].total += d.p_nom ?? 0
  })
  if (Object.keys(grouped).length === 0) return null
  return (
    <div className="flex flex-wrap gap-1.5 px-3 py-2 border-b border-border">
      {Object.entries(grouped).map(([carrier, { count, total }]) => (
        <span key={carrier} className="flex items-center gap-1 bg-panel border border-border rounded-full px-2 py-0.5 text-xs">
          <span className="w-2 h-2 rounded-full bg-accent" />
          {carrier} · {count} · {total.toFixed(0)} MW
        </span>
      ))}
    </div>
  )
}

// ── Generator tab ─────────────────────────────────────────────────────────────
const genHelper = createColumnHelper<Generator>()
const genCols = [
  genHelper.accessor('name', { header: 'Name', size: 160 }),
  genHelper.accessor('bus', { header: 'Bus', size: 120 }),
  genHelper.accessor('carrier', { header: 'Carrier', size: 100 }),
  genHelper.accessor('p_nom', { header: 'p_nom (MW)', size: 110 }),
  genHelper.accessor('p_nom_extendable', { header: 'Extendable', size: 90, cell: i => i.getValue() ? '✓' : '' }),
  genHelper.accessor('marginal_cost', { header: 'MC (€/MWh)', size: 110 }),
  genHelper.accessor('capital_cost', { header: 'CC (€/MW)', size: 110 }),
  genHelper.accessor('efficiency', { header: 'η', size: 70 }),
]

function GeneratorsTab() {
  const qc = useQueryClient()
  const { setSelectedComponent } = useUIStore()
  const currentProject = useUIStore(s => s.currentProject)
  const { data: gens = [], isLoading } = useQuery({ queryKey: nk(currentProject, 'generators'), queryFn: networkApi.getGenerators })
  const del = useMutation({
    mutationFn: (g: Generator) => networkApi.deleteGenerator(g.name),
    onSuccess: () => qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'generators') }),
    onError: () => toast.error('Failed to delete generator'),
  })
  const update = useMutation({
    // The Asset-write chokepoint (utils/assetWrite.ts) owns fetch, spread,
    // PUT and invalidation — the spread keeps every field the grid doesn't
    // show (`marginal_cost`, `efficiency`, `capital_cost`, `committable`, …)
    // alive through the backend's remove+add cycle. The old
    // throw-on-cache-miss is gone: the chokepoint fetches on a miss
    // (ruling 3), so the cold path succeeds instead of erroring.
    mutationFn: ({ name, field, value }: { name: string; field: string; value: unknown }) =>
      updateAsset(qc, useUIStore.getState().currentProject, 'generators', name,
        { [field]: value }),
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Failed to update generator'),
  })

  return (
    <>
      <CarrierChips data={gens as { carrier: string; p_nom?: number }[]} />
      <div className="flex-1 overflow-hidden">
        <DataGrid
          columns={genCols as Parameters<typeof DataGrid>[0]['columns']}
          data={gens as unknown as Record<string, unknown>[]}
          isLoading={isLoading}
          onDelete={(row) => del.mutate(row as unknown as Generator)}
          onUpdate={(rowIdx, colId, value) => {
            const gen = gens[rowIdx]
            if (gen) update.mutate({ name: gen.name, field: colId, value })
          }}
        />
      </div>
    </>
  )
}

// ── Storage Units tab ─────────────────────────────────────────────────────────
const suHelper = createColumnHelper<StorageUnit>()
const suCols = [
  suHelper.accessor('name', { header: 'Name', size: 160 }),
  suHelper.accessor('bus', { header: 'Bus', size: 120 }),
  suHelper.accessor('carrier', { header: 'Carrier', size: 100 }),
  suHelper.accessor('p_nom', { header: 'p_nom (MW)', size: 110 }),
  suHelper.accessor('max_hours', { header: 'Max hours', size: 90 }),
  suHelper.accessor('efficiency_store', { header: 'η store', size: 80 }),
  suHelper.accessor('efficiency_dispatch', { header: 'η dispatch', size: 90 }),
  suHelper.accessor('capital_cost', { header: 'CC (€/MW)', size: 110 }),
]

function StorageUnitsTab() {
  const qc = useQueryClient()
  const currentProject = useUIStore(s => s.currentProject)
  const { data: sus = [], isLoading } = useQuery({ queryKey: nk(currentProject, 'storage_units'), queryFn: networkApi.getStorageUnits })
  const del = useMutation({
    mutationFn: (s: StorageUnit) => networkApi.deleteStorageUnit(s.name),
    onSuccess: () => qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'storage_units') }),
  })

  return (
    <div className="flex-1 overflow-hidden">
      <DataGrid
        columns={suCols as Parameters<typeof DataGrid>[0]['columns']}
        data={sus as unknown as Record<string, unknown>[]}
        isLoading={isLoading}
        onDelete={(row) => del.mutate(row as unknown as StorageUnit)}
      />
    </div>
  )
}

// ── Stores tab ────────────────────────────────────────────────────────────────
const stHelper = createColumnHelper<Store>()
const stCols = [
  stHelper.accessor('name', { header: 'Name', size: 160 }),
  stHelper.accessor('bus', { header: 'Bus', size: 120 }),
  stHelper.accessor('carrier', { header: 'Carrier', size: 100 }),
  stHelper.accessor('e_nom', { header: 'e_nom (MWh)', size: 110 }),
  stHelper.accessor('e_nom_extendable', { header: 'Extendable', size: 90, cell: i => i.getValue() ? '✓' : '' }),
  stHelper.accessor('capital_cost', { header: 'CC (€/MWh)', size: 110 }),
  stHelper.accessor('marginal_cost', { header: 'MC (€/MWh)', size: 110 }),
]

function StoresTab() {
  const qc = useQueryClient()
  const currentProject = useUIStore(s => s.currentProject)
  const { data: stores = [], isLoading } = useQuery({ queryKey: nk(currentProject, 'stores'), queryFn: networkApi.getStores })
  const del = useMutation({
    mutationFn: (s: Store) => networkApi.deleteStore(s.name),
    onSuccess: () => qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'stores') }),
  })

  return (
    <div className="flex-1 overflow-hidden">
      <DataGrid
        columns={stCols as Parameters<typeof DataGrid>[0]['columns']}
        data={stores as unknown as Record<string, unknown>[]}
        isLoading={isLoading}
        onDelete={(row) => del.mutate(row as unknown as Store)}
      />
    </div>
  )
}

export default function GenerationStack() {
  const [tab, setTab] = useState<Tab>('Generators')

  return (
    <div className="flex flex-col h-full">
      {/* Tab bar */}
      <div className="flex border-b border-border px-3 pt-2 shrink-0">
        {TABS.map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm -mb-px border-b-2 transition-colors
              ${tab === t ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-text'}`}
          >
            {t}
          </button>
        ))}
      </div>

      <div className="flex flex-col flex-1 overflow-hidden">
        {tab === 'Generators' && <GeneratorsTab />}
        {tab === 'Storage Units' && <StorageUnitsTab />}
        {tab === 'Stores' && <StoresTab />}
      </div>
    </div>
  )
}

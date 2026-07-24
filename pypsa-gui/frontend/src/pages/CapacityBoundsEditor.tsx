import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Layers, Settings2 } from 'lucide-react'
import toast from 'react-hot-toast'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import type { Generator, Line, Link, StorageUnit, Store, Transformer } from '../api/types'
import VintagePeriodBoundsModal from '../components/VintagePeriodBoundsModal'

// ── Capacity bounds editor ────────────────────────────────────────────────────
// Single page where every extendable asset across the network can have its
// capacity bounds parametrised in one of two modes:
//   • single — one (min, max) pair across the whole horizon (PyPSA's
//              native attribute layout)
//   • per-period — one (min, max) pair per investment period, applied via
//                  the vintage-row expansion in services/vintage_service
// The per-period mode uses the same `VintagePeriodBoundsModal` opened from
// the per-asset Properties panel; the editor wraps it so the user doesn't
// have to navigate to each asset individually.
//
// Layout: one section per supported component class with a small table of
// (name, carrier/buses, mode pill, current Min, current Max, Edit). The
// mode pill flips between "Single" and "Per-period (N)"; clicking opens the
// modal for per-period, and the inline Min/Max inputs are editable in
// single mode (PATCH /_bulk on blur).

// Supported component classes — must match SUPPORTED_COMPONENTS in
// services/vintage_service. The (label, attr, pnomField, unit) tuple drives
// the per-section UI without per-class branches.
const COMPONENT_SECTIONS = [
  { class: 'Generator',    label: 'Generators',     attr: 'generators',    pnom: 'p_nom', unit: 'MW' },
  { class: 'StorageUnit',  label: 'Storage units',  attr: 'storage_units', pnom: 'p_nom', unit: 'MW' },
  { class: 'Store',        label: 'Stores',         attr: 'stores',        pnom: 'e_nom', unit: 'MWh' },
  { class: 'Link',         label: 'Links',          attr: 'links',         pnom: 'p_nom', unit: 'MW' },
  { class: 'Line',         label: 'Lines',          attr: 'lines',         pnom: 's_nom', unit: 'MVA' },
  { class: 'Transformer',  label: 'Transformers',   attr: 'transformers',  pnom: 's_nom', unit: 'MVA' },
] as const

interface AssetRow {
  name: string
  carrier: string
  initial: number  // p_nom / s_nom / e_nom — existing capacity
  min: number
  max: number | null
  // Multi-period mode summary — `null` when this asset has no per-period
  // bounds saved, otherwise the count of periods configured.
  periodCount: number | null
}

export default function CapacityBoundsEditor() {
  const qc = useQueryClient()
  const currentProject = useUIStore(s => s.currentProject)
  // Per-component-class queries. Each fetches the static rows from the
  // existing network endpoints; we filter to extendable-only client-side.
  const { data: generators = [] }   = useQuery({ queryKey: nk(currentProject, 'generators'),    queryFn: networkApi.getGenerators })
  const { data: storageUnits = [] } = useQuery({ queryKey: nk(currentProject, 'storage_units'), queryFn: networkApi.getStorageUnits })
  const { data: stores = [] }       = useQuery({ queryKey: nk(currentProject, 'stores'),        queryFn: networkApi.getStores })
  const { data: links = [] }        = useQuery({ queryKey: nk(currentProject, 'links'),         queryFn: networkApi.getLinks })
  const { data: lines = [] }        = useQuery({ queryKey: nk(currentProject, 'lines'),         queryFn: networkApi.getLines })
  const { data: transformers = [] } = useQuery({ queryKey: nk(currentProject, 'transformers'),  queryFn: networkApi.getTransformers })
  const { data: vintageBounds }     = useQuery({ queryKey: nk(currentProject, 'vintageBounds'), queryFn: networkApi.listVintageBounds })
  const { data: periodInfo }        = useQuery({ queryKey: nk(currentProject, 'investmentPeriods'), queryFn: networkApi.getInvestmentPeriods })

  const periodCount = (componentClass: string, name: string): number | null => {
    const entry = vintageBounds?.bounds?.[componentClass]?.[name]
    if (!entry || Object.keys(entry).length === 0) return null
    return Object.keys(entry).length
  }

  // Build per-section rows. Each row is "extendable" (gate at section level
  // via the `<class>_extendable` field) plus the relevant min/max values.
  // Stored as a record keyed by component class so the rendering loop
  // doesn't need per-class branches.
  const rowsByClass = useMemo<Record<string, AssetRow[]>>(() => {
    const out: Record<string, AssetRow[]> = {}
    // Vintage rows produced by `apply_vintage_bounds` follow the
    // `{parent_name}@{year}` naming convention. They should be transient —
    // dropped during `_capture_and_drop_vintages` on restore — but if a
    // solve crashed mid-flow or the user is loading a project saved while
    // vintages still existed, they leak through here and clutter the
    // editor. Filter them out: per-period bounds belong on the PARENT row,
    // not on the synthetic vintage rows.
    const isVintageName = (name: string) => /@\d{4}$/.test(name)
    const pull = <T extends { name: string; carrier?: string }>(
      cls: string, pnom: string, items: T[],
    ) => {
      const ext = `${pnom}_extendable`
      const init = pnom
      const min = `${pnom}_min`
      const max = `${pnom}_max`
      const rows: AssetRow[] = []
      for (const item of items) {
        if (isVintageName(item.name)) continue
        const rec = item as unknown as Record<string, unknown>
        if (rec[ext] !== true) continue
        rows.push({
          name: item.name,
          carrier: item.carrier ?? '',
          initial: typeof rec[init] === 'number' ? rec[init] as number : 0,
          min: typeof rec[min] === 'number' ? rec[min] as number : 0,
          max: typeof rec[max] === 'number' ? rec[max] as number : null,
          periodCount: periodCount(cls, item.name),
        })
      }
      out[cls] = rows
    }
    pull('Generator', 'p_nom', generators as Generator[])
    pull('StorageUnit', 'p_nom', storageUnits as StorageUnit[])
    pull('Store', 'e_nom', stores as Store[])
    pull('Link', 'p_nom', links as Link[])
    pull('Line', 's_nom', lines as Line[])
    pull('Transformer', 's_nom', transformers as Transformer[])
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generators, storageUnits, stores, links, lines, transformers, vintageBounds])

  const totalCount = useMemo(
    () => Object.values(rowsByClass).reduce((s, rs) => s + rs.length, 0),
    [rowsByClass],
  )

  // Count orphan vintage rows that leaked into the static frames. The
  // cleanup endpoint pattern-matches the `{parent}@{4-digit-year}` naming
  // convention used by `apply_vintage_bounds`, so the count here uses the
  // same heuristic for the banner.
  const orphanVintageCount = useMemo(() => {
    const isVintage = (name: string) => /@\d{4}$/.test(name)
    const lists: Array<{ name: string }[]> = [
      generators as { name: string }[],
      storageUnits as { name: string }[],
      stores as { name: string }[],
      links as { name: string }[],
      lines as { name: string }[],
      transformers as { name: string }[],
    ]
    return lists.reduce((n, arr) => n + arr.filter(x => isVintage(x.name)).length, 0)
  }, [generators, storageUnits, stores, links, lines, transformers])

  const cleanupMut = useMutation({
    mutationFn: () => networkApi.cleanupOrphanVintages(),
    onSuccess: (data) => {
      const total = data.total_removed
      if (total === 0) {
        toast.success('No orphan vintages found.')
      } else {
        toast.success(`Removed ${total} orphan vintage row(s) across ${Object.keys(data.by_class).length} class(es).`)
      }
      // Refresh every component query so the editor (and any other view)
      // sees the cleaned network state.
      const proj = useUIStore.getState().currentProject
      qc.invalidateQueries({ queryKey: nk(proj, 'generators') })
      qc.invalidateQueries({ queryKey: nk(proj, 'storage_units') })
      qc.invalidateQueries({ queryKey: nk(proj, 'stores') })
      qc.invalidateQueries({ queryKey: nk(proj, 'links') })
      qc.invalidateQueries({ queryKey: nk(proj, 'lines') })
      qc.invalidateQueries({ queryKey: nk(proj, 'transformers') })
    },
    onError: (err: Error) => toast.error(err.message || 'Cleanup failed'),
  })

  const hasPeriods = (periodInfo?.periods?.length ?? 0) > 0

  // Modal state — null when closed, otherwise the (class, name) to open it for.
  const [modalFor, setModalFor] = useState<{ cls: string; name: string } | null>(null)

  if (totalCount === 0) {
    return (
      <div className="p-6 text-center text-xs text-muted">
        No extendable assets in this network. Mark assets <code>p_nom_extendable=True</code>{' '}
        (or <code>s_nom</code>/<code>e_nom</code>) in the Properties panel first.
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5 p-5 overflow-y-auto h-full text-sm">
      <div className="text-[11.5px] text-muted -mt-2 leading-relaxed">
        Every extendable asset is listed here. Each can use a <span className="font-medium">single</span>{' '}
        min/max pair across the whole horizon, or <span className="font-medium">per-period</span> bounds
        applied via the vintage-row expansion at solve time (one decision variable per investment period).
        Single-mode min/max edits save on blur; per-period edits open the dedicated modal.
        {!hasPeriods && (
          <span className="ml-2 text-warn">
            No investment periods configured — per-period mode is disabled until you promote snapshots
            to multi-period under Snapshots → Multi-period.
          </span>
        )}
      </div>

      {orphanVintageCount > 0 && (
        <div className="rounded-md border border-warn/40 bg-warn/5 px-3 py-2 flex items-start gap-2">
          <AlertTriangle size={14} className="text-warn shrink-0 mt-0.5" />
          <div className="flex-1">
            <div className="text-[12px] font-semibold text-warn">
              {orphanVintageCount} orphan vintage row{orphanVintageCount === 1 ? '' : 's'} detected
            </div>
            <div className="text-[11px] text-muted mt-0.5 leading-relaxed">
              These rows follow the <code>{'{parent}@{year}'}</code> naming convention and
              should have been dropped at the end of the last solve. They're hidden from
              this editor but still live in the network — they will inflate next solve's LP
              and clutter results. Click to remove them.
            </div>
          </div>
          <button
            onClick={() => cleanupMut.mutate()}
            disabled={cleanupMut.isPending}
            className="self-center px-3 py-1 text-[11.5px] bg-warn text-white rounded font-medium hover:opacity-90 disabled:opacity-40"
          >
            {cleanupMut.isPending ? 'Cleaning…' : 'Clean up'}
          </button>
        </div>
      )}

      {COMPONENT_SECTIONS.map(section => {
        const rows = rowsByClass[section.class] ?? []
        if (rows.length === 0) return null
        return (
          <ClassSection
            key={section.class}
            section={section}
            rows={rows}
            onOpenModal={(name) => setModalFor({ cls: section.class, name })}
            hasPeriods={hasPeriods}
          />
        )
      })}

      {modalFor && (
        <VintagePeriodBoundsModal
          componentClass={modalFor.cls}
          name={modalFor.name}
          onClose={() => setModalFor(null)}
        />
      )}
    </div>
  )
}

function ClassSection({
  section, rows, onOpenModal, hasPeriods,
}: {
  section: typeof COMPONENT_SECTIONS[number]
  rows: AssetRow[]
  onOpenModal: (name: string) => void
  hasPeriods: boolean
}) {
  return (
    <section>
      <h3 className="text-[12.5px] font-semibold text-text mb-2.5 flex items-center gap-2 tracking-[-0.005em]">
        <Settings2 size={13} className="text-muted" />
        {section.label}
        <span className="text-[10px] font-normal text-muted">({rows.length} extendable)</span>
      </h3>
      <div className="border border-border rounded overflow-hidden">
        <table className="w-full text-xs border-collapse">
          <thead className="bg-bg-2">
            <tr className="border-b border-border">
              <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Name</th>
              <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Carrier</th>
              <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase w-24">Existing</th>
              <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase w-28">Mode</th>
              <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase w-28">Min</th>
              <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase w-28">Max</th>
              <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase w-20"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <AssetRowEditor
                key={row.name}
                section={section}
                row={row}
                striped={i % 2 === 1}
                onOpenModal={() => onOpenModal(row.name)}
                hasPeriods={hasPeriods}
              />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function AssetRowEditor({
  section, row, striped, onOpenModal, hasPeriods,
}: {
  section: typeof COMPONENT_SECTIONS[number]
  row: AssetRow
  striped: boolean
  onOpenModal: () => void
  hasPeriods: boolean
}) {
  const qc = useQueryClient()
  const isPerPeriod = row.periodCount !== null && row.periodCount > 0
  // Line/Transformer get a different treatment at solve time: per-period
  // entries are SUMMED into a single horizon-wide bound (single extendable
  // line instead of parallel vintage rows). The UI still lets users enter
  // per-period values — they're aggregated by the backend. This flag
  // controls the mode-pill label and tooltip so the user understands the
  // collapsing behavior.
  const isTransmission = section.class === 'Line' || section.class === 'Transformer'

  // Force a fresh DOM element when the server-side value changes so the
  // `defaultValue`-based input picks up the new figure after a refetch.
  // Matches the bulk-edit refresh pattern documented in CLAUDE.md.
  const inputKey = (col: 'min' | 'max') =>
    `${row.name}-${col}-${col === 'min' ? row.min : row.max ?? ''}`

  const bulkMut = useMutation({
    mutationFn: (updates: Record<string, number>) =>
      networkApi.bulkUpdate({
        component_class: section.class,
        names: [row.name],
        updates,
      }),
    onSuccess: () => {
      // Refresh the relevant component query so the row re-renders with
      // the new min/max coming from the server.
      qc.invalidateQueries({ queryKey: [section.attr] })
    },
    onError: (err: Error) => toast.error(err.message || 'Save failed'),
  })

  const clearVintageMut = useMutation({
    mutationFn: () => networkApi.deleteVintageBounds(section.class, row.name),
    onSuccess: () => {
      const proj = useUIStore.getState().currentProject
      qc.invalidateQueries({ queryKey: nk(proj, 'vintageBounds') })
      qc.invalidateQueries({ queryKey: nk(proj, 'vintage_results') })
      toast.success(`Cleared per-period bounds on ${row.name}`)
    },
    onError: (err: Error) => toast.error(err.message || 'Failed to clear'),
  })

  const onMinBlur = (raw: string) => {
    const v = raw.trim() === '' ? 0 : Number(raw)
    if (!Number.isFinite(v) || v === row.min) return
    bulkMut.mutate({ [`${section.pnom}_min`]: v })
  }
  const onMaxBlur = (raw: string) => {
    if (raw.trim() === '') return  // empty = leave unset (max = infinity)
    const v = Number(raw)
    if (!Number.isFinite(v) || v === row.max) return
    bulkMut.mutate({ [`${section.pnom}_max`]: v })
  }

  return (
    <tr className={striped ? 'bg-panel' : 'bg-bg'}>
      <td className="px-2 py-1 font-mono text-[11px]">{row.name}</td>
      <td className="px-2 py-1 text-[11px] text-muted">{row.carrier || '—'}</td>
      <td className="px-2 py-1 font-mono text-[11px] text-right">
        {row.initial.toFixed(0)} {section.unit}
      </td>
      <td className="px-2 py-1">
        {isPerPeriod ? (
          <span
            className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-accent/10 text-accent text-[10px] font-mono"
            title={
              isTransmission
                ? `Transmission expansion: per-period entries are SUMMED into a single horizon-wide bound at solve time. The LP picks ONE optimal corridor size for the whole horizon (no parallel vintage branches — those produce DC-PF flow-distribution distortion).`
                : `Each period gets its own extendable vintage row with these bounds.`
            }
          >
            <Layers size={10} />
            Per-period ({row.periodCount}{isTransmission ? ', summed' : ''})
          </span>
        ) : (
          <span className="text-[10px] text-muted font-mono">Single</span>
        )}
      </td>
      <td className="px-2 py-1 text-right">
        {isPerPeriod ? (
          <span className="text-[10px] text-muted italic">(see modal)</span>
        ) : (
          <input
            key={inputKey('min')}
            type="number"
            defaultValue={row.min}
            onBlur={e => onMinBlur(e.target.value)}
            className="w-20 px-1.5 py-0.5 border border-border rounded font-mono text-[11px] bg-bg text-right focus:outline-none focus:border-accent"
          />
        )}
      </td>
      <td className="px-2 py-1 text-right">
        {isPerPeriod ? (
          <span className="text-[10px] text-muted italic">(see modal)</span>
        ) : (
          <input
            key={inputKey('max')}
            type="number"
            defaultValue={row.max ?? ''}
            placeholder="∞"
            onBlur={e => onMaxBlur(e.target.value)}
            className="w-20 px-1.5 py-0.5 border border-border rounded font-mono text-[11px] bg-bg text-right focus:outline-none focus:border-accent"
          />
        )}
      </td>
      <td className="px-2 py-1 text-right whitespace-nowrap">
        {isPerPeriod ? (
          <>
            <button
              onClick={onOpenModal}
              className="text-[10.5px] text-accent hover:underline mr-2"
            >Edit</button>
            <button
              onClick={() => clearVintageMut.mutate()}
              disabled={clearVintageMut.isPending}
              className="text-[10.5px] text-muted hover:text-danger disabled:opacity-40"
              title="Drop per-period bounds and fall back to the single min/max above"
            >Single</button>
          </>
        ) : (
          <button
            onClick={onOpenModal}
            disabled={!hasPeriods}
            className="text-[10.5px] text-accent hover:underline disabled:opacity-40 disabled:no-underline"
            title={hasPeriods
              ? 'Configure per-period bounds for this asset'
              : 'Enable multi-period planning first (Snapshots → Multi-period)'}
          >Per-period…</button>
        )}
      </td>
    </tr>
  )
}

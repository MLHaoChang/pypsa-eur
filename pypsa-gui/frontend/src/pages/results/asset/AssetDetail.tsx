import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import toast from 'react-hot-toast'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { useResultsFilter } from '../filterContext'
import { downloadCSV, KPI, Seg } from '../shared'
import AssetCharts from './AssetCharts'
import AssetPicker from './AssetPicker'
import AssetTable, { tableRows } from './AssetTable'
import MetricChecklist from './MetricChecklist'
import { assetResultsApi, type AssetQueryParams } from './api'
import { loadSelection, reconcileSelection, saveSelection } from './selectionMemory'
import { CATEGORY_ORDER, type AssetRef, type Remedy, type ViewMode } from './types'

// NOTE on deep-linking (Task 13): the brief's original shell also read
// `assetDetailRequest` / `clearAssetDetailRequest` off `useUIStore` so other
// panels (PropertiesPanel, BottomPanel, map, chatbot) could jump straight to
// an asset/category/metric here. Those store fields do not exist yet — they
// are Task 13's job to add. Referencing them now would either fail typecheck
// (accessing a non-existent property) or require adding the slot to the
// shared `uiStore.ts` from within this task, which is out of this task's
// file scope and risks colliding with a concurrent Task 13 session editing
// the same file. This shell is fully usable without deep-linking — asset
// selection, category/metric/view state all work standalone — so the
// deep-link wiring is deferred to Task 13, which will add the store slot
// AND the effect that consumes it here.
export default function AssetDetail() {
  const currentProject = useUIStore(s => s.currentProject)
  const setSlidePanel = useUIStore(s => s.setSlidePanel)
  const setSelectedComponent = useUIStore(s => s.setSelectedComponent)
  const filter = useResultsFilter()

  const [asset, setAsset] = useState<AssetRef | null>(null)
  const [category, setCategory] = useState('dispatch')
  const [mode, setMode] = useState<ViewMode>('chronological')
  const [selected, setSelected] = useState<string[]>([])
  const [view, setView] = useState<'table' | 'chart'>('table')

  const { data: assets = [] } = useQuery({
    queryKey: nk(currentProject, 'assetResults', 'assets'),
    queryFn: assetResultsApi.listAssets,
  })

  // Auto-select so the pane is never empty on arrival.
  useEffect(() => {
    if (!asset && assets.length > 0) setAsset(assets[0])
  }, [assets, asset])

  const params: AssetQueryParams | null = asset && {
    componentClass: asset.class, name: asset.name, category, metrics: selected,
    source: 'lopf', fromIso: filter.fromIso, toIso: filter.toIso,
    period: filter.selectedPeriod, mode,
  }

  const { data } = useQuery({
    queryKey: nk(currentProject, 'assetResults', asset?.class, asset?.name,
                 category, selected.join(','), mode,
                 filter.fromIso, filter.toIso, filter.selectedPeriod),
    queryFn: () => assetResultsApi.get(params!),
    enabled: !!params,
    // The reconcile effect below changes `selected` (part of this key) the
    // instant the first fetch resolves — see comment there. Without this,
    // that in-flight refetch drops `data` to `undefined` for one render,
    // which flickers every category/metric UI back to its "loading" state
    // right after it was first shown. Keep the prior payload visible while
    // the new key's fetch is in flight; `isPlaceholderData` distinguishes
    // the two if a caller ever needs to.
    placeholderData: keepPreviousData,
  })

  // Reconcile the remembered tick-set the moment the backend tells us what is
  // actually available for THIS asset. Metrics that became blocked or n/a are
  // dropped silently — their reason is already on screen in the checklist.
  useEffect(() => {
    if (!data || !asset) return
    const next = reconcileSelection(loadSelection(asset.class, category), data.metrics)
    setSelected(prev =>
      prev.length && prev.every(id => next.includes(id) || data.metrics
        .some(m => m.id === id && m.status === 'ok')) ? prev : next)
  }, [data?.metrics, asset?.class, asset?.name, category])

  // A category that is not `ok` for the newly-picked asset falls back to the
  // first that is — summary at worst, which works even unsolved.
  useEffect(() => {
    if (!data) return
    const active = data.categories.find(c => c.id === category)
    if (active && active.status !== 'ok') {
      const fallback = data.categories.find(c => c.status === 'ok')
      if (fallback) setCategory(fallback.id)
    }
  }, [data?.categories, category])

  const toggle = (id: string) => {
    if (!asset) return
    const next = selected.includes(id)
      ? selected.filter(x => x !== id) : [...selected, id]
    setSelected(next)
    saveSelection(asset.class, category, next)
  }

  const onRemedy = (r: Remedy) => {
    if (r.action === 'run_simulation') setSlidePanel('simparams')
    else if (r.action === 'run_ac_pf') setSlidePanel('simparams')
    else if (r.action === 'open_properties' && asset) {
      setSelectedComponent({ type: asset.class, name: asset.name })
      setSlidePanel(null)
    }
  }

  const scalarCards = useMemo(() => {
    if (!data) return []
    return data.metrics
      .filter(m => m.kind === 'scalar' && selected.includes(m.id) && m.id in data.scalars)
      .map(m => ({ metric: m, value: data.scalars[m.id] }))
  }, [data, selected])

  const exportCsv = () => {
    if (!data || !asset) return
    const { header, rows } = tableRows(data)
    downloadCSV(`${asset.name}_${category}.csv`, header, rows)
    toast.success('Exported CSV')
  }

  return (
    <div className="flex h-full min-h-0">
      <div className="w-56 shrink-0"><AssetPicker
        assets={assets} selected={asset} onSelect={a => { setAsset(a); }} /></div>

      <div className="flex-1 min-w-0 flex flex-col">
        {/* Identity header */}
        <div className="shrink-0 px-3 py-2 border-b border-border">
          {asset ? (
            <div className="flex items-baseline gap-2">
              <span className="text-[13px] font-medium">{asset.name}</span>
              <span className="text-[11px] text-muted">
                {asset.class}{asset.carrier && ` · carrier ${asset.carrier}`}
                {asset.bus && ` · bus ${asset.bus}`}
              </span>
            </div>
          ) : <span className="text-[11px] text-muted">No assets in this network.</span>}
        </div>

        {/* Category strip — greyed entries carry their reason as a tooltip */}
        <div role="tablist" className="shrink-0 flex items-center gap-0 px-2
          border-b border-border overflow-x-auto">
          {CATEGORY_ORDER.map(id => {
            const c = data?.categories.find(x => x.id === id)
            const label = c?.label ?? id
            const disabled = !c || c.status !== 'ok'
            return (
              <button key={id} role="tab" disabled={disabled}
                aria-selected={category === id}
                title={disabled ? (c?.reason ?? 'loading…') : label}
                onClick={() => setCategory(id)}
                className={`h-8 px-2.5 text-[11px] whitespace-nowrap border-b-2 -mb-px
                  ${category === id ? 'border-accent text-accent' : 'border-transparent'}
                  ${disabled
                    ? `text-muted/50 cursor-not-allowed
                       ${c?.status === 'blocked' ? 'italic' : 'line-through decoration-border'}`
                    : 'text-muted hover:text-text'}`}
              >{label}</button>
            )
          })}
        </div>

        <div className="flex-1 min-h-0 flex">
          <div className="w-60 shrink-0 overflow-y-auto border-r border-border">
            {data && <MetricChecklist metrics={data.metrics} selected={selected}
              onToggle={toggle} onRemedy={onRemedy} />}
          </div>

          <div className="flex-1 min-w-0 flex flex-col">
            {/* Controls + exports */}
            <div className="shrink-0 flex items-center gap-2 px-2 py-1.5
              border-b border-border">
              <Seg value={view} onChange={setView}
                options={[{ value: 'table', label: 'Table' },
                          { value: 'chart', label: 'Chart' }]} />
              <Seg value={mode} onChange={setMode}
                options={[{ value: 'chronological', label: 'Chronological' },
                          { value: 'duration', label: 'Duration' },
                          { value: 'monthly', label: 'Monthly' }]} />
              <span className="flex-1" />
              <button onClick={exportCsv}
                className="flex items-center gap-1 text-[11px] text-muted hover:text-accent">
                <Download size={11} /> CSV
              </button>
              {params && (
                <>
                  <a href={assetResultsApi.exportXlsxUrl(params, 'view')} download
                    className="flex items-center gap-1 text-[11px] text-muted hover:text-accent">
                    <Download size={11} /> Export configured view
                  </a>
                  <a href={assetResultsApi.exportXlsxUrl(params, 'full')} download
                    className="flex items-center gap-1 text-[11px] text-muted hover:text-accent">
                    <Download size={11} /> Full asset report
                  </a>
                </>
              )}
            </div>

            {scalarCards.length > 0 && (
              <div className="shrink-0 flex flex-wrap gap-2 px-2 py-2 border-b border-border">
                {scalarCards.map(({ metric, value }) => (
                  <KPI key={metric.id} label={metric.label} unit={metric.unit}
                    hint={metric.formula}
                    value={typeof value === 'object' && value !== null
                      ? Object.entries(value).map(([k, v]) => `${k}: ${v}`).join('  ')
                      : String(value ?? '—')} />
                ))}
              </div>
            )}

            <div className="flex-1 min-h-0 flex flex-col">
              {data && (view === 'table'
                ? <AssetTable data={data} />
                : <AssetCharts data={data} />)}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

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

export default function AssetDetail() {
  const currentProject = useUIStore(s => s.currentProject)
  const setSlidePanel = useUIStore(s => s.setSlidePanel)
  const setSelectedComponent = useUIStore(s => s.setSelectedComponent)
  const assetDetailRequest = useUIStore(s => s.assetDetailRequest)
  const clearAssetDetailRequest = useUIStore(s => s.clearAssetDetailRequest)
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

  // Deep-link consumption (Task 13): Properties, the bottom asset table, the
  // map and the chatbot all funnel through `requestAssetDetail`, which sets
  // this request AND opens the Results tab on 'asset' in one atomic store
  // update (see uiStore.ts). This effect only has to map the request onto
  // AssetDetail's own local state. The request carries `{componentClass,
  // name}`, not a full AssetRef (no carrier/bus) — resolve it against the
  // already-fetched `assets` list so `asset` stays a real AssetRef. Declared
  // AFTER the auto-select effect above so it always wins the race when both
  // fire in the same commit (asset arrives at the same time as a request).
  useEffect(() => {
    if (!assetDetailRequest || assets.length === 0) return
    const match = assets.find(a => a.class === assetDetailRequest.componentClass
      && a.name === assetDetailRequest.name)
    if (match) {
      setAsset(match)
      if (assetDetailRequest.category) setCategory(assetDetailRequest.category)
      if (assetDetailRequest.mode) setMode(assetDetailRequest.mode)
      if (assetDetailRequest.metrics) setSelected(assetDetailRequest.metrics)
      if (assetDetailRequest.chart !== undefined) setView(assetDetailRequest.chart ? 'chart' : 'table')
    }
    clearAssetDetailRequest()
  }, [assetDetailRequest, assets])

  const params: AssetQueryParams | null = asset && {
    componentClass: asset.class, name: asset.name, category, metrics: selected,
    source: 'lopf', fromIso: filter.fromIso, toIso: filter.toIso,
    period: filter.selectedPeriod, mode,
  }

  const qResult = useQuery({
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
  const { data } = qResult
  // `isPlaceholderData` is true while a new query key is in flight and the
  // PREVIOUS payload is still on screen (see the comment on the query above).
  // The table and the CSV button both read `data`, so they stay in lockstep
  // with whatever is showing — correct by construction. The xlsx links below
  // are built from live `params`, which updates immediately on every asset /
  // category / mode change, ahead of `data`. Without gating on this flag, the
  // two xlsx links would point at the NEW selection while the table/CSV still
  // show the OLD one — three export affordances in one toolbar, disagreeing.
  const isPlaceholderData = qResult.isPlaceholderData

  // Reconcile the remembered tick-set the moment the backend tells us what is
  // actually available for THIS asset. Metrics that became blocked or n/a are
  // dropped silently — their reason is already on screen in the checklist.
  //
  // INVARIANT this relies on: `data.metrics` (what's available) varies only
  // with asset + category, NEVER with the `metrics`/`mode`/filter part of the
  // query key. That's why a deliberate untick made via `toggle()` survives
  // this effect re-running after the resulting refetch settles — `toggle()`
  // writes `selected` + `saveSelection()` synchronously, and the ids this
  // effect treats as "still ok" don't shrink just because the request that
  // just landed asked for a different `metrics` param. If the backend is ever
  // changed to scope the RETURNED `metrics` list to the requested selection
  // (e.g. only reporting on ticked ids instead of every id applicable to this
  // asset/category), this effect would start silently re-adding or dropping
  // ids based on what happened to be requested last, not what's actually
  // available — the guard above needs `data.metrics` to always be the FULL
  // applicability list, independent of what was ticked when the request went
  // out.
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
                  {/* The xlsx links are built from LIVE `params`, but `data` can be
                      the previous selection's payload while a new fetch is in
                      flight (that is what keepPreviousData buys). Serving the links
                      during that window would point them at a different selection
                      than the table and the CSV button are showing — three export
                      affordances in one toolbar, disagreeing. Disable them until
                      the payload catches up. */}
                  <a
                    href={isPlaceholderData ? undefined : assetResultsApi.exportXlsxUrl(params, 'view')}
                    download
                    aria-disabled={isPlaceholderData || undefined}
                    title={isPlaceholderData ? 'Refreshing — the workbook would not match the view' : undefined}
                    className={`flex items-center gap-1 text-[11px] ${isPlaceholderData
                      ? 'text-muted/40 pointer-events-none' : 'text-muted hover:text-accent'}`}
                  >
                    <Download size={11} /> Export configured view
                  </a>
                  <a
                    href={isPlaceholderData ? undefined : assetResultsApi.exportXlsxUrl(params, 'full')}
                    download
                    aria-disabled={isPlaceholderData || undefined}
                    title={isPlaceholderData ? 'Refreshing — the workbook would not match the view' : undefined}
                    className={`flex items-center gap-1 text-[11px] ${isPlaceholderData
                      ? 'text-muted/40 pointer-events-none' : 'text-muted hover:text-accent'}`}
                  >
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

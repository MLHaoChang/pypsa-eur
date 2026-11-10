import { useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import {
  memoryApi, errorWords, PROJECT_KIND_WORDS, type MemoryItem, type ProjectKind,
} from '../../../api/memory'
import { projectsApi } from '../../../api/projects'
import { useUIStore } from '../../../store/uiStore'
import { useMemoryStore } from '../../../store/memoryStore'
import type { AssetRef, MetricRow, ViewMode } from './types'

// "Save as default" on Asset Detail (assistant-memory plan §2.1, §7.2): the
// chart's metrics and view mode, saved as a `chart_spec@1` memory item for the
// asset's carrier, so Asset Detail opens that way in every project of this kind.

export const ASSET_VIEW_KEY = 'view:asset'
export const SERIES_MAX = 12

const MODE_TO_CHART: Record<ViewMode, string> = {
  chronological: 'line', duration: 'duration', monthly: 'bar',
}
const CHART_TO_MODE: Record<string, ViewMode> = {
  line: 'chronological', duration: 'duration', bar: 'monthly',
}
// Metric units as chart_spec@1's closed SERIES_UNITS. A unit outside it has no
// spec form, so that metric is left out of the saved default.
const SPEC_UNITS: Record<string, string> = {
  kW: 'kW', MW: 'MW', GW: 'GW', kWh: 'kWh', MWh: 'MWh', GWh: 'GWh', TWh: 'TWh',
  '%': '%', h: 'h', tCO2: 'tCO2', pu: '1', 'p.u.': '1', '': '1',
  EUR: 'currency', 'EUR/MWh': 'currency/MWh',
}

export interface AssetChartSettings {
  metrics: string[]
  mode: ViewMode
}

/** The series a default can carry: ticked, drawable now, with a spec unit. */
export function savableSeries(metrics: MetricRow[], selected: string[]): MetricRow[] {
  return metrics
    .filter(m => m.kind === 'series' && m.status === 'ok' && selected.includes(m.id)
      && m.unit in SPEC_UNITS)
    .slice(0, SERIES_MAX)
}

export function assetChartSpec(asset: AssetRef, series: MetricRow[], mode: ViewMode) {
  return {
    schema: 'chart_spec@1',
    view_id: 'asset',
    series: series.map(m => ({
      component_class: asset.class,
      metric: m.id,
      unit: SPEC_UNITS[m.unit],
    })),
    chart: { type: MODE_TO_CHART[mode] },
  }
}

export function conceptKeys(asset: AssetRef): string[] {
  return asset.carrier ? [ASSET_VIEW_KEY, `carrier:${asset.carrier}`] : [ASSET_VIEW_KEY]
}

/** The saved settings of a chart_spec item, when it is an Asset Detail
 * default for this asset's class; null otherwise. */
export function settingsOf(item: MemoryItem, asset: AssetRef): AssetChartSettings | null {
  const spec = (item.body as { spec?: Record<string, unknown> } | null)?.spec
  if (!spec || spec.view_id !== 'asset') return null
  const series = Array.isArray(spec.series) ? spec.series as Array<Record<string, unknown>> : []
  if (!series.length || series.some(s => s.component_class !== asset.class)) return null
  const mode = CHART_TO_MODE[String((spec.chart as { type?: unknown } | undefined)?.type)]
  if (!mode) return null
  return { metrics: series.map(s => String(s.metric)), mode }
}

/** The best saved default for this asset: one for this project kind over one
 * for any project, the user's own over the workspace's (the list arrives in
 * the store's ranking order). */
export function pickDefault(
  items: MemoryItem[], asset: AssetRef, kind: ProjectKind,
): { item: MemoryItem; settings: AssetChartSettings } | null {
  const keys = conceptKeys(asset)
  const fits = items.filter(it => it.kind === 'chart_spec' && it.status === 'confirmed'
    && keys.every(k => it.concept_keys.includes(k))
    && (it.applies_to === kind || it.applies_to == null))
  const ranked = [
    ...fits.filter(it => it.applies_to === kind && it.scope === 'user'),
    ...fits.filter(it => it.applies_to === kind && it.scope === 'org'),
    ...fits.filter(it => it.applies_to == null && it.scope === 'user'),
    ...fits.filter(it => it.applies_to == null && it.scope === 'org'),
  ]
  for (const item of ranked) {
    const settings = settingsOf(item, asset)
    if (settings) return { item, settings }
  }
  return null
}

/** The open project's kind; a project with no kind is capacity expansion. */
export function useProjectKind(): ProjectKind {
  const currentProject = useUIStore(s => s.currentProject)
  const { data: projects } = useQuery({
    queryKey: ['projects'], queryFn: projectsApi.list, staleTime: 10_000,
  })
  const open = projects?.find(p => p.id === currentProject || p.name === currentProject)
  const k = open?.project_kind
  return (k && k in PROJECT_KIND_WORDS ? k : 'capacity_expansion') as ProjectKind
}

/** Saved chart defaults; an unreachable memory API just means no defaults. */
export function useChartDefaults() {
  return useQuery({
    queryKey: ['memory', 'items', 'chart_spec', 'confirmed', 'all'],
    queryFn: () => memoryApi.list({ kind: 'chart_spec', status: 'confirmed' }),
    staleTime: 60_000,
    retry: false,
  })
}

/** Do the chart's savable series and mode equal the saved settings? Saved
 * metrics this asset cannot draw now are left out, as on opening. */
export function sameSettings(
  saved: AssetChartSettings, series: MetricRow[], mode: ViewMode, metrics: MetricRow[],
): boolean {
  if (saved.mode !== mode) return false
  const drawable = new Set(metrics.filter(m => m.status === 'ok').map(m => m.id))
  const want = new Set(saved.metrics.filter(id => drawable.has(id)))
  const have = new Set(series.map(m => m.id))
  return want.size === have.size && [...want].every(id => have.has(id))
}

/** Undo a save: a re-save that made a new version of the same default goes
 * back to the version before it; a first save is retired, so the previous
 * default (another scope's, or none) applies again (UX M6). */
async function undoSave(saved: MemoryItem): Promise<void> {
  if (saved.version > 1) {
    const { versions } = await memoryApi.versions(saved.id)
    const before = versions.find(v => v.version === saved.version - 1)
    if (before?.body) {
      await memoryApi.edit(saved.id, { body: before.body, summary: before.summary })
      return
    }
  }
  await memoryApi.act(saved.id, 'retire')
}

export function useSaveAssetDefault() {
  const qc = useQueryClient()
  return async (asset: AssetRef, series: MetricRow[], mode: ViewMode, kind: ProjectKind) => {
    const what = asset.carrier ? `Asset Detail · ${asset.carrier}` : 'Asset Detail'
    let saved: MemoryItem
    try {
      saved = await memoryApi.create({
        kind: 'chart_spec',
        body: { spec: assetChartSpec(asset, series, mode) },
        concept_keys: conceptKeys(asset),
        project_kind: kind,
        summary: `${what}: ${series.map(m => m.label).join(', ')} · ${mode}`.slice(0, 160),
      })
    } catch (err) {
      toast.error(`Default not saved: ${errorWords(err)}`)
      return
    }
    qc.invalidateQueries({ queryKey: ['memory'] })
    // UX M6: one status line that clears on its own; "›" separators.
    toast.success(t => (
      <span className="flex items-center gap-1.5" data-testid="asset-default-toast">
        <span>Saved as your default for {what}</span>
        <span aria-hidden="true">{' · '}</span>
        <button type="button" className="underline font-medium whitespace-nowrap"
          onClick={() => { toast.dismiss(t.id); useMemoryStore.getState().openMemory({ editId: saved.id }) }}>
          Edit ›
        </button>
        <span aria-hidden="true">{' · '}</span>
        <button type="button" className="underline font-medium whitespace-nowrap"
          data-testid="asset-default-undo"
          onClick={async () => {
            toast.dismiss(t.id)
            try {
              await undoSave(saved)
              qc.invalidateQueries({ queryKey: ['memory'] })
              toast.success(saved.version > 1 ? 'Previous default restored' : 'Default removed',
                { ariaProps: { role: 'status', 'aria-live': 'polite' } })
            } catch (err) {
              toast.error(`Undo failed: ${errorWords(err)}`)
            }
          }}>
          Undo
        </button>
      </span>
    ), { duration: 8000, ariaProps: { role: 'status', 'aria-live': 'polite' } })
  }
}

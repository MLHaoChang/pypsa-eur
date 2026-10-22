import type { CheckedChart as Chart } from '../utils/answerAssurance'
import { useUIStore } from '../store/uiStore'
import { openSectionInExpert } from '../layout/flowNavigation'
import { readableWindow } from '../utils/readableTime'

// The Results tab that draws the same data natively, per checked profile.
const NATIVE: Record<string, { tab: string; label: string }> = {
  'generator-dispatch/1': { tab: 'dispatch', label: 'Dispatch' },
  'storage-unit-dispatch/1': { tab: 'storage', label: 'Storage cycling' },
  'bus-price/1': { tab: 'prices', label: 'Prices' },
  'branch-loading/1': { tab: 'loadflow', label: 'Load Flow' },
}

// The Results panel on that tab, in the mode the user is in. Guided opens the
// panel itself (as ResultsCard and ImproveCard do), never the hub card, and
// stays Guided: Results shows an Expert-only tab there as its advanced tab.
// It opens on the chart's window, so the tab shows the data that was checked.
function openNative(tab: string, window: Chart['window']) {
  const ui = useUIStore.getState()
  if (ui.uiMode === 'guided') ui.setSlidePanel('results')
  else openSectionInExpert('results')
  ui.requestResultsTab(tab)
  if (window?.start && window.end) ui.requestResultsWindow({ start: window.start, end: window.end })
}

/** The checked chart's download and its footer (S10). Every line comes from the
 * receipt's series slots, never from the model's text. */
/** `entity`: the one asset the chart is about (the receipt's request scope), named in the not-drawn lines. */
export function CheckedChart({ chart, profile, entity, compact = false }: {
  chart: Chart; profile?: string; entity?: string; compact?: boolean
}) {
  const drawn = chart.series.filter(s => s.disposition === 'drawn')
  const notDrawn = chart.series.filter(s => s.disposition !== 'drawn')
  const native = profile ? NATIVE[profile] : undefined
  if (compact) {
    return chart.status === 'drawn' && chart.download_url
      ? <a data-testid="checked-chart-download" className="underline" href={chart.download_url}
        download={chart.filename ?? undefined}>Download chart</a>
      : null
  }
  return <div className="mt-2 text-[11px] border border-border rounded p-2" data-testid="checked-chart">
    {chart.status === 'drawn' && chart.download_url
      ? <a data-testid="checked-chart-download" className="underline font-medium" href={chart.download_url}
        download={chart.filename ?? undefined}>Download chart{chart.filename ? ` (${chart.filename})` : ''}</a>
      : <p className="font-medium">Chart not drawn{chart.title ? ` — ${chart.title}` : ''}</p>}
    {chart.status === 'drawn' && chart.window && <p className="mt-1" data-testid="checked-chart-footer">
      Chart drawn from checked evidence · {drawn.length} series · {readableWindow(chart.window.start, chart.window.end)}
    </p>}
    {notDrawn.length > 0 && <ul className="mt-1" aria-label="Not drawn">{notDrawn.map(s =>
      <li key={s.series}>Not drawn: {entity ? `${entity} ` : ''}{s.series.replace(/_/g, ' ')} — {s.title}</li>)}</ul>}
    {native && <button type="button" className="mt-1 underline" data-testid="checked-chart-native"
      onClick={() => openNative(native.tab, chart.window)}>
      Open in Results › {native.label}</button>}
  </div>
}

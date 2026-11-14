// The headline figures at the top of Results › Summary, per project type
// (ui-modern 03; owner decisions DASHBOARD-1, UI-MODERN-1). The backend picks
// the cards and reads every figure; nothing is computed here.
//
// One source line under the row says which solve the figures come from, and
// carries the one "Out of date" mark; the figures then dim (UX plan Q3). A
// card without a figure names its state and why, and LCOE links to the
// Investment case that produces it.
import { useQuery } from '@tanstack/react-query'
import { resultsApi, type HeadlineCard } from '../../api/simulation'
import Kpi from '../../components/Kpi'
import { Skeleton } from '../../components/Skeleton'
import { ChartSourceLine, ChartSourceProvider, useChartSource } from '../../components/charts/chartTheme'
import { showResultsTab } from '../../layout/flowNavigation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { Sparkline } from '../../components/PageKit'
import { HEADLINE_WORDS, comparisonBasis, headlineDelta, headlineFigure, headlineTrend } from './headlineWords'

export const headlineKpisKey = (project: string | null) => nk(project, 'results', 'headline_kpis')

/** The strip's query, shared with the Summary cards that must not repeat its figures. */
export function useHeadlineKpis() {
  const project = useUIStore(s => s.currentProject)
  return useQuery({ queryKey: headlineKpisKey(project), queryFn: resultsApi.getHeadlineKpis, retry: false })
}

const ROW = 'grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(170px,1fr))]'
// Every tile is as tall as the tallest state (label, pill, sentence and the tab link: 128px measured), so the row never jumps.
// A row with a trend line is taller (149px measured); its placeholder is too.
const TILE_MIN = 'min-h-[128px]'
const TILE_MIN_TREND = 'min-h-[149px]'
// The placeholder has as many tiles as the last row shown; before any, the shortest card set (`headline_kpis.py` ORDER; six with a data-centre load).
const TILES_KEY = 'pypsa-gui.headline-strip.tiles'
const TREND_KEY = 'pypsa-gui.headline-strip.trend'
function remembered(): { tiles: number; trend: boolean } {
  try {
    const n = Number(localStorage.getItem(TILES_KEY))
    return { tiles: n >= 1 && n <= 6 ? n : 4, trend: localStorage.getItem(TREND_KEY) === '1' }
  } catch { return { tiles: 4, trend: false } }
}
let loading = remembered()
function remember(tiles: number, trend: boolean) {
  loading = { tiles, trend }
  try {
    localStorage.setItem(TILES_KEY, String(tiles))
    localStorage.setItem(TREND_KEY, trend ? '1' : '0')
  } catch { /* the placeholder's shape is a nicety */ }
}

/** The figure over the investment periods: the line, with its first and last period in words (K6). */
function Trend({ card }: { card: HeadlineCard }) {
  const trend = headlineTrend(card)
  if (!trend) return null
  return (
    <div role="img" aria-label={trend.label} title={trend.basis} data-testid={`headline-${card.id}-trend`} className="flex flex-col gap-0.5">
      <div className="h-5" aria-hidden="true"><Sparkline data={trend.data} color="var(--color-series-1)" /></div>
      <div className="flex flex-wrap justify-between gap-x-2 text-[10.5px] leading-snug text-muted tabular-nums" aria-hidden="true">
        <span>{trend.first}</span><span>{trend.last}</span>
      </div>
    </div>
  )
}

function Tile({ card, dimmed, tall }: { card: HeadlineCard; dimmed: boolean; tall: boolean }) {
  const words = HEADLINE_WORDS[card.id]
  if (!words) return null
  const { value, basis } = headlineFigure(card)
  // "Out of date" is said once, on the source line; a card it leaves without a figure says why.
  const state = card.state === 'stale' ? 'unavailable' : card.state
  return (
    <div className={tall ? TILE_MIN_TREND : TILE_MIN}>
      <Kpi
        headline testId={`headline-${card.id}`} label={words.label} value={value} basis={basis}
        state={state} reason={card.reason ?? undefined} dimmed={dimmed}
        delta={headlineDelta(card)} trend={<Trend card={card} />}
        action={card.state === 'not_run' || (card.id === 'lcoe' && card.value == null && card.state !== 'running')
          ? { label: `${words.tabLabel} ›`, onClick: () => showResultsTab(words.tab, { from: 'Summary' }) }
          : undefined}
      />
    </div>
  )
}

export default function HeadlineStrip() {
  const source = useChartSource()
  const project = useUIStore(s => s.currentProject)
  const plain = useHeadlineKpis()
  const { isError, isLoading } = plain
  // The base case is read after the figures are on screen; until it answers, and if it fails, the strip is the plain one.
  const compared = useQuery({
    queryKey: [...headlineKpisKey(project), 'compare'], queryFn: resultsApi.getHeadlineComparison,
    retry: false, enabled: !!plain.data,
  })
  // After a new solve both are asked again: figures the plain fetch has since replaced are not shown
  // while the comparison is still on its way, nor after it failed.
  const current = !compared.isError && (!compared.isFetching || compared.dataUpdatedAt >= plain.dataUpdatedAt)
  const data = (current ? compared.data : undefined) ?? plain.data
  if (isLoading) {
    // The row's own grid and tile height, and the source line's, so nothing below moves when the figures arrive.
    return (
      <section aria-label="Headline figures" role="status" aria-busy="true" data-testid="headline-strip-loading"
        className="flex flex-col gap-1.5">
        <span className="sr-only">Loading the headline figures…</span>
        <div className={ROW}>
          {Array.from({ length: loading.tiles }, (_, i) => <Skeleton key={i} className={loading.trend ? TILE_MIN_TREND : TILE_MIN} />)}
        </div>
        <Skeleton className="h-3 w-1/3" />
      </section>
    )
  }
  if (isError) {
    return (
      <p role="status" data-testid="headline-strip-failed" className="text-[11.5px] text-muted">
        The headline figures could not be read. The cards below are unaffected.
      </p>
    )
  }
  const cards = data?.cards.filter(c => HEADLINE_WORDS[c.id]) ?? []
  if (!cards.length) return null
  const tall = cards.some(c => c.trend && c.value != null)
  if (cards.length !== loading.tiles || tall !== loading.trend) remember(cards.length, tall)
  const comparison = data?.comparison
  const stale = source.stale || cards.some(c => c.state === 'stale')
  return (
    <section aria-label="Headline figures" data-testid="headline-strip" className="flex flex-col gap-1.5">
      <div className={ROW}>
        {cards.map(card => <Tile key={card.id} card={card} dimmed={stale} tall={tall} />)}
      </div>
      {comparison && !comparison.comparable && comparison.reason && (
        <p role="note" data-testid="headline-not-compared" className="px-0.5 text-[11.5px] leading-snug text-muted">
          {comparison.reason}
        </p>
      )}
      <ChartSourceProvider value={{ ...source, stale }}>
        <ChartSourceLine className="px-0.5" basis={comparisonBasis(comparison)} />
      </ChartSourceProvider>
    </section>
  )
}

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
import { ChartSourceLine, ChartSourceProvider, useChartSource } from '../../components/charts/chartTheme'
import { showResultsTab } from '../../layout/flowNavigation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { HEADLINE_WORDS, headlineFigure } from './headlineWords'

export const headlineKpisKey = (project: string | null) => nk(project, 'results', 'headline_kpis')

/** The strip's query, shared with the Summary cards that must not repeat its figures. */
export function useHeadlineKpis() {
  const project = useUIStore(s => s.currentProject)
  return useQuery({ queryKey: headlineKpisKey(project), queryFn: resultsApi.getHeadlineKpis, retry: false })
}

const ROW = 'grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(170px,1fr))]'
// Every tile is as tall as the tallest state (label, figure, pill, sentence), so the row never jumps.
const TILE_MIN = 'min-h-[104px]'

function Tile({ card, dimmed }: { card: HeadlineCard; dimmed: boolean }) {
  const words = HEADLINE_WORDS[card.id]
  if (!words) return null
  const { value, basis } = headlineFigure(card)
  // "Out of date" is said once, on the source line; a card it leaves without a figure says why.
  const state = card.state === 'stale' ? 'unavailable' : card.state
  return (
    <div className={TILE_MIN}>
      <Kpi
        headline testId={`headline-${card.id}`} label={words.label} value={value} basis={basis}
        state={state} reason={card.reason ?? undefined} dimmed={dimmed}
        action={card.state === 'not_run'
          ? { label: `${words.tabLabel} ›`, onClick: () => showResultsTab(words.tab, { from: 'Summary' }) }
          : undefined}
      />
    </div>
  )
}

export default function HeadlineStrip() {
  const source = useChartSource()
  const { data, isError } = useHeadlineKpis()
  if (isError) {
    return (
      <p role="status" data-testid="headline-strip-failed" className="text-[11.5px] text-muted">
        The headline figures could not be read. The cards below are unaffected.
      </p>
    )
  }
  const cards = data?.cards.filter(c => HEADLINE_WORDS[c.id]) ?? []
  if (!cards.length) return null
  const stale = source.stale || cards.some(c => c.state === 'stale')
  return (
    <section aria-label="Headline figures" data-testid="headline-strip" className="flex flex-col gap-1.5">
      <div className={ROW}>
        {cards.map(card => <Tile key={card.id} card={card} dimmed={stale} />)}
      </div>
      <ChartSourceProvider value={{ ...source, stale }}>
        <ChartSourceLine className="px-0.5" />
      </ChartSourceProvider>
    </section>
  )
}

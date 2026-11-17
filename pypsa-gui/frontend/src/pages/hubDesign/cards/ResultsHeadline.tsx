// The Guided Results headline (guided-results 01; owner decisions UX-NEXT-1,
// VISUALS-1, EXPERT-GUIDED-1): the figures that answer "did it pass, and what is
// it worth", then one chart, reliability against the goal.
//
// Nothing is computed here. Reliability and the NPV are the chapter tiles' own
// reads (`useChapterTiles`), LCOE is the Expert headline strip's card
// (`/results/headline_kpis`), so a figure reads the same in Guided and Expert.
// Each figure keeps its own source and state; when some are current and others
// out of date, one line says which (GK B G1).
import Kpi from '../../../components/Kpi'
import type { ResultStateKind } from '../../../components/ResultState'
import { ChartFrame, ChartSourceProvider, CHART_TICK } from '../../../components/charts/chartTheme'
import { showResultsTab } from '../../../layout/flowNavigation'
import type { HeadlineCard } from '../../../api/simulation'
import { HEADLINE_WORDS, headlineFigure } from '../../results/headlineWords'
import { useHeadlineKpis } from '../../results/HeadlineStrip'
import { FROM_STUDY_HINT, type ChapterInputs, type ChapterOpen, type ChapterTile } from '../chapters'
import { useChapterTiles } from '../ChaptersSection'
import { TABS } from '../../Results'
import { useHubDesignStore } from '../hubDesignStore'

/** The chapter tiles whose figure the headline shows; the tiles below then leave the number out. */
export const HEADLINED = ['reliability', 'investment'] as const

/** One figure of the headline, as the mixed-source line needs it. */
export interface HeadlineFigureState { label: string; state: ResultStateKind }

/** "Not all figures describe the current design: …" when some are current and some are out of date. */
export function mixedSourceNote(figures: HeadlineFigureState[]): string | null {
  const stale = figures.filter(f => f.state === 'stale').map(f => f.label)
  const ready = figures.some(f => f.state === 'ready')
  if (!stale.length || !ready) return null
  const list = stale.length === 1 ? stale[0] : `${stale.slice(0, -1).join(', ')} and ${stale[stale.length - 1]}`
  return `Not all figures describe the current design: ${list} ${stale.length === 1 ? 'is' : 'are'} out of date.`
}

/** The LCOE card's state as a tile shows it; "zero" is a figure. */
function cardState(card: HeadlineCard): ResultStateKind {
  return card.state === 'zero' ? 'ready' : card.state
}

/** The Expert Results tab's own name, so the link says where it goes in Expert's words. */
const tabLabel = (tab: string): string | null => TABS.find(t => t.id === tab)?.label ?? null

function openTile(open: ChapterOpen, setStep: ReturnType<typeof useHubDesignStore.getState>['setStep']) {
  if ('step' in open) { setStep(open.step, { user: true }); return }
  if (open.panel === 'results') showResultsTab(open.tab, { from: 'Results' })
}

/** The way into the Expert tab that holds the same figure; Back returns here (withReturn). */
function OpenLink({ label, onClick, testId }: { label: string; onClick: () => void; testId: string }) {
  return (
    <button type="button" data-testid={testId} onClick={onClick}
      className="self-start text-[11.5px] text-accent hover:underline">{label} ›</button>
  )
}

function TileFigure({ tile, testId }: { tile: ChapterTile; testId: string }) {
  const setStep = useHubDesignStore(s => s.setStep)
  const o = tile.open
  const tab = 'step' in o ? null : o.panel === 'results' ? tabLabel(o.tab) : null
  return (
    <div className="flex flex-col gap-1">
      <Kpi headline testId={testId} label={tile.label} value={tile.value} unit={tile.unit} basis={tile.basis}
        // "Out of date" is said once for the card (the banner or the mixed-source line); the tile only dims.
        state={tile.state === 'stale' ? 'ready' : tile.state} dimmed={tile.state === 'stale'}
        reason={tile.reason} fromStudy={tile.fromStudy} hint={tile.fromStudy ? FROM_STUDY_HINT : undefined}
        action={'step' in o ? { label: 'Go to Goal', onClick: () => openTile(o, setStep) } : undefined} />
      {tab && <OpenLink label={tab} testId={`${testId}-open`} onClick={() => openTile(o, setStep)} />}
    </div>
  )
}

function LcoeFigure({ card }: { card: HeadlineCard }) {
  const words = HEADLINE_WORDS.lcoe
  const { value, basis } = headlineFigure(card)
  return (
    <div className="flex flex-col gap-1">
      <Kpi headline testId="hub-results-lcoe" label={words.label} value={value} basis={basis}
        state={card.state === 'stale' ? 'ready' : cardState(card)} dimmed={card.state === 'stale'}
        reason={card.reason ?? undefined} />
      <OpenLink label={words.tabLabel} testId="hub-results-lcoe-open"
        onClick={() => showResultsTab(words.tab, { from: 'Results' })} />
    </div>
  )
}

/** Shortfall against the goal: one bar, the goal as a marker, the study's interval when it gave one. */
export function ReliabilityBullet({ lole, target, ci, stale }: {
  lole: number; target: number | null; ci: [number, number] | null; stale: boolean
}) {
  const top = Math.max(lole, target ?? 0, ci?.[1] ?? 0) * 1.2 || 1
  const x = (v: number) => `${Math.min(100, (v / top) * 100)}%`
  const words = (v: number) => `${Number(v.toPrecision(3))} h/yr`
  const pass = target != null && lole <= target
  const title = target != null
    ? `Expected shortfall ${words(lole)}, goal ${words(target)}`
    : `Expected shortfall ${words(lole)}, no goal set`
  return (
    <ChartSourceProvider value={{ line: 'Reliability study · Monte Carlo', stale }}>
      <ChartFrame testId="hub-results-reliability-chart" title={title} bodyClassName="px-4 py-3">
        <div role="img" aria-label={title + (ci ? `, likely between ${words(ci[0])} and ${words(ci[1])}` : '')}
          className="relative h-6">
          <div className="absolute inset-y-2 left-0 right-0 rounded-sm bg-bg-2" />
          {target != null && (
            // The allowed band: up to the goal passes.
            <div className="absolute inset-y-2 left-0 rounded-sm bg-success/15" style={{ width: x(target) }} />
          )}
          <div data-testid="hub-results-reliability-bar"
            className={`absolute inset-y-[11px] left-0 rounded-sm ${target == null ? 'bg-accent' : pass ? 'bg-success' : 'bg-danger'}`}
            style={{ width: x(lole) }} />
          {ci && (
            <div data-testid="hub-results-reliability-ci" className="absolute top-1/2 h-px bg-text"
              style={{ left: x(ci[0]), width: `calc(${x(ci[1])} - ${x(ci[0])})` }} />
          )}
          {target != null && (
            <div data-testid="hub-results-reliability-goal" className="absolute inset-y-0 w-0.5 bg-text"
              style={{ left: x(target) }} />
          )}
        </div>
        <div className="mt-1 flex justify-between" aria-hidden="true" style={CHART_TICK}>
          <span>0</span>
          {target != null && <span>goal {words(target)}</span>}
          <span>{words(top)}</span>
        </div>
      </ChartFrame>
    </ChartSourceProvider>
  )
}

export function ResultsHeadline({ reliability, cost, ci }: {
  reliability: ChapterInputs['reliability']
  /** The study's cost tile, unchanged (P24-FE gate B1), placed with the figures. */
  cost: React.ReactNode
  ci: [number, number] | null
}) {
  const { tiles } = useChapterTiles(reliability)
  const lcoe = useHeadlineKpis().data?.cards.find(c => c.id === 'lcoe') ?? null
  const rel = tiles.find(t => t.id === 'reliability')
  const npv = tiles.find(t => t.id === 'investment')
  const figures: HeadlineFigureState[] = [
    ...[rel, npv].filter((t): t is ChapterTile => !!t).map(t => ({ label: t.label, state: t.state })),
    ...(lcoe ? [{ label: HEADLINE_WORDS.lcoe.label, state: cardState(lcoe) }] : []),
  ]
  const note = mixedSourceNote(figures)
  return (
    <section aria-label="Headline figures" data-testid="hub-results-headline" className="flex flex-col gap-2">
      <div className="grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(170px,1fr))]">
        {rel && <TileFigure tile={rel} testId="hub-results-reliability" />}
        {cost}
        {npv && <TileFigure tile={npv} testId="hub-results-npv" />}
        {lcoe && <LcoeFigure card={lcoe} />}
      </div>
      {note && <p role="note" data-testid="hub-results-mixed" className="text-[11.5px] text-muted">{note}</p>}
      {reliability.lole != null && (
        <ReliabilityBullet lole={reliability.lole} target={reliability.target} ci={ci}
          stale={reliability.stale === true} />
      )}
    </section>
  )
}

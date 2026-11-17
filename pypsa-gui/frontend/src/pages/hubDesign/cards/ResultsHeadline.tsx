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
import { Skeleton } from '../../../components/Skeleton'
import type { ResultStateKind } from '../../../components/ResultState'
import { ChartFrame, chartFigure, ChartSourceProvider, CHART_TICK } from '../../../components/charts/chartTheme'
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

/** Each figure carries its own state (UX 23, 1). The study's figure is the one exception: the card's
 *  banner ("These results are from an earlier study") is its mark, so it only dims under it. */
function shown(state: ResultStateKind, studyBanner: boolean): { state: ResultStateKind; dimmed: boolean } {
  return state === 'stale' && studyBanner ? { state: 'ready', dimmed: true } : { state, dimmed: false }
}

function TileFigure({ tile, testId, studyBanner = false }: { tile: ChapterTile; testId: string; studyBanner?: boolean }) {
  const setStep = useHubDesignStore(s => s.setStep)
  const o = tile.open
  const tab = 'step' in o ? null : o.panel === 'results' ? tabLabel(o.tab) : null
  return (
    <div className="flex flex-col gap-1">
      <Kpi headline testId={testId} label={tile.label} value={tile.value} unit={tile.unit} basis={tile.basis}
        {...shown(tile.state, studyBanner)}
        reason={tile.reason} fromStudy={tile.fromStudy} hint={tile.fromStudy ? FROM_STUDY_HINT : undefined}
        action={'step' in o ? { label: 'Go to Goal', onClick: () => openTile(o, setStep) } : undefined} />
      {tab && <OpenLink label={tab} testId={`${testId}-open`} onClick={() => openTile(o, setStep)} />}
    </div>
  )
}

/** A project type whose Expert strip has no LCOE card: said in words, never a blank tile (UX 23, 2). */
export const LCOE_NOT_APPLICABLE = 'Not applicable for this project type.'

/** No figures from `/results/headline_kpis` yet: it answers 204 until the project itself is solved. */
export const LCOE_NO_SOLVE = 'LCOE needs a solve of the project and its investment case.'
/** The strip's own words when the read fails. */
export const LCOE_UNREAD = 'The headline figures could not be read.'

/** `read`: what the headline read gave: a response (with or without an LCOE card), none yet (204), or a failure. */
function LcoeFigure({ read }: { read: { kind: 'card'; card: HeadlineCard | null } | { kind: 'none' } | { kind: 'failed' } }) {
  const words = HEADLINE_WORDS.lcoe
  if (read.kind !== 'card' || !read.card) {
    const [state, reason]: [ResultStateKind, string] = read.kind === 'failed' ? ['unavailable', LCOE_UNREAD]
      : read.kind === 'none' ? ['not_run', LCOE_NO_SOLVE] : ['not_applicable', LCOE_NOT_APPLICABLE]
    return <Kpi headline testId="hub-results-lcoe" label={words.label} value="" state={state} reason={reason} />
  }
  const card = read.card
  const { value, basis } = headlineFigure(card)
  // No link of its own: LCOE lives on the Investment case tab, which the NPV figure already opens
  // (one entry point per destination, UX 23, 5).
  return (
    <Kpi headline testId="hub-results-lcoe" label={words.label} value={value} basis={basis}
      state={cardState(card)} reason={card.reason ?? undefined} />
  )
}

/** Shortfall against the goal: one bar, the goal as a marker, the study's interval when it gave one. */
export function ReliabilityBullet({ lole, target, ci: range, stale }: {
  lole: number; target: number | null; ci: [number, number] | null; stale: boolean
}) {
  const top = Math.max(lole, target ?? 0, range?.[1] ?? 0) * 1.2 || 1
  const x = (v: number) => `${Math.min(100, (v / top) * 100)}%`
  // Rounded as the KPI tile rounds it, so the chart's title and the tile never disagree.
  const words = (v: number) => chartFigure(`${v} h/yr`)
  // The goal is the user's own number: written as the tile's basis writes it ("the goal is 3 h/yr").
  const goalWords = (v: number) => `${Number(v.toPrecision(6))} h/yr`
  const pass = target != null && lole <= target
  // A range that rounds to one value says nothing: it is left out, line and words.
  const ci = range && words(range[0]) !== words(range[1]) ? range : null
  const title = target != null
    ? `Expected shortfall ${words(lole)}, goal ${goalWords(target)}`
    : `Expected shortfall ${words(lole)}, no goal set`
  // The band is not the only cue: the result is also said in words (UX 23, 7).
  const against = target == null ? null : pass ? 'Within goal' : 'Above goal'
  return (
    <ChartSourceProvider value={{ line: 'Reliability study · Monte Carlo', stale }}>
      <ChartFrame testId="hub-results-reliability-chart" title={title} bodyClassName="px-4 py-3"
        right={against && (
          <span data-testid="hub-results-reliability-against"
            className={`text-[11.5px] font-semibold ${pass ? 'text-success' : 'text-danger'}`}>{against}</span>
        )}>
        <div role="img"
          aria-label={[title, against, ci && `likely between ${words(ci[0])} and ${words(ci[1])}`, 'lower is better']
            .filter(Boolean).join(', ')}
          className="relative h-7">
          <div className="absolute inset-y-2 left-0 right-0 rounded-sm bg-bg-2" />
          {target != null && (
            // The allowed band: up to the goal passes.
            <div className="absolute inset-y-2 left-0 rounded-sm bg-success/15" style={{ width: x(target) }} />
          )}
          <div data-testid="hub-results-reliability-bar"
            className={`absolute inset-y-2 left-0 rounded-sm ${target == null ? 'bg-accent' : pass ? 'bg-success' : 'bg-danger'}`}
            // A zero still shows a mark at the axis.
            style={{ width: x(lole), minWidth: 3 }} />
          {ci && (
            // The likely range: a line with end caps over the bar, named under the axis.
            <div data-testid="hub-results-reliability-ci"
              className="absolute top-1/2 h-2.5 -translate-y-1/2 border-x border-text"
              style={{ left: x(ci[0]), width: `calc(${x(ci[1])} - ${x(ci[0])})` }}>
              <div className="absolute top-1/2 h-px w-full bg-text" />
            </div>
          )}
          {target != null && (
            <div data-testid="hub-results-reliability-goal" className="absolute inset-y-0 w-0.5 bg-text"
              style={{ left: x(target) }} />
          )}
        </div>
        <div className="relative mt-1 h-3" aria-hidden="true" style={CHART_TICK}>
          <span className="absolute left-0">0</span>
          {target != null && (
            <span className="absolute -translate-x-1/2 whitespace-nowrap" style={{ left: x(target) }}>Goal</span>
          )}
          <span className="absolute right-0">{words(top)}</span>
        </div>
        {ci && (
          <p data-testid="hub-results-reliability-ci-words" className="mt-1 text-[10.5px] text-muted">
            The line marks the likely range: {words(ci[0])} to {words(ci[1])}.
          </p>
        )}
        <p className="mt-1 text-[10.5px] text-muted" aria-hidden="true">Expected shortfall (h/yr) · lower is better</p>
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
  const headline = useHeadlineKpis()
  const lcoe = headline.data?.cards.find(c => c.id === 'lcoe') ?? null
  const rel = tiles.find(t => t.id === 'reliability')
  const npv = tiles.find(t => t.id === 'investment')
  const figures: HeadlineFigureState[] = [
    ...[rel, npv].filter((t): t is ChapterTile => !!t).map(t => ({ label: t.label, state: t.state })),
    ...(lcoe ? [{ label: HEADLINE_WORDS.lcoe.label, state: cardState(lcoe) }] : []),
  ]
  const note = mixedSourceNote(figures)
  return (
    <section aria-label="Headline figures" data-testid="hub-results-headline" className="flex flex-col gap-2">
      <div className="grid grid-cols-2 gap-2">
        {rel && <TileFigure tile={rel} testId="hub-results-reliability" studyBanner={reliability.stale === true} />}
        {cost}
        {npv && <TileFigure tile={npv} testId="hub-results-npv" />}
        {/* The tile's place is kept while the figures load, so the grid does not move (as Expert's strip). */}
        {headline.isLoading ? <Skeleton className="min-h-[96px]" /> : (
          <LcoeFigure read={headline.isError ? { kind: 'failed' } : headline.data ? { kind: 'card', card: lcoe } : { kind: 'none' }} />
        )}
      </div>
      {note && <p role="note" data-testid="hub-results-mixed" className="text-[11.5px] text-muted">{note}</p>}
      {reliability.lole != null && (
        <ReliabilityBullet lole={reliability.lole} target={reliability.target} ci={ci}
          stale={reliability.stale === true} />
      )}
    </section>
  )
}

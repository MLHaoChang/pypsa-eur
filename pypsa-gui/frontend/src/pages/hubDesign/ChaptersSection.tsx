// The Guided report entry (UX overhaul S6, G4/G6): the four category tiles
// under the Results card, at Guided density, each opening its own detail.
// Reads share the Expert views' query keys, so one cache serves both.
import { useQuery } from '@tanstack/react-query'
import Kpi from '../../components/Kpi'
import { campusApi, isActiveRun } from '../../api/campusElectrical'
import { financeApi } from '../../api/finance'
import { simulationApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { CAMPUS_KEY, CAMPUS_RUN_POLL_MS } from '../CampusElectricalPanel'
import { isRunJob, useTick } from '../campusRunProgress'
import { GFM_KEY } from '../CampusIslandGfmSection'
import { chapterTiles, type ChapterInputs, type ChapterOpen } from './chapters'
import { useHubDesignStore } from './hubDesignStore'

const OPEN_LABEL = (o: ChapterOpen) =>
  'step' in o ? 'Go to Goal' : o.panel === 'campusElectrical' ? 'Open the grid checks' : 'Open the detail'

function is409(e: unknown): boolean {
  return (e as { response?: { status?: number } } | null)?.response?.status === 409
}

export function ChaptersSection({ reliability }: { reliability: ChapterInputs['reliability'] }) {
  const project = useUIStore(s => s.currentProject)
  const setStep = useHubDesignStore(s => s.setStep)
  const icStudy = useQuery({
    queryKey: nk(project, 'results', 'investment_case'),
    queryFn: () => financeApi.getInvestmentCase(), enabled: !!project,
  })
  const icRunning = icStudy.data?.status === 'running'
  const icReport = useQuery({
    queryKey: nk(project, 'results', 'investment_case', 'report'),
    queryFn: () => financeApi.getReport(), enabled: !!project && !icRunning,
  })
  const campus = useQuery({
    queryKey: CAMPUS_KEY(project ?? ''), queryFn: () => campusApi.state(project ?? ''),
    enabled: !!project, retry: false,
    // A running study takes up to an hour: follow it, so the tile shows its stage and time.
    refetchInterval: q => (isRunJob(q.state.data?.run_job) && isActiveRun(q.state.data?.run_job)
      ? CAMPUS_RUN_POLL_MS : false),
  })
  useTick(isRunJob(campus.data?.run_job) && isActiveRun(campus.data?.run_job))
  const status = useQuery({
    queryKey: nk(project, 'simulationStatus'), queryFn: simulationApi.getStatus, enabled: !!project,
  })
  // The campus GFM settings, for the operation tile's control line (GFM-b);
  // only for a hub with a campus run (the island section's own key).
  const gfm = useQuery({
    queryKey: GFM_KEY(project ?? ''), queryFn: () => campusApi.gfmSettings(project ?? ''),
    enabled: !!project && !!campus.data, retry: false,
  })

  const tiles = chapterTiles({
    investment: { study: icStudy.data ?? null, report: icRunning ? null : icReport.data ?? null },
    grid: { campus: campus.data ?? null, notHub: is409(campus.error) },
    reliability,
    operation: { status: status.data ?? null, gfm: gfm.data ?? null },
  })

  const open = (o: ChapterOpen) => {
    if ('step' in o) { setStep(o.step, { user: true }); return }
    const ui = useUIStore.getState()
    ui.setSlidePanel(o.panel)
    if (o.panel === 'results') ui.requestResultsTab(o.tab)
  }

  return (
    <section data-testid="hub-chapters" className="flex flex-col gap-2">
      <h3 className="text-[12px] font-semibold text-text">The whole picture</h3>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-2">
        {tiles.map(t => (
          <div key={t.id} className="flex flex-col gap-1" data-testid={`hub-chapter-${t.id}`}>
            <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">{t.group}</span>
            <Kpi label={t.label} value={t.value} unit={t.unit} basis={t.basis}
              state={t.state} reason={t.reason} testId={`hub-chapter-${t.id}-kpi`} />
            {t.control && (
              <p className="text-[11.5px] text-muted" data-testid={`hub-chapter-${t.id}-control`}>
                {t.control.text}{' '}
                {t.control.resolve && (
                  <button type="button" data-testid={`hub-chapter-${t.id}-solve`}
                    onClick={() => useUIStore.getState().requestRunSolve()}
                    className="text-accent hover:underline">Solve</button>
                )}
              </p>
            )}
            <button type="button" data-testid={`hub-chapter-${t.id}-open`} onClick={() => open(t.open)}
              className="self-start text-[11.5px] text-accent hover:underline">
              {OPEN_LABEL(t.open)}
            </button>
          </div>
        ))}
      </div>
    </section>
  )
}

// The Guided-mode hub design (guided-mode spec §5): a progress rail and one
// step card at a time — Start, Site, Goal, Results, Improve. The state machine
// (§5.4) is derived from the study record and the review; the step shown is
// the store's, moved by the user or by a study finishing (§5.6).
import { useEffect, useRef, type ComponentType } from 'react'
import { GuideButton } from '../../components/GuidedTour'
import { useNetworkRevisionInvalidation } from '../../hooks/useNetworkRevisionInvalidation'
import { useStudyFinishedInvalidation } from '../../hooks/useStudyFinishedInvalidation'
import { useUIStore } from '../../store/uiStore'
import { effectiveStep, flowState, initialStep, studyHasResults } from './flow'
import { useHubDesignStore, type HubStep } from './hubDesignStore'
import { StepRail } from './StepRail'
import { GoalCard } from './cards/GoalCard'
import { ImproveCard } from './cards/ImproveCard'
import { ResultsCard } from './cards/ResultsCard'
import { SiteCard } from './cards/SiteCard'
import { StartCard } from './cards/StartCard'
import { useHubReadiness, useHubReview, useHubStudy, useHubTemplate } from './useHubData'

const CARDS: Record<HubStep, ComponentType> = {
  start: StartCard, site: SiteCard, goal: GoalCard, results: ResultsCard, improve: ImproveCard,
}

export default function HubDesignPanel() {
  const project = useUIStore(s => s.currentProject)
  const studyQ = useHubStudy()
  const { study } = studyQ
  const templateQ = useHubTemplate()
  const { template } = templateQ
  const { review } = useHubReview(studyHasResults(study))
  // The Site / Goal cards' readiness read, observed (never fetched) here so
  // the error line can name it (P30 B8).
  const readinessQ = useHubReadiness(template, studyQ.running,
    !studyQ.isPending && !templateQ.isPending, { observeOnly: true })
  const flow = flowState(project, study, review)
  const isTemplate = template != null

  const storeProject = useHubDesignStore(s => s.project)
  const ready = useHubDesignStore(s => s.ready)
  const step = useHubDesignStore(s => s.step)
  const setStep = useHubDesignStore(s => s.setStep)

  // A status the panel has seen, per project; `undefined` = not read yet.
  const status = !project || studyQ.data === undefined ? undefined : study?.status ?? null
  useStudyFinishedInvalidation(status)
  // P33b: re-read the record and the review when an edit moves the polled
  // counter, so the Results card's edited banner needs no reload. The hub
  // is a Guided surface; the flag keeps Expert free of the hook regardless.
  useNetworkRevisionInvalidation(useUIStore(s => s.uiMode) === 'guided')

  // Reset for a new project once its study record and template are read
  // (or failed to be read), so the first step shown is the one §5.4 names.
  //
  // P24-FE re-gate B4: `settled` LATCHES per project through the store's
  // reset. react-query puts a never-successful query back to `pending` on
  // each refetch; gating the card on `isPending` alone unmounted it, and the
  // card's own observers refetched on remount — an endless request loop.
  const readNow = !studyQ.isPending && !templateQ.isPending
  const latched = ready && storeProject === project
  const settled = !project || readNow || latched
  const failed = studyQ.isError || templateQ.isError
  // A project reset while a read had failed is re-derived once it recovers.
  const resetWhileFailed = useRef<string | null>(null)
  const reset = () => {
    const po = template?.pack_overrides ?? {}
    const str = (v: unknown) => (typeof v === 'number' ? String(v) : '')
    useHubDesignStore.getState().resetFor(project, {
      step: initialStep(flow, isTemplate),
      archetype: template?.recommended_archetype ?? 'strong_grid',
      loleTarget: str(po.target_lole_h),
      ensCap: str(po.ens_cap_permyriad),
    })
    resetWhileFailed.current = failed ? project : null
  }
  useEffect(() => {
    if (!settled || latched) return
    reset()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settled, project, latched])
  useEffect(() => {
    if (resetWhileFailed.current === project && readNow && !failed) reset()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readNow, failed, project])
  // What the error line names: the first read in error, in this order.
  const loadError = studyQ.isError ? 'study state' : templateQ.isError ? 'template'
    : readinessQ.isError ? 'readiness check' : null
  const retry = () => {
    if (studyQ.isError) void studyQ.refetch()
    if (templateQ.isError) void templateQ.refetch()
    if (readinessQ.isError) void readinessQ.refetch()
  }

  // Auto-advance (§5.6): running → done opens Results unless the user moved
  // the rail since; any → running opens Goal. Every transition re-arms it.
  const prevStatus = useRef<string | null | undefined>(undefined)
  useEffect(() => { prevStatus.current = undefined }, [project])
  useEffect(() => {
    const prev = prevStatus.current
    prevStatus.current = status
    if (prev === undefined || status === undefined || prev === status) return
    const s = useHubDesignStore.getState()
    if (prev === 'running' && status === 'done') {
      if (!s.userMovedRail) s.setStep('results')
    } else if (status === 'running') {
      s.setStep('goal')
    }
    s.clearUserMoved()
  }, [status])

  const shown = effectiveStep(flow, ready && storeProject === project ? step : initialStep(flow, isTemplate),
    isTemplate)
  const Card = CARDS[shown]

  return (
    <div data-testid="hub-design-panel" className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-3xl flex-col gap-4 p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <StepRail flow={flow} step={shown} isTemplate={isTemplate}
            // A tour's reveal clicks are not the user moving the rail: they
            // must not suppress the jump to Results (P24-FE gate B2).
            onPick={s => setStep(s, { user: useUIStore.getState().guidedTourHolds === 0 })} />
          <GuideButton tourId="hub_design" testId="hub-guide-button" label="Guide" />
        </div>
        {project !== null && loadError !== null && (
          <div data-testid="hub-load-error" role="alert"
            className="flex flex-wrap items-center gap-2 rounded border border-warn/50 bg-warn/10 px-3 py-2 text-[12px] text-warn">
            This project's {loadError} could not be read from the server, so the steps below
            may be incomplete.
            <button type="button" data-testid="hub-load-retry" onClick={retry}
              className="rounded border border-warn/60 px-2 py-0.5 text-[11px] hover:bg-warn/10">
              Retry
            </button>
          </div>
        )}
        {settled ? <Card /> : (
          <p className="text-[12px] text-muted">Loading the project…</p>
        )}
      </div>
    </div>
  )
}

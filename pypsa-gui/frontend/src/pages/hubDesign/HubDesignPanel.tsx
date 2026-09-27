// The Guided-mode hub design (guided-mode spec §5): a progress rail and one
// step card at a time — Start, Site, Goal, Results, Improve. The state machine
// (§5.4) is derived from the study record and the review; the step shown is
// the store's, moved by the user or by a study finishing (§5.6).
import { useEffect, useRef, type ComponentType } from 'react'
import { GuideButton } from '../../components/GuidedTour'
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
import { useHubReview, useHubStudy, useHubTemplate } from './useHubData'

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
  const flow = flowState(project, study, review)
  const isTemplate = template != null

  const storeProject = useHubDesignStore(s => s.project)
  const ready = useHubDesignStore(s => s.ready)
  const step = useHubDesignStore(s => s.step)
  const setStep = useHubDesignStore(s => s.setStep)

  // A status the panel has seen, per project; `undefined` = not read yet.
  const status = !project || studyQ.data === undefined ? undefined : study?.status ?? null
  useStudyFinishedInvalidation(status)

  // Reset for a new project once its study record and template are read,
  // so the first step shown is the one §5.4 names.
  const settled = !project || (!studyQ.isPending && !templateQ.isPending)
  useEffect(() => {
    if (!settled || (ready && storeProject === project)) return
    const po = template?.pack_overrides ?? {}
    const str = (v: unknown) => (typeof v === 'number' ? String(v) : '')
    useHubDesignStore.getState().resetFor(project, {
      step: initialStep(flow, isTemplate),
      archetype: template?.recommended_archetype ?? 'strong_grid',
      loleTarget: str(po.target_lole_h),
      ensCap: str(po.ens_cap_permyriad),
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settled, project, ready, storeProject])

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
        {(project === null || settled) ? <Card /> : (
          <p className="text-[12px] text-muted">Loading the project…</p>
        )}
      </div>
    </div>
  )
}

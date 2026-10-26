// The Guided study's inputs and its start/abort, read in one place: the Goal
// card edits the project's one reliability goal, section 9 (Readiness and run)
// runs the study. Both go through the Expert panel's own
// `buildGoalFreeStudyBody`: the request carries no goal field and the backend
// fills them from the stored goal (W3b-R), so the same study starts wherever
// it is started from.
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { resultsApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { blockerMessage } from '../../utils/blockerMessage'
import { nk } from '../../utils/queryKeys'
import { buildGoalFreeStudyBody, ehStudyQueryKeys } from '../results/EhReferenceDesignPanel'
import { useReliabilityGoal } from '../../hooks/useReliabilityGoal'
import {
  templateForm, useHubReadiness, useHubSolverConfig, useHubStudy, useHubTemplate,
} from './useHubData'

export function useGoalStudy() {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const { study, running, isPending: studyPending } = useHubStudy()
  const { template, isPending: templatePending, isError: templateError,
    refetch: refetchTemplate } = useHubTemplate()
  const { readiness } = useHubReadiness(template, running, !studyPending && !templatePending)
  const { data: solverConfig } = useHubSolverConfig()
  const { archetype, goal, isError: goalError } = useReliabilityGoal()
  const [blocked, setBlocked] = useState<string | null>(null)

  // Until the goal is read, the site type the rules below depend on is not
  // known: no body, so the Run waits rather than guess.
  const built = goal ? buildGoalFreeStudyBody(archetype, templateForm(template))
    : { body: null, error: goalError ? 'The reliability goal could not be read.' : null }
  const voll = typeof solverConfig?.voll === 'number' ? solverConfig.voll : null
  const vollMissing = voll != null && !(voll > 0)

  const run = useMutation({
    mutationFn: () => resultsApi.startEhStudy(built.body!),
    onMutate: () => setBlocked(null),
    onSuccess: () => {
      for (const key of ehStudyQueryKeys(project)) void qc.invalidateQueries({ queryKey: key })
    },
    onError: (e: unknown) => setBlocked(blockerMessage(e)),
  })
  const abort = useMutation({
    mutationFn: () => resultsApi.abortEhStudy(),
    onSuccess: () => void qc.invalidateQueries({ queryKey: nk(project, 'results', 'eh_study') }),
  })

  return {
    project, study, running, template, templateError, refetchTemplate, readiness,
    // P24-FE re-gate note 2: without the template the study would silently run
    // the default site type's instead of the template's recommended one.
    templateUnknown: templateError && !template,
    voll, vollMissing, built, run, abort,
    problem: built.error ?? blocked,
  }
}

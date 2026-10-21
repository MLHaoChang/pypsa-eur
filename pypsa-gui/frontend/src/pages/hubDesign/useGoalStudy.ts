// The Guided study's inputs and its start/abort, read in one place: the Goal
// card edits the goal, section 9 (Readiness → run) runs the study. Both go
// through the Expert panel's own `buildEhStudyBody`, so the same study starts
// wherever it is started from.
import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { resultsApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { blockerMessage } from '../../utils/blockerMessage'
import { nk } from '../../utils/queryKeys'
import { buildEhStudyBody, ehStudyQueryKeys } from '../results/EhReferenceDesignPanel'
import { useHubDesignStore } from './hubDesignStore'
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
  const archetype = useHubDesignStore(s => s.archetype)
  const loleTarget = useHubDesignStore(s => s.loleTarget)
  const ensCap = useHubDesignStore(s => s.ensCap)
  const [blocked, setBlocked] = useState<string | null>(null)

  // Default goal: the template's own override (set on reset), else the pack
  // default readiness reports for the chosen site type. A pack-seeded value
  // follows the pack; the user's typing is never overwritten. Here, not in the
  // Goal card, so a study started from section 9 sees the same default.
  const packLole = readiness?.pack_defaults?.target_lole_h
  useEffect(() => {
    if (packLole === undefined) return
    const s = useHubDesignStore.getState()
    if (s.loleSource === 'template' || s.loleSource === 'user') return
    const v = packLole == null ? '' : String(packLole)
    if (s.loleTarget !== v || s.loleSource !== 'pack') s.seedLoleFromPack(v)
  }, [packLole])

  const form = { ...templateForm(template), loleTarget, ensCap }
  const built = buildEhStudyBody(archetype, form)
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

// The hub-design cards' reads (guided-mode spec §5.3). Every query uses the
// Expert panel's key and options, so the two views share one cache: a study
// started in one is polled — and its report read — by the other.
//
// Each hook returns NAMED fields, never `{ ...query }`: spreading a
// react-query result reads its `promise` getter, which rejects the observer's
// pending thenable and — for a disabled query — sent HubDesignPanel into an
// intermittent endless re-render (useHubData.test).
import { useQuery } from '@tanstack/react-query'
import {
  resultsApi, simulationApi, type EhReferenceDesignReport, type EhReview,
  type EhStudyPayload, type EhTemplateMeta,
} from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import {
  buildEhStudyBody, EMPTY_PACK_FORM, ehStudyRefetchInterval, formFromTemplate,
  type PackForm,
} from '../results/EhReferenceDesignPanel'
import { useHubDesignStore } from './hubDesignStore'

/** The study record (`nk(project,'results','eh_study')`, 2 s poll while running). */
export function useHubStudy() {
  const project = useUIStore(s => s.currentProject)
  const q = useQuery({
    queryKey: nk(project, 'results', 'eh_study'),
    queryFn: () => resultsApi.getEhStudy({ quiet: true }),
    refetchInterval: ehStudyRefetchInterval,
    enabled: !!project,
  })
  const study = (q.data ?? null) as EhStudyPayload | null
  return { data: q.data, isPending: q.isPending, isError: q.isError, refetch: q.refetch,
    study, running: study?.status === 'running' }
}

/** The template the project was created from (null for an own network). */
export function useHubTemplate() {
  const project = useUIStore(s => s.currentProject)
  const q = useQuery({
    queryKey: nk(project, 'adequacy', 'eh_template'),
    queryFn: () => resultsApi.getEhTemplate(project ?? '', { quiet: true }),
    enabled: !!project,
    staleTime: Infinity,
  })
  return { isPending: q.isPending, isError: q.isError, refetch: q.refetch,
    template: (q.data ?? null) as EhTemplateMeta | null }
}

/** GET /results/eh_review — read only once the study has results. */
export function useHubReview(enabled: boolean) {
  const project = useUIStore(s => s.currentProject)
  const q = useQuery({
    queryKey: nk(project, 'results', 'eh_review'),
    queryFn: () => resultsApi.getEhReview(),
    enabled: !!project && enabled,
  })
  const review = (q.data ?? null) as EhReview | null
  // A running body is chat-tool prose (gate note N3): the cards never show it.
  return { review: review?.status === 'ok' ? review : null }
}

/** The report the Expert panel shows: the study record's copy, else the
 *  stored one (the panel's `reportKey` query). Hidden while a study runs. */
export function useHubReport(study: EhStudyPayload | null, running: boolean) {
  const project = useUIStore(s => s.currentProject)
  const q = useQuery({
    queryKey: nk(project, 'results', 'eh_reference_design'),
    queryFn: () => resultsApi.getEhReferenceDesign(),
    enabled: !!project && !running,
  })
  const report: EhReferenceDesignReport | null = running ? null
    : study?.report ?? ((q.data ?? null) as EhReferenceDesignReport | null)
  return report
}

/** The pack form a project starts from: its template's, else the pack's own. */
export function templateForm(template: EhTemplateMeta | null): PackForm {
  return template ? formFromTemplate(template) : EMPTY_PACK_FORM
}

/** Readiness for the chosen site type with the template's recommended
 *  overrides — "the site as it is", independent of the Goal card's typing.
 *  Paused while a study runs (it copies the network under the study lock);
 *  `ready` = the study record and the template have been read, so the first
 *  request already carries the right overrides. */
export function useHubReadiness(template: EhTemplateMeta | null, running: boolean,
  ready: boolean) {
  const project = useUIStore(s => s.currentProject)
  const archetype = useHubDesignStore(s => s.archetype)
  const overrides = buildEhStudyBody(archetype, templateForm(template)).body?.pack_overrides
  const q = useQuery({
    queryKey: [...nk(project, 'results', 'eh_readiness'), archetype, 'hub-design',
      JSON.stringify(overrides ?? null)],
    queryFn: () => resultsApi.getEhReadiness(archetype, undefined, undefined,
      { stages: undefined, pack_overrides: overrides }),
    enabled: !!project && ready && !running,
  })
  return { isError: q.isError, readiness: q.data ?? null }
}

/** VOLL from the solver settings (same key as the Solver settings page). */
export function useHubSolverConfig() {
  const project = useUIStore(s => s.currentProject)
  const q = useQuery({
    queryKey: nk(project, 'solverConfig'),
    queryFn: simulationApi.getSolverConfig,
    enabled: !!project,
  })
  return { data: q.data }
}

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
  resultsApi, simulationApi, type EhLeverTable, type EhReferenceDesignReport, type EhReview,
  type EhStudyPayload, type EhTemplateMeta,
} from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import type { EhSizedAsset } from '../../api/simulation'
import { sizedByName } from '../results/SizedDesignTable'
import { nk } from '../../utils/queryKeys'
import {
  buildEhStudyBody, EMPTY_PACK_FORM, ehStudyRefetchInterval, formFromTemplate,
  type PackForm,
} from '../results/EhReferenceDesignPanel'
import { dispatchSolved } from './chapters'
import { goalText, useReliabilityGoal } from '../../hooks/useReliabilityGoal'
import { networkApi } from '../../api/network'
import { useProjectFlow } from '../../hooks/useProjectFlow'
import type { FlowConnectionLimit } from '../../api/flow'
import type { Link } from '../../api/types'

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

/** The levers table the Expert panel shows (its `levKey` query); read only
 *  where `enabled`. */
export function useHubLevers(enabled: boolean): EhLeverTable | null {
  const project = useUIStore(s => s.currentProject)
  const q = useQuery({
    queryKey: nk(project, 'results', 'eh_levers'),
    queryFn: () => resultsApi.getEhLevers(),
    enabled: !!project && enabled,
  })
  return enabled ? (q.data ?? null) as EhLeverTable | null : null
}

/** The last study's sized assets by `Component:name`, for section 5's rows
 *  (walkthrough asset-investment 03); read only where `enabled`. */
export function useSizedAssets(enabled: boolean): Map<string, EhSizedAsset> {
  const project = useUIStore(s => s.currentProject)
  const q = useQuery({
    queryKey: nk(project, 'results', 'eh_reference_design'),
    queryFn: () => resultsApi.getEhReferenceDesign(),
    enabled: !!project && enabled,
  })
  return sizedByName(enabled ? (q.data ?? null) as EhReferenceDesignReport | null : null)
}

/** The pack form a project starts from: its template's, else the pack's own. */
export function templateForm(template: EhTemplateMeta | null): PackForm {
  return template ? formFromTemplate(template) : EMPTY_PACK_FORM
}

/** The connection limit the server reads from the network (`flow_service.import_capacity`, the flow's
 *  connection row): its MW, its import links, or why the network fixes none. `undefined` until the
 *  flow is read. The one reader on the client: nothing here re-selects links or re-sums capacity. */
export function useConnectionLimit(): FlowConnectionLimit | undefined {
  const { section } = useProjectFlow()
  return section('connection')?.connection_limit
}

/** The grid import links, as the server selects them for the connection limit (any rule: eh_role,
 *  the PoC bus or the import carrier), with their records for the editor. Empty until the flow is read. */
export function useImportLinks(): Link[] {
  const project = useUIStore(s => s.currentProject)
  const q = useQuery({ queryKey: nk(project, 'links'), queryFn: networkApi.getLinks, enabled: !!project, staleTime: 30_000 })
  const names = useConnectionLimit()?.links
  if (!names?.length) return []
  const chosen = new Set(names)
  return ((q.data ?? []) as Link[]).filter(l => chosen.has(l.name))
}

/** The template's pack form with the import cap the server reads from the network (template-e2e 02,
 *  owner decision (a)): the connection limit is the import links' summed capacity, so an edit to it is
 *  what a weak-grid study runs; the template's value is only the links' initial capacity. Read from the
 *  flow's connection row (`flow_service.import_capacity`), never re-summed here (GK B, #1060).
 *  `importPending` until the flow is read; `importRefusal` where the network fixes no limit (none,
 *  0 MW, unreadable): a weak-grid study then sends nothing rather than the template's value. */
export function useTemplateForm(template: EhTemplateMeta | null):
  PackForm & { importPending: boolean, importRefusal: string | null } {
  const { section, isPending, isError } = useProjectFlow()
  const limit = useConnectionLimit()
  const form = templateForm(template)
  if (!template) return { ...form, importPending: false, importRefusal: null }
  if (isError) {
    return { ...form, importMw: '', importPending: false,
      importRefusal: 'The connection limit can\'t be read: the project\'s state did not load.' }
  }
  if (!limit) return { ...form, importMw: '', importPending: isPending || !section('connection'), importRefusal: null }
  if (limit.refusal != null || limit.mw == null) {
    return { ...form, importMw: '', importPending: false,
      importRefusal: limit.refusal ?? 'The connection limit can\'t be read from the network.' }
  }
  return { ...form, importMw: String(limit.mw), importPending: false, importRefusal: null }
}

/** Why a weak-grid study can't be built from `form` yet: null when it can; `''` while the
 *  connection limit is being read (wait, no message); else the server's refusal. */
export function importBlock(archetype: string, form: ReturnType<typeof useTemplateForm>): string | null {
  if (archetype !== 'weak_flexible') return null
  if (form.importRefusal) return form.importRefusal
  return form.importPending ? '' : null
}

/** Readiness for the chosen site type with the template's recommended
 *  overrides and the stored goal's LOLE and ENS targets, as the study will
 *  run them (W3b-R; the same preview as Expert's EH panel) — independent of
 *  the Goal card's unsaved typing.
 *  Paused while a study runs (it copies the network under the study lock);
 *  `ready` = the study record and the template have been read, so the first
 *  request already carries the right overrides. */
export function useHubReadiness(template: EhTemplateMeta | null, running: boolean,
  ready: boolean, { observeOnly = false }: { observeOnly?: boolean } = {}) {
  const project = useUIStore(s => s.currentProject)
  // The stored site type (W3b-R); read before the first request so it never
  // asks for the default type's readiness first.
  const { goal, archetype, isPending: goalPending } = useReliabilityGoal()
  const form = useTemplateForm(template)
  // A weak-grid preview waits for the network's connection limit, and runs none without one: the
  // flow's connection row says why (never the template's value).
  const importBlocked = !!template && importBlock(archetype, form) !== null
  const overrides = buildEhStudyBody(archetype, {
    ...form, loleTarget: goalText(goal, 'target_lole_h'),
    ensCap: goalText(goal, 'ens_cap_permyriad'), draws: '',
  }).body?.pack_overrides
  const q = useQuery({
    queryKey: [...nk(project, 'results', 'eh_readiness'), archetype, 'hub-design',
      JSON.stringify(overrides ?? null)],
    queryFn: () => resultsApi.getEhReadiness(archetype, undefined, undefined,
      { stages: undefined, pack_overrides: overrides }),
    // `observeOnly` (the panel's load-error line, P30 B8): read the shared
    // cache entry's state without starting a request of its own.
    enabled: !observeOnly && !!project && ready && !goalPending && !running && !importBlocked,
  })
  return { isError: q.isError, refetch: q.refetch, readiness: q.data ?? null }
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

/** Whether the project has its own hour-by-hour solve; `undefined` until the status is read (G5). */
export function useDispatchSolved(): boolean | undefined {
  const project = useUIStore(s => s.currentProject)
  const status = useQuery({
    queryKey: nk(project, 'simulationStatus'), queryFn: simulationApi.getStatus, enabled: !!project,
  })
  return status.isSuccess ? dispatchSolved(status.data) : undefined
}

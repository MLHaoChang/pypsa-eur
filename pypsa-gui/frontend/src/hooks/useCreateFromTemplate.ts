// "Create a project from a template", lifted from the New-project wizard's
// Templates tab (guided-mode spec §5.3, §5.9) so the hub-design Start card
// creates projects exactly the way the wizard does. Behaviour unchanged:
// G4 `noteNewProjectCreated('template')` first, the project becomes current,
// then the caller's `onCreated` (the wizard closes itself there), then the
// new project's tab opens and — off /app — the workbench (Obstacle 2, §2.6).
import { useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { useInRouterContext, useNavigate, type NavigateFunction } from 'react-router-dom'
import { projectsApi } from '../api/projects'
import type { BundleImportResult } from '../api/types'
import { getPostLoginPath } from '../auth/resume'
import { openSectionInExpert } from '../layout/flowNavigation'
import { useUIStore } from '../store/uiStore'
import { appLog } from '../store/simulationStore'
import { invalidateNetworkQueries } from '../utils/projectActions'
import { blockerMessage } from '../utils/blockerMessage'
import { nameClashFrom, showNameClashToast } from '../utils/nameClashToast'

/** A project switch refused because a study runs on the current network
 *  (P24-FE gate decision): plain words instead of the status-code line. */
export const STUDY_RUNNING_SWITCH =
  'A study is still running — wait for it to finish or abort it before switching project.'

function createErrorMessage(e: unknown): string {
  const status = (e as { response?: { status?: number } })?.response?.status
  const detail = blockerMessage(e)
  if (status === 409 && /\bis running\b/.test(detail)) return STUDY_RUNNING_SWITCH
  return `Template import failed: ${detail}`
}

// The wizard also renders outside a router (unit tests), where there is
// nowhere to navigate; router presence is fixed for a mount, so the
// conditional hook call is stable.
export function useOptionalNavigate(): NavigateFunction | null {
  const inRouter = useInRouterContext()
  // eslint-disable-next-line react-hooks/rules-of-hooks
  return inRouter ? useNavigate() : null
}

const ISSUE_FIELD = { commercial: 'commercial settings', finance: 'finance inputs' } as const

/** A template block the backend dropped on create (slice D4): the project is
 *  made, but the investment case needs the block set again. */
function issueMessage(i: NonNullable<BundleImportResult['solver_config_issues']>[number]): string {
  return `The template's ${ISSUE_FIELD[i.field] ?? i.field} were not applied: ${i.message} `
    + `(${i.code}). Set them again before running the investment case.`
}

/** Add the new project's tab (parity with Sidebar.newProjectMut) and, when
 *  created from outside the workbench (/projects), open it. */
export function useOpenInWorkbench(): (name: string) => void {
  const addTab = useUIStore(s => s.addTab)
  const navigate = useOptionalNavigate()
  return (name: string) => {
    addTab(name)
    if (navigate && window.location.pathname !== '/app') navigate(getPostLoginPath(name))
  }
}

export interface CreateFromTemplateOptions {
  /** Runs after the project is current and before the workbench opens
   *  (the wizard closes itself here). */
  onCreated?: (name: string) => void
}

/** A template created with parameters: the generic site (G2). */
export interface TemplateCreate {
  templateId: string
  params: Record<string, unknown>
  name?: string
}

/** A refusal the generic site form shows beside its field (422
 *  `site_params_invalid`), or null. */
export function siteParamsRefusal(e: unknown): { field: string; message: string } | null {
  const res = (e as { response?: { status?: number; data?: { detail?: unknown } } })?.response
  const d = res?.data?.detail as { code?: string; field?: string; message?: string } | undefined
  return res?.status === 422 && d?.code === 'site_params_invalid' && d.field && d.message
    ? { field: d.field, message: d.message } : null
}

/** `{ mutate(templateId), isPending, variables }` — the wizard's mutation.
 *  `mutate({templateId, params})` creates the generic site from its form. */
export function useCreateFromTemplate(options?: CreateFromTemplateOptions) {
  const qc = useQueryClient()
  const setCurrentProject = useUIStore(s => s.setCurrentProject)
  const setProjectName    = useUIStore(s => s.setProjectName)
  const openInWorkbench = useOpenInWorkbench()
  const onCreated = options?.onCreated

  // Available templates import via the backend's /projects/from_template/<id>
  // endpoint, which copies the bundled network.nc into a fresh project dir and
  // loads it — same success path as FromFileTab's bundle import.
  return useMutation({
    mutationFn: (arg: string | TemplateCreate) => typeof arg === 'string'
      ? projectsApi.createFromTemplate(arg)
      : projectsApi.createFromTemplate(arg.templateId, arg.name, arg.params),
    onSuccess: (res) => {
      // G4 (guided-mode spec §3.4): a new project starts Guided unless the
      // user chose a mode explicitly. First, before any navigation.
      useUIStore.getState().noteNewProjectCreated('template')
      invalidateNetworkQueries(qc, res.imported)
      qc.invalidateQueries({ queryKey: ['projects'] })
      setCurrentProject(res.imported)
      setProjectName(res.imported)
      appLog('INFO', `Created '${res.imported}' from template (${res.summary.buses} buses)`)
      toast.success(`Created '${res.imported}' from template`)
      for (const issue of res.solver_config_issues ?? []) {
        const msg = issueMessage(issue)
        appLog('WARN', msg)
        toast(msg, { icon: '⚠️', duration: 10000 })
      }
      onCreated?.(res.imported)
      openInWorkbench(res.imported)
      // Guided opens on its initial step; Expert lands in section 1 so the
      // new project starts at the top of the flow, not on a bare canvas.
      if (useUIStore.getState().uiMode === 'expert') openSectionInExpert('project')
    },
    // A refused site parameter is shown beside its field by the form.
    onError: (e: unknown) => {
      if (siteParamsRefusal(e)) return
      // N17: a taken name stays on screen with a way to open that project.
      const taken = nameClashFrom(blockerMessage(e))
      if (taken) {
        showNameClashToast(taken, () => {
          setCurrentProject(taken)
          setProjectName(taken)
          onCreated?.(taken)
          openInWorkbench(taken)
        })
        return
      }
      toast.error(createErrorMessage(e))
    },
  })
}

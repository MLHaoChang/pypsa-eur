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
import { getPostLoginPath } from '../auth/resume'
import { useUIStore } from '../store/uiStore'
import { appLog } from '../store/simulationStore'
import { invalidateNetworkQueries } from '../utils/projectActions'
import { blockerMessage } from '../utils/blockerMessage'

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

/** `{ mutate(templateId), isPending, variables }` — the wizard's mutation. */
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
    mutationFn: (templateId: string) => projectsApi.createFromTemplate(templateId),
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
      onCreated?.(res.imported)
      openInWorkbench(res.imported)
    },
    onError: (e: unknown) => toast.error(createErrorMessage(e)),
  })
}

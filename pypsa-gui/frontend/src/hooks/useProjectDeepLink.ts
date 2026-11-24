import { useEffect, useRef } from 'react'
import type { QueryClient } from '@tanstack/react-query'
import { projectsApi } from '../api/projects'
import { useUIStore } from '../store/uiStore'
import { switchToProject, type SwitchResult } from '../utils/projectActions'

/** Words for a switch that did not land, shown with a Retry. */
export function openFailureMessage(r: SwitchResult): string {
  switch (r.status) {
    case 'busy-study': return r.message
    case 'busy-solve': return 'A simulation is running and could not be stopped.'
    case 'abort-failed': return 'The running simulation could not be stopped.'
    case 'not-found': return 'This project no longer exists.'
    // `r.message` is the raw transport text ("Request failed with status code 500"):
    // say it in words, the backend's own text reaches the user through the error toast.
    case 'error': return 'The server could not open it. Try again in a moment.'
    default: return 'The project could not be opened.'
  }
}

/**
 * Post-login resume: when auth routes bounce the user back to `/app` with a
 * `?project=` query, activate that project exactly once per value and strip the
 * param. A switch that does not land is reported (`openFailure`, with Retry),
 * never left as an empty "Unnamed Network".
 */
export function useProjectDeepLink(currentProject: string | null, qc: QueryClient) {
  const handled = useRef<string | null>(null)
  useEffect(() => {
    const resumeProject = new URLSearchParams(window.location.search).get('project')
    if (!resumeProject || handled.current === resumeProject) return
    handled.current = resumeProject

    const strip = () => {
      const next = new URLSearchParams(window.location.search)
      if (next.get('project') !== resumeProject) return
      next.delete('project')
      const query = next.toString()
      window.history.replaceState({}, '', `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`)
    }
    if (resumeProject === currentProject) { strip(); return }

    // The link may carry a project NAME or an id: resolve either against the
    // list, then switch by id. An unknown ref falls through to the backend's
    // own not-found.
    const open = async () => {
      let target = resumeProject
      try {
        const all = await projectsApi.list()
        const hit = all.find(p => p.id === resumeProject) ?? all.find(p => p.name === resumeProject)
        if (hit) target = hit.id ?? hit.name
      } catch { /* list unavailable — let activate resolve the raw ref */ }
      const ui = useUIStore.getState()
      ui.setOpenFailure(null)
      let r: SwitchResult
      try {
        r = await switchToProject(target, qc)
      } catch (e) {
        r = { status: 'error', message: String((e as Error)?.message ?? e) }
      }
      if (r.status === 'switched' || r.status === 'noop') return
      useUIStore.getState().setOpenFailure({ target: resumeProject, message: openFailureMessage(r) })
    }
    void open().finally(strip)
  }, [currentProject, qc])
}

/** Retry for the failure strip: the same open, by the ref the link carried. */
export async function retryOpen(qc: QueryClient) {
  const f = useUIStore.getState().openFailure
  if (!f) return
  useUIStore.getState().setOpenFailure(null)
  let r: SwitchResult
  try { r = await switchToProject(f.target, qc) } catch (e) {
    r = { status: 'error', message: String((e as Error)?.message ?? e) }
  }
  if (r.status !== 'switched' && r.status !== 'noop') {
    useUIStore.getState().setOpenFailure({ target: f.target, message: openFailureMessage(r) })
  }
}

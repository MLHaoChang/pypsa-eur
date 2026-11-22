// The open project's kind, from the projects list (pio/08): `undefined` while
// that is unknown (list not loaded, or the open project not in it), `null` for
// an ordinary project, else the kind. Unknown is never read as "not a study",
// and never as "a study": callers keep today's view until the list says.
import { useQuery } from '@tanstack/react-query'
import { projectsApi } from '../api/projects'
import { useUIStore } from '../store/uiStore'
import { STUDY_KIND } from '../utils/projectActions'

function useProjectsList() {
  return useQuery({ queryKey: ['projects'], queryFn: projectsApi.list, staleTime: 10_000 })
}

export function useOpenProjectKind(): string | null | undefined {
  const currentProject = useUIStore(s => s.currentProject)
  const { data: projects } = useProjectsList()
  const open = projects?.find(p => p.id === currentProject || p.name === currentProject)
  return open ? (open.project_kind ?? null) : undefined
}

/**
 * True while a project is open and the list that says its kind has not
 * loaded yet. Views that differ by kind hold their kind-specific parts until
 * then, so a study never shows the hub sidebar for a moment and then drops it.
 */
export function useOpenProjectKindPending(): boolean {
  const currentProject = useUIStore(s => s.currentProject)
  const { isPending } = useProjectsList()
  return !!currentProject && isPending
}

/** True only when the projects list says the open project is a planning → dynamics study. */
export function useOpenProjectIsStudy(): boolean {
  return useOpenProjectKind() === STUDY_KIND
}

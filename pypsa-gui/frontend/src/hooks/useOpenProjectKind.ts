// The open project's kind, from the projects list (pio/08): `undefined` while
// that is unknown (list not loaded, or the open project not in it), `null` for
// an ordinary project, else the kind. Unknown is never read as "not a study",
// and never as "a study": callers keep today's view until the list says.
import { useQuery } from '@tanstack/react-query'
import { projectsApi } from '../api/projects'
import { useUIStore } from '../store/uiStore'
import { STUDY_KIND } from '../utils/projectActions'

export function useOpenProjectKind(): string | null | undefined {
  const currentProject = useUIStore(s => s.currentProject)
  const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: projectsApi.list, staleTime: 10_000 })
  const open = projects?.find(p => p.id === currentProject || p.name === currentProject)
  return open ? (open.project_kind ?? null) : undefined
}

/** True only when the projects list says the open project is a planning → dynamics study. */
export function useOpenProjectIsStudy(): boolean {
  return useOpenProjectKind() === STUDY_KIND
}

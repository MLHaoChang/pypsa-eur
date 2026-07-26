import type { UnclaimedProject } from '../api/projects'
import type { ProjectInfo } from '../api/types'

export function shouldShowResume({
  lastId,
  accessibleIds,
}: {
  lastId: string | null | undefined
  accessibleIds: readonly string[]
}): boolean {
  if (!lastId) return false
  return accessibleIds.includes(lastId)
}

export function projectIdentifiers(project: ProjectInfo): string[] {
  return [project.id, project.name].filter((value): value is string => Boolean(value))
}

export function findProjectByIdentifier(
  projects: readonly ProjectInfo[],
  identifier: string | null | undefined,
): ProjectInfo | null {
  if (!identifier) return null
  return projects.find(project => projectIdentifiers(project).includes(identifier)) ?? null
}

export function isRootProject(project: ProjectInfo): boolean {
  return project.parent_project == null
}

// Newest first. Projects whose timestamp does not parse keep their relative
// order and sink to the bottom rather than poisoning the comparator.
export function sortProjectsByRecency(projects: readonly ProjectInfo[]): ProjectInfo[] {
  return [...projects].sort((a, b) => {
    const left = Date.parse(a.created_at)
    const right = Date.parse(b.created_at)
    if (Number.isNaN(left) && Number.isNaN(right)) return 0
    if (Number.isNaN(left)) return 1
    if (Number.isNaN(right)) return -1
    return right - left
  })
}

export function countScenarios(projects: readonly ProjectInfo[], rootName: string): number {
  return projects.filter(project => project.parent_project === rootName).length
}

// Badge text for a root's scenario children — `null` when there are none, so
// the caller can skip rendering the badge entirely.
export function scenarioCountLabel(count: number): string | null {
  if (!Number.isFinite(count) || count <= 0) return null
  return `+${count} scenario${count === 1 ? '' : 's'}`
}

export function formatCount(count: number, singular: string, plural: string): string {
  return `${count} ${count === 1 ? singular : plural}`
}

export function projectKindLabel(project: ProjectInfo): string {
  return project.parent_project
    ? `Scenario of ${project.parent_project}`
    : 'Root project'
}

// Roots first, then scenario children, alphabetically within each group.
// The backend lists a scenario child as its own importable row, so grouping
// keeps "Base (+1 scenario)" above the child it would bring along.
export function sortUnclaimedRows(
  rows: readonly UnclaimedProject[],
): UnclaimedProject[] {
  return [...rows].sort((a, b) => {
    const aIsRoot = a.parent_project == null
    const bIsRoot = b.parent_project == null
    if (aIsRoot !== bIsRoot) return aIsRoot ? -1 : 1
    return a.name.localeCompare(b.name)
  })
}

// The import section stays hidden both when nothing is unclaimed and when the
// endpoint is unavailable (auth disabled → the query resolves to an empty
// list), so there is never an empty "Import" block on the page. It stays
// mounted after the last row is imported, because a successful import removes
// the row it was triggered from and the confirmation has to survive that.
export function shouldShowImportSection(
  unclaimed: readonly UnclaimedProject[] | null | undefined,
  hasCompletedImports = false,
): boolean {
  return Boolean((unclaimed && unclaimed.length > 0) || hasCompletedImports)
}

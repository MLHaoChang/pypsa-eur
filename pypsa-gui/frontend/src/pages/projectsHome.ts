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

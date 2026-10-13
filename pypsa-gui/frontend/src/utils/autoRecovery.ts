import type { ProjectInfo } from '../api/types'

/**
 * What the App's auto-recovery should do for `current`, once it has seen that
 * the backend's in-memory network is empty.
 *
 *   'load'    — an ordinary project whose network was lost (uvicorn --reload,
 *               a server restart): re-load it from disk.
 *   'study'   — a planning → dynamics study. It has NO network by design, so
 *               an empty one is not a loss and loading would 404.
 *   'missing' — no such project on disk; nothing to recover.
 *
 * Pure, so the decision is testable without rendering the whole App.
 */
export type Recovery = 'load' | 'study' | 'missing'

export function recoveryFor(
  projects: Pick<ProjectInfo, 'name' | 'project_kind'>[],
  current: string,
): Recovery {
  const project = projects.find(p => p.name === current)
  if (!project) return 'missing'
  return project.project_kind === 'planning_dynamics' ? 'study' : 'load'
}

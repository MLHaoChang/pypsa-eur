import { FLOW_SECTIONS, type FlowSectionId } from '../api/flow'

// The flow section a project was left in, remembered across reloads (design
// §7: reopening returns to the last mode and section; the mode is already
// stored by `ui-mode`). localStorage, per project, like the active site: a
// per-viewer convenience, so every read and write survives storage that is
// empty, full or throwing.

export const activeSectionKey = (project: string): string => `network-diagram:active-section:${project}`

export function readActiveSection(project: string | null): FlowSectionId | null {
  if (!project) return null
  try {
    const v = localStorage.getItem(activeSectionKey(project))
    return (FLOW_SECTIONS as readonly string[]).includes(v ?? '') ? (v as FlowSectionId) : null
  } catch { return null }
}

export function writeActiveSection(project: string | null, section: FlowSectionId | null): void {
  if (!project) return
  try {
    if (section) localStorage.setItem(activeSectionKey(project), section)
    else localStorage.removeItem(activeSectionKey(project))
  } catch { /* noop */ }
}

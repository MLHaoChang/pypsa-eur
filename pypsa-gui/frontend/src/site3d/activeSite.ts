// Which site the 3D view shows for a project, remembered across reloads.
// localStorage, per project, like `network-diagram:canvas-view`.
// Pure helpers; the reactive copy lives in uiStore.activeSiteId.

export const activeSiteKey = (project: string | null): string => `network-diagram:active-site:${project ?? '__local__'}`

export function readActiveSite(project: string | null): string | null {
  try { return localStorage.getItem(activeSiteKey(project)) } catch { return null }
}

export function writeActiveSite(project: string | null, siteId: string | null): void {
  try {
    if (siteId) localStorage.setItem(activeSiteKey(project), siteId)
    else localStorage.removeItem(activeSiteKey(project))
  } catch { /* noop */ }
}

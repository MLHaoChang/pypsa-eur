// Which thumbnails were captured in this session (visual-layers plan 3 S5):
// a version per (project, site) the canvas bumps after a successful PUT, so
// the hub-design card's <img> re-reads the file. No three; main bundle.
import { create } from 'zustand'

interface ThumbnailState {
  versions: Record<string, number>
  bump: (project: string | null, siteId: string) => void
  resetForTests: () => void
}

export const thumbnailKey = (project: string | null, siteId: string): string => `${project ?? '__local__'}/${siteId}`

export const useThumbnailStore = create<ThumbnailState>(set => ({
  versions: {},
  bump: (project, siteId) => set(s => {
    const k = thumbnailKey(project, siteId)
    return { versions: { ...s.versions, [k]: (s.versions[k] ?? 0) + 1 } }
  }),
  resetForTests: () => set({ versions: {} }),
}))

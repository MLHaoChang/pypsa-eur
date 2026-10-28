// Whether Site 3D shows the "New 3D site" panel (part Map, opened by
// "Create 3D site"). View state only; nothing here is persisted.
import { create } from 'zustand'

interface SiteCreationState {
  open: boolean
  setOpen: (open: boolean) => void
}

export const useSiteCreation = create<SiteCreationState>(set => ({
  open: false,
  setOpen: open => set({ open }),
}))

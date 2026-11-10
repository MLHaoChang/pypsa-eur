import { create } from 'zustand'

// Whether the Memory dialog is open, and which item's edit form it opens on.
// Its own store (not uiStore): the dialog is account-level, not project state,
// so a project switch or a navigation reset never touches it.
export type MemoryTab = 'items' | 'proposals' | 'suggested'

interface MemoryUIState {
  open: boolean
  tab: MemoryTab
  /** Open this item's edit form when the dialog shows (memory card "Edit first", toast "Edit"). */
  editId: string | null
  openMemory: (opts?: { tab?: MemoryTab; editId?: string | null }) => void
  closeMemory: () => void
  setTab: (tab: MemoryTab) => void
  clearEdit: () => void
  /** Memory cards (tool_use ids) whose "Not now" was recorded in this tab. */
  dismissed: ReadonlySet<string>
  noteDismissed: (toolUseId: string) => void
}

export const useMemoryStore = create<MemoryUIState>(set => ({
  open: false,
  tab: 'items',
  editId: null,
  openMemory: (opts = {}) => set({
    open: true, tab: opts.tab ?? 'items', editId: opts.editId ?? null,
  }),
  closeMemory: () => set({ open: false, editId: null }),
  setTab: tab => set({ tab }),
  clearEdit: () => set({ editId: null }),
  dismissed: new Set(),
  noteDismissed: toolUseId => set(s => ({ dismissed: new Set(s.dismissed).add(toolUseId) })),
}))

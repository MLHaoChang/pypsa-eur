import { create } from 'zustand'

// Assistant-assurance 06 (A5.5, UX 31, GK G6): the chat's one polite status
// region. Repeating the line already announced is a no-op, so A, A is one
// announcement and A, B, A is three. Announcing never moves focus.
interface ChatAnnouncerState {
  text: string
  /** How many announcements were made (a changed line is one). */
  count: number
  announce: (text: string) => void
}

export const useChatAnnouncer = create<ChatAnnouncerState>((set, get) => ({
  text: '',
  count: 0,
  announce: (raw) => {
    const text = raw.trim()
    if (text === '' || text === get().text) return
    set({ text, count: get().count + 1 })
  },
}))

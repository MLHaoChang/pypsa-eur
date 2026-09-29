import type { UiMode } from '../store/uiStore'

// User-facing copy for the Guided / Expert switch (guided-mode spec §3.3).
// Shared by the header's segmented control and the command palette entry so
// both surfaces say the same thing.
export const UI_MODE_TITLES: Record<UiMode, string> = {
  guided: 'A step-by-step hub design with the assistant doing the engineering; advanced panels are hidden but reachable through the assistant.',
  expert: 'Every panel and tab, as today.',
}

export function uiModeToast(mode: UiMode): string {
  return mode === 'guided'
    ? 'Guided mode on — advanced panels hidden, ask the assistant for any of them'
    : 'Expert mode on'
}

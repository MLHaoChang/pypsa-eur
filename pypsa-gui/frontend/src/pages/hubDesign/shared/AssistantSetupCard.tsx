// UX M8 (U3; assessment G-1): Guided without a usable assistant. One card,
// in the centre, once: the cards work by hand, and "Set up the assistant"
// opens the assistant panel, whose greeting carries the key form. Renders
// nothing while readiness is unknown or the assistant is ready, and nothing
// once dismissed for the project (kept in the browser: UM08-CHROME-1).
import { Sparkles, X } from 'lucide-react'
import { useChatReadiness } from '../../../hooks/useChatProfiles'
import { useUIStore } from '../../../store/uiStore'
import { readSetupCardDismissed, useHubDesignStore } from '../hubDesignStore'

export function AssistantSetupCard() {
  const { ready } = useChatReadiness()
  const project = useUIStore(s => s.currentProject)
  const dismissed = useHubDesignStore(s => s.setupCardDismissedFor.includes(project ?? '')) || readSetupCardDismissed(project)
  if (ready !== false || dismissed) return null
  return (
    <div data-testid="assistant-setup-card"
      className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-bg-2 px-4 py-3 text-[12px]">
      <span className="flex-1 min-w-[16rem] text-text">
        Every step below works by hand. To let the assistant do steps for you, set it up once with an API key.
      </span>
      <button type="button" data-testid="assistant-setup-open"
        onClick={() => useUIStore.getState().setAssistantDockOpen(true)}
        className="inline-flex items-center gap-1 rounded border border-accent/60 px-2 py-1 text-[11px] text-accent hover:bg-accent/10">
        <Sparkles size={12} /> Set up the assistant
      </button>
      <button type="button" data-testid="assistant-setup-dismiss" aria-label="Dismiss" title="Dismiss. You can set up the assistant later from its button at the right edge."
        onClick={() => {
          useHubDesignStore.getState().dismissSetupCard(project)
          // The card unmounts; keep keyboard focus nearby: the guide button, else the dock rail.
          const next = document.querySelector<HTMLElement>('[data-testid="hub-guide-button"]')
            ?? document.querySelector<HTMLElement>('[data-testid="companion-live"]')
          next?.focus()
        }}
        className="rounded p-1 text-muted hover:text-text">
        <X size={13} />
      </button>
    </div>
  )
}

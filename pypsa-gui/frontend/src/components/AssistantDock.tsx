import { MessageSquare, PanelRightClose } from 'lucide-react'
import { useUIStore } from '../store/uiStore'
import ChatPanel from './ChatPanel'
import { ErrorBoundary } from './ErrorBoundary'

/**
 * The assistant's own column, mounted beside the main area rather than inside
 * the SlidePanel slot.
 *
 * Why this is not a SlidePanel: `activeSlidePanel` holds ONE value, so while
 * the assistant occupied it the assistant was mutually exclusive with every
 * view it exists to explain — and `applyUiNavigate` calling
 * `setSlidePanel('results')` closed the assistant in the act of obeying you.
 *
 * ChatPanel is rendered unconditionally and hidden with CSS when collapsed.
 * Unmounting it mid-turn is what produced "still streaming, no tokens on
 * screen"; keeping it mounted is the fix, not an optimisation.
 */
export default function AssistantDock() {
  // Per-field selectors, not the bare useUIStore() — see the identical
  // convention (and rationale) at CommandPalette.tsx:223-227: the bare hook
  // subscribes to every slice, so an unrelated mutation elsewhere (e.g.
  // SnapshotPicker scrubbing resultsSnapshotIdx per frame) re-renders this
  // whole subtree. That cost is new to this branch: ChatPanel previously
  // only rendered while the 'chat' slide panel was open, so it never paid
  // for updates it doesn't consume — now that it's always mounted, it does.
  const assistantDockOpen = useUIStore((s) => s.assistantDockOpen)
  const setAssistantDockOpen = useUIStore((s) => s.setAssistantDockOpen)

  // `data-no-panel-close` below is load-bearing. App.tsx's
  // click-outside-to-close effect closes the active slide panel on any
  // mousedown that isn't inside the panel itself, the Sidebar (<aside>), or
  // an element marked that way — and the dock is none of the three. Without
  // the marker, clicking the composer to ask a follow-up question about
  // Results would close Results: the same "the assistant and the view it
  // explains cannot coexist" failure this component exists to remove, just
  // running the other direction. It was structurally impossible before only
  // because the assistant WAS the slide panel. Pinned by
  // AssistantDock.eviction.test.tsx.
  return (
    <div
      className={`flex flex-col min-h-0 border-l border-border bg-bg shrink-0 ${
        assistantDockOpen ? 'w-[380px]' : 'w-10'
      }`}
      data-testid="assistant-dock"
      data-no-panel-close
    >
      {assistantDockOpen ? (
        <div className="flex items-center gap-2 px-3 h-9 border-b border-border bg-bg-2 shrink-0">
          <span className="font-mono text-[9px] font-bold uppercase tracking-[0.14em] text-accent">
            ASSISTANT
          </span>
          <span className="flex-1" />
          <button
            onClick={() => setAssistantDockOpen(false)}
            title="Collapse the assistant"
            aria-label="Collapse the assistant"
            aria-expanded={assistantDockOpen}
            data-testid="assistant-dock-collapse"
            className="text-muted hover:text-text p-1 rounded hover:bg-panel transition-colors"
          >
            <PanelRightClose size={14} />
          </button>
        </div>
      ) : (
        <button
          onClick={() => setAssistantDockOpen(true)}
          title="Open the assistant"
          aria-label="Open the assistant"
          aria-expanded={assistantDockOpen}
          data-testid="assistant-dock-launcher"
          className="flex items-center justify-center h-10 w-full text-muted hover:text-accent hover:bg-panel transition-colors"
        >
          <MessageSquare size={16} />
        </button>
      )}

      {/* Never unmounted — see the module docstring. */}
      <div
        className={`flex-1 min-h-0 overflow-hidden ${assistantDockOpen ? '' : 'hidden'}`}
        data-testid="assistant-dock-body"
      >
        {/*
          No `key` here, unlike the ErrorBoundary around the FullPageTab at
          App.tsx:616 (keyed on `${activeSlidePanel}-${currentProject}` so
          navigating away and back clears a stuck error). That one wraps a
          *conditionally-mounted* panel, where a remount is a normal,
          frequent event driven by navigation. This dock's true sibling is
          the always-mounted, `display:none`-toggled canvas column at
          App.tsx:589, which also has no key — and for the same reason: this
          subtree is deliberately never remounted by anything, so there is no
          navigation event a key could hook into. (Do not "fix" this by
          keying on `assistantDockOpen` — that reintroduces a remount on
          every collapse/expand, which kills a streaming turn exactly like
          the unmount this component exists to prevent; see
          AssistantDock.test.tsx's mount-identity test.)

          Residual: if a crash is deterministic from persisted chat state
          (e.g. a malformed message replayed from chat.jsonl), Retry
          re-renders that same state and can re-throw immediately — there is
          no navigation-driven remount to fall back on here, unlike the
          slide-panel case. Acceptable for now; revisit if that shows up.
        */}
        <ErrorBoundary label="The assistant crashed">
          <ChatPanel />
        </ErrorBoundary>
      </div>
    </div>
  )
}

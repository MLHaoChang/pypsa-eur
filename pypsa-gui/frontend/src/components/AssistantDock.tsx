import { useCallback, useRef } from 'react'
import { MessageSquare, Mic, PanelRightClose } from 'lucide-react'
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
  // convention (and rationale) in CommandPalette.tsx: the bare hook
  // subscribes to every slice, so an unrelated mutation elsewhere (e.g.
  // SnapshotPicker scrubbing resultsSnapshotIdx per frame) re-renders this
  // whole subtree. That cost is new to this branch: ChatPanel previously
  // only rendered while the 'chat' slide panel was open, so it never paid
  // for updates it doesn't consume — now that it's always mounted, it does.
  const assistantDockOpen = useUIStore((s) => s.assistantDockOpen)
  const setAssistantDockOpen = useUIStore((s) => s.setAssistantDockOpen)
  const assistantDockWidth = useUIStore((s) => s.assistantDockWidth)
  const setAssistantDockWidth = useUIStore((s) => s.setAssistantDockWidth)
  const dragRef = useRef<{ startX: number; startW: number } | null>(null)

  // Drag to resize. The store keeps the width the user ASKED for and is
  // written once, at release — not per mousemove, and never with a value the
  // layout imposed. That separation is the compare rail's lesson: clamping
  // by writing the smaller number back is what silently rewrote a 700px
  // preference the first time something opened beside it.
  const onResizeDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    dragRef.current = { startX: e.clientX, startW: assistantDockWidth }
    const onMove = (ev: MouseEvent) => {
      const d = dragRef.current
      if (!d) return
      // A mouseup released outside the window never arrives, so a button-less
      // move is the only signal the gesture ended.
      if (ev.buttons === 0) { onUp(); return }
      // Dragging the LEFT edge of a right-hand dock: leftward widens.
      setAssistantDockWidth(d.startW + (d.startX - ev.clientX))
    }
    const onUp = () => {
      dragRef.current = null
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [assistantDockWidth, setAssistantDockWidth])

  // `data-no-panel-close` below is load-bearing. App.tsx's
  // click-outside-to-close effect closes the active slide panel on any
  // mousedown that isn't inside the panel itself, the Sidebar (<aside>), or
  // an element marked that way — and the dock is none of the three. Without
  // the marker, clicking the composer to ask a follow-up question about
  // Results would close Results: the same "the assistant and the view it
  // explains cannot coexist" failure this component exists to remove, just
  // running the other direction. It was structurally impossible before only
  // because the assistant WAS the slide panel. Pinned by
  // AssistantDock.eviction.test.tsx; its keyboard twin is the editable-target
  // guard on App.tsx's global Escape handler.
  return (
    <div
      className={`relative flex flex-col min-h-0 border-l border-border bg-bg shrink-0 ${
        assistantDockOpen ? '' : 'w-10'
      }`}
      style={assistantDockOpen ? { width: `${assistantDockWidth}px` } : undefined}
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
        <div className="flex flex-col items-center gap-1 pt-2">
          <button
            onClick={() => setAssistantDockOpen(true)}
            title="Open the assistant"
            aria-label="Open the assistant"
            aria-expanded={assistantDockOpen}
            data-testid="assistant-dock-launcher"
            className="flex flex-col items-center gap-2 w-full py-2 text-muted hover:text-accent hover:bg-panel transition-colors"
          >
            <MessageSquare size={18} />
            {/* An unlabelled glyph in a 40px gutter reads as decoration. The
                word is what makes it findable without hunting. */}
            <span
              className="font-mono text-[9px] font-bold uppercase tracking-[0.14em] text-accent"
              style={{ writingMode: 'vertical-rl' }}
            >
              Assistant
            </span>
          </button>
          {/* The spec's collapsed strip carries "the launcher button and the
              microphone" — voice is the affordance most devalued by being
              buried, since it exists to save the trip to the keyboard. */}
          <button
            onClick={() => setAssistantDockOpen(true)}
            title="Open the assistant and dictate"
            aria-label="Open the assistant and dictate"
            data-testid="assistant-dock-mic"
            className="flex items-center justify-center w-full py-2 text-muted hover:text-accent hover:bg-panel transition-colors"
          >
            <Mic size={16} />
          </button>
        </div>
      )}

      {assistantDockOpen && (
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize the assistant"
          data-testid="assistant-dock-resize"
          onMouseDown={onResizeDown}
          className="absolute left-0 top-0 h-full w-1 cursor-col-resize hover:bg-accent/40 z-10"
        />
      )}

      {/* Never unmounted — see the module docstring. */}
      <div
        className={`flex-1 min-h-0 overflow-hidden ${assistantDockOpen ? '' : 'hidden'}`}
        data-testid="assistant-dock-body"
      >
        {/*
          No `key` here, unlike App.tsx's ErrorBoundary around `FullPageTab`
          (keyed on `${activeSlidePanel}-${currentProject}` so navigating away
          and back clears a stuck error). That one wraps a
          *conditionally-mounted* panel, where a remount is a normal,
          frequent event driven by navigation. This dock's true sibling is
          App.tsx's always-mounted canvas column — the div whose className
          toggles `hidden` for a full-screen tab rather than unmounting the
          canvas — which also has no key, and for the same reason: this
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

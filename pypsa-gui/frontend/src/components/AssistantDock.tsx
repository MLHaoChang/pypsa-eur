import { useCallback, useEffect, useRef, useState } from 'react'
import { PanelRightClose } from 'lucide-react'
import { useUIStore } from '../store/uiStore'
import { useChatProfiles, useChatReadiness } from '../hooks/useChatProfiles'
import ChatPanel from './ChatPanel'
import CompanionLauncher, { type CompanionPresence } from './CompanionLauncher'
import { KEY_RESULT_TABS, keyResultsWalkRequested, openProjectList, openProjectRequested, startKeyResultsWalk } from './liveWalk'
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

  const profilesQuery = useChatProfiles()
  const { effectiveProfileId, ready } = useChatReadiness()
  const [presence, setPresence] = useState<CompanionPresence>('idle')
  const [liveOpen, setLiveOpen] = useState(false)
  const [liveCaption, setLiveCaption] = useState('')
  const walkCancel = useRef<(() => void) | null>(null)
  useEffect(() => () => { walkCancel.current?.() }, [])

  const stopWalk = useCallback(() => {
    walkCancel.current?.()
    walkCancel.current = null
  }, [])

  const forwardToHarness = useCallback((text: string) => {
    if (ready !== true) return
    window.dispatchEvent(new CustomEvent('companion:live-utterance', { detail: text }))
  }, [ready])

  const onLiveSubmit = useCallback((text: string) => {
    if (keyResultsWalkRequested(text)) {
      stopWalk()
      setPresence('thinking')
      setLiveCaption('Opening each results tab.')
      walkCancel.current = startKeyResultsWalk({
        onStep: (tab, index, last) => {
          setLiveCaption(`Tab ${index + 1} of ${KEY_RESULT_TABS.length}. ${tab.label}. ${tab.blurb}`)
          setPresence(last ? 'speaking' : 'thinking')
        },
        onPaceMiss: () => {
          setPresence('listening')
          setLiveCaption('Could not hold the tab. The pause did not come from the dev server.')
        },
      })
      forwardToHarness(text)
      return
    }
    if (openProjectRequested(text)) {
      stopWalk()
      openProjectList()
      setPresence('speaking')
      setLiveCaption('Opening the project list.')
      forwardToHarness(text)
      return
    }
    if (ready === true) {
      setPresence('thinking')
      setLiveCaption('Sent to the assistant.')
      forwardToHarness(text)
      return
    }
    setPresence('listening')
    setLiveCaption('Ask to walk through the key results in each tab, or to open a project.')
  }, [forwardToHarness, ready, stopWalk])
  const profileLabel =
    profilesQuery.data?.profiles.find((p) => p.id === effectiveProfileId)?.label
    ?? profilesQuery.data?.profiles.find((p) => p.id === profilesQuery.data.active_profile_id)?.label
    ?? 'Model'

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
    <>
      <div
        className={`relative flex flex-col min-h-0 bg-bg shrink-0 transition-[width] duration-300 ease-out motion-reduce:transition-none ${
          assistantDockOpen
            ? 'border-l border-border'
            : 'w-0 overflow-visible border-0'
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
        ) : null}

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
      {!assistantDockOpen && (
        <CompanionLauncher
          presence={presence}
          profileLabel={profileLabel}
          liveOpen={liveOpen}
          liveCaption={liveCaption}
          modelKeyMissing={ready === false}
          onLiveSubmit={onLiveSubmit}
          onCompose={() => {
            stopWalk()
            useUIStore.getState().setAssistantEntry('compose')
            setAssistantDockOpen(true)
          }}
          onSpeak={() => {
            stopWalk()
            useUIStore.getState().setAssistantEntry('speak')
            setAssistantDockOpen(true)
          }}
          onLive={() => {
            // Stays on this page. A model key sends the turn through the
            // chat harness; without one, the walk still calls applyUiNavigate.
            if (liveOpen) {
              stopWalk()
              setLiveOpen(false)
              setPresence('idle')
              setLiveCaption('')
              return
            }
            setLiveOpen(true)
            setPresence('listening')
            setLiveCaption('Listening. Ask to walk through the key results in each tab, or to open a project.')
          }}
          onProfiles={() => {
            stopWalk()
            useUIStore.getState().setAssistantEntry('profiles')
            setAssistantDockOpen(true)
          }}
        />
      )}
    </>
  )
}

import { useCallback, useEffect, useRef, useState } from 'react'
import { PanelRightClose, Pin, PinOff } from 'lucide-react'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'
import { useChatProfiles, useChatReadiness } from '../hooks/useChatProfiles'
import ChatPanel from './ChatPanel'
import AssistantActivity from './AssistantActivity'
import { ConfirmationCard } from './ConfirmationCard'
import CompanionLauncher, { type CompanionWorkState } from './CompanionLauncher'
import CompanionOutcome from './CompanionOutcome'
import { keyResultsWalkRequested, openProjectList, openProjectRequested, startKeyResultsWalk, type ResultsWalk } from './liveWalk'
import { ErrorBoundary } from './ErrorBoundary'
import { ensureLiveDurableThread } from '../api/durableConversations'

// Matches the companion-echo-fade animation in index.css.
const LIVE_ECHO_MS = 1_600

function clearTimer(timer: { current: number | null }) {
  if (timer.current != null) window.clearTimeout(timer.current)
  timer.current = null
}

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
  const assistantDockPinned = useUIStore((s) => s.assistantDockPinned)
  const setAssistantDockPinned = useUIStore((s) => s.setAssistantDockPinned)
  const assistantDockWidth = useUIStore((s) => s.assistantDockWidth)
  const setAssistantDockWidth = useUIStore((s) => s.setAssistantDockWidth)
  const dragRef = useRef<{ startX: number; startW: number } | null>(null)

  const [surface, setSurface] = useState<'chat' | 'activity'>('chat')
  useEffect(() => { if (!assistantDockOpen) setSurface('chat') }, [assistantDockOpen])
  const profilesQuery = useChatProfiles()
  const { effectiveProfileId, ready } = useChatReadiness()
  const streaming = useChatStore((s) => s.streaming)
  const pending = useChatStore((s) => s.pending)
  const choice = useChatStore((s) => s.choice)
  const queued = useChatStore((s) => s.requestQueue.length > 0)
  const error = useChatStore((s) => s.error)
  const [liveOpen, setLiveOpen] = useState(false)
  const [liveCaption, setLiveCaption] = useState('')
  const [liveEcho, setLiveEcho] = useState('')
  const [walking, setWalking] = useState(false)
  const [walkNextReady, setWalkNextReady] = useState(false)
  const walkCancel = useRef<ResultsWalk | null>(null)
  const echoTimer = useRef<number | null>(null)
  const liveGeneration = useRef(0)
  // Conversation/decision state is independent of in-page text and audio.
  // No continuous capture/playback exists here yet, so media remains off.
  const workState: CompanionWorkState = pending ? 'waiting_for_approval'
    : choice ? 'waiting_for_input'
    : streaming || walking ? 'running'
    : queued ? 'queued'
    : error ? 'failed'
    : 'idle'
  useEffect(() => () => {
    walkCancel.current?.()
    clearTimer(echoTimer)
    liveGeneration.current += 1
  }, [])

  const stopWalk = useCallback(() => {
    walkCancel.current?.()
    walkCancel.current = null
    setWalking(false)
    setWalkNextReady(false)
  }, [])

  const flashEcho = useCallback((text: string) => {
    clearTimer(echoTimer)
    setLiveEcho(text)
    echoTimer.current = window.setTimeout(() => {
      setLiveEcho('')
      echoTimer.current = null
    }, LIVE_ECHO_MS)
  }, [])

  const forwardToHarness = useCallback((text: string) => {
    if (ready !== true) return
    window.dispatchEvent(new CustomEvent('companion:live-utterance', { detail: text }))
  }, [ready])

  const onLiveSubmit = useCallback((text: string) => {
    if (keyResultsWalkRequested(text)) {
      stopWalk()
      setLiveEcho('')
      setWalking(true)
      setLiveCaption('Waiting for matching result evidence.')
      walkCancel.current = startKeyResultsWalk({
        onStep: (tab, _index, last) => {
          setLiveCaption(`${tab.label}. ${tab.blurb} Matching result data is displayed.`)
          setWalkNextReady(!last)
          if (last) setWalking(false)
        },
        onWaiting: (tab) => { setWalkNextReady(false); setLiveCaption(`Waiting for matching ${tab.label} evidence…`) },
        onStop: (reason) => {
          setLiveCaption(reason)
          setWalkNextReady(false)
          setWalking(false)
        },
      })
      return
    }
    if (openProjectRequested(text)) {
      stopWalk()
      setLiveEcho('')
      openProjectList()
      setLiveCaption('Opening the project list.')
      return
    }
    if (ready === true) {
      if (useUIStore.getState().projectMismatch) {
        setLiveCaption('The assistant is busy. Nothing new was sent.')
        return
      }
      if (useChatStore.getState().streaming || useChatStore.getState().pending) {
        // Keep one pending Live correction without opening the console
        // (issue 02). ChatPanel drains requestQueue when the turn ends.
        const queued = useChatStore.getState().sendRequest(text, {
          source: 'live',
          openDock: false,
        })
        setLiveCaption(
          queued
            ? 'Queued — will send when the assistant is free.'
            : 'The assistant is busy. Nothing new was sent.',
        )
        return
      }
      stopWalk()
      setLiveCaption('')
      flashEcho(text)
      forwardToHarness(text)
      return
    }
    setLiveCaption('Add an API key for the assistant first. Tab changes and opening a project still work without one.')
  }, [flashEcho, forwardToHarness, ready, stopWalk])
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
            <button type="button" aria-pressed={surface === 'chat'} onClick={() => setSurface('chat')}
              className={`text-xs ${surface === 'chat' ? 'text-accent' : 'text-muted'}`}>Chat</button>
            <button type="button" aria-pressed={surface === 'activity'} onClick={() => setSurface('activity')}
              className={`text-xs ${surface === 'activity' ? 'text-accent' : 'text-muted'}`}>Activity</button>
            <span className="flex-1" />
            {/* UX overhaul S5 (Q5a): pinned, the dock never steps aside by
                itself over Results or without a key (layout/dockAutoCollapse.ts). */}
            <button
              onClick={() => setAssistantDockPinned(!assistantDockPinned)}
              title={assistantDockPinned ? 'Unpin: let the assistant step aside over Results' : 'Pin the assistant open'}
              aria-label={assistantDockPinned ? 'Unpin the assistant' : 'Pin the assistant open'}
              aria-pressed={assistantDockPinned}
              data-testid="assistant-dock-pin"
              className={`p-1 rounded hover:bg-panel transition-colors ${assistantDockPinned ? 'text-accent' : 'text-muted hover:text-text'}`}
            >
              {assistantDockPinned ? <Pin size={14} /> : <PinOff size={14} />}
            </button>
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
          className={`flex-1 min-h-0 overflow-hidden ${assistantDockOpen && surface === 'chat' ? '' : 'hidden'}`}
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
        {assistantDockOpen && surface === 'activity' && <ErrorBoundary label="Activity could not be displayed">
          <AssistantActivity onConversation={() => setSurface('chat')} />
        </ErrorBoundary>}
      </div>
      {!assistantDockOpen && (
        <CompanionLauncher
          workState={workState}
          navigating={walking}
          profileLabel={profileLabel}
          liveOpen={liveOpen}
          liveCaption={liveCaption}
          liveEcho={liveEcho}
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
              clearTimer(echoTimer)
              liveGeneration.current += 1
              setLiveOpen(false)
              setLiveCaption('')
              setLiveEcho('')
              return
            }
            const generation = ++liveGeneration.current
            setLiveOpen(true)
            setLiveCaption('Type a message here, or open reviewed dictation in the assistant.')
            // Issue 02/09 — bind or rebind the durable parent thread so Live
            // utteranceGeneration tracks the authoritative conversation.
            void ensureLiveDurableThread().catch(() => {
              if (liveGeneration.current !== generation) return
              setLiveCaption(
                'The durable conversation could not be bound. Open the assistant to continue with typed chat.',
              )
            })
          }}
          onProfiles={() => {
            stopWalk()
            useUIStore.getState().setAssistantEntry('profiles')
            setAssistantDockOpen(true)
          }}
        >
          <ErrorBoundary label="The assistant crashed">
            <ConfirmationCard />
            {walking && walkNextReady && <button type="button" data-no-panel-close className="fixed bottom-24 right-4 text-xs rounded border border-border bg-panel px-3 py-2" onClick={() => walkCancel.current?.next()}>Next evidence view</button>}
      <CompanionOutcome />
          </ErrorBoundary>
        </CompanionLauncher>
      )}
    </>
  )
}

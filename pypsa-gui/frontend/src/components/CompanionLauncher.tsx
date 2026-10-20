import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { MessageCircle, Mic, Pencil } from 'lucide-react'
import Pearl from './Pearl'

// Clears toasts beside the chip (max-w12rem) plus the24px right gutter.
// The workbench body ends above the24px status row, preserving bottom spacing.
export const COMPANION_TOAST_OFFSET = 220

export type CompanionWorkState = 'idle' | 'running' | 'queued' | 'waiting_for_approval' | 'waiting_for_input' | 'failed'

const WORK_LABELS: Record<CompanionWorkState, string> = {
  idle: 'Ready for a message',
  running: 'Response in progress',
  queued: 'Message queued',
  waiting_for_approval: 'Needs approval',
  waiting_for_input: 'Needs your answer',
  failed: 'Response interrupted',
}

type Props = {
  workState?: CompanionWorkState
  navigating?: boolean
  profileLabel: string
  onCompose: () => void
  onSpeak: () => void
  onLive: () => void
  onProfiles: () => void
  liveOpen?: boolean
  liveCaption?: string
  /** The user's last Live line, flashed above the input and then cleared. */
  liveEcho?: string
  modelKeyMissing?: boolean
  onLiveSubmit?: (text: string) => void
  children?: ReactNode
}

function useReducedMotion(): boolean {
  const [reduce, setReduce] = useState(() =>
    typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = () => setReduce(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return reduce
}

export default function CompanionLauncher({
  workState = 'idle', navigating = false, profileLabel, onCompose, onSpeak, onLive, onProfiles,
  liveOpen = false, liveCaption = '', liveEcho = '', modelKeyMissing = false, onLiveSubmit,
  children,
}: Props) {
  const reduce = useReducedMotion()
  const [hovered, setHovered] = useState(false)
  const [draft, setDraft] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const pearlRef = useRef<HTMLButtonElement>(null)
  const actionsId = useId()
  const composerId = useId()
  const voiceReasonId = useId()
  useEffect(() => {
    if (liveOpen) inputRef.current?.focus()
  }, [liveOpen])
  useEffect(() => {
    const outside = (event: PointerEvent) => { if (event.target instanceof Node && !rootRef.current?.contains(event.target)) setHovered(false) }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [])
  const presence = workState === 'running' ? 'thinking' : 'idle'
  const lit = presence !== 'idle'
  const echo = liveEcho.trim()
  const caption = liveCaption.trim()
  return (
    <div
      ref={rootRef}
      className="companion-launcher absolute bottom-0 right-6 z-40 flex max-w-[calc(100vw-3rem)] flex-col items-end gap-2"
      data-testid="companion-launcher"
      data-no-panel-close
      data-presence={presence}
      data-work-state={workState}
      data-media-state="off"
      data-motion={reduce ? 'off' : 'on'}
      data-hovered={hovered ? 'true' : 'false'}
      data-live={liveOpen ? 'open' : 'closed'}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={(e) => { if (!e.currentTarget.contains(document.activeElement)) setHovered(false) }}
      onFocus={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setHovered(true) }}
      onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); pearlRef.current?.focus(); setHovered(false) } }}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setHovered(false)
      }}
    >
      {children}
      {(liveOpen || workState !== 'idle') && (
        <p role="status" aria-atomic="true" data-testid="companion-work-status"
          className="rounded-lg border border-border bg-bg-2 px-3 py-1 text-[11px] text-text">
          {navigating && workState === 'running' ? 'Opening results views' : WORK_LABELS[workState]}
        </p>
      )}
      {liveOpen && (
        <form
          id={composerId}
          onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); onLive(); pearlRef.current?.focus(); setHovered(false) } }}
          className="w-72 max-w-full rounded-2xl border border-border bg-bg-2 p-3 shadow-lg"
          data-testid="companion-live-composer"
          onSubmit={(e: FormEvent) => {
            e.preventDefault()
            const text = draft.trim()
            if (!text) return
            onLiveSubmit?.(text)
            setDraft('')
          }}
        >
          {echo ? (
            <p className="companion-live-echo mb-2 text-[12px] leading-snug text-text" data-testid="companion-live-echo">
              {echo}
            </p>
          ) : null}
          {caption ? (
            <p className="mb-2 text-[11px] leading-snug text-muted" data-testid="companion-live-caption">
              {caption}
            </p>
          ) : null}
          {modelKeyMissing && (
            <p className="mb-2 text-[10px] text-muted" data-testid="companion-live-key">
              Model key is not configured. Tab changes still use the assistant&apos;s panel tool.
            </p>
          )}
          <div className="flex items-center gap-2">
            <input
              data-testid="companion-live-input"
              ref={inputRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Ask about your model or results…"
              aria-label="Message the assistant"
              className="min-w-0 flex-1 rounded-full border border-border bg-bg px-3 py-1.5 text-[12px] text-text"
            />
            <button
              type="submit"
              data-testid="companion-live-send"
              className="rounded-full bg-accent px-3 py-1.5 text-[11px] font-medium text-bg"
            >
              Send
            </button>
          </div>
        </form>
      )}
      <button
        type="button"
        onClick={onProfiles}
        data-testid="companion-profile"
        className="companion-profile max-w-[12rem] truncate rounded-full border border-border bg-bg-2 px-3 py-1 text-[10px] text-text shadow-lg"
        title={profileLabel}
        aria-label={`Assistant model: ${profileLabel}. Open the model list.`}
      >
        {profileLabel}
      </button>
      <button type="button" ref={pearlRef} className="companion-character-wrap rounded-full"
        data-lit={lit ? 'true' : 'false'} onClick={() => setHovered(true)}
        aria-label="Choose how to chat with GridSpine" aria-expanded={hovered} aria-controls={actionsId}>
        <span aria-hidden="true" data-testid="companion-character" data-shape="pearl"
          className={reduce ? '' : 'companion-idle'}>
          <Pearl />
        </span>
      </button>
      <div
        id={actionsId} role="group" aria-label="GridSpine conversation options"
        className="companion-actions flex flex-col items-stretch rounded-xl border border-border bg-bg-2 shadow-lg"
        data-testid="companion-actions"
      >
        <button
          type="button"
          onClick={onCompose}
          data-testid="companion-compose"
          aria-label="Compose a message"
          title="Open the assistant and type"
          className="px-3 py-2 text-text hover:text-accent"
        >
          <Pencil size={16} aria-hidden="true" /><span>Write</span>
        </button>
        <span className="h-4 w-px bg-border" aria-hidden="true" />
        <button
          type="button"
          onClick={onSpeak}
          data-testid="companion-speak"
          aria-label="Open reviewed dictation"
          title="Open the assistant and review dictation"
          className="px-3 py-2 text-text hover:text-accent"
        >
          <Mic size={16} aria-hidden="true" /><span>Dictate</span>
        </button>
        <span className="h-4 w-px bg-border" aria-hidden="true" />
        <button
          type="button"
          onClick={onLive}
          data-testid="companion-live"
          aria-label={liveOpen ? 'Close live chat' : 'Live chat'}
          aria-describedby={voiceReasonId}
          aria-controls={liveOpen ? composerId : undefined}
          aria-pressed={liveOpen}
          title={liveOpen ? 'Close in-page chat. Running work and pending approvals remain.' : 'Type to the assistant on this page.'}
          className={`px-3 py-2 hover:text-accent ${liveOpen ? 'text-accent' : 'text-text'}`}
        >
          <MessageCircle size={16} aria-hidden="true" /><span>{liveOpen ? 'Close live chat' : 'Live chat'}<small className="block text-[10px] font-normal text-muted">Text on this page</small></span>
        </button>
      </div>
      <div className="companion-voice-note max-w-72 rounded-lg border border-border bg-bg-2 px-3 py-2 text-[10px] text-muted">
        <button type="button" disabled aria-describedby={voiceReasonId}
          className="font-medium text-muted disabled:cursor-not-allowed">
          Start voice
        </button>
        <p id={voiceReasonId}>Continuous voice is not connected. Use reviewed dictation for speech.</p>
      </div>
    </div>
  )
}

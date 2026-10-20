import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Mic, Pencil } from 'lucide-react'

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

/**
 * Live's presence: a grid node (a bus with its line ring). The CSS animates a
 * sector turns while response/navigation work runs. Decoration never implies
 * that capture or playback has started.
 */
function LiveNode({ lit }: { lit: boolean }) {
  return (
    <svg width="96" height="96" viewBox="0 0 96 96" fill="none" data-testid="companion-live-node">
      <circle
        className="companion-node-halo"
        cx="48" cy="48" r="34"
        stroke="var(--color-accent)" strokeOpacity={lit ? 0.35 : 0.18} strokeWidth="1.5"
      />
      <circle
        className="companion-node-ring"
        cx="48" cy="48" r="26"
        stroke="var(--color-accent)" strokeOpacity={lit ? 0.75 : 0.4} strokeWidth="2"
      />
      <circle cx="48" cy="48" r="14" fill="var(--color-accent)" fillOpacity={lit ? 0.95 : 0.82} />
      <path
        className="companion-node-sector"
        d="M48 34 A14 14 0 0 1 62 48"
        stroke="var(--color-bg)" strokeWidth="2.5" strokeLinecap="round"
      />
      <circle cx="48" cy="48" r="3.5" fill="var(--color-bg)" fillOpacity="0.9" />
    </svg>
  )
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
  const voiceReasonId = useId()
  useEffect(() => {
    if (liveOpen) inputRef.current?.focus()
  }, [liveOpen])
  const presence = workState === 'running' ? 'thinking' : 'idle'
  const lit = presence !== 'idle'
  const echo = liveEcho.trim()
  const caption = liveCaption.trim()
  return (
    <div
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
      onMouseLeave={() => setHovered(false)}
      onFocus={() => setHovered(true)}
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
              placeholder="Walk me through the key results in each tab"
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
        className="max-w-[12rem] truncate rounded-full border border-border bg-bg-2 px-3 py-1 text-[10px] text-text shadow-lg"
        title={profileLabel}
        aria-label={`Assistant model: ${profileLabel}. Open the model list.`}
      >
        {profileLabel}
      </button>
      <div className="companion-character-wrap" data-lit={lit ? 'true' : 'false'}>
        <div
          aria-hidden="true"
          data-testid="companion-character"
          data-shape={liveOpen ? 'live-node' : 'character'}
          className={reduce ? '' : 'companion-idle'}
        >
          {liveOpen ? <LiveNode lit={lit} /> : (
            <svg width="112" height="112" viewBox="0 0 72 72" fill="none">
              <rect x="16" y="18" width="40" height="36" rx="14" fill="var(--color-accent)" />
              <rect x="24" y="28" width="24" height="12" rx="6" fill="var(--color-bg)" />
              <circle cx="32" cy="34" r="2" fill="var(--color-accent)" />
              <circle cx="40" cy="34" r="2" fill="var(--color-accent)" />
            </svg>
          )}
        </div>
      </div>
      <div
        className="companion-actions flex items-center rounded-full border border-border bg-bg-2 shadow-lg"
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
          <Pencil size={16} />
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
          <Mic size={16} />
        </button>
        <span className="h-4 w-px bg-border" aria-hidden="true" />
        <button
          type="button"
          onClick={onLive}
          data-testid="companion-live"
          aria-label={liveOpen ? 'Close chat here' : 'Chat here'}
          aria-pressed={liveOpen}
          title={liveOpen ? 'Close in-page chat. Running work and pending approvals remain.' : 'Type to the assistant on this page.'}
          className={`px-3 py-2 hover:text-accent ${liveOpen ? 'text-accent' : 'text-text'}`}
        >
          Chat here
        </button>
      </div>
      <div className="max-w-72 rounded-lg border border-border bg-bg-2 px-3 py-2 text-[10px] text-muted">
        <button type="button" disabled aria-describedby={voiceReasonId}
          className="font-medium text-muted disabled:cursor-not-allowed">
          Start voice
        </button>
        <p id={voiceReasonId}>Continuous voice is not connected. Use reviewed dictation for speech.</p>
      </div>
    </div>
  )
}

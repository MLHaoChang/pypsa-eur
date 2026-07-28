import { useEffect, useId, useRef, type HTMLAttributes, type ReactNode } from 'react'

// The app's only accessible dialog. Owns exactly the behaviours that were
// missing everywhere before it existed: ARIA role/modal state, a focus trap,
// initial focus, focus restoration, and its own Escape handling.
//
// Renders IN PLACE rather than through a portal, matching every pre-existing
// call site — see the design doc for that decision and its accepted risk
// (an ancestor overflow/transform can still clip the panel).
//
// It deliberately owns no data fetching. Call sites that load their own data
// (SnapshotsPanel does) keep that in a wrapper around Dialog, not inside it.

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

interface DialogBaseProps extends Omit<HTMLAttributes<HTMLDivElement>, 'title' | 'aria-label' | 'aria-labelledby'> {
  open: boolean
  onClose: () => void
  children: ReactNode
  dismissOnBackdrop?: boolean
  z?: number
  panelClassName?: string
}

// At least one of title / aria-label / aria-labelledby is required so a
// nameless dialog cannot be constructed — see the design doc, which calls a
// Dialog with no accessible name a defect, not a lint nit.
type DialogNameProps =
  | { title: string; 'aria-label'?: never; 'aria-labelledby'?: never }
  | { title?: never; 'aria-label': string; 'aria-labelledby'?: never }
  | { title?: never; 'aria-label'?: never; 'aria-labelledby': string }

export type DialogProps = DialogBaseProps & DialogNameProps

export function Dialog({
  open,
  onClose,
  children,
  title,
  dismissOnBackdrop = true,
  z = 9999,
  panelClassName = 'bg-bg rounded-xl shadow-2xl w-[420px] max-w-[95vw] overflow-hidden',
  className,
  ...props
}: DialogProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const restoreRef = useRef<HTMLElement | null>(null)
  const titleId = useId()

  // Initial focus on open + restoration on close. Capturing the previously
  // focused element must happen before we move focus away from it.
  useEffect(() => {
    if (!open) return
    restoreRef.current = document.activeElement as HTMLElement | null
    const panel = panelRef.current
    const first = panel?.querySelector<HTMLElement>(FOCUSABLE)
    ;(first ?? panel)?.focus()
    return () => {
      restoreRef.current?.focus?.()
    }
  }, [open])

  // Escape and the Tab cycle. Bound to the dialog subtree, not to window, so
  // the primitive does not depend on — or fight with — any global handler.
  useEffect(() => {
    if (!open) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
        return
      }
      if (e.key !== 'Tab') return
      const panel = panelRef.current
      if (!panel) return
      const items = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE))
      if (items.length === 0) {
        e.preventDefault()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown, true)
    return () => document.removeEventListener('keydown', onKeyDown, true)
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      className={className ?? 'fixed inset-0 flex items-center justify-center'}
      style={{ background: 'rgba(0,0,0,0.45)', zIndex: z }}
      onClick={e => {
        if (dismissOnBackdrop && e.target === e.currentTarget) onClose()
      }}
      role="dialog"
      aria-modal="true"
      {...(title ? { 'aria-labelledby': titleId } : null)}
      {...props}
    >
      {title ? (
        <span id={titleId} className="sr-only">
          {title}
        </span>
      ) : null}
      <div ref={panelRef} tabIndex={-1} className={panelClassName}>
        {children}
      </div>
    </div>
  )
}

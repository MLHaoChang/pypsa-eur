import { useCallback } from 'react'
import { ChevronDown } from 'lucide-react'
import { useUIStore, type SlidePanel } from '../../store/uiStore'

// The sidebar's row primitives, shared by the step sections in this folder
// and by the project rows in Sidebar.tsx.

// ── Shared sidebar item row ────────────────────────────────────────────────────
export function SItem({
  icon, label, hint, rightEl, onClick, active, title,
}: {
  icon: React.ReactNode; label: string; hint?: string; rightEl?: React.ReactNode
  onClick?: () => void; active?: boolean; title?: string
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="flex items-center gap-3 w-full pl-6 pr-3 transition-colors text-left"
      style={{
        height: 36,
        background: active ? 'var(--color-panel-2)' : undefined,
        borderLeft: active ? '3px solid var(--color-accent)' : '3px solid transparent',
      }}
      onMouseEnter={e => { if (!active) (e.currentTarget as HTMLElement).style.background = 'var(--color-border)' }}
      onMouseLeave={e => { if (!active) (e.currentTarget as HTMLElement).style.background = '' }}
    >
      <span className="shrink-0 text-ink-600" style={{ width: 16, display: 'flex', alignItems: 'center' }}>
        {icon}
      </span>
      <span className="flex-1 text-[13px] font-medium text-text truncate min-w-0">{label}</span>
      {hint && <span className="text-[10px] text-ink-400 font-mono ml-1 max-w-[6rem] truncate shrink">{hint}</span>}
      {rightEl}
    </button>
  )
}

export function SectionHdr({ title, open, onToggle, testId }: { title: string; open: boolean; onToggle: () => void; testId?: string }) {
  return (
    <button
      onClick={onToggle}
      data-testid={testId}
      className="flex items-center justify-between w-full px-4 py-2 mt-1 text-left hover:bg-panel transition-colors"
    >
      <span className="text-[10px] font-bold uppercase tracking-[0.08em] text-ink-400">{title}</span>
      <ChevronDown size={11} className="text-ink-400 transition-transform duration-200 shrink-0"
        style={{ transform: open ? 'rotate(0deg)' : 'rotate(-90deg)' }} />
    </button>
  )
}

// SubHdr is the smaller, non-collapsible group label used inside a SectionHdr's
// expanded body (e.g. "Quick actions", "Recent", "Workspace"). Visual hierarchy:
// SectionHdr (bold, 10px) → SubHdr (medium, 9px) → SItem rows.
export function SubHdr({ title }: { title: string }) {
  return (
    <div className="px-6 pt-3 pb-1 text-[9px] font-semibold uppercase tracking-[0.08em] text-ink-400">
      {title}
    </div>
  )
}

/**
 * A row's click: open its panel, or close it when it is already open, then
 * close the flyout the row sits in (icon-strip mode).
 */
export function usePanelToggle(onCloseModal?: () => void) {
  const activeSlidePanel = useUIStore(s => s.activeSlidePanel)
  const setSlidePanel = useUIStore(s => s.setSlidePanel)
  const toggle = useCallback((panel: SlidePanel) => {
    setSlidePanel(useUIStore.getState().activeSlidePanel === panel ? null : panel)
    onCloseModal?.()
  }, [setSlidePanel, onCloseModal])
  return { activeSlidePanel, toggle }
}

import { X } from 'lucide-react'

// The real, bound shortcuts (App.tsx keydown + AppHeader.tsx). Kept in one place
// so the "?" help overlay stays in sync with what's actually wired.
const SHORTCUTS: Array<{ keys: string; desc: string }> = [
  { keys: 'Ctrl / ⌘ + K', desc: 'Command palette' },
  { keys: 'Ctrl / ⌘ + P', desc: 'Quick-open a project' },
  { keys: 'Ctrl / ⌘ + S', desc: 'Save the current project' },
  { keys: 'Ctrl / ⌘ + Z', desc: 'Undo the last edit' },
  { keys: 'Esc', desc: 'Close the open panel / dismiss the comparison' },
  { keys: '?', desc: 'Show this shortcuts list' },
]

export default function ShortcutsHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-[10000] flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.45)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
    >
      <div className="bg-bg rounded-xl shadow-2xl w-[420px] max-w-[95vw] overflow-hidden">
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-border">
          <div>
            <p className="text-[9px] font-bold text-accent uppercase tracking-widest">HELP</p>
            <p className="text-sm font-semibold text-text">Keyboard shortcuts</p>
          </div>
          <button onClick={onClose} className="p-1 text-muted hover:text-text transition-colors" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <div className="p-5 space-y-2.5">
          {SHORTCUTS.map(s => (
            <div key={s.keys} className="flex items-center justify-between gap-4">
              <span className="text-[12px] text-text">{s.desc}</span>
              <kbd className="px-2 py-0.5 rounded border border-border bg-bg-2 text-[11px] font-mono text-muted whitespace-nowrap">
                {s.keys}
              </kbd>
            </div>
          ))}
        </div>
        <div className="px-5 pb-4 text-[10px] text-muted text-center">
          Press <span className="font-mono">?</span> any time to reopen this.
        </div>
      </div>
    </div>
  )
}

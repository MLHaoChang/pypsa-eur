import type { ReactNode } from 'react'
import { Dialog } from './Dialog'

// Shared confirmation for destructive actions. Deliberately a Dialog, not a
// confirmToast: toasts auto-dismiss (8 s) and double-fire on double-click —
// both wrong for a destructive decision.
interface ConfirmDialogProps {
  open: boolean
  title: string
  message: ReactNode
  confirmLabel: string
  danger?: boolean
  pending?: boolean
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmDialog({
  open, title, message, confirmLabel,
  danger = false, pending = false, onConfirm, onCancel,
}: ConfirmDialogProps) {
  const close = () => { if (!pending) onCancel() }
  return (
    <Dialog open={open} onClose={close} title={title} dismissOnBackdrop={!pending}>
      <div className="p-4 flex flex-col gap-3">
        <h2 className="text-sm font-semibold text-text">{title}</h2>
        <div className="text-sm text-muted">{message}</div>
        <div className="flex justify-end gap-2 mt-2">
          <button
            className="px-3 py-1.5 text-xs border border-border rounded text-text hover:border-accent disabled:opacity-50"
            onClick={onCancel} disabled={pending}
          >
            Cancel
          </button>
          <button
            className={`px-3 py-1.5 text-xs rounded text-white disabled:opacity-50 ${danger ? 'bg-red-600 hover:bg-red-500' : 'bg-accent hover:opacity-90'}`}
            onClick={onConfirm} disabled={pending}
          >
            {pending ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </Dialog>
  )
}

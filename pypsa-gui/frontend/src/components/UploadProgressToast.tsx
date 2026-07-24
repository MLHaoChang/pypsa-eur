/**
 * Phase D polish #2 — sticky per-file upload progress toast.
 *
 * Subscribed to `chatStore.uploadBatches[batchId]` so the host
 * (`ChatPanel.handleUploadFiles`) only mounts the component once per batch
 * via `toast.custom(..., { duration: Infinity, id: 'upload-batch-<ts>' })`.
 * Row updates re-render this component directly.
 *
 * Each row shows an indeterminate animated bar while `status='uploading'`,
 * a green tick on `'ok'`, or a red cross + error_kind on `'failed'`. There
 * is no byte-level percentage — `fetch` doesn't expose progress without
 * switching to XMLHttpRequest, and the on/off signal is the UX win we care
 * about for the multi-file case (the per-file `toast.success` still fires
 * for single-file uploads).
 */
import React from 'react'
import toast from 'react-hot-toast'

import { useChatStore } from '../store/chatStore'

interface UploadProgressToastProps {
  batchId: string
  toastId: string
}

function _formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function UploadProgressToast({ batchId, toastId }: UploadProgressToastProps) {
  const batch = useChatStore((s) => s.uploadBatches[batchId])
  if (!batch) return null
  const total = batch.rows.length
  const done = batch.rows.filter((r) => r.status === 'ok' || r.status === 'failed').length
  const ok = batch.rows.filter((r) => r.status === 'ok').length
  const failed = batch.rows.filter((r) => r.status === 'failed').length
  const finished = done >= total

  return (
    <div
      className="bg-bg-2 border border-border rounded-md shadow-lg p-3 min-w-[260px] max-w-[340px] text-[12px]"
      role="status"
      aria-live="polite"
      data-testid="chat-upload-batch-toast"
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-text font-medium">
          {finished
            ? failed === 0
              ? `Uploaded ${ok}/${total} files`
              : `Uploaded ${ok}/${total} (${failed} failed)`
            : `Uploading ${done}/${total} files`}
        </span>
        <button
          className="text-muted hover:text-text px-1"
          onClick={() => toast.dismiss(toastId)}
          aria-label="Dismiss upload progress"
        >
          ×
        </button>
      </div>
      <div className="w-full h-1 bg-bg rounded overflow-hidden mb-2">
        <div
          className={
            'h-1 transition-all ' +
            (failed === 0 ? 'bg-accent' : 'bg-amber-500')
          }
          style={{ width: `${(done / total) * 100}%` }}
        />
      </div>
      <ul className="space-y-0.5 max-h-48 overflow-y-auto">
        {batch.rows.map((row) => (
          <li
            key={row.rowId}
            className="flex items-center gap-2 px-1 py-0.5 rounded hover:bg-bg/40"
            data-testid="chat-upload-batch-row"
            data-status={row.status}
          >
            <span className="w-4 text-center">
              {row.status === 'ok' && <span className="text-emerald-400">✓</span>}
              {row.status === 'failed' && <span className="text-rose-400">✗</span>}
              {row.status === 'uploading' && (
                <span
                  className="inline-block w-3 h-3 rounded-full border-2 border-accent border-t-transparent animate-spin"
                  aria-label="uploading"
                />
              )}
              {row.status === 'queued' && (
                <span className="text-muted">⌛</span>
              )}
            </span>
            <span
              className={
                'flex-1 truncate ' +
                (row.status === 'failed' ? 'text-rose-300' : 'text-text')
              }
              title={row.filename}
            >
              {row.filename}
            </span>
            <span className="text-muted text-[10px]">
              {_formatSize(row.sizeBytes)}
            </span>
            {row.status === 'failed' && row.errorKind && (
              <span
                className="text-rose-400 text-[10px] truncate max-w-[80px]"
                title={row.errorMessage || row.errorKind}
              >
                {row.errorKind}
              </span>
            )}
          </li>
        ))}
      </ul>
      {finished && (
        <div className="text-[10px] text-muted mt-2">
          {failed === 0 ? 'All files attached. Dismissing soon…' : 'Review failed rows above.'}
        </div>
      )}
    </div>
  )
}

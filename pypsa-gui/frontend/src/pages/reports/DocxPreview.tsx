// A `.docx` preview pane (WP11, plan §WP11 "a docx-preview pane of the
// exported file").
//
// Given the blob URL of an exported upload, fetches the bytes (the same URL
// the download anchor uses — the server streams them with
// `Content-Disposition: inline`) and hands the `Blob` to `docx-preview`'s
// `renderAsync`, which paints the document's pages into the container as
// HTML. The library reads the package client-side; nothing is sent
// anywhere. Rendering is best-effort — Word features docx-preview does not
// implement (SmartArt, some field codes) are skipped, so the pane says it
// is a preview and the download stays the authority.
//
// The effect keys on the URL only: a re-export of the same version gets a
// new upload id, so a new URL, so a fresh render.
import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { renderAsync } from 'docx-preview'

type PreviewState = 'loading' | 'ready' | 'error'

export function DocxPreview({ url, filename }: { url: string; filename?: string }) {
  const container = useRef<HTMLDivElement>(null)
  const [state, setState] = useState<PreviewState>('loading')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const el = container.current
    if (!el) return
    let cancelled = false
    setState('loading')
    setError(null)
    el.replaceChildren()
    ;(async () => {
      try {
        const resp = await fetch(url)
        if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`.trim())
        const blob = await resp.blob()
        if (cancelled) return
        await renderAsync(blob, el, undefined, { className: 'docx', inWrapper: true, ignoreWidth: false })
        if (!cancelled) setState('ready')
      } catch (e) {
        if (cancelled) return
        setError(e instanceof Error && e.message ? e.message : String(e))
        setState('error')
      }
    })()
    return () => { cancelled = true }
  }, [url])

  return (
    <div className="flex flex-col gap-2" data-testid="docx-preview" data-state={state}>
      {state === 'loading' && (
        <p className="text-[11.5px] text-muted inline-flex items-center gap-1.5" data-testid="docx-preview-loading">
          <Loader2 size={12} className="animate-spin" aria-hidden="true" />
          Rendering {filename ?? 'the exported file'}…
        </p>
      )}
      {state === 'error' && (
        <p className="text-[11.5px] text-danger inline-flex items-start gap-1.5" data-testid="docx-preview-error" role="alert">
          <AlertTriangle size={12} className="shrink-0 mt-0.5" aria-hidden="true" />
          <span>Could not preview {filename ?? 'the file'}: {error} — download it instead.</span>
        </p>
      )}
      <div
        ref={container}
        className="docx-preview-host overflow-auto max-h-[70vh] rounded border border-border bg-panel"
        data-testid="docx-preview-container"
        aria-label={filename ? `Preview of ${filename}` : 'Preview of the exported file'}
      />
    </div>
  )
}

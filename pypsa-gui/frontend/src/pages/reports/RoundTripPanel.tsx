// "Upload edited copy" — the round trip's frontend half (WP14, plan
// §Increment 3).
//
// The user exports a report, edits the `.docx` in Word (text, tracked
// changes, comments), and drops it here. The panel uploads it with
// `kind=report_roundtrip` and posts `POST …/{id}/roundtrip` in one call
// (`uploadRoundTrip`); the backend accepts the tracked changes, splits the
// file by section, replaces the sections whose text changed (`source:
// user_edit`), turns each comment into that section's `pending_instruction`
// and answers with the merged version and what it found. The panel shows
// that result — it is the only place the user learns what was NOT merged —
// and hands the response to the viewer (`onMerged`), which switches to the
// new version.
//
// "Keep as template" (on by default → `bind_as_template`): the uploaded file
// also becomes the report's template, so styling the user changed survives
// the next export.
//
// Each comment is offered with "Regenerate with this": the viewer posts a
// regenerate WITHOUT an instruction of its own, and the backend uses the
// section's pending instruction (the pinned regenerate contract). A comment
// on content that matched no section (`section_id: null`) has nothing to
// regenerate and is listed without the button.
//
// The drop zone mirrors ChatPanel's (dragenter / dragover / dragleave /
// drop, `Files` only); the `.docx` / 25 MB gate is TemplatePicker's.
import { useCallback, useId, useRef, useState, type ChangeEvent, type DragEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import { CheckCircle2, FileUp, MessageSquare, RefreshCw, Upload } from 'lucide-react'
import toast from 'react-hot-toast'
import { roundTripErrorMessage, uploadRoundTrip, type RoundTripResponse } from '../../api/reports'
import { Btn, PageSection, Tag } from '../../components/PageKit'
import { SOURCE_LABEL } from './SectionCard'
import { TEMPLATE_ACCEPT, TEMPLATE_MAX_BYTES } from './TemplatePicker'

export const ROUNDTRIP_ACCEPT = TEMPLATE_ACCEPT
export const ROUNDTRIP_MAX_BYTES = TEMPLATE_MAX_BYTES

/** The file gate, shared by the drop and the picker: the toast text, or `null` when the file may go. */
export function roundTripFileProblem(file: File): string | null {
  if (!file.name.toLowerCase().endsWith('.docx')) {
    return 'An edited copy is a Word file (.docx) — export the report, edit it, and upload that file.'
  }
  if (file.size > ROUNDTRIP_MAX_BYTES) {
    return `The file is larger than 25 MB (${(file.size / 1024 / 1024).toFixed(1)} MB).`
  }
  return null
}

export function RoundTripPanel({
  project, reportId, disabled, onMerged, onRegenerateWith, regenerateDisabled,
}: {
  project: string
  reportId: string
  /** No upload while something else writes the report (an export, a job). */
  disabled?: boolean
  /** The merged version arrived: the viewer switches to it. */
  onMerged: (resp: RoundTripResponse) => void
  /** "Regenerate with this" on a comment: the viewer posts the regenerate. */
  onRegenerateWith?: (sectionId: string) => void | Promise<unknown>
  regenerateDisabled?: boolean
}) {
  const fileInput = useRef<HTMLInputElement>(null)
  const boxId = useId()
  const [bindAsTemplate, setBindAsTemplate] = useState(true)
  const [dragActive, setDragActive] = useState(false)
  const [result, setResult] = useState<RoundTripResponse | null>(null)

  const merge = useMutation({
    mutationFn: (file: File) => uploadRoundTrip(project, reportId, file, bindAsTemplate),
    onSuccess: (resp) => {
      setResult(resp)
      onMerged(resp)
      const changed = resp.result.sections.filter(s => s.changed).length
      toast.success(`Edited copy merged — now v${resp.version} (${changed} section${changed === 1 ? '' : 's'} changed)`)
    },
    onError: (e) => toast.error(`Could not merge the edited copy: ${roundTripErrorMessage(e)}`),
  })

  const busy = merge.isPending
  const blocked = Boolean(disabled) || busy

  const take = useCallback((file: File | undefined) => {
    if (!file || blocked) return
    const problem = roundTripFileProblem(file)
    if (problem) {
      toast.error(problem)
      return
    }
    merge.mutate(file)
  }, [blocked, merge])

  function onFilePicked(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''
    take(file)
  }

  // The drop zone (ChatPanel's pattern, without the dwell timer: this zone
  // has no children the pointer traverses while dragging).
  function hasFiles(e: DragEvent): boolean {
    return Boolean(e.dataTransfer?.types && Array.from(e.dataTransfer.types).includes('Files'))
  }
  function onDragEnter(e: DragEvent) {
    if (!hasFiles(e)) return
    e.preventDefault()
    if (!blocked) setDragActive(true)
  }
  function onDragOver(e: DragEvent) {
    if (!hasFiles(e)) return
    e.preventDefault()
    e.dataTransfer.dropEffect = blocked ? 'none' : 'copy'
    if (!blocked) setDragActive(true)
  }
  function onDragLeave(e: DragEvent) {
    e.preventDefault()
    setDragActive(false)
  }
  function onDrop(e: DragEvent) {
    e.preventDefault()
    setDragActive(false)
    take(e.dataTransfer?.files?.[0])
  }

  return (
    <PageSection
      title="Upload edited copy"
      hint="export → edit in Word → upload: your text, tracked changes and comments come back as the next version"
      right={
        <div className="flex items-center gap-2">
          <label htmlFor={boxId} className="inline-flex items-center gap-1.5 text-[11px] text-muted">
            <input
              id={boxId}
              type="checkbox"
              checked={bindAsTemplate}
              onChange={e => setBindAsTemplate(e.target.checked)}
              disabled={blocked}
            />
            Keep as template
          </label>
          <input
            ref={fileInput}
            type="file"
            accept={ROUNDTRIP_ACCEPT}
            className="hidden"
            onChange={onFilePicked}
            disabled={blocked}
            data-testid="roundtrip-file-input"
          />
          <Btn
            onClick={() => fileInput.current?.click()}
            disabled={blocked}
            title="Upload an edited export of this report (.docx, up to 25 MB) and merge it as the next version"
            data-testid="roundtrip-upload"
          >
            <Upload size={12} /> {busy ? 'Merging…' : 'Upload edited copy…'}
          </Btn>
        </div>
      }
      bodyClassName="p-3 flex flex-col gap-3"
    >
      <div
        role="button"
        tabIndex={blocked ? -1 : 0}
        aria-disabled={blocked}
        onClick={() => { if (!blocked) fileInput.current?.click() }}
        onKeyDown={e => { if (!blocked && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); fileInput.current?.click() } }}
        onDragEnter={onDragEnter}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        data-testid="roundtrip-dropzone"
        data-drag-active={dragActive ? 'true' : 'false'}
        className={`flex items-center justify-center gap-2 rounded-[8px] border border-dashed px-4 py-4 text-[11.5px] transition-colors
          ${dragActive ? 'border-accent bg-accent-50/40 text-accent' : 'border-border-2 text-muted'}
          ${blocked ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer hover:border-accent hover:text-accent'}`}
      >
        <FileUp size={14} aria-hidden="true" />
        {busy
          ? 'Uploading and merging…'
          : dragActive
            ? 'Drop the edited .docx to merge it'
            : 'Drop an edited .docx here, or click to choose one'}
      </div>

      {result && <RoundTripResultView result={result} onRegenerateWith={onRegenerateWith} regenerateDisabled={regenerateDisabled} />}
    </PageSection>
  )
}

function RoundTripResultView({
  result, onRegenerateWith, regenerateDisabled,
}: {
  result: RoundTripResponse
  onRegenerateWith?: (sectionId: string) => void | Promise<unknown>
  regenerateDisabled?: boolean
}) {
  const r = result.result
  const changed = r.sections.filter(s => s.changed)
  const comments = r.sections.flatMap(s => s.comments.map((text, i) => ({ key: `${s.section_id ?? s.heading}-${i}`, section: s, text })))
  return (
    <div className="flex flex-col gap-3 text-[11.5px]" data-testid="roundtrip-result">
      <p className="inline-flex flex-wrap items-center gap-2 text-text">
        <CheckCircle2 size={13} className="text-success shrink-0" aria-hidden="true" />
        <span>Merged as <span className="font-mono">v{result.version}</span></span>
        <span className="text-muted">·</span>
        <span>{changed.length} section{changed.length === 1 ? '' : 's'} changed</span>
        <span className="text-muted">·</span>
        <span>{r.accepted_tracked_changes} tracked change{r.accepted_tracked_changes === 1 ? '' : 's'} accepted</span>
        {result.template_file_id && (
          <>
            <span className="text-muted">·</span>
            <span className="text-muted">the file is kept as the report's template</span>
          </>
        )}
      </p>

      {changed.length > 0 && (
        <div data-testid="roundtrip-changed" className="flex flex-col gap-1">
          <h4 className="text-[10.5px] font-semibold uppercase tracking-wide text-muted">Sections you edited</h4>
          <ul className="flex flex-col gap-0.5">
            {changed.map((s, i) => (
              <li key={`${s.section_id ?? s.heading}-${i}`} data-testid="roundtrip-changed-row" className="inline-flex items-center gap-2">
                <span className="text-text">{s.heading}</span>
                <Tag tone="purple">{SOURCE_LABEL.user_edit}</Tag>
                {s.section_id == null && <span className="text-muted">(no matching section — not merged)</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {comments.length > 0 && (
        <div data-testid="roundtrip-comments" className="flex flex-col gap-1">
          <h4 className="text-[10.5px] font-semibold uppercase tracking-wide text-muted">Comments → pending instructions</h4>
          <ul className="flex flex-col gap-1">
            {comments.map(c => (
              <li key={c.key} data-testid="roundtrip-comment" className="flex items-center gap-2">
                <MessageSquare size={12} className="shrink-0 text-purple" aria-hidden="true" />
                <span className="text-muted shrink-0">{c.section.heading}:</span>
                <span className="text-text flex-1 min-w-0 truncate" title={c.text}>{c.text}</span>
                {onRegenerateWith && c.section.section_id != null ? (
                  <button
                    type="button"
                    onClick={() => { void onRegenerateWith(c.section.section_id as string) }}
                    disabled={regenerateDisabled}
                    title={regenerateDisabled
                      ? 'A report job is running'
                      : 'Write this section again using this comment as the instruction'}
                    className="inline-flex items-center gap-1 px-2 py-1 border border-border rounded text-[10.5px] text-text hover:border-accent hover:text-accent disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    <RefreshCw size={10} /> Regenerate with this
                  </button>
                ) : c.section.section_id == null ? (
                  <span className="text-muted">(no matching section)</span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      )}

      {r.unmatched.length > 0 && (
        <div data-testid="roundtrip-unmatched" className="flex flex-col gap-1">
          <h4 className="text-[10.5px] font-semibold uppercase tracking-wide text-warn">
            Not merged — no matching section ({r.unmatched.length})
          </h4>
          <ul className="flex flex-col gap-0.5 list-disc pl-5 text-muted">
            {r.unmatched.map((u, i) => <li key={i}>{u}</li>)}
          </ul>
        </div>
      )}

      {r.comments_global.length > 0 && (
        <div data-testid="roundtrip-global" className="flex flex-col gap-1">
          <h4 className="text-[10.5px] font-semibold uppercase tracking-wide text-muted">Comments on the document</h4>
          <ul className="flex flex-col gap-0.5 list-disc pl-5 text-muted">
            {r.comments_global.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </div>
      )}
    </div>
  )
}

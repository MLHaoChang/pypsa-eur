// Import a filled Excel input workbook as a new project (IMPORT-FLOW-1):
// pick a file, see what it would create, name the project, import, open.
// Each refusal shows once, here, with its one action on the same line (UX 27
// condition 3); a disabled Import says why in visible text; closing over an
// unimported preview asks first (#1405). One host per page opens it from
// `uiStore.requestWorkbookImport`: the Guided Start tile, the wizard's From
// file tab and the Import dialog's redirect of an input workbook.
import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { FileSpreadsheet, Upload } from 'lucide-react'
import toast from 'react-hot-toast'
import { projectsApi } from '../../api/projects'
import type { WorkbookPreview } from '../../api/workbook'
import { Dialog } from '../Dialog'
import { SkeletonLines } from '../Skeleton'
import { useUIStore } from '../../store/uiStore'
import { useOpenInWorkbench } from '../../hooks/useCreateFromTemplate'
import { useWorkbookImport } from '../../hooks/useWorkbookImport'
import { numberedCopyName } from '../../utils/nameClashToast'
import { WorkbookReport } from './WorkbookReport'
import { freeName, nameProblem } from './workbookWords'

const TITLE = 'Import a workbook as a new project'
const BTN = 'px-3 py-1.5 text-xs rounded border border-border text-text hover:bg-border/30 disabled:opacity-40'
const LINK = 'shrink-0 text-text underline'

const REFUSAL_ACTION = { 'preview-again': 'Preview again', 'choose-file': 'Choose another file', 'try-again': 'Try again' } as const

export function WorkbookImportDialog({ initialFile, onClose }: { initialFile: File | null; onClose: () => void }) {
  const openInWorkbench = useOpenInWorkbench()
  const done = (name: string) => {
    onClose()
    toast.success(`Created and opened ‘${name}’`)
    openInWorkbench(name)
  }
  const { file, phase, refusal, busy, preview, importAs, open } = useWorkbookImport(done)
  const { data: projects = [] } = useQuery({ queryKey: ['projects'], queryFn: projectsApi.list, staleTime: 10_000 })
  const taken = (projects as { name: string }[]).map(p => p.name)
  const [name, setName] = useState('')
  const [confirmDiscard, setConfirmDiscard] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  useEffect(() => { if (initialFile) void preview(initialFile) }, [initialFile, preview])
  // A new preview names the project after the file (an exported workbook carries its project's name).
  const previewed = phase.step === 'preview' ? phase.preview : null
  const namedFor = useRef<WorkbookPreview | null>(null)
  useEffect(() => {
    if (!previewed || namedFor.current === previewed) return
    namedFor.current = previewed
    setName(previewed.suggested_name)
  }, [previewed])

  const shown = phase.step === 'preview' || phase.step === 'importing' ? phase.preview : null
  const requestClose = () => {
    if (busy) return
    if (confirmDiscard) { setConfirmDiscard(false); return }
    if (shown) { setConfirmDiscard(true); return }
    onClose()
  }
  const chooseFile = () => fileInput.current?.click()
  const pick = (f: File | undefined) => { if (f) { setConfirmDiscard(false); void preview(f) } }

  // The backend's clash refusal holds while the name is still the clashing one;
  // otherwise the name's own problem, checked against the project list.
  const clashNow = !!refusal?.clash && name.trim() === refusal.clash
  const problem = shown && !clashNow ? nameProblem(name, taken) : null
  const useFree = (base: string) => setName(freeName(base, taken, numberedCopyName))
  const refusalAction = () => {
    if (!refusal) return
    if (refusal.clash) useFree(refusal.clash)
    else if (refusal.action === 'choose-file') chooseFile()
    else if (refusal.action === 'preview-again' && file) void preview(file)
    else if (shown) void importAs(name)
    else if (file) void preview(file)
  }

  return (
    <Dialog open onClose={requestClose} title={TITLE} z={10000} dismissOnBackdrop={false} data-no-panel-close
      data-testid="workbook-import-dialog"
      panelClassName="bg-bg rounded-lg shadow-2xl w-[40rem] max-w-[94vw] max-h-[85vh] flex flex-col overflow-hidden border border-border">
      <input ref={fileInput} type="file" accept=".xlsx" className="hidden" data-testid="workbook-file-input"
        onChange={e => { pick(e.target.files?.[0]); e.target.value = '' }} />
      <div className="px-4 py-3 border-b border-border">
        <div className="text-sm font-semibold text-text">{TITLE}</div>
        <div className="text-[11px] text-muted">
          The workbook is read and checked first: nothing is created until you choose Import, and the project open now is not changed.
        </div>
      </div>
      <div className="flex-1 overflow-auto px-4 py-3 text-xs flex flex-col gap-3">
        {file ? (
          <p className="flex items-center gap-2 text-text" data-testid="workbook-file">
            <FileSpreadsheet size={13} className="text-accent shrink-0" />
            <span className="truncate">{file.name}</span>
            {!busy && phase.step !== 'not-opened' && (
              <button type="button" className={`${LINK} ml-auto`} onClick={chooseFile}>Choose another file</button>
            )}
          </p>
        ) : (
          <button type="button" data-testid="workbook-drop"
            onClick={chooseFile}
            onDragOver={e => e.preventDefault()}
            onDrop={e => { e.preventDefault(); pick(e.dataTransfer.files?.[0]) }}
            className="flex flex-col items-center gap-1.5 px-6 py-8 border-2 border-dashed border-border rounded-lg hover:border-accent/40">
            <Upload size={22} className="text-muted" />
            <span className="text-sm font-medium text-text">Choose a filled Excel input workbook (.xlsx)</span>
            <span className="text-[11px] text-muted">or drop it here</span>
          </button>
        )}

        {refusal && (!refusal.clash || clashNow) && (
          <p className="flex items-baseline gap-2 text-danger" role="alert" data-testid="workbook-refusal">
            <span>{refusal.message}</span>
            <button type="button" className={LINK} data-testid="workbook-refusal-action" onClick={refusalAction} disabled={busy}>
              {refusal.clash ? `Use “${freeName(refusal.clash, taken, numberedCopyName)}”` : REFUSAL_ACTION[refusal.action]}
            </button>
          </p>
        )}

        {phase.step === 'previewing' && <SkeletonLines what="the workbook preview" rows={5} testId="workbook-previewing" />}

        {shown && (
          <>
            <label className="flex flex-col gap-1">
              <span className="text-muted">Project name</span>
              <input value={name} onChange={e => setName(e.target.value)} aria-label="Project name" data-testid="workbook-name"
                disabled={phase.step === 'importing'}
                className="px-2.5 py-1.5 text-xs border border-border rounded bg-bg text-text focus:outline-none focus:border-accent" />
            </label>
            <WorkbookReport contents={shown} />
          </>
        )}

        {phase.step === 'opening' && (
          <p role="status" className="text-muted" data-testid="workbook-opening">Opening ‘{phase.name}’…</p>
        )}
        {phase.step === 'not-opened' && (
          <p role="status" className="flex items-baseline gap-2 text-warn" data-testid="workbook-not-opened">
            <span>Created ‘{phase.name}’, not opened: {phase.reason}</span>
            <button type="button" className={LINK} data-testid="workbook-try-open" onClick={() => { void open(phase.name) }}>Try again</button>
          </p>
        )}
      </div>
      <div className="px-4 py-3 border-t border-border flex items-center justify-end gap-2">
        {confirmDiscard ? (<>
          <span className="mr-auto text-[11px] text-text" data-testid="workbook-discard-confirm">
            Close and discard this preview? Nothing has been created.
          </span>
          <button type="button" className={BTN} onClick={() => setConfirmDiscard(false)}>Keep</button>
          <button type="button" className="px-3 py-1.5 text-xs rounded bg-accent text-white hover:opacity-90" onClick={onClose}>Discard</button>
        </>) : phase.step === 'not-opened' ? (
          <button type="button" className={BTN} onClick={onClose}>Close</button>
        ) : (<>
          {problem && (
            <span className="mr-auto flex items-baseline gap-2 text-[11px] text-muted" data-testid="workbook-import-why">
              <span>{problem}</span>
              {name.trim() && (
                <button type="button" className={LINK} data-testid="workbook-use-free-name" onClick={() => useFree(name)}>
                  Use “{freeName(name, taken, numberedCopyName)}”
                </button>
              )}
            </span>
          )}
          <button type="button" className={BTN} onClick={requestClose} disabled={busy}>Cancel</button>
          {shown && (
            <button type="button" data-testid="workbook-import-btn"
              disabled={busy || problem !== null || clashNow}
              onClick={() => { void importAs(name) }}
              className="px-3 py-1.5 text-xs rounded bg-accent text-white hover:opacity-90 disabled:opacity-40">
              {phase.step === 'importing' ? 'Importing…' : 'Import'}
            </button>
          )}
        </>)}
      </div>
    </Dialog>
  )
}

/** The page's one import dialog, opened by `requestWorkbookImport`. */
export function WorkbookImportHost() {
  const request = useUIStore(s => s.workbookImportRequest)
  const clear = useUIStore(s => s.clearWorkbookImportRequest)
  if (!request) return null
  return <WorkbookImportDialog key={request.id} initialFile={request.file} onClose={clear} />
}

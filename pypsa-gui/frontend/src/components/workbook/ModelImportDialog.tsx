// Import a model file (PSS/E, PowerFactory, PLEXOS …) as a new project (IMPORT-FLOW-1,
// project-io 04): pick the files, see what they would create and what they could
// not carry, name the project, import, open. The same flow and refusal words as the
// workbook dialog. One host per page opens it from `uiStore.requestModelImport`: the
// wizard's From file tab and the Project Data modal's redirect of a model file.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { FileText, Upload } from 'lucide-react'
import toast from 'react-hot-toast'
import { projectsApi } from '../../api/projects'
import { bundlePreview, modelExtensions, modelImportApi, type ModelPreview } from '../../api/modelImport'
import { Dialog } from '../Dialog'
import { SkeletonLines } from '../Skeleton'
import { useUIStore } from '../../store/uiStore'
import { useOpenInWorkbench } from '../../hooks/useCreateFromTemplate'
import { useFileImport, type ImportSource } from '../../hooks/useWorkbookImport'
import { numberedCopyName } from '../../utils/nameClashToast'
import { WorkbookReport } from './WorkbookReport'
import { freeName, nameProblem } from './workbookWords'
import { MODEL_HEADINGS } from './modelImportWords'

const TITLE = 'Import a model as a new project'
const BTN = 'px-3 py-1.5 text-xs rounded border border-border text-text hover:bg-border/30 disabled:opacity-40'
const LINK = 'shrink-0 text-text underline'
const REFUSAL_ACTION = { 'preview-again': 'Preview again', 'choose-file': 'Choose other files', 'try-again': 'Try again' } as const

const isBundle = (files: File[]) => files.length === 1 && /\.pypsaproj\.zip$/i.test(files[0].name)

// A project bundle has its own routes (it keeps results and settings); a model file goes
// through the model-import routes. Both show the same preview and both create a new project.
const source: ImportSource<ModelPreview> = {
  preview: files => (isBundle(files) ? bundlePreview(files[0]) : modelImportApi.preview(files)),
  create: async (files, sha, name) => {
    if (!isBundle(files)) return modelImportApi.import(files, sha, name)
    return { name: (await projectsApi.importBundle(files[0], name)).imported }
  },
  logLine: (files, created) => `Imported '${files.map(f => f.name).join(', ')}' as '${created}'`,
}

export function ModelImportDialog({ initialFiles, onClose }: { initialFiles: File[]; onClose: () => void }) {
  const openInWorkbench = useOpenInWorkbench()
  const done = (name: string) => {
    onClose()
    toast.success(`Created and opened ‘${name}’`)
    openInWorkbench(name)
  }
  const { files, phase, refusal, busy, preview, importAs, open } = useFileImport(source, done)
  const { data: projects = [] } = useQuery({ queryKey: ['projects'], queryFn: projectsApi.list, staleTime: 10_000 })
  const { data: formats } = useQuery({ queryKey: ['model-import-formats'], queryFn: modelImportApi.formats, staleTime: 300_000 })
  const taken = (projects as { name: string }[]).map(p => p.name)
  const accept = useMemo(() => modelExtensions(formats).join(','), [formats])
  const [name, setName] = useState('')
  const [confirmDiscard, setConfirmDiscard] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  useEffect(() => { if (initialFiles.length) void preview(initialFiles) }, [initialFiles, preview])
  const previewed = phase.step === 'preview' ? phase.preview : null
  const namedFor = useRef<ModelPreview | null>(null)
  useEffect(() => {
    if (!previewed || namedFor.current === previewed) return
    namedFor.current = previewed
    // A model with no name of its own (PLEXOS: "Unnamed Network") is named after its file.
    const stem = previewed.files[0]?.replace(/\.[^.]+$/, '')
    setName(/^unnamed network$/i.test(previewed.suggested_name.trim()) && stem ? stem : previewed.suggested_name)
  }, [previewed])

  const shown = phase.step === 'preview' || phase.step === 'importing' ? phase.preview : null
  const requestClose = () => {
    if (busy) return
    if (confirmDiscard) { setConfirmDiscard(false); return }
    if (shown) { setConfirmDiscard(true); return }
    onClose()
  }
  const chooseFiles = () => fileInput.current?.click()
  const pick = (list: FileList | null | undefined) => {
    const fs = Array.from(list ?? [])
    if (fs.length) { setConfirmDiscard(false); void preview(fs) }
  }

  const clashNow = !!refusal?.clash && name.trim() === refusal.clash
  const problem = shown && !clashNow ? nameProblem(name, taken) : null
  const refusalShown = !!refusal && (!refusal.clash || clashNow)
  const refusalChoosesFile = refusalShown && !refusal?.clash && refusal?.action === 'choose-file'
  const useFree = (base: string) => setName(freeName(base, taken, numberedCopyName))
  const refusalAction = () => {
    if (!refusal) return
    if (refusal.clash) useFree(refusal.clash)
    else if (refusal.action === 'choose-file') chooseFiles()
    else if (refusal.action === 'preview-again' && files.length) void preview(files)
    else if (shown) void importAs(name)
    else if (files.length) void preview(files)
  }

  return (
    <Dialog open onClose={requestClose} title={TITLE} z={10000} dismissOnBackdrop={false} data-no-panel-close
      data-testid="model-import-dialog"
      panelClassName="bg-bg rounded-lg shadow-2xl w-[40rem] max-w-[94vw] max-h-[85vh] flex flex-col overflow-hidden border border-border">
      <input ref={fileInput} type="file" multiple accept={accept} className="hidden" data-testid="model-file-input"
        onChange={e => { pick(e.target.files); e.target.value = '' }} />
      <div className="px-4 py-3 border-b border-border">
        <div className="text-sm font-semibold text-text">{TITLE}</div>
        <div className="text-[11px] text-muted">
          The files are read and checked first: nothing is created until you choose Create project, and the project open now is not changed.
        </div>
      </div>
      <div className="flex-1 overflow-auto px-4 py-3 text-xs flex flex-col gap-3">
        {files.length > 0 ? (
          <p className="flex items-center gap-2 text-text" data-testid="model-files">
            <FileText size={13} className="text-accent shrink-0" />
            <span className="truncate">{files.map(f => f.name).join(', ')}</span>
            {!busy && phase.step !== 'not-opened' && !refusalChoosesFile && (
              <button type="button" className={`${LINK} ml-auto`} onClick={chooseFiles}>Choose other files</button>
            )}
          </p>
        ) : (
          <button type="button" data-testid="model-drop" onClick={chooseFiles}
            onDragOver={e => e.preventDefault()}
            onDrop={e => { e.preventDefault(); pick(e.dataTransfer.files) }}
            className="flex flex-col items-center gap-1.5 px-6 py-8 border-2 border-dashed border-border rounded-lg hover:border-accent/40">
            <Upload size={22} className="text-muted" />
            <span className="text-sm font-medium text-text">Choose a model file{formats?.length ? ` (${modelExtensions(formats).join(' ')})` : ''}</span>
            <span className="text-[11px] text-muted">or drop it here. A model in several files: choose them together.</span>
          </button>
        )}

        {refusal && refusalShown && (
          <p className="flex items-baseline gap-2 text-danger" role="alert" data-testid="model-refusal">
            <span>{refusal.message}</span>
            <button type="button" className={LINK} data-testid="model-refusal-action" onClick={refusalAction} disabled={busy}>
              {refusal.clash ? `Use “${freeName(refusal.clash, taken, numberedCopyName)}”` : REFUSAL_ACTION[refusal.action]}
            </button>
          </p>
        )}

        {phase.step === 'previewing' && <SkeletonLines what="the model preview" rows={5} testId="model-previewing" />}

        {shown && (
          <>
            <p className="text-muted" data-testid="model-format">Read as {shown.format_label}.</p>
            <label className="flex flex-col gap-1">
              <span className="text-muted">Project name</span>
              <input value={name} onChange={e => setName(e.target.value)} aria-label="Project name" data-testid="model-name"
                disabled={phase.step === 'importing'}
                className="px-2.5 py-1.5 text-xs border border-border rounded bg-bg text-text focus:outline-none focus:border-accent" />
            </label>
            <WorkbookReport headings={MODEL_HEADINGS} testId="model-report"
              contents={{ report: { ...shown, source_format: shown.format_label, source_files: shown.files } }} />
          </>
        )}

        {phase.step === 'opening' && <p role="status" className="text-muted" data-testid="model-opening">Opening ‘{phase.name}’…</p>}
        {phase.step === 'not-opened' && (
          <p role="status" className="flex items-baseline gap-2 text-warn" data-testid="model-not-opened">
            <span>Created ‘{phase.name}’, not opened: {phase.reason}</span>
            <button type="button" className={LINK} data-testid="model-try-open" onClick={() => { void open(phase.name) }}>Try again</button>
          </p>
        )}
      </div>
      <div className="px-4 py-3 border-t border-border flex items-center justify-end gap-2">
        {confirmDiscard ? (<>
          <span className="mr-auto text-[11px] text-text" data-testid="model-discard-confirm">
            Close and discard this preview? Nothing has been created.
          </span>
          <button type="button" className={BTN} onClick={() => setConfirmDiscard(false)}>Keep</button>
          <button type="button" className="px-3 py-1.5 text-xs rounded bg-accent text-white hover:opacity-90" onClick={onClose}>Discard</button>
        </>) : phase.step === 'not-opened' ? (
          <button type="button" className={BTN} onClick={onClose}>Close</button>
        ) : (<>
          {problem && (
            <span className="mr-auto flex items-baseline gap-2 text-[11px] text-muted" data-testid="model-import-why">
              <span>{problem}</span>
              {name.trim() && (
                <button type="button" className={LINK} data-testid="model-use-free-name" onClick={() => useFree(name)}>
                  Use “{freeName(name, taken, numberedCopyName)}”
                </button>
              )}
            </span>
          )}
          <button type="button" className={BTN} onClick={requestClose} disabled={busy}>Cancel</button>
          {shown && (
            <button type="button" data-testid="model-import-btn"
              disabled={busy || problem !== null || clashNow}
              onClick={() => { void importAs(name) }}
              className="px-3 py-1.5 text-xs rounded bg-accent text-white hover:opacity-90 disabled:opacity-40">
              {phase.step === 'importing' ? 'Creating…' : 'Create project'}
            </button>
          )}
        </>)}
      </div>
    </Dialog>
  )
}

/** The page's one model-import dialog, opened by `requestModelImport`. */
export function ModelImportHost() {
  const request = useUIStore(s => s.modelImportRequest)
  const clear = useUIStore(s => s.clearModelImportRequest)
  if (!request) return null
  return <ModelImportDialog key={request.id} initialFiles={request.files} onClose={clear} />
}

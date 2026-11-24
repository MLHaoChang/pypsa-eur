// Preview, then import, an Excel input workbook as a new project
// (IMPORT-FLOW-1), then open it. The preview and the import never touch the
// open network; only the final switch does, through the ordinary
// `switchToProject` (it saves the outgoing project first, as every switch does).
// A project that was created but could not be opened is never imported again:
// "Try again" retries only the open.
import { useCallback, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { workbookApi, workbookRefusal, type WorkbookPreview, type WorkbookRefusal } from '../api/workbook'
import { useUIStore } from '../store/uiStore'
import { appLog } from '../store/simulationStore'
import { switchToProject } from '../utils/projectActions'
import { STUDY_RUNNING_NOT_OPENED, notOpenedReason } from '../components/workbook/workbookWords'
import { resultsApi } from '../api/simulation'

export type ImportPhase<P> =
  | { step: 'pick' }
  | { step: 'previewing' }
  | { step: 'preview'; preview: P }
  | { step: 'importing'; preview: P }
  | { step: 'opening'; name: string }
  | { step: 'not-opened'; name: string; reason: string }
export type WorkbookPhase = ImportPhase<WorkbookPreview>

/** What the flow needs of a previewable import: the workbook and the model-file imports both fit. */
export interface ImportSource<P extends { sha256: string }> {
  preview: (files: File[]) => Promise<P>
  /** Creates the project (left closed) and says its name. */
  create: (files: File[], sha256: string, name: string) => Promise<{ name: string }>
  /** One line for the app log, once the project exists. */
  logLine: (files: File[], created: string) => string
}

const workbookSource: ImportSource<WorkbookPreview> = {
  preview: ([f]) => workbookApi.preview(f),
  create: ([f], sha, name) => workbookApi.import(f, sha, name),
  logLine: ([f], created) => `Imported the Excel input workbook '${f.name}' as '${created}'`,
}

export function useWorkbookImport(onOpened: (name: string) => void) {
  const r = useFileImport(workbookSource, onOpened)
  const preview = useCallback((f: File) => r.preview([f]), [r.preview])
  return { ...r, file: r.files[0] ?? null, preview }
}

export function useFileImport<P extends { sha256: string }>(source: ImportSource<P>, onOpened: (name: string) => void) {
  const qc = useQueryClient()
  const [files, setFiles] = useState<File[]>([])
  const [phase, setPhase] = useState<ImportPhase<P>>({ step: 'pick' })
  const [refusal, setRefusal] = useState<WorkbookRefusal | null>(null)
  // A newer pick supersedes a preview still in flight.
  const seq = useRef(0)

  const preview = useCallback(async (fs: File[]) => {
    const mine = ++seq.current
    setFiles(fs)
    setRefusal(null)
    setPhase({ step: 'previewing' })
    try {
      const p = await source.preview(fs)
      if (mine === seq.current) setPhase({ step: 'preview', preview: p })
    } catch (e) {
      if (mine !== seq.current) return
      setRefusal(workbookRefusal(e))
      setPhase({ step: 'pick' })
    }
  }, [source])

  const open = useCallback(async (name: string) => {
    setRefusal(null)
    setPhase({ step: 'opening', name })
    // A running study holds the open network: a switch would wait for an abort
    // that never comes (the Start tile is off for the same reason). Say so now.
    const study = await resultsApi.getEhStudy({ quiet: true }).catch(() => null)
    if (study?.status === 'running') { setPhase({ step: 'not-opened', name, reason: STUDY_RUNNING_NOT_OPENED }); return }
    const ui = useUIStore.getState()
    ui.setProjectSwitchInProgress(true)
    try {
      const reason = notOpenedReason(await switchToProject(name, qc))
      if (reason) setPhase({ step: 'not-opened', name, reason })
      else onOpened(name)
    } catch {
      setPhase({ step: 'not-opened', name, reason: 'the app could not open it.' })
    } finally {
      ui.setProjectSwitchInProgress(false)
    }
  }, [qc, onOpened])

  const importAs = useCallback(async (name: string) => {
    if (files.length === 0 || phase.step !== 'preview') return
    const p = phase.preview
    setRefusal(null)
    setPhase({ step: 'importing', preview: p })
    let created: string
    try {
      created = (await source.create(files, p.sha256, name.trim())).name
    } catch (e) {
      setRefusal(workbookRefusal(e))
      setPhase({ step: 'preview', preview: p })
      return
    }
    appLog('INFO', source.logLine(files, created))
    // G4 (guided-mode spec §3.4): a new project starts Guided unless the user chose a mode.
    useUIStore.getState().noteNewProjectCreated('file')
    qc.invalidateQueries({ queryKey: ['projects'] })
    await open(created)
  }, [files, phase, qc, open, source])

  const busy = phase.step === 'previewing' || phase.step === 'importing' || phase.step === 'opening'
  return { files, phase, refusal, busy, preview, importAs, open }
}

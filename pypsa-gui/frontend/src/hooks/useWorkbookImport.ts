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

export type WorkbookPhase =
  | { step: 'pick' }
  | { step: 'previewing' }
  | { step: 'preview'; preview: WorkbookPreview }
  | { step: 'importing'; preview: WorkbookPreview }
  | { step: 'opening'; name: string }
  | { step: 'not-opened'; name: string; reason: string }

export function useWorkbookImport(onOpened: (name: string) => void) {
  const qc = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [phase, setPhase] = useState<WorkbookPhase>({ step: 'pick' })
  const [refusal, setRefusal] = useState<WorkbookRefusal | null>(null)
  // A newer pick supersedes a preview still in flight.
  const seq = useRef(0)

  const preview = useCallback(async (f: File) => {
    const mine = ++seq.current
    setFile(f)
    setRefusal(null)
    setPhase({ step: 'previewing' })
    try {
      const p = await workbookApi.preview(f)
      if (mine === seq.current) setPhase({ step: 'preview', preview: p })
    } catch (e) {
      if (mine !== seq.current) return
      setRefusal(workbookRefusal(e))
      setPhase({ step: 'pick' })
    }
  }, [])

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
    if (!file || phase.step !== 'preview') return
    const p = phase.preview
    setRefusal(null)
    setPhase({ step: 'importing', preview: p })
    let created: string
    try {
      created = (await workbookApi.import(file, p.sha256, name.trim())).name
    } catch (e) {
      setRefusal(workbookRefusal(e))
      setPhase({ step: 'preview', preview: p })
      return
    }
    appLog('INFO', `Imported the Excel input workbook '${file.name}' as '${created}'`)
    // G4 (guided-mode spec §3.4): a new project starts Guided unless the user chose a mode.
    useUIStore.getState().noteNewProjectCreated('file')
    qc.invalidateQueries({ queryKey: ['projects'] })
    await open(created)
  }, [file, phase, qc, open])

  const busy = phase.step === 'previewing' || phase.step === 'importing' || phase.step === 'opening'
  return { file, phase, refusal, busy, preview, importAs, open }
}

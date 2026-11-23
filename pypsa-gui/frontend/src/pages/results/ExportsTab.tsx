// Hand-off › Exports (card-workspaces.md §3.6, R6): the one export chooser. It was the
// Export tab of the sidebar's Project Data dialog; the sidebar's "Export…" row and the
// assistant's export request now open this tab instead.
import { useEffect, useState } from 'react'
import { Download } from 'lucide-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { useUIStore } from '../../store/uiStore'
import { networkApi } from '../../api/network'
import { ioApi } from '../../api/io'
import { workbookApi } from '../../api/workbook'
import { projectsApi } from '../../api/projects'
import {
  AS_LAST_SAVED, INPUT_WORKBOOK_DESC, INPUT_WORKBOOK_LABEL, SAVE_FIRST_REASON, TEMPLATE_DESC, TEMPLATE_LABEL,
} from '../../components/workbook/workbookWords'
import { saveProjectQuietly, slugify } from '../../utils/projectActions'
import { nk } from '../../utils/queryKeys'
import {
  ResultsWorkbookPicker, WORKBOOK_DEFAULT, useWorkbookViews, workbookBlock, type WorkbookChoice,
} from '../../layout/sidebar/ResultsWorkbookPicker'

export type ExportFormat = 'bundle' | 'netcdf' | 'csv' | 'excel' | 'matpower' | 'workbook' | 'input' | 'template'

export default function ExportsTab() {
  const projectName = useUIStore(s => s.projectName)
  const [exportFmt, setExportFmt] = useState<ExportFormat>(() => useUIStore.getState().ioModalFormat ?? 'bundle')
  const [workbook, setWorkbook] = useState<WorkbookChoice>(WORKBOOK_DEFAULT)
  const workbookViews = useWorkbookViews(exportFmt === 'workbook')
  const workbookOff = exportFmt === 'workbook' && workbookBlock(workbookViews.data, workbook) !== null
  const [exportFilename, setExportFilename] = useState(slugify(projectName))
  const [exporting, setExporting] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (!exporting) { setElapsed(0); return }
    const t0 = Date.now()
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 1000)
    return () => clearInterval(id)
  }, [exporting])
  const qc = useQueryClient()
  // The assistant's "export the results workbook" request names a format once: take it, then clear it.
  useEffect(() => { if (useUIStore.getState().ioModalFormat) useUIStore.setState({ ioModalFormat: null }) }, [])
  const currentProject = useUIStore(s => s.currentProject)
  // The input workbook holds the project as last saved: offer "Save first" while edits are unsaved.
  const { data: undoInfo } = useQuery({
    queryKey: nk(currentProject, 'undoInfo'), queryFn: networkApi.undoInfo,
    enabled: exportFmt === 'input' && !!currentProject, staleTime: 0,
  })
  const [savingFirst, setSavingFirst] = useState(false)
  const saveFirst = async () => {
    if (!currentProject) return
    setSavingFirst(true)
    const saved = await saveProjectQuietly(currentProject, true)
    setSavingFirst(false)
    qc.invalidateQueries({ queryKey: nk(currentProject, 'undoInfo') })
    qc.invalidateQueries({ queryKey: ['projects'] })
    if (!saved) toast.error(`Could not save ‘${currentProject}’.`)
  }
  const inputOff = exportFmt === 'input' && !currentProject

  const handleExport = async () => {
    setExporting(true)
    try {
      let blob: Blob
      let ext: string
      let templateName: string | null = null
      if (exportFmt === 'bundle') {
        if (!currentProject) {
          toast.error('Save the project first, then export as bundle.')
          return
        }
        blob = await projectsApi.downloadBundle(currentProject)
        ext = '.pypsaproj.zip'
      } else if (exportFmt === 'workbook') {
        const wb = await ioApi.exportResultsWorkbook(workbook.extra, workbook.all)
        if (!wb) {
          toast.error('Run the model first: the results workbook holds the solved results.')
          return
        }
        blob = wb
        ext = '.xlsx'
      } else if (exportFmt === 'input' || exportFmt === 'template') {
        if (exportFmt === 'input' && !currentProject) return
        const f = exportFmt === 'input' ? await workbookApi.project(currentProject!) : await workbookApi.template()
        blob = f.blob
        ext = '.xlsx'
        // The blank template keeps its own name: it is no project's file.
        if (exportFmt === 'template') templateName = f.filename
      } else {
        const fns = { netcdf: ioApi.exportNetcdf, csv: ioApi.exportCsv, excel: ioApi.exportExcel, matpower: ioApi.exportMatpower }
        const exts = { netcdf: '.nc', csv: '.zip', excel: '.xlsx', matpower: '.m' }
        blob = await fns[exportFmt]()
        ext = exts[exportFmt]
      }
      const fname = templateName ?? (exportFilename.trim() || slugify(projectName)) + ext
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = fname; a.click()
      URL.revokeObjectURL(url)
      toast.success(`Downloaded ${fname}`)
    } catch (e) {
      toast.error(['workbook', 'input', 'template', 'excel'].includes(exportFmt) && e instanceof Error && !('isAxiosError' in e) ? e.message : 'Export failed')
    } finally { setExporting(false) }
  }

  return (
    <div className="h-full overflow-auto p-4" data-testid="handoff-exports">
      <div className="max-w-[520px]">
      {exportFmt !== 'template' && <label className="flex flex-col gap-1 mb-4">
        <span className="text-xs font-medium text-text">File name</span>
        <div className="flex items-center gap-1">
          <input
            type="text"
            value={exportFilename}
            onChange={e => setExportFilename(e.target.value)}
            placeholder={slugify(projectName)}
            className="flex-1 px-2.5 py-1.5 text-xs border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20"
          />
          <span className="text-[10px] text-muted font-mono">
            {exportFmt === 'bundle' ? '.pypsaproj.zip' : exportFmt === 'netcdf' ? '.nc' : exportFmt === 'csv' ? '.zip' : exportFmt === 'matpower' ? '.m' : '.xlsx'}
          </span>
        </div>
      </label>}
      <p className="text-xs text-muted mb-3">Format:</p>
      <div className="space-y-2 mb-4">
        {([
          { id: 'bundle',   label: 'Project bundle (.pypsaproj.zip)', desc: 'Network + solve results + time series + solver config + layout — full round-trip' },
          { id: 'netcdf',   label: 'NetCDF (.nc)',   desc: 'Native PyPSA format — lossless' },
          { id: 'excel',    label: 'Excel (.xlsx)',  desc: 'One sheet per component' },
          { id: 'workbook', label: 'Results workbook (Excel)', desc: 'The solved results: overview, key charts and one sheet per result tab' },
          { id: 'input',    label: INPUT_WORKBOOK_LABEL, desc: INPUT_WORKBOOK_DESC },
          { id: 'template', label: TEMPLATE_LABEL, desc: TEMPLATE_DESC },
          { id: 'csv',      label: 'CSV zip (.zip)', desc: 'Folder of CSVs' },
          { id: 'matpower', label: 'MATPOWER (.m)',  desc: 'AC-only, for MATLAB/Octave' },
        ] as const).map(f => (
          <div key={f.id}>
            <label className="flex items-center gap-3 cursor-pointer p-2.5 rounded-lg border border-border hover:border-accent/50 transition-colors">
              <input type="radio" name="fmt" value={f.id} checked={exportFmt === f.id}
                onChange={() => setExportFmt(f.id)} className="accent-accent" />
              <div>
                <div className="text-xs font-semibold text-text">{f.label}</div>
                <div className="text-[10px] text-muted">{f.desc}</div>
                {f.id === 'input' && (currentProject ? (
                  <div className="flex items-baseline gap-2 text-[10px] text-muted" data-testid="input-workbook-note">
                    <span>{AS_LAST_SAVED}</span>
                    {undoInfo?.unsaved && (
                      <button type="button" data-testid="input-workbook-save-first" disabled={savingFirst}
                        onClick={e => { e.preventDefault(); void saveFirst() }}
                        className="shrink-0 text-text underline disabled:opacity-50">
                        {savingFirst ? 'Saving…' : 'Save first'}
                      </button>
                    )}
                  </div>
                ) : (
                  <div className="text-[10px] text-warn" data-testid="input-workbook-why">{SAVE_FIRST_REASON}</div>
                ))}
              </div>
            </label>
            {f.id === 'workbook' && exportFmt === 'workbook' && (
              <ResultsWorkbookPicker value={workbook} onChange={setWorkbook} />
            )}
          </div>
        ))}
      </div>
      <p className="text-[10px] text-muted mb-4 flex items-start gap-1.5">
        <span className="shrink-0 mt-0.5">ⓘ</span>
        Files are downloaded to your browser's default download folder. To change the folder, update your browser settings.
      </p>
      <div className="flex gap-2 justify-end">
        <button onClick={handleExport} disabled={exporting || workbookOff || inputOff}
          className="flex items-center gap-1.5 px-4 py-1.5 bg-accent text-white rounded text-xs font-semibold hover:bg-accent/90 disabled:opacity-50 transition-colors">
          <Download size={12} /> {exporting ? `Preparing… ${elapsed} s` : 'Download File'}
        </button>
      </div>
      </div>
    </div>
  )
}

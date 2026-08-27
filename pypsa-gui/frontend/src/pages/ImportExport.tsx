import { useCallback, useState, useRef } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Upload, Download, RotateCcw, FileText, Archive, Table2, Cpu } from 'lucide-react'
import { ioApi } from '../api/io'
import { networkApi } from '../api/network'
import { projectsApi } from '../api/projects'
import type { ImportSummary } from '../api/types'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { invalidateNetworkQueries, saveProjectQuietly } from '../utils/projectActions'
import toast from 'react-hot-toast'
import { confirmToast } from '../utils/toasts'
import { ConfirmDialog } from '../components/ConfirmDialog'

const EXPORT_FORMATS = [
  {
    id: 'netcdf',
    label: 'NetCDF',
    ext: '.nc',
    description: 'Native PyPSA format — lossless, compact',
    icon: Cpu,
    fn: () => ioApi.exportNetcdf(),
  },
  {
    id: 'excel',
    label: 'Excel',
    ext: '.xlsx',
    description: 'One sheet per component, human-readable',
    icon: Table2,
    fn: () => ioApi.exportExcel(),
  },
  {
    id: 'csv',
    label: 'CSV (zip)',
    ext: '.zip',
    description: 'Folder of CSVs — compatible with n.import_from_csv_folder()',
    icon: Archive,
    fn: () => ioApi.exportCsv(),
  },
  {
    id: 'matpower',
    label: 'MATPOWER',
    ext: '.m',
    description: 'AC-only format for MATLAB/Octave (buses + lines + generators)',
    icon: FileText,
    fn: () => ioApi.exportMatpower(),
  },
]

function ExportCard({ format }: { format: typeof EXPORT_FORMATS[number] }) {
  const [loading, setLoading] = useState(false)

  const handleDownload = async () => {
    setLoading(true)
    try {
      const blob = await format.fn()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `network${format.ext}`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      toast.error(`Export to ${format.label} failed`)
    } finally {
      setLoading(false)
    }
  }

  const Icon = format.icon

  return (
    <div className="bg-panel border border-border rounded-lg p-4 flex flex-col gap-3 hover:border-accent/50 transition-colors">
      <div className="flex items-center gap-2">
        <Icon size={18} className="text-accent" />
        <span className="text-sm font-semibold text-text">{format.label}</span>
        <span className="text-[10px] text-muted font-mono ml-auto">{format.ext}</span>
      </div>
      <p className="text-xs text-muted flex-1">{format.description}</p>
      <button
        onClick={handleDownload}
        disabled={loading}
        className="flex items-center justify-center gap-1.5 py-1.5 bg-accent/20 text-accent border border-accent/40 rounded text-xs hover:bg-accent/30 transition-colors disabled:opacity-40"
      >
        <Download size={12} />
        {loading ? 'Exporting…' : 'Download'}
      </button>
    </div>
  )
}

export function ImportZone({ onSuccess }: { onSuccess: (summary: ImportSummary, fileName?: string) => void }) {
  const [dragging, setDragging] = useState(false)
  const [pendingImport, setPendingImport] = useState<{ file: File; message: string } | null>(null)
  const pendingFileRef = useRef<string>('')
  const importedNameRef = useRef<string | null>(null)
  const { setCurrentProject, setProjectName, currentProject } = useUIStore()
  const importMut = useMutation({
    mutationFn: async (file: File) => {
      pendingFileRef.current = file.name
      importedNameRef.current = null
      const lower = file.name.toLowerCase()
      if (lower.endsWith('.pypsaproj.zip')) {
        // When a project is already active, load the bundle's data INTO it
        // under the current name. Treats the imported file as a data source
        // for the new project rather than switching the user's tab to the
        // bundle's original project name. Falls back to the bundle's
        // metadata.json name when no project is active.
        const target = currentProject || undefined
        const res = await projectsApi.importBundle(file, target)
        importedNameRef.current = res.imported
        return res.summary
      }
      const ext = lower.split('.').pop() ?? ''
      if (ext === 'nc') return ioApi.importNetcdf(file)
      if (ext === 'xlsx') return ioApi.importExcel(file)
      if (ext === 'zip') return ioApi.importCsv(file)
      if (ext === 'm') return ioApi.importMatpower(file)
      return Promise.reject(new Error(`Unknown file type: .${ext}`))
    },
    onSuccess: (s) => {
      // Only switch the active project / displayed name when the bundle was
      // imported under a NEW name (i.e. there was no current project).
      // Importing into the current project keeps the user's tab and label
      // pointing where they already were.
      const name = importedNameRef.current
      if (name && name !== currentProject) {
        setCurrentProject(name)
        setProjectName(name)
      } else if (!name && currentProject) {
        // Raw network import (.nc / .xlsx / .csv / .m): the in-memory network
        // was REPLACED with content that belongs to no saved project — the
        // backend leaves its loaded-project binding unbound (None). Clear the
        // active project so the 5-min autosave (which bails when currentProject
        // is null) can't CLAIM and overwrite the previously-active project's
        // folder with this freshly-imported network. The user must explicitly
        // Save (As) to bind it to a project — that first-save claims it
        // intentionally, not silently.
        setCurrentProject(null)
      }
      onSuccess(s, pendingFileRef.current)
    },
    onError: (e: Error) => toast.error(e.message),
    // M2: the DIALOG closes here, on settle — not in onConfirm. Clearing
    // `pendingImport` synchronously in onConfirm unmounted the dialog on the
    // same tick, so `pending={importMut.isPending}` had nothing left to render
    // and the "Working…" affordance never appeared; and on failure the user
    // was left with no dialog and no signal. ScenariosPanel's delete dialog is
    // the precedent: confirm fires the mutation, the mutation closes the dialog.
    onSettled: () => setPendingImport(null),
  })

  const handleFile = useCallback(async (file: File) => {
    const isBundle = file.name.toLowerCase().endsWith('.pypsaproj.zip')
    if (currentProject && isBundle) {
      // Bundle-into-current overwrites the project's contents and is NOT
      // undo-captured (backend _UNDO_PREFIXES excludes /api/projects/) — ask.
      setPendingImport({
        file,
        message: `Importing this bundle will replace the contents of '${currentProject}'.`,
      })
      return
    }
    if (currentProject && !isBundle) {
      // Raw import is undo-captured; persist the outgoing project first so
      // nothing is lost, then proceed without a prompt (Sidebar precedent).
      //
      // M3: the prompt-less path is prompt-less BECAUSE the save happens
      // first. If that save is refused — a foreign edit lock is the realistic
      // cause — importing anyway destroys the in-memory network the save was
      // meant to protect. Stop, say so once, and leave the user's work alone.
      const saved = await saveProjectQuietly(currentProject)
      if (!saved) {
        toast.error(`Could not save '${currentProject}' before importing — nothing was imported.`)
        return
      }
      importMut.mutate(file)
      return
    }
    // ADR-0001 on a DESTRUCTIVE path. `depth` used to initialise to 0 with the
    // failed probe falling through to it, so an unreachable or refusing backend
    // produced exactly the value a genuinely clean network produces — and the
    // import then ran with no confirmation at all. The unknown is its own
    // state, and on a path that replaces the user's network it must fail
    // CLOSED: ask, rather than assume there was nothing to lose.
    // `unsaved`, NOT `depth > 0`. Undo depth counts undoable EDITS; solver
    // results are written straight into the network and never pushed, so a
    // solved-but-unsaved project reports depth 0 and this guard used to let the
    // solve be destroyed silently.
    let unsaved = false
    let unsavedKnown = true
    try { unsaved = (await networkApi.undoInfo()).unsaved } catch { unsavedKnown = false }
    if (!unsavedKnown || unsaved) {
      setPendingImport({
        file,
        message: unsavedKnown
          ? 'The current unsaved network will be replaced.'
          : 'Could not check for unsaved changes — the backend did not answer. '
            + 'Any unsaved work in the current network will be replaced.',
      })
      return
    }
    importMut.mutate(file)
  }, [importMut, currentProject])

  const onDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }, [handleFile])

  return (
    <div
      onDrop={onDrop}
      onDragOver={e => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      className={`border-2 border-dashed rounded-lg p-8 flex flex-col items-center gap-3 transition-colors
        ${dragging ? 'border-accent bg-accent/5' : 'border-border hover:border-accent/50'}`}
    >
      <Upload size={32} className={dragging ? 'text-accent' : 'text-muted'} />
      <div className="text-center">
        <p className="text-sm text-text font-semibold">Drop a project or network file here</p>
        <p className="text-xs text-muted mt-1">
          Recommended: .pypsaproj.zip (network + solve results + config). Also: .nc · .xlsx · .zip (CSV) · .m (network only)
        </p>
      </div>
      <label className="flex items-center gap-1.5 px-4 py-2 bg-panel border border-border rounded text-xs text-text hover:border-accent cursor-pointer transition-colors">
        <Upload size={12} /> Browse file
        <input
          type="file"
          accept=".pypsaproj.zip,.nc,.xlsx,.zip,.m"
          className="hidden"
          onChange={e => { const f = e.target.files?.[0]; if (f) handleFile(f) }}
        />
      </label>
      {importMut.isPending && <p className="text-xs text-muted animate-pulse">Importing…</p>}
      <ConfirmDialog
        open={pendingImport != null}
        title="Replace current network"
        message={pendingImport?.message ?? ''}
        confirmLabel="Import"
        danger
        pending={importMut.isPending}
        onConfirm={() => { if (pendingImport) importMut.mutate(pendingImport.file) }}
        onCancel={() => setPendingImport(null)}
      />
    </div>
  )
}

function ImportSummaryCard({ summary }: { summary: ImportSummary }) {
  const fields: [string, number][] = [
    ['Buses', summary.buses],
    ['Generators', summary.generators],
    ['Lines', summary.lines],
    ['Links', summary.links],
    ['Storage Units', summary.storage_units],
    ['Stores', summary.stores],
    ['Loads', summary.loads],
    ['Transformers', summary.transformers],
    ['Snapshots', summary.snapshots],
  ]
  return (
    <div className="bg-panel border border-success/40 rounded-lg p-4 mt-4">
      <p className="text-xs font-semibold text-success mb-3">Import successful</p>
      <div className="grid grid-cols-3 gap-2">
        {fields.map(([label, val]) => (
          <div key={label} className="flex justify-between text-xs">
            <span className="text-muted">{label}</span>
            <span className="font-mono text-text">{val}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function ImportExport() {
  const qc = useQueryClient()
  const [importResult, setImportResult] = useState<ImportSummary | null>(null)

  const resetMut = useMutation({
    mutationFn: () => networkApi.resetNetwork(),
    onSuccess: () => {
      // Reset/import swap the CURRENT project's in-memory network. Scope to its
      // network + result roots instead of a bare `invalidateQueries()` that
      // nuked every resident project's cache (B8 keeps other projects warm).
      const proj = useUIStore.getState().currentProject
      invalidateNetworkQueries(qc, proj)
      qc.invalidateQueries({ queryKey: nk(proj, 'results') })
      setImportResult(null)
      toast.success('Network reset')
    },
  })

  const handleImportSuccess = useCallback((summary: ImportSummary) => {
    setImportResult(summary)
    const proj = useUIStore.getState().currentProject
    invalidateNetworkQueries(qc, proj)
    qc.invalidateQueries({ queryKey: nk(proj, 'results') })
    toast.success(`Imported: ${summary.buses} buses, ${summary.generators} generators, ${summary.snapshots} snapshots`)
  }, [qc])

  return (
    <div className="flex flex-col h-full overflow-auto p-4 space-y-6">
      <div className="flex gap-6">
        {/* Import */}
        <div className="flex-1">
          <h2 className="text-sm font-semibold text-text mb-3">Import Network</h2>
          <ImportZone onSuccess={handleImportSuccess} />
          {importResult && <ImportSummaryCard summary={importResult} />}
        </div>

        {/* Export */}
        <div className="flex-1">
          <h2 className="text-sm font-semibold text-text mb-3">Export Network</h2>
          <div className="grid grid-cols-2 gap-3">
            {EXPORT_FORMATS.map(f => <ExportCard key={f.id} format={f} />)}
          </div>
        </div>
      </div>

      {/* Danger zone */}
      <div className="border border-danger/30 rounded-lg p-4 bg-danger/5">
        <h3 className="text-sm font-semibold text-danger mb-1">Danger Zone</h3>
        <p className="text-xs text-muted mb-3">Reset the network to an empty PyPSA.Network(). This cannot be undone.</p>
        <button
          onClick={() => confirmToast(
            'Reset the network? All unsaved changes will be lost.',
            () => resetMut.mutate(),
            { confirmLabel: 'Reset', danger: true },
          )}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-danger/20 text-danger border border-danger/40 rounded text-xs hover:bg-danger/30 transition-colors"
        >
          <RotateCcw size={12} /> Reset Network
        </button>
      </div>
    </div>
  )
}

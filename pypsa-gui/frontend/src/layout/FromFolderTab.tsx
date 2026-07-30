import { useState } from 'react'
import { FolderInput } from 'lucide-react'
import toast from 'react-hot-toast'
import { useQueryClient } from '@tanstack/react-query'
import { projectsApi, type ImportFolderReport } from '../api/projects'

/**
 * Bring a folder of pre-desktop projects into the app. DESKTOP ONLY.
 *
 * Why this exists: the packaged app cannot reach an old project tree at all.
 * `resolve_legacy_root()` looks for `backend/projects`, which no bundle
 * contains, and `PYPSAGUI_LEGACY_IMPORT_ROOT` is cleared on every frozen launch
 * so a stale inherited variable cannot copy a tree the app never chose into the
 * user's Documents. Correct — and it left anyone who installed the app with no
 * way in except importing one file at a time.
 *
 * PREVIEW FIRST, always. The destructive version of this is one click on a path
 * somebody typed, so the button that copies only appears after the app has said
 * what it found. `import_all(apply=False)` is a faithful dry run, and it
 * consults the same already-imported signals as the real thing, so the second
 * run says "already imported" rather than promising duplicates.
 */
export default function FromFolderTab({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const [path, setPath] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<ImportFolderReport | null>(null)

  async function run(apply: boolean) {
    setBusy(true)
    setError(null)
    try {
      const report = await projectsApi.importFolder(path.trim(), apply)
      if (apply) {
        await qc.invalidateQueries({ queryKey: ['projects'] })
        toast.success(
          report.imported.length
            ? `Imported ${report.imported.length} project${report.imported.length === 1 ? '' : 's'}`
            : 'Nothing new to import — everything there is already in the app',
        )
        onClose()
        return
      }
      setPreview(report)
    } catch (e) {
      // The 400s are the useful ones: "that folder does not exist" / "not a
      // folder" name which of the two mistakes the typed path made.
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail ?? (e as Error)?.message ?? 'Import failed')
      setPreview(null)
    } finally {
      setBusy(false)
    }
  }

  const nothingToDo =
    preview !== null && preview.would_import.length === 0

  return (
    <div className="p-4 space-y-4">
      <div className="space-y-1">
        <label className="text-xs font-medium text-text" htmlFor="import-folder-path">
          Folder to import from
        </label>
        <input
          id="import-folder-path"
          className="w-full rounded-md border border-border bg-bg px-2.5 py-1.5 text-sm text-text"
          onChange={e => { setPath(e.target.value); setPreview(null); setError(null) }}
          placeholder="/Users/you/old-pypsa-gui/backend/projects"
          spellCheck={false}
          value={path}
        />
        <p className="text-xs text-muted">
          Every project folder inside is copied in. Your originals are not moved
          or changed.
        </p>
      </div>

      {error && (
        <p className="rounded-md border border-red-500/40 bg-red-500/10 px-2.5 py-2 text-xs text-red-300">
          {error}
        </p>
      )}

      {preview && (
        <div className="space-y-2 text-xs">
          <ReportList label="Will be imported" items={preview.would_import} tone="good" />
          <ReportList label="Already imported" items={preview.already_imported} tone="muted" />
          <ReportList label="Skipped" items={preview.skipped} tone="muted" />
          <ReportList label="Problems" items={[...preview.collisions, ...preview.failed]} tone="bad" />
        </div>
      )}

      <div className="flex items-center justify-end gap-2 pt-1">
        <button className="px-3 py-1.5 text-xs text-muted hover:text-text" onClick={onClose} type="button">
          Cancel
        </button>
        {preview === null ? (
          <button
            className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
            disabled={busy || path.trim() === ''}
            onClick={() => void run(false)}
            type="button"
          >
            <FolderInput size={12} />
            {busy ? 'Checking…' : 'Check folder'}
          </button>
        ) : (
          <button
            className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
            disabled={busy || nothingToDo}
            onClick={() => void run(true)}
            type="button"
          >
            <FolderInput size={12} />
            {busy
              ? 'Importing…'
              : nothingToDo
                ? 'Nothing to import'
                : `Import ${preview.would_import.length}`}
          </button>
        )}
      </div>
    </div>
  )
}

function ReportList({ items, label, tone }: {
  items: string[]
  label: string
  tone: 'good' | 'muted' | 'bad'
}) {
  if (items.length === 0) return null
  const colour =
    tone === 'good' ? 'text-text' : tone === 'bad' ? 'text-red-300' : 'text-muted'
  return (
    <div>
      <div className={`font-medium ${colour}`}>{label} ({items.length})</div>
      <ul className="mt-0.5 list-disc pl-5 text-muted">
        {items.slice(0, 20).map(item => <li key={item}>{item}</li>)}
        {items.length > 20 && <li>…and {items.length - 20} more</li>}
      </ul>
    </div>
  )
}

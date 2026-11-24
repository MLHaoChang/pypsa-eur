// A model file (PSS/E, PowerFactory, PLEXOS …) imported as a NEW project after a
// preview (IMPORT-FLOW-1, project-io 04). The preview reads the files and writes
// nothing; the import creates the project and leaves it closed. Every name the
// preview shows is plain (RAW-NAMES-1); the backend words each refusal.
import client from './client'
import type { WorkbookReport } from './workbook'

export interface ModelFormat {
  id: string
  label: string
  extensions: string[]
  help?: string
}

/** What the backend's preview says: counts per kind, then what was left out and assumed. */
export interface ModelPreview {
  sha256: string
  format: string
  format_label: string
  suggested_name: string
  files: string[]
  bus_count: number
  snapshot_count: number
  counts: Record<string, number>
  unmapped: WorkbookReport['unmapped']
  assumed: WorkbookReport['assumed']
  warnings: string[]
  suggestions: WorkbookReport['suggestions']
  stored_files: string[]
}

export interface ModelImported extends ModelPreview { project_id: string; name: string }

// A preview reads every file; a large model takes a while.
const LONG = 180000

function form(files: File[], extra: Record<string, string> = {}): FormData {
  const fd = new FormData()
  for (const f of files) fd.append('files', f)
  for (const [k, v] of Object.entries(extra)) fd.append(k, v)
  return fd
}

export const modelImportApi = {
  formats: () => client.get<{ formats: ModelFormat[] }>('/model-import/formats', { skipErrorToast: true }).then(r => r.data.formats),
  /** Writes nothing. The dialog shows its own refusals (no toast). */
  preview: (files: File[]) =>
    client.post<ModelPreview>('/model-import/preview', form(files), { timeout: LONG, skipErrorToast: true }).then(r => r.data),
  /** Creates the project and leaves it closed; `expectedSha256` is the preview's. */
  import: (files: File[], expectedSha256: string, name: string) =>
    client.post<ModelImported>('/model-import/import', form(files, { expected_sha256: expectedSha256, name }),
      { timeout: LONG, skipErrorToast: true }).then(r => r.data),
}

/** `.raw`-style extensions the registry lists, lowercase with the dot. */
export function modelExtensions(formats: ModelFormat[] | undefined): string[] {
  return (formats ?? []).flatMap(f => f.extensions.map(e => (e.startsWith('.') ? e : `.${e}`).toLowerCase()))
}

export const hasModelExtension = (name: string, exts: string[]) => exts.some(e => name.toLowerCase().endsWith(e))

/** What the bundle import's dry run answers (`dry_run=true`): the fields the real import reports. */
interface BundleDryRun {
  name: string
  stripped?: string[]
  stripped_note?: string
  ignored?: string[]
  warnings?: string[]
}

/** A project bundle's dry run in the model preview's shape: the no-manifest warning and the
 *  note on what the import clears show with the other warnings; archive parts it never
 *  reads are listed under "Not carried". Nothing is written. */
export function bundlePreview(file: File): Promise<ModelPreview> {
  const fd = new FormData()
  fd.append('file', file)
  return client.post<BundleDryRun>('/projects/import_bundle', fd,
    { params: { dry_run: true }, timeout: LONG, skipErrorToast: true }).then(({ data: d }) => ({
    // A bundle import takes no hash: the files are sent again and checked again.
    sha256: 'bundle',
    format: 'bundle',
    format_label: 'a PyPSA Studio project file',
    suggested_name: d.name,
    files: [file.name],
    bus_count: 0,
    snapshot_count: 0,
    counts: {},
    unmapped: (d.ignored ?? []).map(m => ({ source_item: m, reason: 'not part of a project, so it is not read' })),
    assumed: [],
    warnings: [...(d.warnings ?? []), ...(d.stripped_note ? [d.stripped_note] : [])],
    suggestions: [],
    stored_files: [],
  }))
}

import { useEffect, useMemo, useState } from 'react'
import { Dialog } from './Dialog'
import { EditCardTable, EditReceiptLine } from './EditCard'
import { equipmentTableApi, type ColumnMap, type EquipmentPreview, type MappingApplied } from '../api/equipmentTable'
import { listUploads, uploadFile, type UploadMeta } from '../api/uploads'
import { networkApi } from '../api/network'
import type { FlowSectionId } from '../api/flow'
import { openSectionInExpert } from '../layout/flowNavigation'
import { sectionMeta } from '../layout/flowSections'
import { useUIStore } from '../store/uiStore'

// "Import rows from a table…" (data-to-model, owner AI-567): map an uploaded
// CSV/XLSX, one row per existing asset, onto assets of ONE class. The user
// picks the upload (the project uploads list chat shares), the name column and
// a field and unit per column from the server's field schema; the preview is
// the same edit card the assistant shows, and Apply writes exactly that card
// and shows its receipt. The server decides every value (services/equipment_table.py).

const TABLE_FILE = /\.(csv|xlsx|xlsm)$/i

/** The asset-table classes the server's mapper writes to (section 3: lines and
 *  transformers; 4: loads; 5: the rest). */
export const TABLE_IMPORT_CLASSES: ReadonlySet<string> = new Set([
  'Line', 'Transformer', 'Load', 'Generator', 'StorageUnit', 'Store', 'Link',
])

function errorText(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && 'message' in detail) return String((detail as { message: unknown }).message)
  if (e instanceof Error && e.message) return e.message
  return fallback
}

const select = 'border border-border rounded bg-bg px-1.5 py-1 text-[11px] focus:outline-none focus:border-accent'

export default function EquipmentTableImport({ componentClass, assetsLabel, onClose, onApplied }: {
  componentClass: string
  /** What the asset table calls these assets ("generators"). */
  assetsLabel: string
  onClose: () => void
  /** After a write: refresh what shows the class's assets. */
  onApplied: () => void
}) {
  const project = useUIStore(s => s.currentProject)
  const [uploads, setUploads] = useState<UploadMeta[] | null>(null)
  const [uploadId, setUploadId] = useState('')
  const [sheet, setSheet] = useState<string | null>(null)
  const [table, setTable] = useState<EquipmentPreview | null>(null)
  const [nameColumn, setNameColumn] = useState('')
  const [columns, setColumns] = useState<Record<string, ColumnMap>>({})
  const [currency, setCurrency] = useState('')
  const [moneyYear, setMoneyYear] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [preview, setPreview] = useState<EquipmentPreview | null>(null)
  const [applied, setApplied] = useState<MappingApplied | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!project) { setUploads([]); return }
    let cancelled = false
    listUploads(project, 'user_upload')
      .then(list => { if (!cancelled) setUploads(list.filter(u => TABLE_FILE.test(u.filename))) })
      .catch(() => { if (!cancelled) setUploads([]) })
    return () => { cancelled = true }
  }, [project])

  // A table added here joins the same project uploads list chat uses.
  const addUpload = async (file: File | undefined) => {
    if (!file || !project) return
    setBusy(true); setError(null)
    try {
      const meta = await uploadFile(project, file)
      setUploads(prev => [meta, ...(prev ?? []).filter(u => u.file_id !== meta.file_id)])
      setUploadId(meta.file_id); setSheet(null)
    } catch (e) { setError(errorText(e, 'The file could not be uploaded.')) }
    finally { setBusy(false) }
  }

  // Reading the table: its headers, a few rows, and the class's mappable fields.
  useEffect(() => {
    if (!uploadId) { setTable(null); return }
    let cancelled = false
    setError(null); setPreview(null); setConfirmed(false)
    equipmentTableApi.preview({ upload_id: uploadId, sheet, component_class: componentClass })
      .then(t => {
        if (cancelled) return
        setTable(t)
        const headers = t.table.headers
        setNameColumn(headers.find(h => h.toLowerCase() === 'name') ?? headers[0] ?? '')
        setColumns({})
      })
      .catch(e => { if (!cancelled) { setTable(null); setError(errorText(e, 'The table could not be read.')) } })
    return () => { cancelled = true }
  }, [uploadId, sheet, componentClass])

  const fields = useMemo(() => new Map((table?.fields ?? []).map(f => [f.field, f])), [table])
  const costPicked = Object.values(columns).some(c => fields.get(c.field)?.currency)

  // Any change to what the preview was built from drops it and the money-year
  // confirmation with it: a confirmation covers only the plan it was shown on.
  const invalidate = () => { setPreview(null); setConfirmed(false) }

  const request = (confirm = confirmed) => ({
    upload_id: uploadId, sheet, component_class: componentClass, name_column: nameColumn, columns,
    currency: costPicked ? currency || null : null,
    money_year: costPicked && moneyYear ? Number(moneyYear) : null,
    confirm: confirm ? ['money_year'] : [],
  })

  const setColumn = (header: string, field: string) => {
    invalidate()
    setColumns(prev => {
      const next = { ...prev }
      if (!field) delete next[header]
      // The unit is the user's statement about the table, never assumed.
      else next[header] = { field, unit: '' }
      return next
    })
  }

  const runPreview = async (confirm = confirmed) => {
    setBusy(true); setError(null)
    try { setPreview(await equipmentTableApi.preview(request(confirm))) }
    catch (e) { setPreview(null); setError(errorText(e, 'The preview failed.')) }
    finally { setBusy(false) }
  }

  const runApply = async () => {
    if (!preview?.card) return
    setBusy(true); setError(null)
    try {
      setApplied(await equipmentTableApi.apply({ ...request(), source_sha256: preview.table.source.sha256,
        digest: preview.card.digest }))
      onApplied()
    } catch (e) { setError(errorText(e, 'Nothing was applied.')) }
    finally { setBusy(false) }
  }

  const undo = async () => {
    setBusy(true)
    try { await networkApi.undo(); setApplied(null); setPreview(null); onApplied() }
    catch (e) { setError(errorText(e, 'Undo failed.')) }
    finally { setBusy(false) }
  }

  const source = table?.table.source
  const plan = preview?.plan
  const sectionId = (applied?.receipt.section ?? preview?.card?.section)?.id as FlowSectionId | undefined
  const mapped = Object.keys(columns).length
  const unitless = Object.keys(columns).find(h => !columns[h].unit)
  // Why Preview or Apply is not available yet, with its fix, shown beside the buttons.
  const previewWhy = !table ? 'Choose a table first.'
    : !nameColumn ? 'Pick the name column first.'
      : mapped === 0 ? 'Pick a field for at least one column.'
        : unitless ? `Pick the unit of “${unitless}” in the table.`
          : costPicked && (!currency || moneyYear.length !== 4) ? 'Pick a currency and money year for the cost columns.'
            : null
  const applyWhy = previewWhy ?? (!plan ? 'Preview the changes first.'
    : preview?.card ? null
      : plan.missing.length ? 'Confirm the money year above, then preview again.'
        : 'Nothing to change: every mapped value is already set or left out.')

  return (
    <Dialog open onClose={onClose} title="Import rows from a table" z={950} dismissOnBackdrop={!busy}
      panelClassName="bg-bg rounded-lg shadow-2xl w-[44rem] max-w-[94vw] max-h-[85vh] flex flex-col overflow-hidden border border-border">
      <div className="px-4 py-3 border-b border-border">
        <div className="text-sm font-semibold text-text">Import rows from a table</div>
        <div className="text-[11px] text-muted">
          One row per existing asset among the {assetsLabel}, named in the table. Each value is checked against its field before anything is written.
        </div>
      </div>
      <div className="flex-1 overflow-auto px-4 py-3 text-xs flex flex-col gap-4" data-testid="table-import">
        {applied ? (
          <section data-testid="table-import-receipt">
            <EditReceiptLine receipt={applied.receipt} disabled={busy} onRequest={() => { void undo() }} />
            {applied.partial && applied.failed && (
              <p className="mt-2 text-warn" data-testid="table-import-partial">
                Stopped at {applied.failed.name}: {applied.failed.message}
                {applied.not_attempted?.length ? ` Not attempted: ${applied.not_attempted.join(', ')}.` : ''}
              </p>
            )}
          </section>
        ) : (<>
          <section className="flex flex-col gap-1.5">
            <h3 className="text-[10px] font-bold uppercase tracking-wider text-muted">Table</h3>
            {uploads === null ? <p className="text-muted">Reading the project's uploads…</p>
              : (
                <div className="flex flex-wrap items-center gap-2">
                  {uploads.length === 0 && <span className="text-muted" data-testid="table-import-no-uploads">
                    No CSV or XLSX in this project's uploads yet.
                  </span>}
                  {uploads.length > 0 && <select aria-label="Uploaded table" className={select} value={uploadId} data-testid="table-import-upload"
                    onChange={e => { setUploadId(e.target.value); setSheet(null) }}>
                    <option value="">Choose an upload…</option>
                    {uploads.map(u => <option key={u.file_id} value={u.file_id}>{u.filename}</option>)}
                  </select>}
                  <label className="cursor-pointer text-accent hover:underline">
                    Upload a table…
                    <input type="file" accept=".csv,.xlsx,.xlsm" className="sr-only" data-testid="table-import-file"
                      disabled={busy} onChange={e => { void addUpload(e.target.files?.[0]); e.target.value = '' }} />
                  </label>
                  {source && source.sheets.length > 1 && (
                    <select aria-label="Sheet" className={select} value={source.sheet}
                      onChange={e => setSheet(e.target.value)}>
                      {source.sheets.map(s => <option key={s} value={s}>{s}</option>)}
                    </select>
                  )}
                  {table && <span className="text-muted">{table.table.row_count} row{table.table.row_count === 1 ? '' : 's'}</span>}
                </div>
              )}
          </section>

          {table && (
            <section className="flex flex-col gap-1.5">
              <h3 className="text-[10px] font-bold uppercase tracking-wider text-muted">Columns</h3>
              <label className="flex items-center gap-2">
                <span className="w-40 text-text">Asset name column</span>
                <select aria-label="Asset name column" className={select} value={nameColumn}
                  onChange={e => { setNameColumn(e.target.value); setColumn(e.target.value, '') }}>
                  {table.table.headers.map(h => <option key={h} value={h}>{h}</option>)}
                </select>
              </label>
              <table className="w-full text-left" data-testid="table-import-columns">
                <thead className="text-[10px] text-muted">
                  <tr><th className="py-0.5 pr-2 font-medium">Column</th><th className="py-0.5 pr-2 font-medium">First value</th>
                    <th className="py-0.5 pr-2 font-medium">Field</th><th className="py-0.5 font-medium">Unit in the table</th></tr>
                </thead>
                <tbody>
                  {table.table.headers.filter(h => h !== nameColumn).map(h => {
                    const pick = columns[h]
                    const units = pick ? fields.get(pick.field)?.units ?? [] : []
                    const first = table.table.rows[0]?.cells[h]
                    return (
                      <tr key={h} className="border-t border-border/60">
                        <td className="py-1 pr-2 font-mono text-text">{h}</td>
                        <td className="py-1 pr-2 text-muted truncate max-w-[8rem]">{first === null || first === undefined ? '' : String(first)}</td>
                        <td className="py-1 pr-2">
                          <select aria-label={`Field for ${h}`} className={select} value={pick?.field ?? ''}
                            onChange={e => setColumn(h, e.target.value)}>
                            <option value="">Not imported</option>
                            {(table.fields ?? []).map(f => <option key={f.field} value={f.field}>{f.label === f.field ? f.field : `${f.label} (${f.field})`}</option>)}
                          </select>
                        </td>
                        <td className="py-1">
                          {pick && (
                            <select aria-label={`Unit for ${h}`} className={select} value={pick.unit}
                              onChange={e => { invalidate(); setColumns(prev => ({ ...prev, [h]: { ...pick, unit: e.target.value } })) }}>
                              <option value="">Choose…</option>
                              {units.map(u => <option key={u} value={u}>{u}</option>)}
                            </select>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              {costPicked && (
                <div className="flex flex-wrap items-center gap-2" data-testid="table-import-money">
                  <span className="text-text">Costs in the table are in</span>
                  <input aria-label="Currency" value={currency} onChange={e => { invalidate(); setCurrency(e.target.value.toUpperCase()) }}
                    className={`${select} w-16 font-mono`} maxLength={3} />
                  <input aria-label="Money year" value={moneyYear} inputMode="numeric" placeholder="year"
                    onChange={e => { invalidate(); setMoneyYear(e.target.value.replace(/\D/g, '').slice(0, 4)) }}
                    className={`${select} w-16 font-mono`} />
                  <span className="text-muted">money. Values are copied as stated, never converted.</span>
                </div>
              )}
            </section>
          )}

          {plan && (
            <section className="flex flex-col gap-2" data-testid="table-import-preview">
              <p className="text-text">
                {plan.changed_cells} value{plan.changed_cells === 1 ? '' : 's'} change
                {plan.unchanged_cells > 0 && `, ${plan.unchanged_cells} already set`}
                {plan.unmapped_total > 0 && `, ${plan.unmapped_total} left out`}.
              </p>
              {plan.needs.map(n => (
                <label key={n.confirmation} className="flex items-start gap-2 text-warn" data-testid="table-import-confirm">
                  <input type="checkbox" checked={confirmed} disabled={busy}
                    onChange={e => { setConfirmed(e.target.checked); void runPreview(e.target.checked) }} />
                  <span>{n.text}</span>
                </label>
              ))}
              {preview?.card && <EditCardTable card={preview.card} guided={false} />}
              {plan.unmapped.length > 0 && !preview?.card && (
                <ul className="flex flex-col gap-0.5" data-testid="table-import-gaps">
                  {plan.unmapped.map((g, i) => <li key={i} className="text-muted">{g.message}</li>)}
                  {plan.unmapped_total > plan.unmapped.length && (
                    <li className="text-muted">{plan.unmapped_total - plan.unmapped.length} more left out.</li>
                  )}
                </ul>
              )}
              {plan.notes.map(t => <p key={t} className="text-muted">{t}</p>)}
            </section>
          )}
        </>)}
        {error && <p className="text-danger" role="alert" data-testid="table-import-error">{error}</p>}
      </div>
      <div className="px-4 py-3 border-t border-border flex items-center justify-end gap-2">
        {applied ? (<>
          <button type="button" onClick={onClose} className="px-3 py-1.5 text-xs rounded border border-border text-text hover:bg-border/30">Close</button>
          {sectionId && (
            <button type="button" data-testid="table-import-open-section"
              onClick={() => { openSectionInExpert(sectionId); onClose() }}
              className="px-3 py-1.5 text-xs rounded bg-accent text-white hover:opacity-90">
              Open {sectionMeta(sectionId).label} ›
            </button>
          )}
        </>) : (<>
          {applyWhy && <span className="mr-auto text-[11px] text-muted" data-testid="table-import-why">{previewWhy ?? applyWhy}</span>}
          <button type="button" onClick={onClose} className="px-3 py-1.5 text-xs rounded border border-border text-text hover:bg-border/30">Cancel</button>
          <button type="button" disabled={busy || previewWhy !== null} onClick={() => { void runPreview() }}
            data-testid="table-import-preview-btn"
            className="px-3 py-1.5 text-xs rounded border border-border text-text hover:bg-border/30 disabled:opacity-40">
            Preview
          </button>
          <button type="button" disabled={busy || applyWhy !== null} onClick={() => { void runApply() }}
            data-testid="table-import-apply"
            className="px-3 py-1.5 text-xs rounded bg-accent text-white hover:opacity-90 disabled:opacity-40">
            {plan ? `Apply ${plan.changed_cells} change${plan.changed_cells === 1 ? '' : 's'}` : 'Apply'}
          </button>
        </>)}
      </div>
    </Dialog>
  )
}

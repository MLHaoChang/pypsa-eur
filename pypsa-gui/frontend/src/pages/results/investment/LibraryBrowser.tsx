// Results → Investment → Library (IC P3 WP3.7a): the org's Library — tariffs,
// contracts and connection agreements (versions, what the project pins, the
// item as JSON with a summary), a URDB tariff import, and price / meter series
// uploads. "Attach as import tariff" follows the P2 rule: an inline tariff that
// is not this item is never replaced silently (a confirm dialog).
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  commercialApi, libraryApi, type LibraryItemKind, type MeterDataResult, type UrdbImportResult,
} from '../../../api/commercial'
import type { LibraryItemRef, Tariff } from '../../../api/types'
import { Dialog } from '../../../components/Dialog'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { pinnedVersion, replacesInline, urdbRate, type RateChoice } from './libraryModel'

const input = 'border border-border rounded px-1 py-0.5 text-[11px] bg-bg'
const KINDS: Array<{ id: LibraryItemKind; label: string }> = [
  { id: 'tariff', label: 'Tariffs' }, { id: 'contract', label: 'Contracts' },
  { id: 'connection_agreement', label: 'Connection agreements' },
]

function detailText(e: unknown): string {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof d === 'string') return d
  if (d && typeof d === 'object' && 'message' in d) return String((d as { message: unknown }).message)
  return e instanceof Error ? e.message : 'the request failed'
}

function tariffSummary(t: Tariff): string {
  const kinds = t.items.map(i => `${i.id} (${i.kind})`).join(', ')
  return `${t.name} — ${t.jurisdiction}, from ${t.valid_from}; items: ${kinds}`
}

function ItemView({ kind, refItem, onAttach }: {
  kind: LibraryItemKind; refItem: LibraryItemRef; onAttach: (ref: LibraryItemRef) => void
}) {
  const project = useUIStore(s => s.currentProject)
  const [version, setVersion] = useState(refItem.version)
  const item = useQuery({ queryKey: nk(project, 'library', 'item', kind, refItem.id, version),
                          queryFn: () => libraryApi.getItem(kind, refItem.id, version) })
  const commercial = useQuery({ queryKey: nk(project, 'commercial', 'config'),
                                queryFn: () => commercialApi.getCommercial() })
  const pinned = pinnedVersion(commercial.data, kind, refItem.id)
  return (
    <div className="space-y-2 border border-border rounded p-2" data-testid="lib-item-view">
      <div className="flex flex-wrap items-center gap-2">
        <strong>{refItem.id}</strong>
        <label>version{' '}
          <select aria-label={`Version of ${refItem.id}`} className={input} value={version}
                  onChange={e => setVersion(Number(e.target.value))}>
            {Array.from({ length: refItem.version }, (_, i) => refItem.version - i).map(v => (
              <option key={v} value={v}>v{v}{v === refItem.version ? ' (latest)' : ''}</option>))}
          </select>
        </label>
        {pinned != null && (
          <span className="text-muted" data-testid="lib-pinned">
            pinned by this project: v{pinned}{pinned !== refItem.version ? ' (a newer version exists)' : ''}
          </span>
        )}
        {kind === 'tariff' && item.data && (
          <button type="button" className="underline" onClick={() => onAttach(item.data.ref)}>
            Attach v{item.data.ref.version} as the import tariff</button>
        )}
      </div>
      {item.isError && <p role="alert" className="text-danger">{detailText(item.error)}</p>}
      {item.data && (
        <>
          {kind === 'tariff' && <p>{tariffSummary(item.data.payload as unknown as Tariff)}</p>}
          <details>
            <summary>JSON</summary>
            <pre className="text-[10px] overflow-auto max-h-64">
              {JSON.stringify(item.data.payload, null, 2)}</pre>
          </details>
          {Object.keys(item.data.meta ?? {}).length > 0 && (
            <p className="text-muted">Source: {String(item.data.meta.source ?? '—')}
              {item.data.meta.provider ? ` (${String(item.data.meta.provider)})` : ''}</p>
          )}
        </>
      )}
    </div>
  )
}

function UrdbImport({ onDone }: { onDone: () => void }) {
  const [file, setFile] = useState<unknown>(null)
  const [choice, setChoice] = useState<RateChoice | null>(null)
  const [index, setIndex] = useState<number | undefined>(undefined)
  const [name, setName] = useState('')
  const [validFrom, setValidFrom] = useState('')
  const [cyclic, setCyclic] = useState(false)
  const [refusals, setRefusals] = useState<Array<{ field: string; reason: string }>>([])
  const [result, setResult] = useState<UrdbImportResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const pick = (data: unknown, i?: number) => {
    setIndex(i); setChoice(urdbRate(data, i))
  }
  const run = async (acceptPartial: boolean) => {
    if (choice?.kind !== 'rate') return
    setBusy(true); setError(null); setResult(null)
    try {
      const out = await libraryApi.importUrdb({ urdb_response: choice.rate, name: name.trim(),
        cyclic_year: cyclic, accept_partial: acceptPartial, valid_from: validFrom || null })
      setResult(out); setRefusals([]); onDone()
    } catch (e) {
      const d = (e as { response?: { status?: number; data?: { detail?: unknown } } })?.response
      const detail = d?.data?.detail as { code?: string; refusals?: Array<{ field: string; reason: string }> } | undefined
      if (d?.status === 422 && detail?.code === 'urdb_refused') setRefusals(detail.refusals ?? [])
      setError(detailText(e))
    } finally { setBusy(false) }
  }
  return (
    <section aria-labelledby="lib-urdb" className="space-y-1">
      <h4 id="lib-urdb" className="font-semibold">Import a URDB tariff</h4>
      <label>URDB or OpenEI JSON file{' '}
        <input type="file" accept="application/json,.json" aria-label="URDB file"
               onChange={async e => {
                 const f = e.target.files?.[0]
                 setResult(null); setError(null); setRefusals([])
                 if (!f) return
                 try { const data = JSON.parse(await f.text()); setFile(data); pick(data) }
                 catch { setChoice({ kind: 'error', message: 'the file is not JSON' }) }
               }} />
      </label>
      {choice?.kind === 'error' && <p role="alert" className="text-danger">{choice.message}</p>}
      {choice?.kind === 'choose' && (
        <label className="block">The file holds several rates; pick one{' '}
          <select aria-label="Rate" className={input} value={index ?? ''}
                  onChange={e => pick(file, e.target.value === '' ? undefined : Number(e.target.value))}>
            <option value="">choose…</option>
            {choice.rates.map(r => <option key={r.index} value={r.index}>{r.label}</option>)}
          </select>
        </label>
      )}
      {choice?.kind === 'rate' && (
        <div className="flex flex-wrap items-center gap-2">
          <label>Library name <input aria-label="Tariff name in the Library" className={input}
                                     value={name} onChange={e => setName(e.target.value)} /></label>
          <label>valid from <input type="date" aria-label="Valid from (when the rate has no start date)"
                                   className={input} value={validFrom}
                                   onChange={e => setValidFrom(e.target.value)} /></label>
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={cyclic} onChange={e => setCyclic(e.target.checked)} />
            ratchet lookback wraps within the rate year</label>
          <button type="button" className="underline" disabled={!name.trim() || busy}
                  onClick={() => run(false)}>Import</button>
        </div>
      )}
      {refusals.length > 0 && (
        <div role="alert" className="text-danger" data-testid="lib-urdb-refusals">
          <p>These URDB fields cannot be imported:</p>
          <ul className="pl-3 list-disc">{refusals.map(r => <li key={r.field}>{r.field}: {r.reason}</li>)}</ul>
          <button type="button" className="underline" disabled={busy} onClick={() => run(true)}>
            Import without them (the bill is then marked incomplete)</button>
        </div>
      )}
      {error && refusals.length === 0 && <p role="alert" className="text-danger">{error}</p>}
      {result && (
        <p role="status" className="text-success" data-testid="lib-urdb-done">
          Imported as {result.ref.id} v{result.ref.version}
          {result.unsupported_fields.length ? `; not imported: ${result.unsupported_fields.join(', ')}` : ''}
          {result.notes.length ? `; notes: ${result.notes.join(', ')}` : ''}.</p>
      )}
    </section>
  )
}

function SeriesPanel() {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const series = useQuery({ queryKey: nk(project, 'library', 'series'),
                            queryFn: () => libraryApi.listSeries() })
  const [file, setFile] = useState<File | null>(null)
  const [name, setName] = useState('')
  const [kind, setKind] = useState<'price' | 'meter'>('price')
  const [unit, setUnit] = useState('')
  const [settlement, setSettlement] = useState('15min')
  const [label, setLabel] = useState('start')
  const [tz, setTz] = useState('')
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const [meter, setMeter] = useState<MeterDataResult | null>(null)

  const upload = async () => {
    if (!file) return
    setMessage(null); setMeter(null)
    try {
      if (kind === 'price') {
        const ref = await libraryApi.uploadSeries(file, { name: name.trim(), timezone: tz || null })
        setMessage({ tone: 'ok', text: `Stored as ${ref.id} v${ref.version}.` })
      } else {
        const out = await libraryApi.uploadMeterData(file, { name: name.trim(), unit, settlement,
                                                               label, timezone: tz || null })
        setMeter(out)
        setMessage({ tone: 'ok', text: `Stored as ${out.ref.id} v${out.ref.version}.` })
      }
      await qc.invalidateQueries({ queryKey: nk(project, 'library', 'series') })
    } catch (e) {
      const d = (e as { response?: { status?: number; data?: { detail?: { code?: string } } } })?.response
      setMessage({ tone: 'error', text: d?.status === 409 && d.data?.detail?.code === 'meter_meta_conflict'
        ? `This data is already stored under that name with other settings: ${detailText(e)}`
        : detailText(e) })
    }
  }
  return (
    <section aria-labelledby="lib-series" className="space-y-1">
      <h4 id="lib-series" className="font-semibold">Series</h4>
      {series.isError && <p role="alert" className="text-danger">{detailText(series.error)}</p>}
      <ul className="pl-3 list-disc" data-testid="lib-series-list">
        {(series.data ?? []).map(s => (
          <li key={`${s.id}:${s.version}`}>{s.id} v{s.version} ({s.source}{s.provider ? `, ${s.provider}` : ''})</li>
        ))}
        {series.data?.length === 0 && <li className="text-muted">No series yet.</li>}
      </ul>
      <fieldset className="space-y-1 border border-border rounded p-1">
        <legend>Upload</legend>
        <div className="flex flex-wrap gap-2 items-center">
          <label>kind <select aria-label="Series kind" className={input} value={kind}
                              onChange={e => setKind(e.target.value as 'price' | 'meter')}>
            <option value="price">price series</option><option value="meter">meter data</option></select></label>
          <label>file <input type="file" aria-label="Series file" accept=".csv,.xlsx"
                             onChange={e => setFile(e.target.files?.[0] ?? null)} /></label>
          <label>name <input aria-label="Series name" className={input} value={name}
                             onChange={e => setName(e.target.value)} /></label>
          <label>time zone <input aria-label="Time zone of the file (blank: the snapshot clock)"
                                  className={input} value={tz} placeholder="Europe/Berlin"
                                  onChange={e => setTz(e.target.value)} /></label>
          {kind === 'meter' && (
            <>
              <label>unit <select aria-label="Meter unit" className={input} value={unit}
                                  onChange={e => setUnit(e.target.value)}>
                <option value="">choose…</option><option value="kW">kW</option>
                <option value="W">W</option><option value="kWh_per_interval">kWh per interval</option>
              </select></label>
              <label>settlement <select aria-label="Meter settlement" className={input} value={settlement}
                                        onChange={e => setSettlement(e.target.value)}>
                <option value="15min">15 min</option><option value="30min">30 min</option>
                <option value="h">hourly</option></select></label>
              <label>timestamps label <select aria-label="Timestamp label" className={input} value={label}
                                              onChange={e => setLabel(e.target.value)}>
                <option value="start">interval start</option><option value="end">interval end</option></select></label>
            </>
          )}
          <button type="button" className="underline"
                  disabled={!file || !name.trim() || (kind === 'meter' && !unit)} onClick={upload}>
            Upload</button>
        </div>
        {kind === 'meter' && !unit && <p className="text-muted">A meter file needs its unit: a kWh file read as kW is 4× low at 15 minutes.</p>}
      </fieldset>
      {message && <p role="status" className={message.tone === 'ok' ? 'text-success' : 'text-danger'}>
        {message.text}</p>}
      {meter && (
        <div data-testid="lib-meter-result" className="text-muted">
          <p>Monthly peaks (kW): {Object.entries(meter.meter_history_peaks_kw).map(([m, v]) => `${m} ${v.toFixed(1)}`).join(', ') || '—'}</p>
          {meter.notes.length > 0 && <p>Notes: {meter.notes.join(', ')}</p>}
          <p>{meter.help}</p>
        </div>
      )}
    </section>
  )
}

export default function LibraryBrowser() {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const [kind, setKind] = useState<LibraryItemKind>('tariff')
  const [open, setOpen] = useState<LibraryItemRef | null>(null)
  const [confirm, setConfirm] = useState<LibraryItemRef | null>(null)
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const items = useQuery({ queryKey: nk(project, 'library', 'items', kind),
                           queryFn: () => libraryApi.listItems(kind) })

  const attach = async (ref: LibraryItemRef) => {
    setMessage(null)
    try {
      await commercialApi.saveCommercial({ import_tariff_ref: ref, import_tariff: null,
                                           import_tariff_id: null } as never)
      setMessage({ tone: 'ok', text: `The project's import tariff is now ${ref.id} v${ref.version}. Re-solve for a new dispatch.` })
      await qc.invalidateQueries({ queryKey: nk(project, 'commercial') })
      await qc.invalidateQueries({ queryKey: nk(project, 'results') })
    } catch (e) {
      setMessage({ tone: 'error', text: `The tariff could not be attached: ${detailText(e)}` })
    }
  }
  const requestAttach = async (ref: LibraryItemRef) => {
    const commercial = await qc.fetchQuery({ queryKey: nk(project, 'commercial', 'config'),
                                             queryFn: () => commercialApi.getCommercial(), staleTime: 0 })
    const old = commercial?.import_tariff_ref
    let oldPayload: Tariff | null = null
    if (old && old.hash !== ref.hash) {
      try { oldPayload = (await libraryApi.getItem<Tariff>('tariff', old.id, old.version)).payload }
      catch { oldPayload = null }
    }
    if (replacesInline(commercial, ref, oldPayload)) setConfirm(ref)
    else await attach(ref)
  }

  return (
    <div className="space-y-4 text-[11px]" data-testid="library-browser">
      <div role="radiogroup" aria-label="Library item kind" className="flex gap-2">
        {KINDS.map(k => (
          <label key={k.id} className="flex items-center gap-1">
            <input type="radio" name="lib-kind" checked={kind === k.id}
                   onChange={() => { setKind(k.id); setOpen(null) }} />{k.label}</label>
        ))}
      </div>
      {message && <p role="status" className={message.tone === 'ok' ? 'text-success' : 'text-danger'}>
        {message.text}</p>}
      {items.isError && <p role="alert" className="text-danger">{detailText(items.error)}</p>}
      <ul className="space-y-1" data-testid="lib-items">
        {(items.data ?? []).map(r => (
          <li key={r.id} className="flex items-center gap-2">
            <button type="button" className="underline" aria-expanded={open?.id === r.id}
                    onClick={() => setOpen(open?.id === r.id ? null : r)}>{r.id}</button>
            <span className="text-muted">v{r.version}</span>
          </li>
        ))}
        {items.data?.length === 0 && <li className="text-muted">No items of this kind yet.</li>}
      </ul>
      {open && <ItemView key={`${kind}:${open.id}`} kind={kind} refItem={open} onAttach={requestAttach} />}
      {kind === 'tariff' && (
        <UrdbImport onDone={() => { void qc.invalidateQueries({ queryKey: nk(project, 'library', 'items', 'tariff') }) }} />
      )}
      <SeriesPanel />
      <Dialog open={confirm !== null} onClose={() => setConfirm(null)}
              title="Replace the project's import tariff?"
              panelClassName="bg-bg rounded-xl shadow-2xl w-[440px] max-w-[95vw] p-4 space-y-3">
        <p className="text-[11px]">
          The project has its own import tariff, which is not this Library item. Attaching{' '}
          {confirm?.id} v{confirm?.version} replaces it; the project's copy is not kept.</p>
        <div className="flex justify-end gap-2">
          <button type="button" className="text-[11px] px-2 py-1" onClick={() => setConfirm(null)}>Cancel</button>
          <button type="button" className="text-[11px] px-2 py-1 rounded bg-accent text-on-accent"
                  onClick={async () => { const r = confirm!; setConfirm(null); await attach(r) }}>
            Replace it</button>
        </div>
      </Dialog>
    </div>
  )
}

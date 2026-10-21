// The Library's shipped cost references (common to every organization): browse,
// compare each value with the default a project would otherwise use, read the source
// notes and the credit, and "Use on asset…", which opens the same apply drawer as the
// asset's own "Compare with references". A self-contained section: the Library page
// mounts it, wherever that page lives.
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { libraryReferencesApi, type ReferenceSummary } from '../../../api/libraryReferences'
import { networkApi } from '../../../api/network'
import { formatApiDetail } from '../../../api/client'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { ReferenceApplyDrawer } from './ReferenceApplyDrawer'
import {
  basisWords, creditBadge, fmtNumber, gridWords, parameterWords, spellOutNotes, targetClass, technologyWords,
} from './referenceWords'

function detailText(e: unknown): string {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (d != null) return formatApiDetail(d)
  return e instanceof Error ? e.message : 'the request failed'
}

function shown(value: number | null | undefined, unit: string): string {
  if (value == null) return '—'
  if (unit === 'EUR/MW') return `${fmtNumber(value / 1000)} EUR/kW`
  if (unit === 'share/year') return `${fmtNumber(value * 100)} % per year`
  if (unit === 'per unit') return `${fmtNumber(value * 100)} %`
  return `${fmtNumber(value)} ${unit}`
}

const key = (r: ReferenceSummary) => `${r.technology}@${r.projection_year}`

function ReferenceDetailView({ refRow }: { refRow: ReferenceSummary }) {
  const project = useUIStore(s => s.currentProject)
  const cls = targetClass(refRow.capacity_basis_code)
  const detail = useQuery({ queryKey: nk(project, 'library', 'references', key(refRow)),
                            queryFn: () => libraryReferencesApi.detail(refRow.technology, refRow.projection_year) })
  const assets = useQuery({ queryKey: nk(project, cls === 'Link' ? 'links' : 'generators'),
                            queryFn: () => (cls === 'Link' ? networkApi.getLinks() : networkApi.getGenerators()) as
                              Promise<Array<{ name: string }>> })
  const [asset, setAsset] = useState('')
  const [drawer, setDrawer] = useState(false)
  if (detail.isError) return <p role="alert" className="text-danger">{detailText(detail.error)}</p>
  const d = detail.data
  if (!d) return <p className="text-muted">Loading the reference…</p>
  const notes = spellOutNotes([d.reference.included_costs, ...d.reference.unmapped_terms])
  return (
    <div className="space-y-2 border-l-2 border-border pl-2" data-testid="lib-reference-detail">
      <p className="text-muted">{d.reference.applicability}.</p>
      <table className="w-full text-left border-collapse" data-testid="lib-reference-comparison">
        <caption className="text-left font-semibold mb-1">
          Beside the default a new project uses ({d.pack.chosen_by === 'latest_generic' ? 'the current generic defaults' : 'its defaults'})
        </caption>
        <thead><tr>{['Value', 'Default', 'This reference'].map(h => (
          <th key={h} scope="col" className="p-1 border-b border-border">{h}</th>))}</tr></thead>
        <tbody>{d.comparison.map(row => (
          <tr key={row.parameter} data-differs={row.differs} className={row.differs ? 'bg-warn/10' : ''}>
            <th scope="row" className="p-1 border-b border-border font-normal">{parameterWords(row.parameter)}</th>
            <td className="p-1 border-b border-border">
              {row.pack ? <>{shown(row.pack.value, row.pack.unit)}
                {row.pack.currency_year != null && ` (${row.pack.currency_year} EUR)`}
                <span className="block text-[10px] text-muted">
                  {row.pack.projection_year != null && `${row.pack.projection_year} projection · `}{row.pack.source}</span></>
                : <span className="text-muted">No default for this</span>}
            </td>
            <td className="p-1 border-b border-border">
              {shown(row.reference.value, row.reference.unit)}
              {row.reference.currency_year != null && ` (${row.reference.currency_year} EUR)`}
              <span className="block text-[10px] text-muted">
                {row.reference.projection_year} projection · {row.reference.source}</span>
            </td>
          </tr>
        ))}</tbody>
      </table>
      <div>
        <h5 className="font-medium">Source notes</h5>
        <ul className="list-disc pl-4" data-testid="lib-reference-notes">{notes.map(n => <li key={n}>{n}</li>)}</ul>
      </div>
      <p className="text-muted" data-testid="lib-reference-credit">
        {creditBadge(refRow.publisher, refRow.licence)}. {d.reference.attribution}{' '}
        <a className="underline" href={refRow.source_url} target="_blank" rel="noreferrer">Source workbook</a></p>
      <div className="flex items-center gap-2">
        <label className="flex items-center gap-1">
          <span>Use on</span>
          <select className="border border-border rounded px-1 py-0.5 bg-bg" value={asset}
            data-testid="lib-reference-asset" onChange={e => setAsset(e.target.value)}>
            <option value="">{cls === 'Link' ? 'Choose a link…' : 'Choose a generator…'}</option>
            {(assets.data ?? []).map(a => <option key={a.name} value={a.name}>{a.name}</option>)}
          </select>
        </label>
        <button type="button" disabled={!asset} className="px-2 py-0.5 rounded border border-border disabled:opacity-40"
          data-testid="lib-reference-use" onClick={() => setDrawer(true)}>Preview…</button>
      </div>
      {drawer && asset && (
        <ReferenceApplyDrawer open onClose={() => setDrawer(false)} componentClass={cls} name={asset}
          technology={refRow.technology} projectionYear={refRow.projection_year} />
      )}
    </div>
  )
}

export function LibraryReferences() {
  const project = useUIStore(s => s.currentProject)
  const list = useQuery({ queryKey: nk(project, 'library', 'references'), queryFn: libraryReferencesApi.list,
                          staleTime: 300_000 })
  const [open, setOpen] = useState<string | null>(null)
  return (
    <section aria-labelledby="lib-references" className="space-y-2" data-testid="lib-references">
      <h4 id="lib-references" className="font-semibold">Cost references</h4>
      <p className="text-muted">
        Published technology costs you can compare and, after a preview, apply to one asset. They never
        change a project on their own.</p>
      {list.isError && <p role="alert" className="text-danger">{detailText(list.error)}</p>}
      <ul className="space-y-1" data-testid="lib-reference-list">
        {(list.data ?? []).map(r => (
          <li key={key(r)}>
            <div className="flex flex-wrap items-center gap-x-2">
              <button type="button" className="underline" aria-expanded={open === key(r)}
                onClick={() => setOpen(open === key(r) ? null : key(r))}>
                {technologyWords(r.technology)}, {r.projection_year}</button>
              <span className="text-muted">{r.currency_year} {r.currency} {basisWords(r.capacity_basis_code)}</span>
              <span className="text-muted">· {gridWords(r.includes_grid_connection)}</span>
              <span className="rounded border border-border px-1 text-[10px]" data-testid="lib-reference-badge">
                {creditBadge(r.publisher, r.licence)}</span>
            </div>
            {open === key(r) && <ReferenceDetailView refRow={r} />}
          </li>
        ))}
      </ul>
    </section>
  )
}

// Apply one shipped cost reference to one asset: pick the reference, see each value
// beside the default it replaces (both with their source and money year), tick only
// the confirmations this asset needs, see the old → new card, apply, read the receipt.
// Opened from the asset's investment cost ("Compare with references") and from the
// Library's references ("Use on asset…"); both show this same drawer and the same card
// the assistant's apply shows. Nothing is converted or applied without the card.
import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { Dialog } from '../../../components/Dialog'
import { EditCardTable } from '../../../components/EditCard'
import { formatApiDetail } from '../../../api/client'
import {
  libraryReferencesApi, type Confirmation, type ReferenceApplied, type ReferenceApplyArgs,
  type ReferencePlanRow, type ReferenceSummary,
} from '../../../api/libraryReferences'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import {
  CONFIRM_LABELS, basisWords, creditBadge, fmtNumber, parameterWords, sourceWords, targetClass, technologyWords,
} from './referenceWords'

const ROOT = { Generator: 'generators', Link: 'links' } as const

function detailText(e: unknown): string {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (d != null) return formatApiDetail(d)
  return e instanceof Error ? e.message : 'the request failed'
}

/** EUR/MW as the asset form shows it, EUR/kW; other units as stated. */
function shown(value: number | null | undefined, unit: string): [string, string] {
  if (value == null) return ['—', '']
  if (unit === 'EUR/MW') return [fmtNumber(value / 1000), 'EUR/kW']
  if (/^EUR\/MW\/(yr|year)$/.test(unit)) return [fmtNumber(value / 1000), 'EUR/kW per year']
  if (unit === 'share/year') return [fmtNumber(value * 100), '% per year']
  if (unit === 'per unit') return [fmtNumber(value * 100), '%']
  return [fmtNumber(value), unit]
}

/** The money year, on a money value only (a share or a lifetime has none). */
function money(year: number | null | undefined, unit: string): string {
  return year != null && unit.startsWith('EUR') ? ` (${year} EUR)` : ''
}

function ComparisonTable({ rows }: { rows: ReferencePlanRow[] }) {
  return (
    <table className="w-full text-left border-collapse text-[11px]" data-testid="ref-apply-comparison">
      <caption className="text-left font-semibold mb-1">The reference beside the default it replaces</caption>
      <thead><tr>{['Value', 'Default', 'Reference'].map(h => (
        <th key={h} scope="col" className="p-1 border-b border-border">{h}</th>))}</tr></thead>
      <tbody>{rows.map(r => {
        const [refV, refU] = shown(r.reference.value, r.reference.unit)
        const [packV, packU] = shown(r.pack?.value, r.pack?.unit ?? '')
        return (
          <tr key={r.field} className={r.differs ? 'bg-warn/10' : ''} data-differs={r.differs}>
            <th scope="row" className="p-1 border-b border-border font-normal">{parameterWords(r.reference.parameter)}</th>
            <td className="p-1 border-b border-border">
              {r.pack ? <>{packV} {packU}{money(r.pack.currency_year, r.pack.unit)}
                <span className="block text-[10px] text-muted">{sourceWords(r.pack.source)}</span></>
                : <span className="text-muted">No default for this</span>}
            </td>
            <td className="p-1 border-b border-border">
              {refV} {refU}{money(r.reference.currency_year, r.reference.unit)}
              <span className="block text-[10px] text-muted">{sourceWords(r.reference.source)}, “{r.reference.label}”</span>
            </td>
          </tr>
        )
      })}</tbody>
    </table>
  )
}

export function ReferenceApplyDrawer({ open, onClose, componentClass, name, technology: initialTech,
  projectionYear: initialYear, onApplied }: {
  open: boolean
  onClose: () => void
  /** The stored values the apply wrote, e.g. for an open edit form to take them in. */
  onApplied?: (written: Record<string, number>) => void
  componentClass: 'Generator' | 'Link'
  name: string
  technology?: string
  projectionYear?: number
}) {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const list = useQuery({ queryKey: nk(project, 'library', 'references'), queryFn: libraryReferencesApi.list,
                          enabled: open, staleTime: 300_000 })
  const choices = useMemo(() => (list.data ?? []).filter(r => targetClass(r.capacity_basis_code) === componentClass),
    [list.data, componentClass])
  const [choice, setChoice] = useState<string>(initialTech && initialYear ? `${initialTech}@${initialYear}` : '')
  const [confirm, setConfirm] = useState<Confirmation[]>([])
  const [applied, setApplied] = useState<ReferenceApplied | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { setConfirm([]); setApplied(null); setError(null) }, [choice, name])

  const selected: ReferenceSummary | undefined = choices.find(r => `${r.technology}@${r.projection_year}` === choice)
  const args: ReferenceApplyArgs | null = selected ? {
    component_class: componentClass, name, technology: selected.technology,
    projection_year: selected.projection_year, confirm,
  } : null
  const preview = useQuery({
    queryKey: nk(project, ROOT[componentClass], 'reference-preview', name, choice, confirm.join(',')),
    queryFn: () => libraryReferencesApi.preview(args!),
    enabled: open && !!args && !applied, retry: false, staleTime: 0,
    // Ticking a confirmation previews again; the checklist stays while it does.
    placeholderData: keepPreviousData,
  })

  const toggle = (c: Confirmation, on: boolean) =>
    setConfirm(prev => on ? [...prev.filter(x => x !== c), c].sort() : prev.filter(x => x !== c))

  const apply = async () => {
    if (!args || !preview.data?.card) return
    setBusy(true)
    setError(null)
    try {
      const out = await libraryReferencesApi.apply({ ...args, digest: preview.data.card.digest })
      setApplied(out)
      onApplied?.(out.written)
      await qc.invalidateQueries({ queryKey: nk(project, ROOT[componentClass]) })
      await qc.invalidateQueries({ queryKey: nk(project, 'results') })
    } catch (e) {
      setError(`Nothing was applied: ${detailText(e)}`)
    } finally {
      setBusy(false)
    }
  }

  const p = preview.data
  return (
    <Dialog open={open} onClose={onClose} title={`Cost references for ${name}`}
      panelClassName="fixed right-0 top-0 h-full w-[560px] max-w-[95vw] bg-bg shadow-2xl overflow-y-auto p-4 space-y-3 text-[11px]">
      <div data-testid="ref-apply-drawer" className="space-y-3">
        <label className="block">
          <span className="block font-medium mb-0.5">Reference</span>
          <select className="border border-border rounded px-1 py-0.5 bg-bg w-full" value={choice}
            data-testid="ref-apply-choice" onChange={e => setChoice(e.target.value)}>
            <option value="">Choose a reference…</option>
            {choices.map(r => (
              <option key={`${r.technology}@${r.projection_year}`} value={`${r.technology}@${r.projection_year}`}>
                {technologyWords(r.technology)}, {r.projection_year} projection, {r.currency_year} {r.currency}{' '}
                {basisWords(r.capacity_basis_code)}
              </option>
            ))}
          </select>
        </label>
        {list.isError && <p role="alert" className="text-danger">{detailText(list.error)}</p>}
        {selected && <p className="text-muted" data-testid="ref-apply-credit">
          {creditBadge(selected.publisher, selected.licence)}. {selected.attribution}</p>}
        {preview.isError && <p role="alert" className="text-danger" data-testid="ref-apply-refused">
          {detailText(preview.error)}</p>}
        {p && !applied && (
          <>
            <ComparisonTable rows={p.rows} />
            {p.notes.length > 0 && <ul className="list-disc pl-4 text-muted" data-testid="ref-apply-notes">
              {p.notes.map(n => <li key={n}>{n}</li>)}</ul>}
            {p.needs.length > 0 && (
              <fieldset className="space-y-2" data-testid="ref-apply-needs">
                <legend className="font-medium">Before applying</legend>
                {p.needs.map(need => (
                  <label key={need.confirmation} className="flex gap-2 items-start">
                    <input type="checkbox" className="mt-0.5" checked={confirm.includes(need.confirmation)}
                      data-testid={`ref-apply-confirm-${need.confirmation}`}
                      onChange={e => toggle(need.confirmation, e.target.checked)} />
                    <span>
                      <span className="font-medium">{CONFIRM_LABELS[need.confirmation]}</span>
                      <span className="block text-muted">{need.text}</span>
                    </span>
                  </label>
                ))}
              </fieldset>
            )}
            {p.card ? (
              <>
                <EditCardTable card={p.card} guided />
                <div className="flex justify-end gap-2">
                  <button type="button" className="px-2 py-1" onClick={onClose}>Cancel</button>
                  <button type="button" disabled={busy} data-testid="ref-apply-apply"
                    className="px-2 py-1 rounded bg-accent text-on-accent disabled:opacity-40" onClick={() => { void apply() }}>
                    Apply to {name}</button>
                </div>
              </>
            ) : <p className="text-muted" data-testid="ref-apply-waiting">
              Tick what applies above to see the changes. Nothing is applied until you press Apply.</p>}
          </>
        )}
        {error && <p role="alert" className="text-danger">{error}</p>}
        {applied && (
          <div className="rounded border border-border p-2 space-y-1" data-testid="ref-apply-receipt">
            {applied.receipt.rows.filter(r => r.role === 'typed').map(r => {
              const ok = typeof r.readback === 'number' && typeof r.new === 'number'
                && Math.abs(r.readback - r.new) <= 1e-9 * Math.max(1, Math.abs(r.new))
              return (
                <div key={r.field} data-testid={`ref-apply-receipt-${r.field}`}>
                  {parameterWords(r.field)}: applied.{' '}
                  {ok ? `The project now shows ${shown(r.readback as number, r.unit).join(' ')}.`
                    : <span className="text-warn">The project does not show the expected value. Check it before going on.</span>}
                </div>
              )
            })}
            {applied.receipt.results_stale && <div className="text-muted">
              Results are now stale: they were computed before this change.</div>}
            <div className="text-muted">{creditBadge('Danish Energy Agency', applied.licence)}. {applied.attribution}</div>
            <div className="flex justify-end"><button type="button" className="px-2 py-1" onClick={onClose}>Close</button></div>
          </div>
        )}
      </div>
    </Dialog>
  )
}

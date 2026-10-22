import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { EditCardTable, EditReceiptLine } from './EditCard'
import {
  documentValuesApi, type AssetCitation, type Candidates, type CommercialCitation, type CommercialTargets,
  type AssetDocumentPreview, type DecimalMark, type DocumentApplied, type DocumentGap, type DocumentSource, type QuoteCandidate,
} from '../api/documentValues'
import type { MappableField } from '../api/equipmentTable'
import type { EditCard } from './EditCard'
import { networkApi } from '../api/network'
import type { FlowSectionId, FlowTarget } from '../api/flow'
import { openFlowTarget, openSectionInExpert } from '../layout/flowNavigation'
import { sectionMeta } from '../layout/flowSections'
import { errorText } from '../utils/errorText'

// The quote picker of "Import values from a file…" (data-to-model slice 2, owner
// AI-567b): a PDF's values, each cited by page and quote. The server lists the
// lines that could be cited for each field (`find`); the user ticks at most one
// per field and names what they are for (assets of one class, or the import
// tariff or a PPA contract in section 7). Preview is the edit card the assistant
// shows, Apply writes exactly it. The server reads every number.

export type PickerMode =
  | { kind: 'assets'; componentClass: string; assetNames: string[] }
  | { kind: 'commercial'; target: 'tariff' | 'ppa' }

interface Row {
  key: string
  label: string
  /** The finder's key: the field, `tariff:<item>` or `ppa:<field>`. */
  find: string
  money: boolean
  /** A price that needs its money year (asset costs, PPA prices). */
  year: boolean
  cite: (c: QuoteCandidate) => Omit<AssetCitation, 'names'> | CommercialCitation
}

type Plan = NonNullable<AssetDocumentPreview['plan']>

const select = 'border border-border rounded bg-bg px-1.5 py-1 text-[11px] focus:outline-none focus:border-accent'

/** "periods[peak].tier_rates[0]" → "peak, tier 1". */
function pathLabel(path: string): string {
  let m = path.match(/^periods\[(.+)\]\.rate$/)
  if (m) return `${m[1]} rate`
  m = path.match(/^periods\[(.+)\]\.tier_rates\[(\d+)\]$/)
  if (m) return `${m[1]}, tier ${Number(m[2]) + 1}`
  m = path.match(/^tiers\[(\d+)\]\.rate$/)
  if (m) return `tier ${Number(m[1]) + 1} rate`
  return path
}

function rowsFor(target: 'tariff' | 'ppa' | null, fields: MappableField[], targets: CommercialTargets | null,
  contract: string): Row[] {
  if (target === null) {
    return fields.map(f => ({
      key: f.field, label: f.label === f.field ? f.field : `${f.label} (${f.field})`, find: f.field,
      money: !!f.currency, year: !!f.currency,
      cite: c => ({ field: f.field, page: c.page, quote: c.quote, unit: c.unit, value_text: c.value_text }),
    }))
  }
  if (target === 'tariff') {
    return (targets?.tariff?.items ?? []).flatMap(item => item.fields.map(path => ({
      key: `${item.id}/${path}`, label: `${item.id} · ${pathLabel(path)}`, find: `tariff:${item.id}`, money: true, year: false,
      cite: (c: QuoteCandidate): CommercialCitation => ({ target: 'tariff', entity: item.id, field: path, page: c.page,
        quote: c.quote, unit: c.unit, value_text: c.value_text }),
    })))
  }
  const ppa = targets?.ppa.find(p => p.id === contract)
  return (ppa?.fields ?? []).map(f => ({
    key: f.field, label: f.label, find: `ppa:${f.field}`, money: f.unit === '/MWh', year: f.unit === '/MWh',
    cite: (c: QuoteCandidate): CommercialCitation => ({ target: 'ppa', entity: contract, field: f.field, page: c.page,
      quote: c.quote, unit: c.unit, value_text: c.value_text }),
  }))
}

/** The candidates grouped by page, pages in the order the server ranked them. */
function byPage(list: QuoteCandidate[]): [number, { c: QuoteCandidate; i: number }[]][] {
  const groups = new Map<number, { c: QuoteCandidate; i: number }[]>()
  list.forEach((c, i) => { groups.set(c.page, [...(groups.get(c.page) ?? []), { c, i }]) })
  return [...groups]
}

const LIBRARY: FlowTarget = { section: 'commercial', panel: 'library', component: null, entity: null, field: null }

/** A library-pinned tariff or contract is updated in the Commercial library. */
function LibraryLink({ onClose }: { onClose: () => void }) {
  return (
    <button type="button" data-testid="document-values-library-link" className="ml-1 text-accent hover:underline"
      onClick={() => { onClose(); openFlowTarget(LIBRARY) }}>
      Update it there ›
    </button>
  )
}

function GapLine({ gap, onClose }: { gap: DocumentGap; onClose: () => void }) {
  return (
    <li className="text-muted">
      {gap.message}
      {gap.library && <span className="ml-1">({gap.library.id}, v{gap.library.version})</span>}
      {gap.kind === 'library_pinned' && <LibraryLink onClose={onClose} />}
    </li>
  )
}

export default function DocumentValuesPicker({ uploadId, mode, fileSection, onClose, onApplied }: {
  uploadId: string
  mode: PickerMode
  /** The dialog's file step, shown above the picker. */
  fileSection: ReactNode
  onClose: () => void
  onApplied: () => void
}) {
  const [source, setSource] = useState<DocumentSource | null>(null)
  const [fields, setFields] = useState<MappableField[]>([])
  const [targets, setTargets] = useState<CommercialTargets | null>(null)
  const [candidates, setCandidates] = useState<Candidates>({})
  const [decimalMark, setDecimalMark] = useState<DecimalMark | ''>('')
  const [names, setNames] = useState<string[]>([])
  const [contract, setContract] = useState('')
  const [picks, setPicks] = useState<Record<string, number | null>>({})
  const [currency, setCurrency] = useState('')
  const [moneyYear, setMoneyYear] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [plan, setPlan] = useState<Plan | null>(null)
  const [card, setCard] = useState<EditCard | null>(null)
  const [applied, setApplied] = useState<DocumentApplied | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const commercial = mode.kind === 'commercial'
  const componentClass = mode.kind === 'assets' ? mode.componentClass : ''
  const target = mode.kind === 'commercial' ? mode.target : null

  // Reading the document and the lines each field could cite.
  useEffect(() => {
    let cancelled = false
    setError(null); setSource(null); setPlan(null); setCard(null); setConfirmed(false)
    const mark = decimalMark || null
    const read = commercial
      ? documentValuesApi.previewCommercial({ upload_id: uploadId, find: true, decimal_mark: mark })
      : documentValuesApi.previewAssets({ upload_id: uploadId, component_class: componentClass, find: true, decimal_mark: mark })
    read.then(out => {
      if (cancelled) return
      setSource(out.document.source)
      setCandidates(out.candidates ?? {})
      if ('targets' in out) {
        setTargets(out.targets)
        setContract(prev => prev || (out.targets.ppa.length === 1 ? out.targets.ppa[0].id : ''))
      } else setFields(out.fields ?? [])
    }).catch(e => { if (!cancelled) setError(errorText(e, 'The document could not be read.')) })
    return () => { cancelled = true }
  }, [uploadId, commercial, componentClass, decimalMark])

  const rows = useMemo(() => rowsFor(target, fields, targets, contract), [target, fields, targets, contract])
  const offered = rows.filter(r => candidates[r.find]?.candidates.length)
  const empty = rows.filter(r => !candidates[r.find]?.candidates.length)
  // Fields a line names come first; the others (every MW field takes any line in MW)
  // fold away, unless no field is named at all.
  const named = offered.filter(r => candidates[r.find].candidates.some(c => c.label))
  const folded = named.length ? offered.filter(r => !named.includes(r)) : []
  const shown = named.length ? named : offered

  // Nothing is ticked for the user, except a shown field with exactly one candidate.
  useEffect(() => {
    setPicks(Object.fromEntries(offered.map(r => [r.key,
      shown.includes(r) && candidates[r.find].candidates.length === 1 ? 0 : null])))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, candidates])

  // A preview answers only the picks it was asked for: any change since drops its answer.
  const asked = useRef(0)
  const invalidate = () => { asked.current += 1; setPlan(null); setCard(null); setConfirmed(false) }
  const picked = offered.filter(r => picks[r.key] !== null && picks[r.key] !== undefined)
  const moneyPicked = picked.some(r => r.money)
  const yearPicked = picked.some(r => r.year)

  const pinned = commercial && (target === 'tariff' ? targets?.tariff?.pinned
    : targets?.ppa.find(p => p.id === contract)?.pinned)
  const noTarget = commercial && targets && (target === 'tariff' ? !targets.tariff : targets.ppa.length === 0)

  const citations = () => picked.map(r => {
    const c = candidates[r.find].candidates[picks[r.key] as number]
    return commercial ? r.cite(c) : { ...r.cite(c), names }
  })
  const money = (confirm: boolean) => ({
    decimal_mark: decimalMark || null,
    currency: moneyPicked ? currency || null : null,
    money_year: yearPicked && moneyYear ? Number(moneyYear) : null,
    confirm: confirm ? ['money_year'] : [],
  })

  const runPreview = async (confirm = confirmed) => {
    const ask = ++asked.current
    setBusy(true); setError(null)
    try {
      const out = commercial
        ? await documentValuesApi.previewCommercial({ upload_id: uploadId, citations: citations() as CommercialCitation[], ...money(confirm) })
        : await documentValuesApi.previewAssets({ upload_id: uploadId, component_class: componentClass,
          citations: citations() as AssetCitation[], ...money(confirm) })
      if (ask !== asked.current) return
      setPlan((out.plan ?? null) as Plan | null); setCard(out.card ?? null)
    } catch (e) { if (ask === asked.current) { setPlan(null); setCard(null); setError(errorText(e, 'The preview failed.')) } }
    finally { setBusy(false) }
  }

  const runApply = async () => {
    if (!card || !source) return
    setBusy(true); setError(null)
    const signed = { ...money(confirmed), source_sha256: source.sha256, digest: card.digest }
    try {
      setApplied(commercial
        ? await documentValuesApi.applyCommercial({ upload_id: uploadId, citations: citations() as CommercialCitation[], ...signed })
        : await documentValuesApi.applyAssets({ upload_id: uploadId, component_class: componentClass,
          citations: citations() as AssetCitation[], ...signed }))
      onApplied()
    } catch (e) { setError(errorText(e, 'Nothing was applied.')) }
    finally { setBusy(false) }
  }

  const undo = async () => {
    setBusy(true)
    try { await networkApi.undo(); setApplied(null); invalidate(); onApplied() }
    catch (e) { setError(errorText(e, 'Undo failed.')) }
    finally { setBusy(false) }
  }

  const renderRow = (r: Row) => {
    const found = candidates[r.find]
    return (
      <fieldset key={r.key} className="rounded border border-border/60 px-2 py-1.5" data-testid={`document-values-row-${r.key}`}>
        <legend className="px-1 text-text font-medium">{r.label}</legend>
        {byPage(found.candidates).map(([page, list]) => (
          <div key={page} className="flex flex-col gap-0.5">
            <div className="text-[10px] text-muted">p. {page}</div>
            {list.map(({ c, i }) => (
              <label key={i} className="flex items-start gap-1.5">
                <input type="radio" name={`pick-${r.key}`} checked={picks[r.key] === i} disabled={busy}
                  onChange={() => { invalidate(); setPicks(prev => ({ ...prev, [r.key]: i })) }} />
                <span className="flex-1">
                  <span className="text-text">“{c.quote}”</span>
                  <span className="ml-1 font-mono text-muted">→ {c.value} {c.unit}</span>
                </span>
              </label>
            ))}
          </div>
        ))}
        {found.more > 0 && <div className="text-[10px] text-muted">{found.more} more lines are not listed.</div>}
        {picks[r.key] !== null && picks[r.key] !== undefined && (
          <button type="button" className="text-[10px] text-accent hover:underline" disabled={busy}
            onClick={() => { invalidate(); setPicks(prev => ({ ...prev, [r.key]: null })) }}>
            Leave this one out
          </button>
        )}
      </fieldset>
    )
  }

  // Why Preview or Apply is not available yet, with its fix, shown beside the buttons.
  const previewWhy = !source ? (error ? 'Choose a readable PDF.' : 'Reading the document…')
    : noTarget ? (target === 'tariff' ? 'Add the import tariff first, in Import tariff.' : 'Add a PPA contract first, in Contracts.')
      : pinned ? 'This one comes from the Commercial library; update it there.'
        : mode.kind === 'assets' && names.length === 0 ? 'Pick the assets these values are for.'
          : target === 'ppa' && !contract ? 'Pick the contract these values are for.'
            : offered.length === 0 ? 'No line in the document states one of these values with its unit.'
              : picked.length === 0 ? 'Tick one line for at least one field.'
                : moneyPicked && currency.length !== 3 ? 'State the currency of the picked values.'
                  : yearPicked && moneyYear.length !== 4 ? 'State the money year of the picked prices.'
                    : null
  const applyWhy = previewWhy ?? (!plan ? 'Preview the changes first.'
    : card ? null
      : plan.missing.length ? 'Confirm the money year above, then preview again.'
        : 'Nothing to change: every picked value is already set or left out.')

  const sectionId = (applied?.receipt.section ?? card?.section)?.id as FlowSectionId | undefined

  return (<>
    <div className="flex-1 overflow-auto px-4 py-3 text-xs flex flex-col gap-4" data-testid="document-values">
      {applied ? (
        <section data-testid="document-values-receipt">
          <EditReceiptLine receipt={applied.receipt} disabled={busy} onRequest={() => { void undo() }} />
          {!applied.receipt.undo.available && (
            <p className="mt-2 text-muted" data-testid="document-values-revert">
              Undo does not revert this change: the commercial settings are not on the project's undo history.
              To revert it, set the old values from the card back in this block.
            </p>
          )}
        </section>
      ) : (<>
        {fileSection}
        {source && (
          <p className="text-muted" data-testid="document-values-source">
            {source.filename}: {source.pages} page{source.pages === 1 ? '' : 's'} read.
            {source.truncated.length > 0 && ` Pages ${source.truncated.join(', ')} are read in part.`}
            {' '}Text in the document is only quoted, never followed.
          </p>
        )}

        {source && (
          <section className="flex flex-col gap-1.5">
            <h3 className="text-[10px] font-bold uppercase tracking-wider text-muted">For</h3>
            {mode.kind === 'assets' && (
              <div className="flex flex-wrap gap-x-3 gap-y-1" data-testid="document-values-assets">
                {mode.assetNames.length === 0 && <span className="text-muted">No assets of this kind in the project yet.</span>}
                {mode.assetNames.map(nm => (
                  <label key={nm} className="flex items-center gap-1">
                    <input type="checkbox" checked={names.includes(nm)} disabled={busy}
                      onChange={e => { invalidate(); setNames(prev => e.target.checked ? [...prev, nm] : prev.filter(x => x !== nm)) }} />
                    <span className="font-mono">{nm}</span>
                  </label>
                ))}
              </div>
            )}
            {target === 'tariff' && targets?.tariff && <span className="text-text">The project's import tariff.</span>}
            {target === 'ppa' && targets && targets.ppa.length > 0 && (
              <select aria-label="Contract" className={`${select} self-start`} value={contract} data-testid="document-values-contract"
                onChange={e => { invalidate(); setContract(e.target.value) }}>
                <option value="">Choose a PPA contract…</option>
                {targets.ppa.map(p => <option key={p.id} value={p.id}>{p.id}</option>)}
              </select>
            )}
            {noTarget && <span className="text-muted" data-testid="document-values-no-target">{previewWhy}</span>}
            {pinned && (
              <p className="text-muted" data-testid="document-values-pinned">
                This {target === 'tariff' ? 'tariff' : 'contract'} comes from the Commercial library; it is not edited here.
                <LibraryLink onClose={onClose} />
              </p>
            )}
          </section>
        )}

        {source && !pinned && !noTarget && (
          <section className="flex flex-col gap-2" data-testid="document-values-fields">
            <div className="flex items-center justify-between">
              <h3 className="text-[10px] font-bold uppercase tracking-wider text-muted">Values in the document</h3>
              <label className="flex items-center gap-1 text-muted">
                Decimal mark
                <select aria-label="Decimal mark" className={select} value={decimalMark} disabled={busy}
                  onChange={e => setDecimalMark(e.target.value as DecimalMark | '')}>
                  <option value="">as written</option>
                  <option value=".">. (1,250.5)</option>
                  <option value=",">, (1.250,5)</option>
                </select>
              </label>
            </div>
            {shown.map(renderRow)}
            {folded.length > 0 && (
              <details className="flex flex-col gap-2" data-testid="document-values-more">
                <summary className="cursor-pointer text-muted">
                  {folded.length} more field{folded.length === 1 ? '' : 's'} could take a line of this document
                </summary>
                <div className="mt-2 flex flex-col gap-2">{folded.map(renderRow)}</div>
              </details>
            )}
            {empty.length > 0 && (
              <p className="text-muted" data-testid="document-values-empty">
                No line in the document states {empty.length === 1 ? 'this one' : 'these'} with its unit, so {empty.length === 1 ? 'it is' : 'they are'} left as {empty.length === 1 ? 'it is' : 'they are'}: {empty.map(r => r.label).join(', ')}.
              </p>
            )}
            {moneyPicked && (
              <div className="flex flex-wrap items-center gap-2" data-testid="document-values-money">
                <span className="text-text">The picked {yearPicked ? 'prices are' : 'values are'} in</span>
                <input aria-label="Currency" value={currency} maxLength={3} className={`${select} w-16 font-mono`}
                  onChange={e => { invalidate(); setCurrency(e.target.value.toUpperCase()) }} />
                {yearPicked && (
                  <input aria-label="Money year" value={moneyYear} inputMode="numeric" placeholder="year"
                    className={`${select} w-16 font-mono`}
                    onChange={e => { invalidate(); setMoneyYear(e.target.value.replace(/\D/g, '').slice(0, 4)) }} />
                )}
                <span className="text-muted">{yearPicked ? 'money. ' : ''}Copied as stated, never converted or adjusted for inflation.</span>
              </div>
            )}
          </section>
        )}

        {plan && (
          <section className="flex flex-col gap-2" data-testid="document-values-preview">
            <p className="text-text">
              {plan.changed_cells} value{plan.changed_cells === 1 ? '' : 's'} change
              {plan.unchanged_cells > 0 && `, ${plan.unchanged_cells} already set`}
              {plan.unmapped_total > 0 && `, ${plan.unmapped_total} left out`}.
            </p>
            {plan.needs.map(n => (
              <label key={n.confirmation} className="flex items-start gap-2 text-warn" data-testid="document-values-confirm">
                <input type="checkbox" checked={confirmed} disabled={busy}
                  onChange={e => { setConfirmed(e.target.checked); void runPreview(e.target.checked) }} />
                <span>{n.text}</span>
              </label>
            ))}
            {card && <EditCardTable card={card} guided={false} />}
            {plan.unmapped.length > 0 && (
              <ul className="flex flex-col gap-0.5" data-testid="document-values-gaps">
                {plan.unmapped.map((g, i) => <GapLine key={i} gap={g} onClose={onClose} />)}
              </ul>
            )}
            {plan.notes.map(t => <p key={t} className="text-muted">{t}</p>)}
          </section>
        )}
      </>)}
      {error && <p className="text-danger" role="alert" data-testid="document-values-error">{error}</p>}
    </div>
    <div className="px-4 py-3 border-t border-border flex items-center justify-end gap-2">
      {applied ? (<>
        <button type="button" onClick={onClose} data-testid="document-values-close"
          className="px-3 py-1.5 text-xs rounded border border-border text-text hover:bg-border/30">Close</button>
        {/* Section 7 ends on the block it was opened from; an asset import offers its section. */}
        {!commercial && sectionId && (
          <button type="button" data-testid="document-values-open-section"
            onClick={() => { openSectionInExpert(sectionId); onClose() }}
            className="px-3 py-1.5 text-xs rounded bg-accent text-white hover:opacity-90">
            Open {sectionMeta(sectionId).label} ›
          </button>
        )}
      </>) : (<>
        {applyWhy && <span className="mr-auto text-[11px] text-muted" data-testid="document-values-why">{previewWhy ?? applyWhy}</span>}
        <button type="button" onClick={onClose} className="px-3 py-1.5 text-xs rounded border border-border text-text hover:bg-border/30">Cancel</button>
        <button type="button" disabled={busy || previewWhy !== null} onClick={() => { void runPreview() }}
          data-testid="document-values-preview-btn"
          className="px-3 py-1.5 text-xs rounded border border-border text-text hover:bg-border/30 disabled:opacity-40">
          Preview
        </button>
        <button type="button" disabled={busy || applyWhy !== null} onClick={() => { void runApply() }}
          data-testid="document-values-apply"
          className="px-3 py-1.5 text-xs rounded bg-accent text-white hover:opacity-90 disabled:opacity-40">
          {plan ? `Apply ${plan.changed_cells} change${plan.changed_cells === 1 ? '' : 's'}` : 'Apply'}
        </button>
      </>)}
    </div>
  </>)
}

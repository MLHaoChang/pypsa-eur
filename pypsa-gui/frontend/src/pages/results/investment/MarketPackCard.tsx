// "Market pack" (SCU06 K8a; placement agreed with UX 3): below the prices the
// optimisation uses, in the one tariff editor (TariffSection), so Guided and Expert
// show the same card. Pick a bidding-zone pack, see every value it would change
// old → new beside the ones kept as yours, apply, read the receipt. Applying is an
// ordinary commercial edit (no undo, exactly as typing the values); a cell the user
// edited since the last apply is never overwritten. The card also carries the
// reserve products' missing prices and the K8b "Confirm no backup obligation" list.
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { commercialApi, libraryApi } from '../../../api/commercial'
import {
  marketPacksApi, type MarketPackApplied, type MarketPackPlan, type MarketPackRow, type PackKept,
} from '../../../api/marketPacks'
import type { CommercialConfig, LibraryRef, ReserveProduct } from '../../../api/types'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { LibraryLink, ResultsWhere } from './marketPackLinks'
import {
  addedWords, cellField, cellWords, namedProducts, productOf, productWords, refusalText, valueWords,
} from './marketPackWords'
import NoBackupChecklist from './NoBackupChecklist'
import { SeriesSelect } from './SeriesSelect'

/** From this many cells (changed or kept as yours) the preview folds behind
 *  "Show all" (UX 3 term 4); an added product is one row but counts every cell. */
export const FOLD_AT = 100
const FOLDED_ROWS = 20

/** Why a cell was left as it is, in words. The server keeps a product's cell only
 *  where the pack has no price and the project's product has one, so the line
 *  says the price is kept (a product with no price is named by UnpricedProducts). */
function KeptLine({ kept, products }: { kept: PackKept; products: ReserveProduct[] }) {
  if (kept.cell === 'export_price_ref') {
    return <li>{kept.reason === 'no_export_link'
      ? 'Export price: the site has no export connection, so no export price is set.'
      : 'Export price: the pack has no day-ahead price, so yours is left as it is.'}</li>
  }
  return <li title={kept.cell}>{cellWords(kept.cell.replace(/[^/]+$/, 'id'), products)}: the pack has no capacity
    price, so yours is kept.</li>
}

/** The products a plan adds whole (their id cell from nothing). */
function addedIds(plan: Pick<MarketPackPlan, 'changes'>): Set<string> {
  return new Set(plan.changes.filter(c => cellField(c.cell) === 'id' && c.from == null)
    .map(c => productOf(c.cell) ?? ''))
}

/** Each product the plan adds, as its cells' new values say (never the cached list). */
function addedFromPlan(plan: Pick<MarketPackPlan, 'changes'>): ReserveProduct[] {
  const ids = addedIds(plan)
  return [...ids].map(id => Object.fromEntries(plan.changes.filter(c => productOf(c.cell) === id)
    .map(c => [cellField(c.cell), c.to])) as unknown as ReserveProduct)
}

interface Row { cell: string; now: unknown; after: unknown; yours: boolean; pack?: unknown; words?: boolean }

function PlanTable({ plan, products, testId = 'mp-preview-table' }: {
  plan: MarketPackPlan; products: ReserveProduct[]; testId?: string
}) {
  // An added product is one row, not one "none → none" row per field; its words
  // come from the plan's own values.
  const added = addedIds(plan)
  const fresh = addedFromPlan(plan)
  products = namedProducts(fresh, products)
  const rows: Row[] = [
    ...fresh.map(p => ({ cell: `reserve_products/${p.id}/id`, now: 'not in the project',
                         after: addedWords(p), yours: false, words: true })),
    ...plan.changes.filter(c => !added.has(productOf(c.cell) ?? ''))
      .map(c => ({ cell: c.cell, now: c.from, after: c.to, yours: false })),
    ...plan.conflicts.map(c => ({ cell: c.cell, now: c.yours, after: c.yours, yours: true, pack: c.pack })),
  ]
  const [all, setAll] = useState(false)
  if (rows.length === 0) return null
  const cells = plan.changes.length + plan.conflicts.length
  const folded = cells >= FOLD_AT && !all && rows.length > FOLDED_ROWS
  const shown = folded ? rows.slice(0, FOLDED_ROWS) : rows
  return (
    <div className="space-y-1">
      <table className="w-full text-left border-collapse text-[11px]" data-testid={testId}>
        <thead><tr>{['Value', 'Now', 'After the apply'].map(h => (
          <th key={h} scope="col" className="p-1 border-b border-border">{h}</th>))}</tr></thead>
        <tbody>{shown.map(r => {
          const field = cellField(r.cell)
          return (
            <tr key={r.cell} className={r.yours ? 'bg-warn/10' : ''} data-testid="mp-preview-row" data-yours={r.yours}>
              <th scope="row" title={r.cell} className="p-1 border-b border-border font-normal">
                {cellWords(r.cell, products)}</th>
              <td className="p-1 border-b border-border">{r.words ? String(r.now) : valueWords(r.now, field, products)}</td>
              <td className="p-1 border-b border-border">
                {r.words ? String(r.after) : r.yours && field === 'id' ? 'kept removed'
                  : valueWords(r.after, field, products)}
                {r.yours && <span className="block text-[10px] text-muted">
                  {field === 'id' ? 'You removed it after the last apply, so it is not added again'
                    : `Kept as yours (the pack has ${valueWords(r.pack, field, products)})`}</span>}
              </td>
            </tr>
          )
        })}</tbody>
      </table>
      {rows.length > FOLDED_ROWS && cells >= FOLD_AT && (
        <button type="button" className="underline text-[11px]" data-testid="mp-preview-show-all"
                aria-expanded={all} onClick={() => setAll(!all)}>
          {all ? '▾ Show fewer' : `▸ (${rows.length - FOLDED_ROWS} more) Show all`}</button>
      )}
    </div>
  )
}

/** "2 products to add" / "3 values to change", counting an added product once. */
function counts(plan: Pick<MarketPackPlan, 'changes'>): { added: number; values: number } {
  const added = addedIds(plan)
  return { added: added.size, values: plan.changes.filter(c => !added.has(productOf(c.cell) ?? '')).length }
}
const many = (n: number, one: string) => `${n} ${one}${n === 1 ? '' : 's'}`

function totals(plan: MarketPackPlan): string {
  const { added, values } = counts(plan)
  return [added ? `${many(added, 'product')} to add` : null, `${many(values, 'value')} to change`,
    `${plan.conflicts.length} kept as yours`, `${plan.kept.length} left as they are`]
    .filter(Boolean).join(' · ')
}

function PackPreview({ pack, products, onApplied, onClose }: {
  pack: MarketPackRow
  /** The project's own products. */
  products: ReserveProduct[]
  onApplied: (out: MarketPackApplied) => void
  onClose: () => void
}) {
  const project = useUIStore(s => s.currentProject)
  const preview = useQuery({
    queryKey: nk(project, 'commercial', 'market_packs', 'preview', pack.id, pack.pack_hash),
    queryFn: () => marketPacksApi.preview(pack.id), retry: false, staleTime: 0,
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // A stale refusal (UX 3): the plan on screen is out of date, so it is fetched
  // again and Apply stays off until the new plan lands; a second click never
  // re-sends the refused digest.
  const [stale, setStale] = useState(false)
  const plan = preview.data
  const named = namedProducts(products, pack.products)

  const apply = async () => {
    if (!plan) return
    setBusy(true)
    setError(null)
    setStale(false)
    try {
      onApplied(await marketPacksApi.apply(pack.id, plan.pack_hash, plan.plan_digest))
    } catch (e) {
      const code = (e as { response?: { data?: { detail?: { code?: unknown } } } })?.response?.data?.detail?.code
      if (code === 'market_pack_stale') {
        setStale(true)
        await preview.refetch()
      } else {
        setError(`Nothing was applied. ${refusalText(e)}`)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded border border-border p-2 space-y-2" data-testid="mp-preview">
      <p className="text-[11px] font-semibold">What applying {pack.label} would change</p>
      {preview.isPending && <p className="text-[11px] text-muted">Working out the changes…</p>}
      {preview.isError && <p role="alert" className="text-[11px] text-danger" data-testid="mp-preview-refused">
        {refusalText(preview.error)}</p>}
      {/* A failed fetch after a stale refusal (GK B2 F1): the plan still held is out of
          date, so it is not shown and cannot be applied; the user previews again. */}
      {plan && preview.isError && (
        <>
          {stale && <p role="status" className="text-[11px] text-warn" data-testid="mp-apply-stale">
            Changed since you looked, so nothing was applied. The new changes could not be worked out.</p>}
          <div className="flex items-center gap-2">
            <button type="button" disabled={preview.isFetching} data-testid="mp-preview-again"
                    className="px-2 py-0.5 rounded bg-accent text-on-accent text-[11px] disabled:opacity-40"
                    onClick={() => { void preview.refetch() }}>Preview again</button>
            <button type="button" className="px-2 py-0.5 text-[11px]" onClick={onClose}>Cancel</button>
          </div>
        </>
      )}
      {plan && !preview.isError && (
        <>
          {/* The totals and Apply sit above the list, so a long list never hides them. */}
          <p className="text-[11px]" data-testid="mp-preview-totals">{totals(plan)}</p>
          <div className="flex items-center gap-2">
            {plan.changes.length > 0 && (
              <button type="button" disabled={busy || preview.isFetching} data-testid="mp-apply"
                      className="px-2 py-0.5 rounded bg-accent text-on-accent text-[11px] disabled:opacity-40"
                      onClick={() => { void apply() }}>Apply {pack.label}</button>)}
            <button type="button" className="px-2 py-0.5 text-[11px]" onClick={onClose}>Cancel</button>
            {plan.changes.length === 0 && <span className="text-[11px] text-muted" data-testid="mp-nothing">
              Nothing to apply: every value already matches the pack or is yours.</span>}
          </div>
          {error && <p role="alert" className="text-[11px] text-danger" data-testid="mp-apply-refused">{error}</p>}
          {stale && <p role="status" className="text-[11px] text-warn" data-testid="mp-apply-stale">
            {preview.isFetching ? 'Changed since you looked, so nothing was applied. Working out the changes again…'
              : 'Changed since you looked, so nothing was applied. The list below is up to date.'}</p>}
          <PlanTable plan={plan} products={named} />
          {plan.kept.length > 0 && (
            <ul className="list-disc pl-4 text-[11px] text-muted" data-testid="mp-preview-kept">
              {plan.kept.map(k => <KeptLine key={k.cell} kept={k} products={named} />)}</ul>
          )}
        </>
      )}
    </div>
  )
}

function Receipt({ pack, out, products, onClose }: {
  pack: MarketPackRow; out: MarketPackApplied; products: ReserveProduct[]; onClose: () => void
}) {
  const { added, values } = counts(out)
  const unpricedAdded = addedFromPlan(out).filter(p => p.capacity_price == null)
  return (
    <div className="rounded border border-border p-2 space-y-1 text-[11px]" role="status" data-testid="mp-receipt">
      <p>Applied {pack.label}: {added ? `${many(added, 'product')} added, ` : ''}{many(values, 'value')} changed.</p>
      {unpricedAdded.length > 0 && <p className="text-warn" data-testid="mp-receipt-unpriced">
        {unpricedAdded.map(productWords).join(', ')} came in with no price: pick one with “Open here”
        on this card, or upload one in the <LibraryLink />.</p>}
      {/* Every cell, old → new, beside the ones kept as yours (from the apply's answer:
          after it the pack no longer reads as an update). */}
      <PlanTable plan={out} products={products} testId="mp-receipt-table" />
      {out.kept.length > 0 && <ul className="list-disc pl-4 text-muted">
        {out.kept.map(k => <KeptLine key={k.cell} kept={k} products={products} />)}</ul>}
      <p className="text-muted"><ResultsWhere /></p>
      <div className="flex justify-end"><button type="button" className="px-2 py-0.5" onClick={onClose}>Close</button></div>
    </div>
  )
}

/** The project's reserve products with no price (UX 3 term 1): each named in words,
 *  with "Open here" to pick its price series in place. */
function UnpricedProducts({ unpriced }: { unpriced: ReserveProduct[] }) {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const [openId, setOpenId] = useState<string | null>(null)
  const [pick, setPick] = useState<LibraryRef | null>(null)
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const series = useQuery({ queryKey: nk(project, 'library', 'series'), queryFn: () => libraryApi.listSeries(),
                            retry: false, enabled: openId !== null })
  const save = async (id: string) => {
    if (!pick) return
    setMessage(null)
    try {
      // The products as stored now, not as this card last read them, so a change
      // made elsewhere since is kept.
      const fresh = await commercialApi.getCommercial()
      await commercialApi.saveCommercial({
        reserve_products: (fresh?.reserve_products ?? []).map(p => p.id === id ? { ...p, capacity_price: pick } : p),
      })
      setOpenId(null)
      setPick(null)
      setMessage({ tone: 'ok', text: 'The price is saved.' })
      await qc.invalidateQueries({ queryKey: nk(project, 'commercial') })
      await qc.invalidateQueries({ queryKey: nk(project, 'results') })
    } catch (e) {
      setMessage({ tone: 'error', text: `The price is not saved. ${refusalText(e)}` })
    }
  }
  if (unpriced.length === 0 && !message) return null
  return (
    <div className="space-y-1 text-[11px]" data-testid="mp-unpriced">
      {unpriced.map(p => (
        <div key={p.id} className="space-y-1" data-testid={`mp-unpriced-${p.id}`}>
          <p className="text-warn">
            {productWords(p)} has no price, so the solve refuses it.{' '}
            <button type="button" className="underline" data-testid={`mp-unpriced-open-${p.id}`}
                    aria-expanded={openId === p.id}
                    onClick={() => { setOpenId(openId === p.id ? null : p.id); setPick(null); setMessage(null) }}>
              Open here</button></p>
          {openId === p.id && (
            <div className="flex items-center gap-2 flex-wrap pl-2">
              <SeriesSelect label={`Capacity price for ${productWords(p)}`} value={pick}
                            series={series.data ?? []} onChange={setPick} />
              <button type="button" disabled={!pick} data-testid={`mp-unpriced-save-${p.id}`}
                      className="border border-border rounded px-2 py-0.5 disabled:opacity-40"
                      onClick={() => { void save(p.id) }}>Save the price</button>
              <span className="text-muted">No price: upload one in the <LibraryLink />.</span>
            </div>
          )}
        </div>
      ))}
      {message && <p role={message.tone === 'error' ? 'alert' : 'status'} data-testid="mp-unpriced-message"
                     className={message.tone === 'error' ? 'text-danger' : 'text-success'}>
        {message.text}{message.tone === 'ok' && <> <ResultsWhere /></>}</p>}
    </div>
  )
}

function Badge({ pack }: { pack: MarketPackRow }) {
  if (pack.update_available) {
    return <span className="rounded px-1 bg-warn/15 text-warn" data-testid={`mp-badge-${pack.id}`}>Update available</span>
  }
  if (!pack.pinned) return null
  return (
    <span className="rounded px-1 bg-success/15 text-success" data-testid={`mp-badge-${pack.id}`}>
      Using version {pack.valid_year}</span>
  )
}

function PackItem({ p, expert, open, onToggle, preview }: {
  p: MarketPackRow; expert: boolean; open: boolean; onToggle: () => void; preview: React.ReactNode
}) {
  return (
    <li className="space-y-0.5" data-testid={`mp-pack-${p.id}`}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-medium">{p.label}</span>
        <span className="text-muted">{p.region}, {p.currency}, {p.valid_year}</span>
        {expert && <span className="text-muted">{p.id}</span>}
        <Badge pack={p} />
        {!p.refusal && (
          <button type="button" className="underline ml-auto" data-testid={`mp-preview-${p.id}`}
                  aria-expanded={open} onClick={onToggle}>
            {open ? 'Hide' : p.update_available ? 'See the update' : 'See what it changes'}</button>
        )}
      </div>
      {p.refusal && <p className="text-muted" data-testid={`mp-refusal-${p.id}`}>{p.refusal.message}</p>}
      {open && preview}
    </li>
  )
}

export default function MarketPackCard({ cfg }: { cfg: CommercialConfig }) {
  const project = useUIStore(s => s.currentProject)
  const expert = useUIStore(s => s.uiMode) === 'expert'
  const qc = useQueryClient()
  const list = useQuery({ queryKey: nk(project, 'commercial', 'market_packs'), queryFn: marketPacksApi.list,
                          retry: false })
  const [openId, setOpenId] = useState<string | null>(null)
  const [done, setDone] = useState<{ pack: MarketPackRow; out: MarketPackApplied; products: ReserveProduct[] } | null>(null)
  const products = cfg.reserve_products ?? []
  // Incomplete only for a product the project holds (applied or entered), never for
  // one a pack merely lists.
  const unpriced = products.filter(p => p.capacity_price == null)
  const data = list.data
  const fits = data?.packs.filter(p => !p.refusal) ?? []
  const others = data?.packs.filter(p => p.refusal) ?? []

  const applied = async (pack: MarketPackRow, out: MarketPackApplied) => {
    // The answer carries the cells kept as yours; after the apply the pack no
    // longer reads as an update, so the receipt is the one place they show.
    setDone({ pack, out, products: namedProducts(products, pack.products) })
    setOpenId(null)
    await qc.invalidateQueries({ queryKey: nk(project, 'commercial') })
    await qc.invalidateQueries({ queryKey: nk(project, 'results') })
  }
  const item = (p: MarketPackRow) => (
    <PackItem key={p.id} p={p} expert={expert} open={openId === p.id}
              onToggle={() => { setDone(null); setOpenId(openId === p.id ? null : p.id) }}
              preview={<PackPreview pack={p} products={products} onClose={() => setOpenId(null)}
                                    onApplied={out => { void applied(p, out) }} />} />
  )

  return (
    <section className="space-y-2 border border-border rounded p-2" data-testid="mp-card">
      <h4 className="text-[11px] font-semibold flex items-center gap-2">
        Market pack
        {unpriced.length > 0 && <span className="rounded px-1 bg-warn/15 text-warn font-normal"
                                      data-testid="mp-incomplete">Incomplete</span>}
      </h4>
      <p className="text-[11px] text-muted">
        A market pack fills the reserve products and the day-ahead export price for one bidding zone.
        It changes only values you left as the pack set them; your own entries are kept as yours.</p>
      {list.isPending && <p className="text-[11px] text-muted">Loading the market packs…</p>}
      {list.isError && <p role="alert" className="text-[11px] text-danger">
        The market packs could not be listed. {refusalText(list.error)}</p>}
      {data && !data.saved && (
        <p className="text-[11px] text-warn" data-testid="mp-unsaved">
          This project is not saved yet. Save the project first; a pack applies only to a saved project.</p>
      )}
      {data?.pin_error && (
        <p className="text-[11px] text-warn" data-testid="mp-pin-unreadable">
          The record of what the last pack applied cannot be read, so the card cannot tell your edits from
          the pack's and applies nothing.{' '}
          {expert
            ? 'Restore the project\'s market_pack.json from a backup, or delete it (every filled value then counts as yours), and reload.'
            : 'Restore the project from a backup, or ask whoever manages the project files to delete its market-pack record (every filled value then counts as yours), and reload.'}</p>
      )}
      <UnpricedProducts unpriced={unpriced} />
      {fits.length > 0 && <ul className="space-y-1 text-[11px]" data-testid="mp-list">{fits.map(item)}</ul>}
      {others.length > 0 && (
        <details className="text-[11px]" data-testid="mp-other-zones">
          <summary className="cursor-pointer text-muted">Packs for other zones or currencies ({others.length})</summary>
          <ul className="space-y-1 pt-1">{others.map(item)}</ul>
        </details>
      )}
      {done && <Receipt pack={done.pack} out={done.out} products={done.products} onClose={() => setDone(null)} />}
      <NoBackupChecklist cfg={cfg} />
    </section>
  )
}

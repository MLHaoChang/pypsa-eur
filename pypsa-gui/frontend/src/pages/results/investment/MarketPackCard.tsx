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
import { withReturnFrom } from '../../../layout/flowNavigation'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { cellWords, productWords, refusalText, valueWords } from './marketPackWords'
import NoBackupChecklist from './NoBackupChecklist'
import { SeriesSelect } from './SeriesSelect'

/** From this many cells the preview folds behind "Show all" (UX 3 term 4). */
export const FOLD_AT = 100
const FOLDED_ROWS = 20

const RESULTS_WHERE = 'Re-solve, then see the reserves in Results → Storage cycling.'

export function LibraryLink({ children = 'Library' }: { children?: string }) {
  return (
    <button type="button" className="underline" data-testid="mp-open-library"
            onClick={() => withReturnFrom(() => useUIStore.getState().setSlidePanel('library'))}>
      {children}</button>
  )
}

/** Why a cell was left as it is, in words. */
function KeptLine({ kept, products }: { kept: PackKept; products: ReserveProduct[] }) {
  if (kept.cell === 'export_price_ref') {
    return <li>{kept.reason === 'no_export_link'
      ? 'Export price: the site has no export connection, so no export price is set.'
      : 'Export price: the pack has no day-ahead price, so yours is left as it is.'}</li>
  }
  return <li>{cellWords(kept.cell, products)}. No price: upload one in the <LibraryLink />.</li>
}

interface Row { cell: string; now: unknown; after: unknown; yours: boolean; pack?: unknown }

function PlanTable({ plan, products }: { plan: MarketPackPlan; products: ReserveProduct[] }) {
  const rows: Row[] = [
    ...plan.changes.map(c => ({ cell: c.cell, now: c.from, after: c.to, yours: false })),
    ...plan.conflicts.map(c => ({ cell: c.cell, now: c.yours, after: c.yours, yours: true, pack: c.pack })),
  ]
  const [all, setAll] = useState(false)
  if (rows.length === 0) return null
  const folded = rows.length >= FOLD_AT && !all
  const shown = folded ? rows.slice(0, FOLDED_ROWS) : rows
  return (
    <div className="space-y-1">
      <table className="w-full text-left border-collapse text-[11px]" data-testid="mp-preview-table">
        <thead><tr>{['Value', 'Now', 'After the apply'].map(h => (
          <th key={h} scope="col" className="p-1 border-b border-border">{h}</th>))}</tr></thead>
        <tbody>{shown.map(r => (
          <tr key={r.cell} className={r.yours ? 'bg-warn/10' : ''} data-testid="mp-preview-row" data-yours={r.yours}>
            <th scope="row" className="p-1 border-b border-border font-normal">{cellWords(r.cell, products)}</th>
            <td className="p-1 border-b border-border">{valueWords(r.now)}</td>
            <td className="p-1 border-b border-border">
              {valueWords(r.after)}
              {r.yours && <span className="block text-[10px] text-muted">
                Kept as yours (the pack has {valueWords(r.pack)})</span>}
            </td>
          </tr>
        ))}</tbody>
      </table>
      {folded && (
        <button type="button" className="underline text-[11px]" data-testid="mp-preview-show-all"
                onClick={() => setAll(true)}>Show all {rows.length}</button>
      )}
    </div>
  )
}

function totals(plan: MarketPackPlan): string {
  const n = plan.changes.length
  return `${n} value${n === 1 ? '' : 's'} to change, ${plan.conflicts.length} kept as yours, `
    + `${plan.kept.length} left as they are.`
}

function PackPreview({ pack, products, onApplied, onClose }: {
  pack: MarketPackRow
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
  const plan = preview.data
  // The products named in the words: the project's, then the pack's new ones.
  const named = [...products, ...pack.products.filter(p => !products.some(q => q.id === p.id))]

  const apply = async () => {
    if (!plan) return
    setBusy(true)
    setError(null)
    try {
      onApplied(await marketPacksApi.apply(pack.id, plan.pack_hash))
    } catch (e) {
      setError(`Nothing was applied. ${refusalText(e)}`)
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
      {plan && (
        <>
          {/* The totals and Apply sit above the list, so a long list never hides them. */}
          <p className="text-[11px]" data-testid="mp-preview-totals">{totals(plan)}</p>
          <div className="flex items-center gap-2">
            <button type="button" disabled={busy || plan.changes.length === 0} data-testid="mp-apply"
                    className="px-2 py-0.5 rounded bg-accent text-on-accent text-[11px] disabled:opacity-40"
                    onClick={() => { void apply() }}>Apply {pack.label}</button>
            <button type="button" className="px-2 py-0.5 text-[11px]" onClick={onClose}>Cancel</button>
            {plan.changes.length === 0 && <span className="text-[11px] text-muted">
              Nothing to apply: every value already matches the pack or is yours.</span>}
          </div>
          {error && <p role="alert" className="text-[11px] text-danger" data-testid="mp-apply-refused">{error}</p>}
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
  return (
    <div className="rounded border border-border p-2 space-y-1 text-[11px]" role="status" data-testid="mp-receipt">
      <p>Applied {pack.label}: {out.changed} value{out.changed === 1 ? '' : 's'} changed.</p>
      {out.conflicts.length > 0 && (
        <div data-testid="mp-receipt-kept">
          <p>{out.conflicts.length} kept as yours:</p>
          <ul className="list-disc pl-4">{out.conflicts.map(c => (
            <li key={c.cell}>{cellWords(c.cell, products)}: {valueWords(c.yours)} (the pack has {valueWords(c.pack)})</li>
          ))}</ul>
        </div>
      )}
      {out.kept.length > 0 && <ul className="list-disc pl-4 text-muted">
        {out.kept.map(k => <KeptLine key={k.cell} kept={k} products={products} />)}</ul>}
      <p className="text-muted">{RESULTS_WHERE}</p>
      <div className="flex justify-end"><button type="button" className="px-2 py-0.5" onClick={onClose}>Close</button></div>
    </div>
  )
}

/** The project's reserve products with no price (UX 3 term 1): each named in words,
 *  with "Open here" to pick its price series in place. */
function UnpricedProducts({ cfg, unpriced }: { cfg: CommercialConfig; unpriced: ReserveProduct[] }) {
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
      await commercialApi.saveCommercial({
        reserve_products: (cfg.reserve_products ?? []).map(p => p.id === id ? { ...p, capacity_price: pick } : p),
      })
      setOpenId(null)
      setPick(null)
      setMessage({ tone: 'ok', text: `The price is saved. ${RESULTS_WHERE}` })
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
      {message && <p role={message.tone === 'error' ? 'alert' : 'status'}
                     className={message.tone === 'error' ? 'text-danger' : 'text-success'}>{message.text}</p>}
    </div>
  )
}

function Badge({ pack, expert }: { pack: MarketPackRow; expert: boolean }) {
  if (pack.update_available) {
    return <span className="rounded px-1 bg-warn/15 text-warn" data-testid={`mp-badge-${pack.id}`}>Update available</span>
  }
  if (!pack.pinned) return null
  return (
    <span className="rounded px-1 bg-success/15 text-success" data-testid={`mp-badge-${pack.id}`}>
      Using version {pack.valid_year}{expert ? ` · ${pack.pack_hash.slice(0, 8)}` : ''}</span>
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

  const applied = async (pack: MarketPackRow, out: MarketPackApplied) => {
    // The answer carries the cells kept as yours; after the apply the pack no
    // longer reads as an update, so the receipt is the one place they show.
    const named = [...products, ...pack.products.filter(p => !products.some(q => q.id === p.id))]
    setDone({ pack, out, products: named })
    setOpenId(null)
    await qc.invalidateQueries({ queryKey: nk(project, 'commercial') })
    await qc.invalidateQueries({ queryKey: nk(project, 'results') })
  }

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
          the pack's and applies nothing. Restore the project's market_pack.json from a backup, or delete it
          (every filled value then counts as yours), and reload.</p>
      )}
      <UnpricedProducts cfg={cfg} unpriced={unpriced} />
      {data && data.packs.length > 0 && (
        <ul className="space-y-1 text-[11px]" data-testid="mp-list">
          {data.packs.map(p => (
            <li key={p.id} className="space-y-0.5" data-testid={`mp-pack-${p.id}`}>
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-medium">{p.label}</span>
                <span className="text-muted">{p.region}, {p.currency}, {p.valid_year}</span>
                {expert && <span className="text-muted">{p.id}</span>}
                <Badge pack={p} expert={expert} />
                {!p.refusal && (
                  <button type="button" className="underline ml-auto" data-testid={`mp-preview-${p.id}`}
                          onClick={() => { setDone(null); setOpenId(openId === p.id ? null : p.id) }}>
                    {openId === p.id ? 'Hide' : p.update_available ? 'See the update' : 'See what it changes'}</button>
                )}
              </div>
              {p.refusal && <p className="text-muted" data-testid={`mp-refusal-${p.id}`}>{p.refusal.message}</p>}
              {openId === p.id && (
                <PackPreview pack={p} products={products} onClose={() => setOpenId(null)}
                             onApplied={out => { void applied(p, out) }} />
              )}
            </li>
          ))}
        </ul>
      )}
      {done && <Receipt pack={done.pack} out={done.out} products={done.products} onClose={() => setDone(null)} />}
      <NoBackupChecklist cfg={cfg} />
    </section>
  )
}

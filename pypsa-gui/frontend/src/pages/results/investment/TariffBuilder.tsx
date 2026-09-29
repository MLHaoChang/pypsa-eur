// Results → Investment → Tariff (IC P3 WP3.7b): build or edit an import tariff —
// items, periods (windows), tiers with per-period tier rates, ratchets (range,
// cyclic, designated months), settlement, measured_on and direction — preview
// the last solve's bill under it, and save it to the Library or as the
// project's tariff. The server is the source of truth: its 422s are shown at
// the fields they name.
import { createContext, useContext, useEffect, useId, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { commercialApi, libraryApi, type BillingPayload } from '../../../api/commercial'
import type { Tariff, TariffRatchet } from '../../../api/types'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { fmtAmount } from './valueFlows'
import {
  blankItem, errorsByField, errorsUnder, isWindowed, KINDS, listText, MEASURED,
  normaliseTariff, parseIntList, parseNumberList, SETTLEMENTS, UNITS, type Item, type Period,
} from './tariffModel'

const input = 'border border-border rounded px-1 py-0.5 text-[11px] bg-bg'

function Errors({ list }: { list: string[] }) {
  if (!list.length) return null
  return <ul role="alert" className="text-[11px] text-danger pl-3 list-disc">
    {list.map(e => <li key={e}>{e}</li>)}</ul>
}

/** The list fields that do not parse: Preview and Save wait for them. */
const InvalidLists = createContext<(key: string, label: string | null) => void>(() => {})

/** A comma list edited as text; committed on blur when it parses. The text
 *  follows the value whenever the field is not being edited (a removed row
 *  or an added tier re-renders it, review #1); a list that does not parse is
 *  named beside the field and blocks Preview / Save (review #2). */
function ListInput({ label, value, onChange, parse }: {
  label: string; value: number[] | null | undefined
  onChange: (v: number[] | undefined) => void
  parse: (t: string) => number[] | undefined | null
}) {
  const shown = listText(value)
  const [text, setText] = useState(shown)
  const [focused, setFocused] = useState(false)
  const [bad, setBad] = useState(false)
  const report = useContext(InvalidLists)
  const errId = useId()
  const key = useId()   // stable across a rename of its item (review round 2 #2)
  useEffect(() => {
    if (!focused) { setText(shown); setBad(false); report(key, null) }
  }, [shown]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (bad) report(key, label) }, [label, bad, key, report])
  useEffect(() => () => report(key, null), [key, report])
  return (
    <span>
      <input aria-label={label} aria-invalid={bad} aria-describedby={bad ? errId : undefined}
             className={`${input} w-28 ${bad ? 'border-danger' : ''}`} value={text}
             onFocus={() => setFocused(true)}
             onChange={e => setText(e.target.value)}
             onBlur={() => {
               setFocused(false)
               const v = parse(text)
               setBad(v === null); report(key, v === null ? label : null)
               if (v !== null) onChange(v)
             }} />
      {bad && <span id={errId} role="alert" className="text-danger ml-1">not a list of numbers</span>}
    </span>
  )
}

function num(v: string): number { return v === '' ? NaN : Number(v) }

function RatchetEditor({ item, set, label }: {
  item: Item; set: (r: TariffRatchet | null) => void; label: string
}) {
  const r = item.ratchet
  const mode = !r ? 'none' : r.months ? 'months' : r.cyclic_year ? 'cyclic' : 'range'
  return (
    <div className="flex flex-wrap items-center gap-2">
      <label>Ratchet{' '}
        <select aria-label={`${label} ratchet`} className={input} value={mode}
                onChange={e => {
                  const m = e.target.value
                  const share = r?.share ?? 0.8
                  set(m === 'none' ? null : m === 'months' ? { share, months: [1] }
                    : { share, lookback_months: r?.lookback_months ?? 11, cyclic_year: m === 'cyclic' })
                }}>
          <option value="none">none</option>
          <option value="range">range (months before)</option>
          <option value="cyclic">range, cyclic within the rate year</option>
          <option value="months">designated months</option>
        </select>
      </label>
      {r && (
        <label>share{' '}
          <input type="number" step="any" min={0} max={1} aria-label={`${label} ratchet share`}
                 className={`${input} w-16`} value={r.share}
                 onChange={e => set({ ...r, share: num(e.target.value) })} />
        </label>
      )}
      {r && !r.months && (
        <label>lookback months{' '}
          <input type="number" min={1} max={36} aria-label={`${label} ratchet lookback months`}
                 className={`${input} w-16`} value={r.lookback_months ?? ''}
                 onChange={e => set({ ...r, lookback_months: num(e.target.value) })} />
        </label>
      )}
      {r?.months && (
        <label>months{' '}
          <ListInput label={`${label} ratchet months`} value={r.months} parse={parseIntList}
                     onChange={v => set({ ...r, months: v ?? [] })} />
        </label>
      )}
    </div>
  )
}

function PeriodRow({ p, i, label, windowed, set, remove }: {
  p: Period; i: number; label: string; windowed: boolean
  set: (p: Period) => void; remove: () => void
}) {
  const hour = (v: string) => (v === '' ? null : Number(v))
  return (
    <tr>
      <td><input aria-label={`${label} period ${i + 1} name`} className={`${input} w-20`} value={p.name}
                 onChange={e => set({ ...p, name: e.target.value })} /></td>
      <td><input type="number" step="any" aria-label={`${label} period ${i + 1} rate`}
                 className={`${input} w-20`} value={p.rate}
                 onChange={e => set({ ...p, rate: num(e.target.value) })} /></td>
      <td><ListInput label={`${label} period ${i + 1} months`} value={p.months}
                     parse={parseIntList} onChange={v => set({ ...p, months: v })} /></td>
      <td><ListInput label={`${label} period ${i + 1} weekdays`} value={p.weekdays}
                     parse={parseIntList} onChange={v => set({ ...p, weekdays: v })} /></td>
      <td><input type="number" min={0} max={24} aria-label={`${label} period ${i + 1} start hour`}
                 className={`${input} w-12`} value={p.start_hour ?? ''}
                 onChange={e => set({ ...p, start_hour: hour(e.target.value) })} /></td>
      <td><input type="number" min={0} max={24} aria-label={`${label} period ${i + 1} end hour`}
                 className={`${input} w-12`} value={p.end_hour ?? ''}
                 onChange={e => set({ ...p, end_hour: hour(e.target.value) })} /></td>
      {windowed && (
        <td><ListInput label={`${label} period ${i + 1} tier rates`} value={p.tier_rates}
                       parse={parseNumberList} onChange={v => set({ ...p, tier_rates: v ?? null })} /></td>
      )}
      <td><button type="button" aria-label={`Remove ${label} period ${i + 1}`} onClick={remove}>×</button></td>
    </tr>
  )
}

export function ItemEditor({ item, idx, set, remove, errors }: {
  item: Item; idx: number; set: (it: Item) => void; remove: () => void; errors: string[]
}) {
  const label = `Item ${item.id || idx + 1}`
  const whyId = useId()   // an IDREF with no spaces (a label has them; WP3.7c review #13)
  const windowed = isWindowed(item)
  const tiers = item.tiers ?? []
  return (
    <fieldset className="border border-border rounded p-2 space-y-2" data-testid={`tb-item-${idx}`}>
      <legend className="font-semibold">{label}</legend>
      <div className="flex flex-wrap gap-2 items-center">
        <label>id <input aria-label={`${label} id`} className={`${input} w-24`} value={item.id}
                         onChange={e => set({ ...item, id: e.target.value })} /></label>
        <label>kind <select aria-label={`${label} kind`} className={input} value={item.kind}
                            onChange={e => {
                              // A kind change keeps nothing another kind cannot carry (review #8).
                              const kind = e.target.value as Item['kind']
                              const next: Item = { ...item, kind, unit: blankItem(kind, item.id).unit }
                              if (kind !== 'demand') delete next.ratchet
                              set(next)
                            }}>
          {KINDS.map(k => <option key={k} value={k}>{k}</option>)}</select></label>
        <label>unit <select aria-label={`${label} unit`} className={input} value={item.unit}
                            onChange={e => set({ ...item, unit: e.target.value as Item['unit'] })}>
          {UNITS.map(k => <option key={k} value={k}>{k}</option>)}</select></label>
        <label>measured on <select aria-label={`${label} measured on`} className={input}
                                   value={item.measured_on ?? 'import'}
                                   onChange={e => set({ ...item, measured_on: e.target.value as Item['measured_on'] })}>
          {MEASURED.map(k => <option key={k} value={k}>{k}</option>)}</select></label>
        <label>direction <select aria-label={`${label} direction`} className={input}
                                 value={item.direction ?? 'cost'}
                                 onChange={e => set({ ...item, direction: e.target.value as Item['direction'] })}>
          <option value="cost">cost</option><option value="revenue">revenue</option></select></label>
        {(item.kind === 'demand' || item.kind === 'capacity' || item.measured_on === 'peak_import') && (
          <label>settlement <select aria-label={`${label} settlement`} className={input}
                                    value={item.settlement ?? '15min'}
                                    onChange={e => set({ ...item, settlement: e.target.value as Item['settlement'] })}>
            {SETTLEMENTS.map(k => <option key={k} value={k}>{k}</option>)}</select></label>
        )}
        <button type="button" aria-label={`Remove ${label}`} onClick={remove}>Remove item</button>
      </div>
      <table className="text-[11px]">
        <caption className="text-left">Periods (months 1–12, weekdays 0 = Monday, hours [start, end))</caption>
        <thead><tr><th scope="col">name</th><th scope="col">rate</th><th scope="col">months</th>
          <th scope="col">weekdays</th><th scope="col">start</th><th scope="col">end</th>
          {windowed && <th scope="col">tier rates</th>}<th scope="col"><span className="sr-only">Remove</span></th></tr></thead>
        <tbody>
          {item.periods.map((p, j) => (
            <PeriodRow key={j} p={p} i={j} label={label} windowed={windowed}
                       set={np => set({ ...item, periods: item.periods.map((x, k) => (k === j ? np : x)) })}
                       remove={() => set({ ...item, periods: item.periods.filter((_, k) => k !== j) })} />
          ))}
        </tbody>
      </table>
      <button type="button" className="underline" onClick={() => set({ ...item, periods: [
        ...item.periods, { name: `p${item.periods.length + 1}`, rate: 0,
                           ...(windowed ? { tier_rates: tiers.map(() => 0) } : {}) }] })}>
        Add period to {label}
      </button>
      {(item.kind === 'energy' || item.kind === 'demand') && (
        <div className="space-y-1">
          <div>Tiers (threshold in kWh a month, or kW for demand){windowed ? ' — each period has its own tier rates' : ''}</div>
          {tiers.map((t, k) => (
            <div key={k} className="flex gap-2 items-center">
              <input type="number" step="any" min={0} aria-label={`${label} tier ${k + 1} threshold`}
                     className={`${input} w-24`} value={t.threshold}
                     onChange={e => set({ ...item, tiers: tiers.map((x, m) => (m === k ? { ...x, threshold: num(e.target.value) } : x)) })} />
              {!windowed && (
                <input type="number" step="any" aria-label={`${label} tier ${k + 1} rate`}
                       className={`${input} w-20`} value={t.rate}
                       onChange={e => set({ ...item, tiers: tiers.map((x, m) => (m === k ? { ...x, rate: num(e.target.value) } : x)) })} />
              )}
              <button type="button" aria-label={`Remove ${label} tier ${k + 1}`}
                      onClick={() => set({ ...item, tiers: tiers.filter((_, m) => m !== k),
                        periods: item.periods.map(p => (p.tier_rates ? { ...p, tier_rates: p.tier_rates.filter((_, m) => m !== k) } : p)) })}>×</button>
            </div>
          ))}
          <div className="flex gap-2">
            <button type="button" className="underline" onClick={() => set({ ...item,
              tiers: [...tiers, { threshold: tiers.length ? (tiers[tiers.length - 1].threshold || 0) + 1000 : 0, rate: 0 }],
              periods: item.periods.map(p => (p.tier_rates ? { ...p, tier_rates: [...p.tier_rates, 0] } : p)) })}>
              Add tier to {label}</button>
            {tiers.length > 0 && (
              <label className="flex items-center gap-1">
                <input type="checkbox" checked={windowed} aria-label={`${label} tier rates per period`}
                       disabled={windowed && !sameTierRates(item)}
                       aria-describedby={windowed && !sameTierRates(item) ? whyId : undefined}
                       onChange={e => {
                         if (e.target.checked) {
                           set({ ...item, periods: item.periods.map(p => ({ ...p, tier_rates: tiers.map(t => t.rate) })),
                                 tiers: tiers.map(t => ({ ...t, rate: 0 })) })
                         } else {
                           // Back to one set of tier rates: the first period's (review #5).
                           const first = item.periods.find(p => p.tier_rates)?.tier_rates ?? []
                           set({ ...item, periods: item.periods.map(p => ({ ...p, tier_rates: null })),
                                 tiers: tiers.map((t, k) => ({ ...t, rate: first[k] ?? t.rate })) })
                         }
                       }} />
                tier rates per period
                {windowed && !sameTierRates(item) && (
                  <span id={whyId} className="text-muted">
                    (the periods' tier rates differ; make them equal to use one set)</span>
                )}
              </label>
            )}
          </div>
        </div>
      )}
      {item.kind === 'demand' && (
        <RatchetEditor item={item} label={label} set={r => set({ ...item, ratchet: r })} />
      )}
      <Errors list={errors} />
    </fieldset>
  )
}

function Preview({ bill }: { bill: BillingPayload }) {
  return (
    <div className="space-y-1" data-testid="tb-preview">
      <p className="text-muted">
        The last solve's dispatch billed under this draft. The dispatch was optimised for the
        attached tariff, not this one; no settlement or gap is computed.</p>
      {Object.entries(bill.summary).map(([p, s]) => (
        <table key={p} className="text-[11px] max-w-md w-full">
          <caption className="text-left">{p === '_' ? 'Modelled year' : p}</caption>
          <tbody>
            {s === null ? <tr><td>not established</td></tr> : (
              <>
                {Object.entries(s.per_item).map(([k, v]) => (
                  <tr key={k}><td>{k}</td><td className="text-right">{fmtAmount(v)}</td></tr>))}
                <tr className="border-t border-border"><td>Total</td>
                  <td className="text-right">{fmtAmount(s.total)}</td></tr>
                {s.total == null && s.total_supported != null && (
                  <tr><td>Total of the items it could bill</td>
                    <td className="text-right">{fmtAmount(s.total_supported)}</td></tr>
                )}
              </>
            )}
          </tbody>
        </table>
      ))}
      {[...new Set(Object.entries(bill.per_period ?? {}).flatMap(([p, v]) =>
        Object.entries(((v ?? {}) as { notes?: Record<string, string[]> }).notes ?? {})
          .map(([item, notes]) => `${p === '_' ? '' : `${p} `}${item}: ${notes.join(', ')}`)))]
        .slice(0, 12).map(n => (
          <p key={n} className="text-muted" data-testid="tb-preview-note">{n}</p>))}
      {bill.flags.filter(f => f !== 'preview_dispatch_not_optimised_for_draft').length > 0 && (
        <p className="text-warn">Flags: {bill.flags.filter(f => f !== 'preview_dispatch_not_optimised_for_draft').join(', ')}</p>
      )}
    </div>
  )
}

export default function TariffBuilder({ initial, onSaved }: {
  initial: Tariff; onSaved?: (where: 'library' | 'project') => void
}) {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const [t, setT] = useState<Tariff>(() => structuredClone(initial))
  const [errors, setErrors] = useState<Record<string, string[]>>({})
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const [preview, setPreview] = useState<BillingPayload | null>(null)
  const [libName, setLibName] = useState(initial.id)
  const setItem = (i: number, it: Item) => setT({ ...t, items: t.items.map((x, j) => (j === i ? it : x)) })

  const fail = (e: unknown, what: string) => {
    const r = (e as { response?: { status?: number; data?: { detail?: unknown } } })?.response
    if (r?.status === 422) {
      const byField = errorsByField(r.data?.detail)
      setErrors(byField)
      const d = r.data?.detail
      // A Library 422's detail is a plain string (review #4).
      const why = typeof d === 'string' ? d : (d as { message?: string } | undefined)?.message
      setMessage({ tone: 'error', text: Object.keys(byField).length
        ? `${what}: the server refused some fields (shown below).` : `${what}: ${why ?? 'refused'}` })
      return
    }
    // A typed error (a solve running, no commercial config) says why (review #7).
    const detail = (r?.data?.detail as { message?: string } | undefined)?.message
    setMessage({ tone: 'error', text: `${what} failed${detail ? `: ${detail}`
      : e instanceof Error && !r ? `: ${e.message}` : '.'}` })
  }
  // key → the field's current label, while it does not parse.
  const [invalid, setInvalid] = useState<Map<string, string>>(new Map())
  const reportList = useState(() => (key: string, label: string | null) => setInvalid(prev => {
    if ((prev.get(key) ?? null) === label) return prev
    const next = new Map(prev)
    if (label) next.set(key, label); else next.delete(key)
    return next
  }))[0]
  const blocked = invalid.size > 0
  const draft = () => normaliseTariff(t)

  return (
    <InvalidLists.Provider value={reportList}>
    <div className="space-y-3 text-[11px]" data-testid="tariff-builder">
      {blocked && <p role="alert" className="text-danger" data-testid="tb-blocked">
        Fix the lists that are not numbers ({[...invalid.values()].join(', ')}) before previewing or saving.</p>}
      <div className="flex flex-wrap gap-2 items-center">
        <label>id <input aria-label="Tariff id" className={`${input} w-28`} value={t.id}
                         onChange={e => setT({ ...t, id: e.target.value })} /></label>
        <label>name <input aria-label="Tariff name" className={`${input} w-40`} value={t.name}
                           onChange={e => setT({ ...t, name: e.target.value })} /></label>
        <label>jurisdiction <input aria-label="Jurisdiction" className={`${input} w-14`} value={t.jurisdiction}
                                   onChange={e => setT({ ...t, jurisdiction: e.target.value })} /></label>
        <label>valid from <input type="date" aria-label="Valid from" className={input} value={t.valid_from}
                                 onChange={e => setT({ ...t, valid_from: e.target.value })} /></label>
      </div>
      <Errors list={Object.entries(errors).filter(([k]) => !/^items\.\d+/.test(k))
        .flatMap(([k, msgs]) => msgs.map(m => `${k || 'tariff'}: ${m}`))} />
      {t.items.map((item, i) => (
        <ItemEditor key={i} item={item} idx={i} set={it => setItem(i, it)}
                    remove={() => setT({ ...t, items: t.items.filter((_, j) => j !== i) })}
                    errors={errorsUnder(errors, `items.${i}`)} />
      ))}
      <div className="flex flex-wrap gap-2">
        {KINDS.map(k => (
          <button key={k} type="button" className="underline"
                  onClick={() => setT({ ...t, items: [...t.items, blankItem(k, nextId(t, k))] })}>
            Add {k.replace('_', ' ')} item</button>
        ))}
      </div>
      {message && <p role="status" className={message.tone === 'ok' ? 'text-success' : 'text-danger'}>
        {message.text}</p>}
      <div className="flex flex-wrap gap-2 items-center">
        <button type="button" className="px-2 py-1 rounded border border-border" disabled={blocked}
                onClick={async () => {
          setMessage(null); setErrors({})
          try {
            const bill = await commercialApi.previewBilling(draft())
            setPreview(bill)
            if (!bill) setMessage({ tone: 'error', text: 'No bill to preview: solve the project first.' })
          } catch (e) { setPreview(null); fail(e, 'The preview') }
        }}>Preview the bill</button>
        <label>Library name <input aria-label="Library name" className={`${input} w-28`} value={libName}
                                   onChange={e => setLibName(e.target.value)} /></label>
        <button type="button" className="px-2 py-1 rounded border border-border"
                disabled={!libName.trim() || blocked}
                onClick={async () => {
                  setMessage(null); setErrors({})
                  try {
                    const ref = await libraryApi.putItem('tariff', libName.trim(), draft())
                    setMessage({ tone: 'ok', text: `Saved to the Library as ${ref.id} v${ref.version}.` })
                    await qc.invalidateQueries({ queryKey: nk(project, 'library') })
                    onSaved?.('library')
                  } catch (e) { fail(e, 'Saving to the Library') }
                }}>Save to Library</button>
        <button type="button" className="px-2 py-1 rounded bg-accent text-on-accent disabled:opacity-50"
                disabled={blocked} onClick={async () => {
          setMessage(null); setErrors({})
          try {
            // Inline: the draft replaces the project's tariff and any Library
            // ref (an edited copy is no longer that Library item).
            await commercialApi.saveCommercial({ import_tariff: draft(), import_tariff_ref: null } as never)
            setMessage({ tone: 'ok', text: 'Saved as the project\'s import tariff. Re-solve for a new dispatch.' })
            await qc.invalidateQueries({ queryKey: nk(project, 'results') })
            await qc.invalidateQueries({ queryKey: nk(project, 'commercial') })
            onSaved?.('project')
          } catch (e) { fail(e, 'Saving as the project tariff') }
        }}>Save as the project tariff</button>
      </div>
      {preview && <Preview bill={preview} />}
    </div>
    </InvalidLists.Provider>
  )
}

/** Every period carries the same tier rates (one set can replace them). */
function sameTierRates(item: Item): boolean {
  const sets = item.periods.map(p => JSON.stringify(p.tier_rates ?? null))
  return sets.every(x => x === sets[0])
}

/** The next unused `<kind>_<n>` item id (review #6). */
function nextId(t: Tariff, kind: string): string {
  const taken = new Set(t.items.map(i => i.id))
  let n = t.items.length + 1
  while (taken.has(`${kind}_${n}`)) n += 1
  return `${kind}_${n}`
}

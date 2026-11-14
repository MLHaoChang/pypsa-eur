/**
 * The business case's line table (business-case 09, spec §4 and §7): one row
 * per line, the S columns as selects (only the units the source allows), the T
 * columns as typed cells, a per-year grid with paste from a spreadsheet, "Add
 * line" with the template's offered rows, "Apply preset", provenance chips, the
 * locked rows from the energy model beside the table, and every refusal on its
 * row and column (G9). Saved through `PUT /results/business_case/inputs` with
 * If-Match, as the finance inputs are.
 */
import { Fragment, useEffect, useId, useState, type ClipboardEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  businessCaseApi, BusinessCaseRejected, SolverInFlightError, StaleEditError,
  type BusinessCaseConfig, type BusinessCaseOptions, type LineItem, type LineKind, type LockedRow,
} from '../../../api/businessCase'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { openPanel } from '../openPanel'
import { ACTION } from './actionStyle'
import { NumInput } from './NumInput'
import {
  blankLine, edited, hintUnitWords, linkedOutputId, priceUnitWords, unitWords, linkedSource, OUTPUTS_REASON_WORDS,
  parsePaste, placeRefusals, placeValidation, provenanceDetail, provenanceOf,
  REFUSALS_REASON_WORDS, sourceKind, uniqueName, unitNotAllowed, unitsFor, withKind,
  yearsOf, type Placed,
} from './businessCaseModel'

const KINDS: LineKind[] = ['revenue', 'opex', 'capex']
const KIND_WORD: Record<LineKind, string> = { revenue: 'Revenue', opex: 'Operating cost', capex: 'Capital cost' }
const words = (id: string) => id.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase())
const CELL = 'border border-border rounded px-1 py-0.5 bg-bg text-[11px]'
const BAD = ' border-danger'

/** The stream words for the locked rows (the ledger's stream ids, in words). */
const STREAM_WORD: Record<string, string> = {
  bill: 'Energy bill', export: 'Export sales', ppa: 'PPA', cfd: 'CfD settlement',
  eaas_fee: 'Energy-as-a-service fee', ancillary: 'Ancillary services',
  fom: 'Fixed O&M (energy assets)', vom: 'Variable O&M (energy assets)',
  lost_production: 'Lost production',
}

function optionsErrorWords(e: unknown): string {
  const code = (e as { response?: { data?: { detail?: { code?: string } } } })?.response?.data?.detail?.code
  return code === 'solver_in_flight' ? 'a solve is running'
    : code === 'network_busy' ? 'the model is busy' : 'they could not be read'
}

function cellRefusal(placed: Placed[], col: string): Placed | undefined {
  return placed.find(p => p.column === col)
}

function SourceSelect({ line, opts, onChange, invalid, label }: {
  line: LineItem; opts: BusinessCaseOptions | null; invalid: boolean; label: string
  onChange: (qs: string) => void
}) {
  const outputs = opts?.outputs ?? []
  const current = line.quantity_source
  const known = current === 'typed' || current.startsWith('share_of:')
    || outputs.some(o => linkedSource(o.id, opts?.catalogue_version ?? '1') === current)
  return (
    <select aria-label={label} className={CELL + ' max-w-[11rem]' + (invalid ? BAD : '')} value={current}
            aria-invalid={invalid || undefined} onChange={e => onChange(e.target.value)}>
      <option value="typed">Typed</option>
      {outputs.length > 0 && (
        <optgroup label="From the model">
          {outputs.map(o => (
            <option key={o.id} value={linkedSource(o.id, opts?.catalogue_version ?? '1')}>{o.label}</option>
          ))}
        </optgroup>
      )}
      <optgroup label="Share of">
        {KINDS.map(k => <option key={k} value={`share_of:${k}`}>Share of all {KIND_WORD[k].toLowerCase()}</option>)}
        {KINDS.flatMap(k => (opts?.categories[k] ?? []).map(c => (
          <option key={`${k}/${c}`} value={`share_of:${k}/${c}`}>Share of {KIND_WORD[k].toLowerCase()} · {words(c)}</option>
        )))}
      </optgroup>
      {!known && <option value={current}>
        {sourceKind(current) === 'linked' ? `Model output ${linkedOutputId(current)} (not in this model)` : current}
      </option>}
    </select>
  )
}

function YearGrid({ line, years, onChange, placed }: {
  line: LineItem; years: number[]; placed: Placed[]
  onChange: (patch: Partial<LineItem>) => void
}) {
  const [note, setNote] = useState<string | null>(null)
  const rows: { col: 'quantity_by_year' | 'price_by_year'; label: string }[] = [
    ...(sourceKind(line.quantity_source) === 'linked' ? [] : [{ col: 'quantity_by_year' as const, label: 'Quantity' }]),
    { col: 'price_by_year', label: 'Price' },
  ]
  if (sourceKind(line.quantity_source) === 'share_of') rows.shift()
  if (years.length === 0) {
    return <p className="text-muted" data-testid="bc-grid-no-years">
      Per-year values need the line’s start and end year, or a run investment case for its years.</p>
  }
  const paste = (col: 'quantity_by_year' | 'price_by_year', year: number) =>
    (e: ClipboardEvent<HTMLInputElement>) => {
      const text = e.clipboardData.getData('text')
      if (!/[\t\n]/.test(text.trim())) return            // one value: the input takes it
      e.preventDefault()
      const got = parsePaste(text, years, year)
      onChange({ [col]: { ...(line[col] ?? {}), ...got.values } })
      const parts = [`${Object.keys(got.values).length} pasted`]
      if (got.bad.length) parts.push(`${got.bad.length} not a number (${got.bad.join(', ')}), left as they were`)
      if (got.extra) parts.push(`${got.extra} past ${years[years.length - 1]}, not used`)
      setNote(parts.join(' · '))
    }
  return (
    <div className="space-y-1" data-testid="bc-year-grid">
      <p className="text-muted">A year’s value replaces the row’s value for that year; a price cell is in the line’s money year
        and is not escalated. Paste a row or column from a spreadsheet into any cell.</p>
      <div className="overflow-x-auto">
        <table className="text-[11px]">
          <thead><tr><th className="text-left pr-2 font-normal text-muted">Year</th>
            {years.map(y => <th key={y} className="font-normal text-muted px-0.5">{y}</th>)}</tr></thead>
          <tbody>
            {rows.map(({ col, label }) => {
              const bad = cellRefusal(placed, col)
              return (
                <tr key={col}>
                  <th scope="row" className="text-left pr-2 font-normal">{label}</th>
                  {years.map(y => (
                    <td key={y} className="px-0.5">
                      <NumInput aria-label={`${label} ${y}, ${line.name}`} className={CELL + ' w-16' + (bad ? BAD : '')}
                                value={line[col]?.[String(y)] ?? null}
                                onPaste={paste(col, y)}
                                onChange={v => {
                                  const next = { ...(line[col] ?? {}) }
                                  if (v === null) delete next[String(y)]; else next[String(y)] = v
                                  onChange({ [col]: next })
                                }} />
                    </td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {note && <p role="status" className="text-muted" data-testid="bc-paste-note">{note}</p>}
    </div>
  )
}

function Details({ line, set, placed, years }: {
  line: LineItem; placed: Placed[]; years: number[]
  set: (patch: Partial<LineItem>) => void
}) {
  const num = (col: keyof LineItem, label: string, extra?: string) => {
    const bad = cellRefusal(placed, col as string)
    return (
      <label className="flex items-center gap-1">
        <span>{label}</span>
        <NumInput aria-label={`${label}, ${line.name}`} className={CELL + ' w-20' + (bad ? BAD : '')}
                  aria-invalid={bad ? true : undefined}
                  value={line[col] ?? null} onChange={v => set({ [col]: v } as Partial<LineItem>)} />
        {extra && <span className="text-muted">{extra}</span>}
      </label>
    )
  }
  return (
    <div className="space-y-2 py-1" data-testid="bc-line-details">
      <div className="flex flex-wrap gap-3 items-center">
        {num('money_year', 'Money year')}
        {line.escalation === 'contract' && num('indexation', 'Indexation', 'per year, as a fraction (0.02 = 2 %)')}
        {sourceKind(line.quantity_source) === 'linked' && num('linked_share', 'Sold share', '0 to 1; empty reads 1')}
        {line.kind === 'capex' && (
          <>
            <label className="flex items-center gap-1">
              <span>Depreciation</span>
              <select aria-label={`Depreciation, ${line.name}`} className={CELL}
                      value={line.depreciation ?? 'straight_line'}
                      onChange={e => set({ depreciation: e.target.value as LineItem['depreciation'] })}>
                <option value="straight_line">Straight line</option>
                <option value="declining_balance">Declining balance</option>
              </select>
            </label>
            {num('life_years', 'Life (years)')}
            {line.depreciation === 'declining_balance' && num('rate', 'Rate', '0 to 1')}
            {num('refresh_years', 'Refresh every (years)')}
          </>
        )}
      </div>
      <label className="flex items-center gap-1">
        <span>Source note</span>
        <input aria-label={`Source note, ${line.name}`} className={CELL + ' flex-1'} value={line.note ?? ''}
               onChange={e => set({ note: e.target.value })} />
      </label>
      {line.preset_rule && (line.preset_rule.requires.length > 0 || line.preset_rule.excludes.length > 0
        || line.preset_rule.price_includes_energy) && (
        <p className="text-muted" data-testid="bc-preset-rule">Preset rule:{' '}
          {[line.preset_rule.requires.length ? `needs capex ${line.preset_rule.requires.map(words).join(', ')}` : '',
            line.preset_rule.excludes.length ? `the project does not own capex ${line.preset_rule.excludes.map(words).join(', ')}` : '',
            line.preset_rule.price_includes_energy ? 'the price includes energy' : '']
            .filter(Boolean).join(' · ')}</p>
      )}
      <YearGrid line={line} years={years} placed={placed} onChange={set} />
    </div>
  )
}

function PriceCell({ line, set, bad, currency }: {
  line: LineItem; bad?: Placed; currency: string | null
  set: (patch: Partial<LineItem>) => void
}) {
  const unit = priceUnitWords(currency, line.unit)
  const empty = line.price == null && Object.keys(line.price_by_year ?? {}).length === 0
  return (
    <div className="space-y-0.5">
      <div className="flex items-center gap-1">
        <NumInput aria-label={`Price, ${line.name}`} className={CELL + ' w-20' + (bad ? BAD : '')}
                  aria-invalid={bad ? true : undefined}
                  value={line.price ?? null} onChange={v => set({ price: v })} />
        <span className="text-muted whitespace-nowrap">{unit}</span>
      </div>
      {line.kind === 'revenue' && empty && (
        <p className="text-warn" data-testid="bc-not-established">Not established</p>
      )}
      {line.kind === 'revenue' && line.hint && (
        <p className="text-muted max-w-[12rem]" data-testid="bc-hint">
          Public range {line.hint.low}–{line.hint.high} {hintUnitWords(line.hint.unit)} · {line.hint.source}, {line.hint.year}
        </p>
      )}
    </div>
  )
}

function LockedRows({ locked, ran }: { locked: LockedRow[] | null; ran: boolean }) {
  return (
    <section aria-labelledby="bc-locked-h" className="border border-border rounded p-2 space-y-1"
             data-testid="bc-locked">
      <h4 id="bc-locked-h" className="font-semibold">From the energy model</h4>
      <p className="text-muted">Locked: the investment case’s energy streams, carried unchanged.</p>
      {!ran ? (
        <p className="text-muted" data-testid="bc-locked-none">These rows list after the first business-case run.</p>
      ) : (locked ?? []).length === 0 ? (
        <p className="text-muted">The investment case carries no energy streams.</p>
      ) : (
        <ul className="space-y-0.5">
          {(locked ?? []).map(r => (
            <li key={`${r.stream}/${r.key}`} data-testid="bc-locked-row">
              <span>{STREAM_WORD[r.stream] ?? words(r.stream)}</span>
              {r.key && r.key !== r.stream && <span className="text-muted"> · {r.key}</span>}
              {r.superseded_by.length > 0 && (
                <span className="text-muted"> · replaced in {r.dropped.length} year{r.dropped.length === 1 ? '' : 's'} by {r.superseded_by.join(', ')}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export default function BusinessCaseLines() {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const addId = useId()
  const inputsKey = nk(project, 'results', 'business_case', 'inputs')
  const optionsKey = nk(project, 'results', 'business_case', 'options')
  const inputs = useQuery({ queryKey: inputsKey, queryFn: () => businessCaseApi.getInputs() })
  const options = useQuery({ queryKey: optionsKey, queryFn: () => businessCaseApi.getOptions() })
  const study = useQuery({ queryKey: nk(project, 'results', 'business_case'), queryFn: () => businessCaseApi.getStudy() })
  const [draft, setDraft] = useState<BusinessCaseConfig | null | undefined>(undefined)
  const [digest, setDigest] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [open, setOpen] = useState<Set<number>>(new Set())
  const [variant, setVariant] = useState('')
  const [confirmPreset, setConfirmPreset] = useState(false)
  const [rejected, setRejected] = useState<Placed[]>([])
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string; finance?: boolean } | null>(null)
  const [confirmDiscard, setConfirmDiscard] = useState(false)
  const [stale, setStale] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (inputs.data && draft === undefined && !inputs.isFetching) {
      setDraft(inputs.data.business_case ? structuredClone(inputs.data.business_case) : null)
      setDigest(inputs.data.digest)
    }
  }, [inputs.data, inputs.isFetching, draft])

  const opts = options.data ?? null
  const variants = Object.entries(opts?.variants ?? {})
  useEffect(() => {
    if (!variant && variants.length) setVariant(variants[0][0])
  }, [variant, variants])

  if (inputs.isError) {
    return <p className="text-[11px] text-warn py-2" data-testid="bc-load-error">
      {inputs.error instanceof SolverInFlightError
        ? 'A solve is running: the business-case lines can be edited after it.'
        : 'The business-case lines could not be read: reload before editing them.'}</p>
  }
  if (draft === undefined) return <p className="text-[11px] text-muted py-2">Loading the business-case lines…</p>

  const lines = draft?.inputs.lines ?? []
  const names = lines.map(l => l.name)
  const currency = (study.data?.report?.currency as string | undefined) ?? null
  const setLines = (next: LineItem[]) => {
    setDraft(d => ({ ...(d ?? {}), inputs: { ...(d?.inputs ?? {}), lines: next } }))
    setDirty(true); setRejected([]); setMessage(null)
  }
  const setLine = (i: number, patch: Partial<LineItem>) =>
    setLines(lines.map((l, k) => (k === i ? edited(l, patch) : l)))

  // Refusals of the stored lines sit on their row only while the table is what was stored.
  const placed: Placed[] = dirty ? rejected : [...rejected, ...placeRefusals(opts?.refusals)]
  const placedOn = (i: number) => placed.filter(p => p.line === i)
  const caseLevel = placed.filter(p => p.line < 0 || p.line >= lines.length)

  const reload = async () => {
    setStale(false); setRejected([]); setMessage(null)
    try {
      const fresh = await qc.fetchQuery({ queryKey: inputsKey, queryFn: () => businessCaseApi.getInputs(), staleTime: 0 })
      setDraft(fresh.business_case ? structuredClone(fresh.business_case) : null)
      setDigest(fresh.digest); setDirty(false)
    } catch {
      setMessage({ tone: 'error', text: 'The business-case lines could not be reloaded.' })
    }
  }
  const save = async () => {
    if (saving || !draft) return
    setSaving(true); setRejected([]); setMessage(null)
    try {
      const out = await businessCaseApi.putInputs(draft, digest ?? '')
      setDigest(out.digest)
      if (out.business_case) setDraft(structuredClone(out.business_case))
      setDirty(false)
      setMessage({ tone: 'ok', text: 'Lines saved.' })
      await qc.invalidateQueries({ queryKey: inputsKey })
      await qc.invalidateQueries({ queryKey: optionsKey })
    } catch (e) {
      if (e instanceof StaleEditError) { setStale(true); return }
      if (e instanceof SolverInFlightError) {
        setMessage({ tone: 'error', text: 'Not saved: a solve is running; save after it finishes.' }); return
      }
      if (e instanceof BusinessCaseRejected) {
        setRejected(placeValidation(e.detail.errors))
        setMessage({ tone: 'error', text: 'Not saved: some cells were refused (shown on their rows).' })
        return
      }
      setMessage({ tone: 'error', text: 'The business-case lines could not be saved.' })
    } finally { setSaving(false) }
  }
  const applyPreset = async () => {
    setConfirmPreset(false); setMessage(null)
    try {
      const got = await businessCaseApi.getPreset(variant || undefined)
      if (!got.business_case) return
      setDraft(d => ({ ...(d ?? {}), inputs: got.business_case!.inputs }))
      setDirty(true); setRejected([]); setOpen(new Set())
      setMessage({ tone: 'ok', text: 'Preset applied. Review the lines, then save.' })
    } catch (e) {
      const code = e instanceof BusinessCaseRejected ? e.detail.code : undefined
      setMessage(code === 'finance_inputs_missing'
        ? { tone: 'error', finance: true,
            text: 'Preset not applied: set the finance inputs first (their currency prices the cost defaults).' }
        : { tone: 'error', text: `Preset not applied: ${e instanceof Error ? e.message : 'the preset could not be read'}.` })
    }
  }
  const add = (value: string) => {
    if (!value) return
    if (value.startsWith('blank:')) {
      setLines([...lines, blankLine(value.slice(6) as LineKind, names, opts)])
    } else {
      const row = opts?.offered_rows[Number(value)]
      if (!row) return
      const line = structuredClone(row.line)
      line.name = uniqueName(line.name, names)
      setLines([...lines, line])
    }
  }
  const toggle = (i: number) => setOpen(prev => {
    const next = new Set(prev); if (next.has(i)) next.delete(i); else next.add(i); return next
  })
  const remove = (i: number) => {
    setLines(lines.filter((_, k) => k !== i))
    setOpen(new Set())
  }
  const horizon = opts?.horizon ?? null
  const ran = !!study.data?.report

  return (
    <div className="space-y-3 text-[11px]" data-testid="bc-lines">
      <p className="text-muted">The project on its own: its own revenue and costs. Never added to the investment case,
        which is versus the site today.</p>
      {stale && (
        <div role="alert" className="text-warn" data-testid="bc-stale">
          The lines changed elsewhere since you opened them; your edits were not saved.{' '}
          <button type="button" className={ACTION} onClick={() => { void reload() }}>Reload the lines</button>
        </div>
      )}
      {inputs.data?.status === 'business_case_inputs_invalid' && (
        <p role="alert" className="text-warn" data-testid="bc-stored-invalid">
          The stored lines do not read{inputs.data.message ? ` (${inputs.data.message})` : ''}: fix them and save.</p>
      )}
      <div className="flex flex-wrap gap-2 items-center" data-testid="bc-toolbar">
        {variants.length > 1 && (
          <select aria-label="Preset variant" className={CELL} value={variant} onChange={e => setVariant(e.target.value)}>
            {variants.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select>
        )}
        <button type="button" className={ACTION} disabled={!opts?.template_id || variants.length === 0}
                onClick={() => (lines.length ? setConfirmPreset(true) : void applyPreset())}>Apply preset</button>
        <label className="flex items-center gap-1">
          <span className="sr-only">Add line</span>
          <select id={addId} aria-label="Add line" className={CELL} value=""
                  onChange={e => add(e.target.value)} data-testid="bc-add">
            <option value="">Add line…</option>
            {(opts?.offered_rows.length ?? 0) > 0 && (
              <optgroup label="Offered for this template">
                {opts!.offered_rows.map((r, k) => <option key={k} value={String(k)}>{r.label}</option>)}
              </optgroup>
            )}
            <optgroup label="Blank line">
              {KINDS.map(k => <option key={k} value={`blank:${k}`}>{KIND_WORD[k]}</option>)}
            </optgroup>
          </select>
        </label>
        {opts && !opts.template_id && <span className="text-muted" data-testid="bc-no-preset">
          No preset: this project was not made from a template.</span>}
        {opts?.template_id && variants.length === 0 && <span className="text-muted" data-testid="bc-no-preset">
          No preset for this template: add the lines yourself.</span>}
      </div>
      {confirmPreset && (
        <div role="alert" className="text-warn flex flex-wrap gap-2 items-center" data-testid="bc-preset-confirm">
          Replace the {lines.length} line{lines.length === 1 ? '' : 's'} with the preset?{dirty ? ' Your unsaved changes to them are lost.' : ''}
          <button type="button" className={ACTION} onClick={() => { void applyPreset() }}>Replace</button>
          <button type="button" className={ACTION} onClick={() => setConfirmPreset(false)}>Cancel</button>
        </div>
      )}
      <p className="text-muted" data-testid="bc-help">
        Energy bought or sold in the model (the bill, export, PPA and CfD) is in the rows from the energy model: don’t type it
        again here. Revenue prices start empty; a public range is shown beside them, never filled in.</p>
      {options.isError && (
        <p className="text-muted" data-testid="bc-options-error">
          Model outputs and line checks are not offered now: {optionsErrorWords(options.error)}.{' '}
          <button type="button" className={ACTION} onClick={() => { void options.refetch() }}>Read again</button></p>
      )}
      {opts?.outputs === null && opts.outputs_reason && (
        <p className="text-muted" data-testid="bc-outputs-reason">
          Model outputs are not offered now: {OUTPUTS_REASON_WORDS[opts.outputs_reason] ?? words(opts.outputs_reason)}.</p>
      )}
      <div className="space-y-3">
        <div className="min-w-0 overflow-x-scroll">
          {lines.length === 0 ? (
            <p className="text-muted py-2" data-testid="bc-empty">No lines yet: apply the preset or add a line.</p>
          ) : (
            <table className="text-[11px] w-full" data-testid="bc-table">
              <thead>
                <tr className="text-left text-muted">
                  <th className="font-normal px-1 sticky left-0 bg-bg z-10 min-w-[11rem] shadow-[1px_0_0_var(--color-border)]">Name</th>
                  {['Kind', 'Category', 'Quantity from', 'Quantity', 'Unit', 'Price', 'Escalation', 'Years', 'Basis']
                    .map(h => <th key={h} className="font-normal px-1">{h}</th>)}
                  <th className="font-normal px-1"><span className="sr-only">Remove</span></th>
                </tr>
              </thead>
              <tbody>
                {lines.map((ln, i) => {
                  const here = placedOn(i)
                  const bad = (c: string) => cellRefusal(here, c)
                  const src = sourceKind(ln.quantity_source)
                  const unitBad = bad('unit') ?? (unitNotAllowed(ln, opts)
                    ? { line: i, column: 'unit', kind: 'unit_not_allowed', message: 'Unit: not offered for this quantity source' }
                    : undefined)
                  const prov = provenanceOf(ln)
                  const shown = unitBad && !here.includes(unitBad) ? [...here, unitBad] : here
                  return (
                    <Fragment key={i}>
                      <tr className="align-top border-t border-border" data-testid="bc-row">
                        <td className="px-1 sticky left-0 bg-bg z-10 whitespace-nowrap min-w-[11rem] shadow-[1px_0_0_var(--color-border)]">
                          <button type="button" className={ACTION + ' mr-1'} aria-expanded={open.has(i)}
                                  aria-label={`${open.has(i) ? 'Hide' : 'Show'} details of ${ln.name}`}
                                  onClick={() => toggle(i)}>{open.has(i) ? '▾' : '▸'}</button>
                          <input aria-label={`Name, line ${i + 1}`} className={CELL + ' w-32' + (bad('name') ? BAD : '')}
                                 maxLength={80} value={ln.name} aria-invalid={bad('name') ? true : undefined}
                                 onChange={e => setLine(i, { name: e.target.value })} />
                        </td>
                        <td className="px-1">
                          <select aria-label={`Kind, ${ln.name}`} className={CELL} value={ln.kind}
                                  onChange={e => setLines(lines.map((l, k) => (k === i ? withKind(l, e.target.value as LineKind, opts) : l)))}>
                            {KINDS.map(k => <option key={k} value={k}>{KIND_WORD[k]}</option>)}
                          </select>
                        </td>
                        <td className="px-1">
                          <select aria-label={`Category, ${ln.name}`} className={CELL + ' max-w-[8rem]' + (bad('category') ? BAD : '')}
                                  aria-invalid={bad('category') ? true : undefined}
                                  value={ln.category} onChange={e => setLine(i, { category: e.target.value })}>
                            {(opts?.categories[ln.kind] ?? [ln.category]).map(c => <option key={c} value={c}>{words(c)}</option>)}
                          </select>
                        </td>
                        <td className="px-1">
                          <SourceSelect line={ln} opts={opts} invalid={!!bad('quantity_source')}
                                        label={`Quantity from, ${ln.name}`}
                                        onChange={qs => {
                                          const next = { ...ln, quantity_source: qs }
                                          const units = unitsFor({ ...next, unit: '' }, opts)
                                          setLine(i, { quantity_source: qs,
                                                       unit: units.includes(ln.unit) ? ln.unit : (units[0] ?? ln.unit),
                                                       ...(sourceKind(qs) === 'linked' ? { quantity: null } : {}) })
                                        }} />
                        </td>
                        <td className="px-1">
                          {src === 'linked' ? <span className="text-muted" data-testid="bc-from-model">From the model</span> : (
                            <NumInput aria-label={`${src === 'share_of' ? 'Share' : 'Quantity'}, ${ln.name}`}
                                      className={CELL + ' w-20' + (bad('quantity') ? BAD : '')}
                                      aria-invalid={bad('quantity') ? true : undefined}
                                      value={ln.quantity ?? null} onChange={v => setLine(i, { quantity: v })} />
                          )}
                        </td>
                        <td className="px-1">
                          <select aria-label={`Unit, ${ln.name}`} className={CELL + (unitBad ? BAD : '')}
                                  aria-invalid={unitBad ? true : undefined}
                                  value={ln.unit} onChange={e => setLine(i, { unit: e.target.value })}>
                            {unitsFor(ln, opts).map(u => <option key={u} value={u}>{unitWords(u)}</option>)}
                          </select>
                        </td>
                        <td className="px-1">
                          {src === 'share_of' ? <span className="text-muted">No price: a share</span>
                            : <PriceCell line={ln} bad={bad('price')} currency={currency} set={p => setLine(i, p)} />}
                        </td>
                        <td className="px-1">
                          <select aria-label={`Escalation, ${ln.name}`} className={CELL} value={ln.escalation}
                                  onChange={e => setLine(i, { escalation: e.target.value })}>
                            {(opts?.escalations ?? [ln.escalation]).map(x => <option key={x} value={x}>{words(x)}</option>)}
                          </select>
                        </td>
                        <td className="px-1 whitespace-nowrap">
                          <NumInput aria-label={`Start year, ${ln.name}`} placeholder={horizon ? String(horizon[0]) : 'COD'}
                                    className={CELL + ' w-14' + (bad('start_year') ? BAD : '')}
                                    value={ln.start_year ?? null} onChange={v => setLine(i, { start_year: v })} />
                          <span className="text-muted">–</span>
                          <NumInput aria-label={`End year, ${ln.name}`} placeholder={horizon ? String(horizon[1]) : 'end'}
                                    className={CELL + ' w-14' + (bad('end_year') ? BAD : '')}
                                    value={ln.end_year ?? null} onChange={v => setLine(i, { end_year: v })} />
                        </td>
                        <td className="px-1">
                          <span className={`rounded px-1 ${prov === 'assumed' ? 'bg-warn/15 text-warn' : 'bg-accent/10 text-accent'}`}
                                title={prov === 'assumed' ? provenanceDetail(ln).join('\n') : 'Typed by you'}
                                data-testid="bc-provenance">{prov === 'assumed' ? 'Assumed' : 'Stated'}</span>
                        </td>
                        <td className="px-1">
                          <button type="button" className={ACTION} aria-label={`Remove ${ln.name}`}
                                  onClick={() => remove(i)}>Remove</button>
                        </td>
                      </tr>
                      {shown.length > 0 && (
                        <tr data-testid="bc-row-refusals">
                          <td colSpan={11} className="px-1 pb-1">
                            <ul className="text-danger sticky left-0 max-w-[56rem]">
                              {shown.map((p, k) => (
                                <li key={k} data-column={p.column}>
                                  {p.message}{' '}
                                  {!open.has(i) && ['money_year', 'indexation', 'life_years', 'rate', 'refresh_years',
                                    'quantity_by_year', 'price_by_year', 'linked_share'].includes(p.column) && (
                                    <button type="button" className="underline" onClick={() => toggle(i)}>Show details</button>
                                  )}
                                </li>
                              ))}
                            </ul>
                          </td>
                        </tr>
                      )}
                      {open.has(i) && (
                        <tr>
                          <td colSpan={11} className="px-1 pb-2">
                            <div className="sticky left-0 max-w-[56rem]">
                            <Details line={ln} placed={here} years={yearsOf(ln, horizon)} set={p => setLine(i, p)} />
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          )}
          <p className="text-muted mt-1" data-testid="bc-chip-key">
            <span className="rounded px-1 bg-accent/10 text-accent">Stated</span> typed by you ·{' '}
            <span className="rounded px-1 bg-warn/15 text-warn">Assumed</span> a preset default (its source and year on hover;
            editing it makes it stated)</p>
          {caseLevel.length > 0 && (
            <ul className="text-danger" data-testid="bc-case-refusals">
              {caseLevel.map((p, k) => <li key={k}>{p.message}</li>)}
            </ul>
          )}
          {!dirty && opts?.refusals === null && opts.refusals_reason && lines.length > 0 && (
            <p className="text-muted" data-testid="bc-refusals-reason">
              {REFUSALS_REASON_WORDS[opts.refusals_reason] ?? words(opts.refusals_reason)}</p>
          )}
          {dirty && <p className="text-muted" data-testid="bc-unsaved">Unsaved changes: save to check the lines.</p>}
        </div>
        <div className="max-w-xl">
          <LockedRows locked={study.data?.report?.locked ?? null} ran={ran} />
        </div>
      </div>
      {message && <p role="status" className={message.tone === 'ok' ? 'text-success' : 'text-danger'}>{message.text}
        {message.finance && <>{' '}<button type="button" className="underline" data-testid="bc-open-finance"
          onClick={() => openPanel('commercial', 'economics')}>Finance inputs ›</button></>}</p>}
      <div className="flex flex-wrap gap-2 items-center">
        <button type="button" className="px-2 py-1 rounded bg-accent text-on-accent disabled:opacity-50"
                disabled={!dirty || saving || draft === null} onClick={() => { void save() }}>Save the lines</button>
        <button type="button" className={ACTION} disabled={!dirty} onClick={() => setConfirmDiscard(true)}>Discard changes</button>
        {!dirty && <span className="text-muted">No unsaved changes.</span>}
        {dirty && confirmDiscard && (
          <span role="alert" className="text-warn flex flex-wrap gap-2 items-center" data-testid="bc-discard-confirm">
            Discard the unsaved changes to the lines? The stored lines are reloaded.
            <button type="button" className={ACTION} onClick={() => { setConfirmDiscard(false); void reload() }}>Discard</button>
            <button type="button" className={ACTION} onClick={() => setConfirmDiscard(false)}>Keep editing</button>
          </span>
        )}
      </div>
    </div>
  )
}


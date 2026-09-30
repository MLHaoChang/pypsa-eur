// Results → Investment → Contracts (IC P3 WP3.7c): typed forms per P2 contract
// type (generated from `CONTRACT_FIELDS`; party pickers offer the participants,
// externals and contract parties) and the connection agreement (kind, caps,
// envelope series, curtailment, the capacity-fee item through the tariff
// builder's item editor, available_from, group). Library pins are shown. The
// server judges every combination: its 422 is shown at the contract it names.
import { useEffect, useId, useState } from 'react'
import { NumInput } from './NumInput'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { commercialApi, libraryApi } from '../../../api/commercial'
import type { CommercialConfig, CommercialContract, ConnectionAgreement, LibraryRef } from '../../../api/types'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { ItemEditor } from './TariffBuilder'
import { blankItem } from './tariffModel'
import {
  blankContract, CONNECTION, CONTRACT_FIELDS, CONTRACT_TYPES, contractErrors, contractType,
  missingRequired, nextContractId, setField, type ContractType, type FieldSpec,
} from './contractModel'

const input = 'border border-border rounded px-1 py-0.5 text-[11px] bg-bg'

function Errors({ list }: { list: string[] }) {
  if (!list.length) return null
  return <ul role="alert" className="text-[11px] text-danger pl-3 list-disc">
    {list.map(e => <li key={e}>{e}</li>)}</ul>
}

function IdsInput({ label, value, onChange }: {
  label: string; value: string[]; onChange: (v: string[]) => void
}) {
  const shown = value.join(', ')
  const [text, setText] = useState(shown)
  const [focused, setFocused] = useState(false)
  useEffect(() => { if (!focused) setText(shown) }, [shown]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <input aria-label={label} className={`${input} w-56`} value={text}
           onFocus={() => setFocused(true)} onChange={e => setText(e.target.value)}
           onBlur={() => { setFocused(false)
                           onChange(text.split(',').map(x => x.trim()).filter(Boolean)) }} />
  )
}

const seriesKey = (r: LibraryRef) => `${r.id}@${r.version}`

/** A Library series picker; a stored ref the Library no longer lists (an older
 *  version) stays shown as such — never as "none" (WP3.7c review #3). */
function SeriesSelect({ label, value, series, onChange }: {
  label: string; value: LibraryRef | null; series: LibraryRef[]; onChange: (r: LibraryRef | null) => void
}) {
  const listed = !value || series.some(r => seriesKey(r) === seriesKey(value))
  return (
    <select aria-label={label} className={input} value={value ? seriesKey(value) : ''}
            onChange={e => onChange(e.target.value === '' ? null
              : series.find(r => seriesKey(r) === e.target.value) ?? value)}>
      <option value="">none</option>
      {series.map(r => <option key={seriesKey(r)} value={seriesKey(r)}>{r.id} v{r.version}</option>)}
      {!listed && <option value={seriesKey(value!)}>{value!.id} v{value!.version} (not listed)</option>}
    </select>
  )
}

function Field({ spec, contract, label, parties, series, set }: {
  spec: FieldSpec; contract: Record<string, unknown>; label: string; parties: string
  series: LibraryRef[]; set: (value: unknown) => void
}) {
  const v = contract[spec.key]
  const name = `${label} ${spec.label}`
  // An integer field sends what was typed: 2.5 is the server's to refuse,
  // never truncated to 2 (WP3.7c review #6).
  let control
  switch (spec.kind) {
    case 'number': case 'int':
      // NumInput keeps the typed text ("0.0" stays while typing 0.05 — WP4.7
      // review B1, the same controlled-number reset).
      control = <NumInput step={spec.kind === 'int' ? 1 : 'any'} aria-label={name}
                          className={`${input} w-28`} value={v} onChange={set} />
      break
    case 'select':
      control = <select aria-label={name} className={input} value={String(v ?? '')}
                        onChange={e => set(e.target.value || null)}>
        {!spec.required && <option value="">(default)</option>}
        {spec.options!.map(o => <option key={o} value={o}>{o.replace(/_/g, ' ')}</option>)}
      </select>
      break
    case 'bool':
      control = <input type="checkbox" aria-label={name} checked={Boolean(v)}
                       onChange={e => set(e.target.checked || null)} />
      break
    case 'ids':
      control = <IdsInput label={name} value={(v as string[] | undefined) ?? []} onChange={set} />
      break
    case 'series':
      control = <SeriesSelect label={name} value={(v as LibraryRef | null | undefined) ?? null}
                              series={series} onChange={set} />
      break
    default:
      control = <input aria-label={name} className={`${input} w-40`} value={String(v ?? '')}
                       list={spec.kind === 'party' ? parties : undefined}
                       onChange={e => set(e.target.value)} />
  }
  return (
    <label className="flex items-center gap-2">
      <span className="w-48">{spec.label}{spec.required ? '' : ' (optional)'}</span>{control}
    </label>
  )
}

function ConnectionEditor({ current, series, errors, onSave }: {
  current: ConnectionAgreement | null | undefined; series: LibraryRef[]; errors: string[]
  onSave: (c: ConnectionAgreement | null) => Promise<boolean>
}) {
  const [c, setC] = useState<ConnectionAgreement | null>(current ?? null)
  const set = (patch: Partial<ConnectionAgreement>) => setC(prev => ({ ...(prev as ConnectionAgreement), ...patch }))
  const num = (t: string) => (t === '' ? null : Number(t))
  // No cap until one is typed: a firm 0 MW import cap is never a default
  // (the server refuses a missing cap; WP3.7c review #11).
  const noCap = null as unknown as number
  if (!c) {
    return (
      <div className="space-y-1">
        <p className="text-muted">No connection agreement: the import link is uncapped by a contract.</p>
        <button type="button" className="underline" onClick={() => setC({ kind: 'firm', import_cap_mw: noCap,
          available_from: `${new Date().getFullYear()}-01-01` })}>Add a connection agreement</button>
      </div>
    )
  }
  return (
    <fieldset className="border border-border rounded p-2 space-y-1" data-testid="ce-connection">
      <legend className="font-semibold">Connection agreement</legend>
      {c.library_ref && <p className="text-muted" data-testid="ce-connection-pin">
        Copied from Library item {c.library_ref.id} v{c.library_ref.version}.</p>}
      <label className="flex items-center gap-2"><span className="w-48">kind</span>
        <select aria-label="Connection kind" className={input} value={c.kind}
                onChange={e => set({ kind: e.target.value as ConnectionAgreement['kind'] })}>
          <option value="firm">firm</option><option value="non_firm_static">non-firm, static</option>
          <option value="non_firm_dynamic">non-firm, dynamic (envelope)</option>
          <option value="fca">flexible connection (FCA)</option></select></label>
      <label className="flex items-center gap-2"><span className="w-48">import cap (MW)</span>
        <input type="number" step="any" min={0} aria-label="Import cap (MW)" className={`${input} w-28`}
               value={c.import_cap_mw ?? ''}
               onChange={e => set({ import_cap_mw: e.target.value === '' ? noCap : Number(e.target.value) })} /></label>
      <label className="flex items-center gap-2"><span className="w-48">export cap (MW, optional)</span>
        <input type="number" step="any" min={0} aria-label="Export cap (MW)" className={`${input} w-28`}
               value={c.export_cap_mw ?? ''} onChange={e => set({ export_cap_mw: num(e.target.value) })} /></label>
      <label className="flex items-center gap-2"><span className="w-48">envelope series</span>
        <SeriesSelect label="Envelope series" value={c.envelope ?? null} series={series}
                      onChange={r => set({ envelope: r })} /></label>
      <label className="flex items-center gap-2"><span className="w-48">curtailment hours a year</span>
        <input type="number" step="any" min={0} aria-label="Curtailment hours a year" className={`${input} w-28`}
               value={c.curtailment_hours_per_year ?? ''}
               onChange={e => set({ curtailment_hours_per_year: num(e.target.value) })} /></label>
      <label className="flex items-center gap-2"><span className="w-48">curtailment compensation (per MWh)</span>
        <input type="number" step="any" min={0} aria-label="Curtailment compensation per MWh" className={`${input} w-28`}
               value={c.curtailment_compensation_eur_per_mwh ?? ''}
               onChange={e => set({ curtailment_compensation_eur_per_mwh: num(e.target.value) })} /></label>
      <label className="flex items-center gap-2"><span className="w-48">available from</span>
        <input type="date" aria-label="Available from" className={input} value={c.available_from}
               onChange={e => set({ available_from: e.target.value })} /></label>
      <label className="flex items-center gap-2"><span className="w-48">group (optional)</span>
        <input aria-label="Connection group" className={input} value={c.group ?? ''}
               onChange={e => set({ group: e.target.value || null })} /></label>
      {c.capacity_fee ? (
        <>
          <p className="text-muted">The capacity fee is a capacity item, per kW-year or kW-month, with a single all-year period (no windows or tiers).</p>
          <ItemEditor item={c.capacity_fee} idx={0} set={it => set({ capacity_fee: it })}
                      kinds={['capacity']} units={['per_kw_year', 'per_kw_month']}
                      remove={() => set({ capacity_fee: null })} errors={[]} />
        </>
      ) : (
        <button type="button" className="underline"
                onClick={() => set({ capacity_fee: { ...blankItem('capacity', 'capacity_fee') } })}>
          Add a capacity fee</button>
      )}
      <Errors list={errors} />
      <div className="flex gap-2">
        <button type="button" className="px-2 py-1 rounded bg-accent text-on-accent"
                onClick={() => { void onSave(c) }}>Save the connection agreement</button>
        {/* Removed only once the server has removed it (WP3.7c review #4). */}
        <button type="button" className="underline"
                onClick={async () => { if (await onSave(null)) setC(null) }}>
          Remove the connection agreement</button>
      </div>
    </fieldset>
  )
}

/** The form's copy: an untagged P0-era contract gets the type its fields give
 *  (as the server tags it on save), so clearing a field never turns it into
 *  another type mid-edit (WP3.7c round 2). */
const tagged = (list: CommercialContract[]) => structuredClone(list).map(c => (
  (c as { type?: unknown }).type == null ? { ...c, type: contractType(c) } as CommercialContract : c))

export default function ContractsEditor() {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const datalist = useId()
  const commercial = useQuery({ queryKey: nk(project, 'commercial', 'config'),
                                queryFn: () => commercialApi.getCommercial() })
  const flows = useQuery({ queryKey: nk(project, 'value_flows', 'state'),
                           queryFn: () => commercialApi.getValueFlows(), retry: false })
  const series = useQuery({ queryKey: nk(project, 'library', 'series'),
                            queryFn: () => libraryApi.listSeries(), retry: false })
  const [contracts, setContracts] = useState<CommercialContract[] | null>(null)
  // The stored list the edits started from: a save over a list changed since
  // (the assistant, a template's drafts) is refused, never overwritten (review #10).
  const [baseline, setBaseline] = useState<string | null>(null)
  const [errors, setErrors] = useState<Map<number, string[]>>(new Map())
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const [newType, setNewType] = useState<ContractType>('ppa')

  useEffect(() => {
    if (commercial.data !== undefined && contracts === null && !commercial.isFetching) {
      setContracts(tagged(commercial.data?.contracts ?? []))
      setBaseline(JSON.stringify(commercial.data?.contracts ?? []))
    }
  }, [commercial.data, commercial.isFetching, contracts])

  if (commercial.isError) {
    return <p className="text-[11px] text-warn py-2">The project's contracts could not be read; reload before editing them.</p>
  }
  if (commercial.data === null) {
    return <p className="text-[11px] text-muted py-2">Set up the commercial config (the point of connection) before adding contracts.</p>
  }
  if (!contracts || !commercial.data) return <p className="text-[11px] text-muted py-2">Loading the contracts…</p>
  const cfg: CommercialConfig = commercial.data
  const site = cfg.site_party ?? 'site'
  const vf = flows.data?.status === 'ok' ? flows.data.value_flows : null
  const parties = [...new Set([site, ...(vf?.participants ?? []).map(p => p.id), ...(vf?.externals ?? []),
                               ...contracts.flatMap(c => ['seller', 'buyer', 'lessor', 'lessee', 'provider',
                                 'customer', 'retailer', 'counterparty', 'generator_owner', 'sleeving_party']
                                 .map(k => (c as unknown as Record<string, unknown>)[k])
                                 .filter((x): x is string => typeof x === 'string' && !!x))])]
  const refs = series.data ?? []

  const save = async (patch: Partial<CommercialConfig>, what: string): Promise<boolean> => {
    setMessage(null); setErrors(new Map())
    try {
      await commercialApi.saveCommercial(patch as never)
      setMessage({ tone: 'ok', text: `${what} saved. The bill and the ledger follow on the next read; re-solve if a contract changes the dispatch.` })
      await qc.invalidateQueries({ queryKey: nk(project, 'commercial') })
      await qc.invalidateQueries({ queryKey: nk(project, 'results') })
      await qc.invalidateQueries({ queryKey: nk(project, 'value_flows') })
      return true
    } catch (e) {
      const r = (e as { response?: { status?: number; data?: { detail?: unknown } } })?.response
      setErrors(contractErrors(r?.data?.detail ?? (e instanceof Error ? e.message : null), contracts))
      setMessage({ tone: 'error', text: `${what} not saved: the server refused it (see below).` })
      return false
    }
  }
  const saveContracts = async () => {
    // A blank required party or id is refused here (the model takes '').
    const blank = new Map(contracts.map((c, i) => [i, missingRequired(c)] as const).filter(([, m]) => m.length))
    if (blank.size) {
      setErrors(blank)
      setMessage({ tone: 'error', text: 'The contracts not saved: fill in the required fields (see below).' })
      return
    }
    try {
      const stored = await commercialApi.getCommercial()
      if (JSON.stringify(stored?.contracts ?? []) !== baseline) {
        setErrors(new Map())
        setMessage({ tone: 'error', text: 'The contracts not saved: the project\'s contracts changed since they were loaded (the assistant or a template may have added some). Reload them, then redo the edits.' })
        return
      }
    } catch (e) {
      setMessage({ tone: 'error', text: `The contracts not saved: the project could not be read (${e instanceof Error ? e.message : 'error'}).` })
      return
    }
    // After a save the stored form (the server may tag or normalise) is the
    // new baseline; the form keeps its state (the connection editor's included).
    if (await save({ contracts }, 'The contracts')) {
      try { setBaseline(JSON.stringify((await commercialApi.getCommercial())?.contracts ?? [])) }
      catch { setBaseline(null) }                      // unknown: the next save asks for a reload
    }
  }
  const reload = async () => {
    setMessage(null); setErrors(new Map())
    try {
      const fresh = (await commercialApi.getCommercial())?.contracts ?? []
      setContracts(tagged(fresh)); setBaseline(JSON.stringify(fresh))
      await qc.invalidateQueries({ queryKey: nk(project, 'commercial') })
    } catch (e) {
      setMessage({ tone: 'error', text: `The contracts could not be reloaded (${e instanceof Error ? e.message : 'error'}).` })
    }
  }
  const edit = (next: CommercialContract[], clearErrors = false) => {
    setContracts(next)
    // Errors are by position: an added or removed contract moves them (review #7).
    if (clearErrors) setErrors(new Map())
  }

  return (
    <div className="space-y-4 text-[11px]" data-testid="contracts-editor">
      <datalist id={datalist}>{parties.map(p => <option key={p} value={p} />)}</datalist>
      {message && <p role="status" className={message.tone === 'ok' ? 'text-success' : 'text-danger'}>
        {message.text}</p>}
      <Errors list={errors.get(-1) ?? []} />
      {contracts.map((c, i) => {
        const label = `Contract ${c.id || i + 1}`
        const rec = c as unknown as Record<string, unknown>
        // An untagged (P0-era) contract by its fields; an unknown tag is shown
        // read-only and kept unchanged (WP3.7c review #5).
        const type = contractType(c)
        return (
          <fieldset key={i} className="border border-border rounded p-2 space-y-1" data-testid={`ce-contract-${i}`}>
            <legend className="font-semibold">{type ? type.toUpperCase() : String(rec.type ?? 'contract')} {c.id}</legend>
            {c.library_ref && <p className="text-muted">Copied from Library item {c.library_ref.id} v{c.library_ref.version}.</p>}
            {type ? CONTRACT_FIELDS[type].filter(f => !f.when || f.when(rec)).map(f => (
              <Field key={f.key} spec={f} contract={rec} label={label} parties={datalist} series={refs}
                     set={v => edit(contracts.map((x, j) => (j === i ? setField(x, f, v, type) : x)))} />
            )) : (
              <p className="text-muted">A contract type this editor does not know: kept as stored.</p>
            )}
            <Errors list={errors.get(i) ?? []} />
            <button type="button" className="underline" aria-label={`Remove ${label}`}
                    onClick={() => edit(contracts.filter((_, j) => j !== i), true)}>Remove</button>
          </fieldset>
        )
      })}
      <div className="flex items-center gap-2">
        <label>Add a{' '}
          <select aria-label="New contract type" className={input} value={newType}
                  onChange={e => setNewType(e.target.value as ContractType)}>
            {CONTRACT_TYPES.map(t => <option key={t} value={t}>{t.toUpperCase()}</option>)}</select></label>
        <button type="button" className="underline" onClick={() => edit([...contracts,
          blankContract(newType, nextContractId(contracts, newType), site)], true)}>Add contract</button>
        <button type="button" className="px-2 py-1 rounded bg-accent text-on-accent"
                onClick={() => { void saveContracts() }}>Save the contracts</button>
        <button type="button" className="underline" onClick={() => { void reload() }}>
          Reload the contracts</button>
      </div>
      <ConnectionEditor current={cfg.connection} series={refs} errors={errors.get(CONNECTION) ?? []}
                        onSave={conn => save({ connection: conn }, 'The connection agreement')} />
    </div>
  )
}

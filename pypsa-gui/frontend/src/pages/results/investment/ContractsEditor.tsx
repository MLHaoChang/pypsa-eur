// Results → Investment → Contracts (IC P3 WP3.7c): typed forms per P2 contract
// type (generated from `CONTRACT_FIELDS`; party pickers offer the participants,
// externals and contract parties) and the connection agreement (kind, caps,
// envelope series, curtailment, the capacity-fee item through the tariff
// builder's item editor, available_from, group). Library pins are shown. The
// server judges every combination: its 422 is shown at the contract it names.
import { useEffect, useId, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { commercialApi, libraryApi } from '../../../api/commercial'
import type { CommercialConfig, CommercialContract, ConnectionAgreement, LibraryRef } from '../../../api/types'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { ItemEditor } from './TariffBuilder'
import { blankItem } from './tariffModel'
import {
  blankContract, CONTRACT_FIELDS, contractErrors, nextContractId, setField,
  type ContractType, type FieldSpec,
} from './contractModel'

const input = 'border border-border rounded px-1 py-0.5 text-[11px] bg-bg'
const TYPES: ContractType[] = ['ppa', 'cfd', 'dr', 'lease', 'eaas', 'retail']

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

function Field({ spec, contract, label, parties, series, set }: {
  spec: FieldSpec; contract: Record<string, unknown>; label: string; parties: string
  series: LibraryRef[]; set: (value: unknown) => void
}) {
  const v = contract[spec.key]
  const name = `${label} ${spec.label}`
  const num = (text: string, int: boolean) => (text === '' ? null : int ? parseInt(text, 10) : Number(text))
  let control
  switch (spec.kind) {
    case 'number': case 'int':
      control = <input type="number" step={spec.kind === 'int' ? 1 : 'any'} aria-label={name}
                       className={`${input} w-28`} value={v == null ? '' : String(v)}
                       onChange={e => set(num(e.target.value, spec.kind === 'int'))} />
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
      control = <select aria-label={name} className={input}
                        value={v ? seriesKey(v as LibraryRef) : ''}
                        onChange={e => set(series.find(r => seriesKey(r) === e.target.value) ?? null)}>
        <option value="">none</option>
        {series.map(r => <option key={seriesKey(r)} value={seriesKey(r)}>{r.id} v{r.version}</option>)}
        {!!v && !series.some(r => seriesKey(r) === seriesKey(v as LibraryRef)) && (
          <option value={seriesKey(v as LibraryRef)}>{(v as LibraryRef).id} v{(v as LibraryRef).version} (not listed)</option>
        )}
      </select>
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

function ConnectionEditor({ current, series, onSave }: {
  current: ConnectionAgreement | null | undefined; series: LibraryRef[]
  onSave: (c: ConnectionAgreement | null) => Promise<void>
}) {
  const [c, setC] = useState<ConnectionAgreement | null>(current ?? null)
  const set = (patch: Partial<ConnectionAgreement>) => setC(prev => ({ ...(prev as ConnectionAgreement), ...patch }))
  const num = (t: string) => (t === '' ? null : Number(t))
  if (!c) {
    return (
      <div className="space-y-1">
        <p className="text-muted">No connection agreement: the import link is uncapped by a contract.</p>
        <button type="button" className="underline" onClick={() => setC({ kind: 'firm', import_cap_mw: 0,
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
               value={c.import_cap_mw} onChange={e => set({ import_cap_mw: Number(e.target.value) })} /></label>
      <label className="flex items-center gap-2"><span className="w-48">export cap (MW, optional)</span>
        <input type="number" step="any" min={0} aria-label="Export cap (MW)" className={`${input} w-28`}
               value={c.export_cap_mw ?? ''} onChange={e => set({ export_cap_mw: num(e.target.value) })} /></label>
      <label className="flex items-center gap-2"><span className="w-48">envelope series</span>
        <select aria-label="Envelope series" className={input}
                value={c.envelope ? seriesKey(c.envelope) : ''}
                onChange={e => set({ envelope: series.find(r => seriesKey(r) === e.target.value) ?? null })}>
          <option value="">none</option>
          {series.map(r => <option key={seriesKey(r)} value={seriesKey(r)}>{r.id} v{r.version}</option>)}
        </select></label>
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
        <ItemEditor item={c.capacity_fee} idx={0} set={it => set({ capacity_fee: it })}
                    remove={() => set({ capacity_fee: null })} errors={[]} />
      ) : (
        <button type="button" className="underline"
                onClick={() => set({ capacity_fee: { ...blankItem('capacity', 'capacity_fee') } })}>
          Add a capacity fee</button>
      )}
      <div className="flex gap-2">
        <button type="button" className="px-2 py-1 rounded bg-accent text-on-accent"
                onClick={() => onSave(c)}>Save the connection agreement</button>
        <button type="button" className="underline" onClick={async () => { setC(null); await onSave(null) }}>
          Remove the connection agreement</button>
      </div>
    </fieldset>
  )
}

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
  const [errors, setErrors] = useState<Map<number, string[]>>(new Map())
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const [newType, setNewType] = useState<ContractType>('ppa')

  useEffect(() => {
    if (commercial.data !== undefined && contracts === null && !commercial.isFetching) {
      setContracts(structuredClone(commercial.data?.contracts ?? []))
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

  const save = async (patch: Partial<CommercialConfig>, what: string) => {
    setMessage(null); setErrors(new Map())
    try {
      await commercialApi.saveCommercial(patch as never)
      setMessage({ tone: 'ok', text: `${what} saved. The bill and the ledger follow on the next read; re-solve if a contract changes the dispatch.` })
      await qc.invalidateQueries({ queryKey: nk(project, 'commercial') })
      await qc.invalidateQueries({ queryKey: nk(project, 'results') })
      await qc.invalidateQueries({ queryKey: nk(project, 'value_flows') })
    } catch (e) {
      const r = (e as { response?: { status?: number; data?: { detail?: unknown } } })?.response
      const byContract = contractErrors(r?.data?.detail ?? (e instanceof Error ? e.message : null))
      setErrors(byContract)
      setMessage({ tone: 'error', text: `${what} not saved: the server refused it (see below).` })
    }
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
        return (
          <fieldset key={i} className="border border-border rounded p-2 space-y-1" data-testid={`ce-contract-${i}`}>
            <legend className="font-semibold">{c.type.toUpperCase()} {c.id}</legend>
            {c.library_ref && <p className="text-muted">Copied from Library item {c.library_ref.id} v{c.library_ref.version}.</p>}
            {CONTRACT_FIELDS[c.type].filter(f => !f.when || f.when(rec)).map(f => (
              <Field key={f.key} spec={f} contract={rec} label={label} parties={datalist} series={refs}
                     set={v => setContracts(contracts.map((x, j) => (j === i ? setField(x, f, v) : x)))} />
            ))}
            <Errors list={errors.get(i) ?? []} />
            <button type="button" className="underline" aria-label={`Remove ${label}`}
                    onClick={() => setContracts(contracts.filter((_, j) => j !== i))}>Remove</button>
          </fieldset>
        )
      })}
      <div className="flex items-center gap-2">
        <label>Add a{' '}
          <select aria-label="New contract type" className={input} value={newType}
                  onChange={e => setNewType(e.target.value as ContractType)}>
            {TYPES.map(t => <option key={t} value={t}>{t.toUpperCase()}</option>)}</select></label>
        <button type="button" className="underline" onClick={() => setContracts([...contracts,
          blankContract(newType, nextContractId(contracts, newType), site)])}>Add contract</button>
        <button type="button" className="px-2 py-1 rounded bg-accent text-on-accent"
                onClick={() => save({ contracts }, 'The contracts')}>Save the contracts</button>
      </div>
      <ConnectionEditor current={cfg.connection} series={refs}
                        onSave={conn => save({ connection: conn }, 'The connection agreement')} />
    </div>
  )
}

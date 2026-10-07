// Results → Investment → Finance inputs (IC P4 WP4.7a): the finance case's
// inputs — dates and length, capex phasing and contingency, escalation by
// class, degradation by asset, debt tranches, the tax pack and its
// jurisdiction inputs, incentives, the rates, the terminal value and
// solve-for-PPA — saved through PUT /simulation/finance with the digest of the
// GET the edit started from (If-Match; 412 → "changed elsewhere, reload").
// The server judges every combination: its 422 is shown at the field it names
// (the WP3.7 pattern). A value not stated is shown as "not stated" and sent as
// null / absent — never as 0 (plan C12).
import { useEffect, useId, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { financeApi, SolverInFlightError, StaleEditError, StudyBusyError } from '../../../api/finance'
import { commercialApi } from '../../../api/commercial'
import type {
  DebtTranche, EscalationClass, FinanceInputs, Incentive, SolvePpa, TerminalValueRule,
} from '../../../api/types'
import { useUIStore } from '../../../store/uiStore'
import { NumInput } from './NumInput'
import { nk } from '../../../utils/queryKeys'
import {
  errorsFor, financeErrors, NOT_STATED, numOrNull, numText, parseNumberList, parseRateOrList,
  rateOrListText, triText, triValue, withKey, type FinanceDraft,
} from './financeModel'

const input = 'border border-border rounded px-1 py-0.5 text-[11px] bg-bg'
const ESCALATION: EscalationClass[] = ['opex', 'fuel', 'tariff', 'ppa', 'export', 'capex']
const PACKS = ['us_federal', 'eu_de', 'eu_nl', 'ca_federal']
const INCENTIVE_KINDS: Incentive['kind'][] = ['itc', 'ptc', 'grant', 'accelerated_depreciation', 'cfd',
  'capacity_payment']
const TRANCHE_KINDS: DebtTranche['kind'][] = ['term_loan', 'mini_perm', 'construction', 'mezzanine']

type Errs = Record<string, string[]>
type Report = (key: string, label: string | null) => void

// ── controls ─────────────────────────────────────────────────────────────

function FieldErrors({ id, list }: { id: string; list: string[] }) {
  if (!list.length) return null
  return <ul id={id} role="alert" className="text-[11px] text-danger pl-3 list-disc">
    {list.map(e => <li key={e}>{e}</li>)}</ul>
}

/** One labelled row: the control, then the server's errors for its path. */
function Row({ label, hint, errors, children }: {
  label: string; hint?: string; errors: string[]
  children: (a11y: { 'aria-label': string; 'aria-invalid': boolean; 'aria-describedby'?: string }) => ReactNode
}) {
  const id = useId()
  const bad = errors.length > 0
  return (
    <div className="flex items-start gap-2">
      <span className="w-56 pt-0.5">{label}{hint && <span className="text-muted"> {hint}</span>}</span>
      <div className="space-y-0.5">
        {children({ 'aria-label': label, 'aria-invalid': bad, 'aria-describedby': bad ? id : undefined })}
        <FieldErrors id={id} list={errors} />
      </div>
    </div>
  )
}

function NumField({ label, hint, value, onChange, errors, int }: {
  label: string; hint?: string; value: unknown; onChange: (v: number | null) => void
  errors: string[]; int?: boolean
}) {
  return (
    <Row label={label} hint={hint} errors={errors}>
      {a => <NumInput step={int ? 1 : 'any'} placeholder={NOT_STATED} {...a}
                      className={`${input} w-28`} value={value} onChange={onChange} />}
    </Row>
  )
}

function DateField({ label, value, onChange, errors }: {
  label: string; value: unknown; onChange: (v: string | null) => void; errors: string[]
}) {
  const v = typeof value === 'string' ? value : ''
  return (
    <Row label={label} hint={v ? undefined : `(${NOT_STATED})`} errors={errors}>
      {a => <input type="date" {...a} className={input} value={v}
                   onChange={e => onChange(e.target.value || null)} />}
    </Row>
  )
}

function SelectField({ label, value, options, onChange, errors, empty = NOT_STATED }: {
  label: string; value: unknown; options: Array<[string, string]>
  onChange: (v: string | null) => void; errors: string[]; empty?: string
}) {
  return (
    <Row label={label} errors={errors}>
      {a => <select {...a} className={input} value={typeof value === 'string' ? value : ''}
                    onChange={e => onChange(e.target.value || null)}>
        <option value="">{empty}</option>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>}
    </Row>
  )
}

/** A yes / no that may be unstated (`bool | None`). */
function TriField({ label, value, onChange, errors }: {
  label: string; value: unknown; onChange: (v: boolean | null) => void; errors: string[]
}) {
  return (
    <Row label={label} errors={errors}>
      {a => <select {...a} className={input} value={triText(value)}
                    onChange={e => onChange(triValue(e.target.value))}>
        <option value="">{NOT_STATED}</option><option value="yes">yes</option><option value="no">no</option>
      </select>}
    </Row>
  )
}

function TextField({ label, hint, value, onChange, errors, list }: {
  label: string; hint?: string; value: unknown; onChange: (v: string | null) => void
  errors: string[]; list?: string
}) {
  return (
    <Row label={label} hint={hint} errors={errors}>
      {a => <input {...a} className={`${input} w-40`} placeholder={NOT_STATED} list={list}
                   value={typeof value === 'string' ? value : ''}
                   onChange={e => onChange(e.target.value === '' ? null : e.target.value)} />}
    </Row>
  )
}

/** Text parsed on blur (a list, a rate or a list): the text follows the value
 *  while not being edited; text that does not parse is named and blocks Save. */
function BlurInput({ label, a11y, shown, commit, report, width = 'w-40' }: {
  label: string; a11y?: Record<string, unknown>; shown: string
  commit: (text: string) => boolean; report: Report; width?: string
}) {
  const [text, setText] = useState(shown)
  const [focused, setFocused] = useState(false)
  const [bad, setBad] = useState(false)
  const key = useId()
  useEffect(() => { if (!focused) { setText(shown); setBad(false); report(key, null) } },
    [shown]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => report(key, null), [key, report])
  return (
    <input aria-label={label} {...a11y} placeholder={NOT_STATED}
           className={`${input} ${width} ${bad ? 'border-danger' : ''}`} value={text}
           onFocus={() => setFocused(true)} onChange={e => setText(e.target.value)}
           onBlur={() => {
             setFocused(false)
             const ok = commit(text)
             setBad(!ok); report(key, ok ? null : label)
           }} />
  )
}

/** A per-asset map (COD dates, degradation, depreciation classes). No entry =
 *  not stated; clearing a value removes the entry (never a 0). */
function AssetMap({ title, noun, path, map, kind, onChange, errors, report, assets }: {
  title: string; noun: string; path: string; map: Record<string, unknown> | undefined
  kind: 'date' | 'rateOrList' | 'text'
  onChange: (next: Record<string, unknown>) => void
  errors: Errs; report: Report; assets: string
}) {
  const [name, setName] = useState('')
  const entries = Object.entries(map ?? {})
  const put = (asset: string, v: unknown) => onChange(withKey(map ?? {}, asset, v))
  const valueControl = (asset: string, v: unknown, a: Record<string, unknown>) => {
    const label = `${title}, ${asset}`
    if (kind === 'date') {
      return <input type="date" {...a} aria-label={label} className={input}
                    value={typeof v === 'string' ? v : ''}
                    onChange={e => put(asset, e.target.value || undefined)} />
    }
    if (kind === 'text') {
      return <BlurInput label={label} a11y={a} shown={typeof v === 'string' ? v : ''} report={report}
                        commit={t => { put(asset, t.trim() || undefined); return true }} />
    }
    return <BlurInput label={label} a11y={a} shown={rateOrListText(v)} report={report}
                      commit={t => { const p = parseRateOrList(t); if (p === null) return false
                                     put(asset, p); return true }} />
  }
  const taken = name.trim() !== '' && name.trim() in (map ?? {})
  return (
    <fieldset className="border border-border rounded p-2 space-y-1">
      <legend className="font-semibold">{title}</legend>
      {entries.length === 0 && <p className="text-muted">None stated.</p>}
      {entries.map(([asset, v]) => (
        <div key={asset} className="flex items-start gap-2">
          <Row label={`${title}, ${asset}`} errors={errorsFor(errors, `${path}.${asset}`)}>
            {a => valueControl(asset, v, a)}
          </Row>
          <button type="button" className="underline" aria-label={`Remove ${title}, ${asset}`}
                  onClick={() => put(asset, undefined)}>Remove</button>
        </div>
      ))}
      <FieldErrors id={`${path}-errors`} list={errorsFor(errors, path, entries.map(([k]) => `${path}.${k}`))} />
      <div className="flex items-center gap-2">
        <input aria-label={`${title}: new asset`} className={`${input} w-40`} list={assets}
               placeholder="asset name" value={name} onChange={e => setName(e.target.value)} />
        <button type="button" className="underline" disabled={!name.trim() || taken}
                onClick={() => {
                  // A new entry is not stated until a value is typed; a date
                  // starts blank (the server refuses a blank one at its field).
                  put(name.trim(), kind === 'date' ? '' : kind === 'text' ? '' : null)
                  setName('')
                }}>Add {noun} for this asset</button>
      </div>
    </fieldset>
  )
}

function IdsInput({ label, value, onChange, report }: {
  label: string; value: string[] | undefined; onChange: (v: string[]) => void; report: Report
}) {
  return <BlurInput label={label} shown={(value ?? []).join(', ')} report={report}
                    commit={t => { onChange(t.split(',').map(x => x.trim()).filter(Boolean)); return true }} />
}

// ── sections ─────────────────────────────────────────────────────────────

const TRANCHE_FIELDS = ['kind', 'sculpting', 'amount', 'gearing', 'gearing_base', 'dscr_target',
  'max_gearing', 'rate', 'tenor_years', 'upfront_fee', 'commitment_fee', 'dsra_months', 'grace_years']

function TrancheEditor({ t, i, set, remove, errors, report }: {
  t: DebtTranche; i: number; set: (t: DebtTranche) => void; remove: () => void
  errors: Errs; report: Report
}) {
  const p = `debt.${i}`
  const name = `Tranche ${i + 1}`
  const e = (f: string) => errorsFor(errors, `${p}.${f}`)
  const f = (key: string, v: unknown) => set(withKey(t, key, v) as DebtTranche)
  const sculpted = t.sculpting === 'dscr_target'
  const sizing = t.gearing != null ? 'gearing' : t.amount != null ? 'amount'
    : 'gearing' in t ? 'gearing' : 'amount'
  const setSculpting = (v: string | null) => {
    let next = withKey(t, 'sculpting', v ?? undefined) as unknown as Record<string, unknown>
    // The server refuses amount / gearing on a sculpted tranche, and
    // dscr_target / max_gearing on the others: switching clears the other side.
    if (v === 'dscr_target') {
      next = withKey(withKey(next, 'amount', undefined), 'gearing', undefined)
      if (!('dscr_target' in next)) next.dscr_target = null
    } else if (sculpted) {
      next = withKey(withKey(next, 'dscr_target', undefined), 'max_gearing', undefined)
      if (!('amount' in next) && !('gearing' in next)) next.amount = null
    }
    set(next as unknown as DebtTranche)
  }
  const setSizing = (v: string | null) => {
    if (v === sizing) return
    const next = v === 'gearing'
      ? withKey(withKey(t, 'amount', undefined), 'gearing', null)
      : withKey(withKey(t, 'gearing', undefined), 'amount', null)
    set(next as DebtTranche)
  }
  return (
    <fieldset className="border border-border rounded p-2 space-y-1" data-testid={`fi-tranche-${i}`}>
      <legend className="font-semibold">{name}</legend>
      <SelectField label={`${name} kind`} value={t.kind} errors={e('kind')}
                   options={TRANCHE_KINDS.map(k => [k, k.replace(/_/g, ' ')])} empty="(choose)"
                   onChange={v => f('kind', v ?? undefined)} />
      <SelectField label={`${name} repayment`} value={t.sculpting} errors={e('sculpting')}
                   empty="(default: annuity)"
                   options={[['annuity', 'annuity'], ['level', 'level principal'],
                             ['dscr_target', 'sculpted to a DSCR target']]}
                   onChange={setSculpting} />
      {sculpted ? (
        <>
          <NumField label={`${name} DSCR target`} hint="(> 1)" value={t.dscr_target} errors={e('dscr_target')}
                    onChange={v => f('dscr_target', v)} />
          <NumField label={`${name} maximum gearing`} hint="(share, optional cap)" value={t.max_gearing}
                    errors={e('max_gearing')} onChange={v => f('max_gearing', v)} />
        </>
      ) : (
        <>
          <SelectField label={`${name} sizing`} value={sizing} errors={[]} empty="(choose)"
                       options={[['amount', 'an amount'], ['gearing', 'a gearing share']]}
                       onChange={setSizing} />
          {sizing === 'amount'
            ? <NumField label={`${name} amount`} value={t.amount} errors={e('amount')}
                        onChange={v => f('amount', v)} />
            : <NumField label={`${name} gearing`} hint="(share of the base)" value={t.gearing}
                        errors={e('gearing')} onChange={v => f('gearing', v)} />}
        </>
      )}
      {(sizing === 'gearing' && !sculpted) || t.gearing_base !== undefined ? (
        <SelectField label={`${name} gearing base`} value={t.gearing_base} errors={e('gearing_base')}
                     empty="(default: capex)"
                     options={[['capex', 'installed capex incl. contingency'], ['total_uses', 'total uses']]}
                     onChange={v => f('gearing_base', v ?? undefined)} />
      ) : null}
      <Row label={`${name} rate`} hint="(one rate, or one per year: 0.05, 0.055)" errors={e('rate')}>
        {a => <BlurInput label={`${name} rate`} a11y={a} shown={rateOrListText(t.rate)} report={report}
                         commit={text => { const r = parseRateOrList(text); if (r === null) return false
                                           f('rate', r === undefined ? null : r); return true }} />}
      </Row>
      <NumField label={`${name} tenor (years)`} int value={t.tenor_years} errors={e('tenor_years')}
                onChange={v => f('tenor_years', v)} />
      <NumField label={`${name} upfront fee`} hint="(share)" value={t.upfront_fee} errors={e('upfront_fee')}
                onChange={v => f('upfront_fee', v)} />
      <NumField label={`${name} commitment fee`} hint="(share)" value={t.commitment_fee}
                errors={e('commitment_fee')} onChange={v => f('commitment_fee', v)} />
      <NumField label={`${name} DSRA (months)`} int value={t.dsra_months} errors={e('dsra_months')}
                onChange={v => f('dsra_months', v)} />
      <NumField label={`${name} grace (years)`} int value={t.grace_years} errors={e('grace_years')}
                onChange={v => f('grace_years', v)} />
      <FieldErrors id={`${p}-errors`} list={errorsFor(errors, p, TRANCHE_FIELDS.map(x => `${p}.${x}`))} />
      <button type="button" className="underline" aria-label={`Remove ${name}`} onClick={remove}>Remove</button>
    </fieldset>
  )
}

const INCENTIVE_FIELDS = ['kind', 'rate', 'amount', 'grant_tax_treatment', 'feoc_flag',
  'eligibility.begin_construction_by', 'eligibility.placed_in_service_by', 'eligibility.asset_classes']

function IncentiveEditor({ inc, i, set, remove, errors, report }: {
  inc: Incentive; i: number; set: (x: Incentive) => void; remove: () => void; errors: Errs; report: Report
}) {
  const p = `incentives.${i}`
  const name = `Incentive ${i + 1}`
  const e = (f: string) => errorsFor(errors, `${p}.${f}`)
  const f = (key: string, v: unknown) => set(withKey(inc, key, v) as Incentive)
  const elig = (key: string, v: unknown) => f('eligibility', withKey(inc.eligibility ?? {}, key, v))
  const grant = inc.kind === 'grant' || inc.grant_tax_treatment != null
  return (
    <fieldset className="border border-border rounded p-2 space-y-1" data-testid={`fi-incentive-${i}`}>
      <legend className="font-semibold">{name}</legend>
      <SelectField label={`${name} kind`} value={inc.kind} errors={e('kind')} empty="(choose)"
                   options={INCENTIVE_KINDS.map(k => [k, k.replace(/_/g, ' ')])}
                   onChange={v => f('kind', v ?? undefined)} />
      <NumField label={`${name} rate`} hint="(share of the basis; per MWh for a PTC)" value={inc.rate}
                errors={e('rate')} onChange={v => f('rate', v)} />
      <NumField label={`${name} amount`} hint="(ITC cap or grant sum)" value={inc.amount}
                errors={e('amount')} onChange={v => f('amount', v)} />
      {grant && (
        <SelectField label={`${name} grant tax treatment`} value={inc.grant_tax_treatment}
                     errors={e('grant_tax_treatment')}
                     options={[['reduces_basis', 'reduces the depreciable basis'], ['taxable', 'taxable when received']]}
                     onChange={v => f('grant_tax_treatment', v)} />
      )}
      <DateField label={`${name} construction begins by`} value={inc.eligibility?.begin_construction_by}
                 errors={e('eligibility.begin_construction_by')}
                 onChange={v => elig('begin_construction_by', v)} />
      <DateField label={`${name} placed in service by`} value={inc.eligibility?.placed_in_service_by}
                 errors={e('eligibility.placed_in_service_by')}
                 onChange={v => elig('placed_in_service_by', v)} />
      <Row label={`${name} asset classes`} hint="(comma-separated)" errors={e('eligibility.asset_classes')}>
        {() => <IdsInput label={`${name} asset classes`} value={inc.eligibility?.asset_classes}
                         report={report} onChange={v => elig('asset_classes', v)} />}
      </Row>
      <TriField label={`${name} FEOC material assistance`} value={inc.feoc_flag} errors={e('feoc_flag')}
                onChange={v => f('feoc_flag', v)} />
      <FieldErrors id={`${p}-errors`} list={errorsFor(errors, p, INCENTIVE_FIELDS.map(x => `${p}.${x}`))} />
      <button type="button" className="underline" aria-label={`Remove ${name}`} onClick={remove}>Remove</button>
    </fieldset>
  )
}

/** Every top-level path the form places; the rest of a 422 is shown at the top. */
const PLACED = ['currency', 'currency_year', 'price_basis', 'financial_close', 'acquisition_date', 'construction_start', 'analysis_years',
  'annualise', 'cod_by_asset', 'capex_phasing', 'contingency_share', 'replacement_rule', 'escalation',
  'degradation_by_asset',
  'debt', 'tax_pack_id', 'hebesatz_pct', 'state_rate', 'tax_losses', 'financing_fee_tax', 'pwa_met',
  'small_business_163j', 'depreciation_class_by_asset', 'incentives', 'wacc_nominal', 'cost_of_equity',
  'inflation', 'reserves_rate', 'terminal_value', 'solve_ppa']

function Form({ d, set, errors, report, assets }: {
  d: FinanceDraft; set: (key: string, v: unknown) => void; errors: Errs; report: Report; assets: string
}) {
  const e = (p: string) => errorsFor(errors, p)
  const debt = (d.debt ?? []) as DebtTranche[]
  const incentives = (d.incentives ?? []) as Incentive[]
  const tv = (d.terminal_value ?? {}) as TerminalValueRule
  const sp = d.solve_ppa as SolvePpa | null | undefined
  const esc = (d.escalation ?? {}) as Record<string, number>
  return (
    <div className="space-y-3">
      <fieldset className="border border-border rounded p-2 space-y-1">
        <legend className="font-semibold">Case dates and length</legend>
        <TextField label="Currency" hint="(ISO code; blank: EUR)" value={d.currency} errors={e('currency')}
                   onChange={v => set('currency', v ?? undefined)} />
        <NumField label="Currency year" hint="(the money year of the costs and rates)" int
                  value={d.currency_year} errors={e('currency_year')}
                  onChange={v => set('currency_year', v)} />
        <SelectField label="Price basis" value={d.price_basis} errors={e('price_basis')}
                     empty="(default: nominal)"
                     options={[['nominal', 'nominal (escalated)'],
                               ['real', 'real (constant money of the currency year)']]}
                     onChange={v => set('price_basis', v ?? undefined)} />
        <DateField label="Financial close" value={d.financial_close} errors={e('financial_close')}
                   onChange={v => set('financial_close', v ?? '')} />
        <DateField label="Acquisition date" value={d.acquisition_date} errors={e('acquisition_date')}
                   onChange={v => set('acquisition_date', v)} />
        <DateField label="Construction start" value={d.construction_start} errors={e('construction_start')}
                   onChange={v => set('construction_start', v)} />
        <NumField label="Analysis years" hint="(1–60)" int value={d.analysis_years} errors={e('analysis_years')}
                  onChange={v => set('analysis_years', v)} />
        <Row label="Annualise a partial-year template" errors={e('annualise')}>
          {a => <input type="checkbox" {...a} checked={d.annualise === true}
                       onChange={ev => set('annualise', ev.target.checked)} />}
        </Row>
      </fieldset>
      <AssetMap title="COD" noun="a COD" path="cod_by_asset" map={d.cod_by_asset} kind="date" errors={errors}
                report={report} assets={assets} onChange={m => set('cod_by_asset', m)} />
      <fieldset className="border border-border rounded p-2 space-y-1">
        <legend className="font-semibold">Capex</legend>
        <Row label="Capex phasing" hint="(a share per construction year, summing to 1; blank: all at close)"
             errors={e('capex_phasing')}>
          {a => <BlurInput label="Capex phasing" a11y={a} report={report}
                           shown={Array.isArray(d.capex_phasing) ? d.capex_phasing.join(', ') : ''}
                           commit={t => { const l = parseNumberList(t); if (l === null) return false
                                          set('capex_phasing', l.length ? l : undefined); return true }} />}
        </Row>
        <NumField label="Contingency" hint="(share of capex)" value={d.contingency_share}
                  errors={e('contingency_share')} onChange={v => set('contingency_share', v)} />
        <SelectField label="Replacements" value={d.replacement_rule} errors={e('replacement_rule')}
                     empty="(default: the stated replacement entries)"
                     options={[['fixed', 'the stated replacement entries'],
                               ['part_lifetimes', 'each part at the end of its lifetime']]}
                     onChange={v => set('replacement_rule', v ?? undefined)} />
      </fieldset>
      <fieldset className="border border-border rounded p-2 space-y-1">
        <legend className="font-semibold">Escalation (nominal, a year)</legend>
        <p className="text-muted">A class with cashflows and no rate is not established; type 0 for none.</p>
        {ESCALATION.map(c => (
          <NumField key={c} label={`Escalation ${c}`} value={esc[c]} errors={e(`escalation.${c}`)}
                    onChange={v => set('escalation', withKey(esc, c, v === null ? undefined : v))} />
        ))}
        <FieldErrors id="escalation-errors"
                     list={errorsFor(errors, 'escalation', ESCALATION.map(c => `escalation.${c}`))} />
      </fieldset>
      <AssetMap title="Degradation" noun="a degradation rate" path="degradation_by_asset" map={d.degradation_by_asset}
                kind="rateOrList" errors={errors} report={report} assets={assets}
                onChange={m => set('degradation_by_asset', m)} />
      <fieldset className="border border-border rounded p-2 space-y-2">
        <legend className="font-semibold">Debt</legend>
        {debt.length === 0 && <p className="text-muted">No tranches: the case is all equity.</p>}
        {debt.map((t, i) => (
          <TrancheEditor key={i} t={t} i={i} errors={errors} report={report}
                         set={x => set('debt', debt.map((y, j) => (j === i ? x : y)))}
                         remove={() => set('debt', debt.filter((_, j) => j !== i))} />
        ))}
        <FieldErrors id="debt-errors" list={errorsFor(errors, 'debt', debt.map((_, i) => `debt.${i}`))} />
        <button type="button" className="underline" onClick={() => set('debt', [...debt, {
          kind: 'term_loan', amount: null, rate: null as unknown as number, tenor_years: null as unknown as number,
          upfront_fee: null, commitment_fee: null, dsra_months: null, grace_years: null,
        }])}>Add a debt tranche</button>
      </fieldset>
      <fieldset className="border border-border rounded p-2 space-y-1">
        <legend className="font-semibold">Tax</legend>
        <datalist id={`${assets}-packs`}>{PACKS.map(p => <option key={p} value={p} />)}</datalist>
        <TextField label="Tax pack" hint="(us_federal, eu_de, eu_nl, ca_federal)" value={d.tax_pack_id}
                   list={`${assets}-packs`} errors={e('tax_pack_id')} onChange={v => set('tax_pack_id', v)} />
        <NumField label="Hebesatz (%)" hint="(DE trade tax multiplier)" value={d.hebesatz_pct}
                  errors={e('hebesatz_pct')} onChange={v => set('hebesatz_pct', v)} />
        <NumField label="State / provincial rate" hint="(share)" value={d.state_rate} errors={e('state_rate')}
                  onChange={v => set('state_rate', v)} />
        <SelectField label="Tax losses" value={d.tax_losses} errors={e('tax_losses')}
                     options={[['offset_other_income', 'offset against other income'],
                               ['carryforward', 'carried forward']]}
                     onChange={v => set('tax_losses', v)} />
        <SelectField label="Financing fee tax" value={d.financing_fee_tax} errors={e('financing_fee_tax')}
                     options={[['amortised', 'amortised over the tenor'], ['not_deducted', 'not deducted']]}
                     onChange={v => set('financing_fee_tax', v)} />
        <TriField label="Prevailing wage and apprenticeship met" value={d.pwa_met} errors={e('pwa_met')}
                  onChange={v => set('pwa_met', v)} />
        <TriField label="Small business (§163(j) exempt)" value={d.small_business_163j}
                  errors={e('small_business_163j')} onChange={v => set('small_business_163j', v)} />
      </fieldset>
      <AssetMap title="Depreciation class" noun="a depreciation class" path="depreciation_class_by_asset"
                map={d.depreciation_class_by_asset} kind="text" errors={errors} report={report} assets={assets}
                onChange={m => set('depreciation_class_by_asset', m)} />
      <fieldset className="border border-border rounded p-2 space-y-2">
        <legend className="font-semibold">Incentives</legend>
        {incentives.length === 0 && <p className="text-muted">None stated.</p>}
        {incentives.map((x, i) => (
          <IncentiveEditor key={i} inc={x} i={i} errors={errors} report={report}
                           set={y => set('incentives', incentives.map((z, j) => (j === i ? y : z)))}
                           remove={() => set('incentives', incentives.filter((_, j) => j !== i))} />
        ))}
        <FieldErrors id="incentives-errors"
                     list={errorsFor(errors, 'incentives', incentives.map((_, i) => `incentives.${i}`))} />
        <button type="button" className="underline"
                onClick={() => set('incentives', [...incentives, { kind: 'itc' }])}>Add an incentive</button>
      </fieldset>
      <fieldset className="border border-border rounded p-2 space-y-1">
        <legend className="font-semibold">Rates (nominal, as shares: 0.07 = 7 %)</legend>
        <NumField label="WACC" value={d.wacc_nominal} errors={e('wacc_nominal')}
                  onChange={v => set('wacc_nominal', v)} />
        <NumField label="Cost of equity" value={d.cost_of_equity} errors={e('cost_of_equity')}
                  onChange={v => set('cost_of_equity', v)} />
        <NumField label="Inflation" value={d.inflation} errors={e('inflation')}
                  onChange={v => set('inflation', v)} />
        <NumField label="Reserves interest rate" value={d.reserves_rate} errors={e('reserves_rate')}
                  onChange={v => set('reserves_rate', v)} />
      </fieldset>
      <fieldset className="border border-border rounded p-2 space-y-1">
        <legend className="font-semibold">Terminal value</legend>
        <SelectField label="Terminal value method" value={tv.method} errors={e('terminal_value.method')}
                     empty="(default: none)"
                     options={[['none', 'none'], ['book_value', 'book value'],
                               ['multiple_of_ebitda', 'a multiple of EBITDA'], ['fixed', 'a fixed amount'],
                               ['remaining_life_annuity', "the parts' remaining life (LP annuity)"]]}
                     onChange={v => set('terminal_value', v === 'remaining_life_annuity'
                       // The remaining life takes no value: a kept one would be refused (422).
                       ? { ...withKey(tv, 'method', v), value: null }
                       : withKey(tv, 'method', v ?? undefined))} />
        <NumField label="Terminal value" hint="(the multiple, or the amount; none for the remaining life)"
                  value={tv.value}
                  errors={e('terminal_value.value')}
                  onChange={v => set('terminal_value', withKey(tv, 'value', v))} />
        <FieldErrors id="tv-errors" list={errorsFor(errors, 'terminal_value',
          ['terminal_value.method', 'terminal_value.value'])} />
      </fieldset>
      <fieldset className="border border-border rounded p-2 space-y-1">
        <legend className="font-semibold">Solve for a PPA price</legend>
        <Row label="Solve for a PPA price" errors={[]}>
          {a => <input type="checkbox" {...a} checked={!!sp}
                       onChange={ev => set('solve_ppa', ev.target.checked
                         ? { contract_id: null, target_irr: null, target_year: null } : null)} />}
        </Row>
        {sp && (
          <>
            <TextField label="PPA contract" hint="(blank: the case's single owner-sold PPA)"
                       value={sp.contract_id} errors={e('solve_ppa.contract_id')}
                       onChange={v => set('solve_ppa', withKey(sp, 'contract_id', v))} />
            <NumField label="Target equity IRR (post-tax)" hint="(share)" value={sp.target_irr}
                      errors={e('solve_ppa.target_irr')}
                      onChange={v => set('solve_ppa', withKey(sp, 'target_irr', v))} />
            <NumField label="Target year" hint="(operating year)" int value={sp.target_year}
                      errors={e('solve_ppa.target_year')}
                      onChange={v => set('solve_ppa', withKey(sp, 'target_year', v))} />
          </>
        )}
        <FieldErrors id="sp-errors" list={errorsFor(errors, 'solve_ppa',
          ['solve_ppa.contract_id', 'solve_ppa.target_irr', 'solve_ppa.target_year'])} />
      </fieldset>
    </div>
  )
}

export default function FinanceInputsEditor() {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const assets = useId()
  const stateKey = nk(project, 'simulation', 'finance')
  const state = useQuery({ queryKey: stateKey, queryFn: () => financeApi.getFinance() })
  // Asset names to pick from (the participants designer's list); optional.
  const designer = useQuery({ queryKey: nk(project, 'value_flows', 'designer'),
                              queryFn: () => commercialApi.getDesigner(), retry: false })
  // undefined = not seeded yet; null = no finance inputs stated.
  const [draft, setDraft] = useState<FinanceDraft | null | undefined>(undefined)
  const [digest, setDigest] = useState<string | null>(null)
  const [errors, setErrors] = useState<Errs>({})
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const [stale, setStale] = useState(false)
  const [saving, setSaving] = useState(false)
  const [invalid, setInvalid] = useState<Map<string, string>>(new Map())
  const report = useState<Report>(() => (key: string, label: string | null) => setInvalid(prev => {
    if ((prev.get(key) ?? null) === label) return prev
    const next = new Map(prev)
    if (label) next.set(key, label); else next.delete(key)
    return next
  }))[0]

  // Start from what the server holds, once per load — never from a cache
  // that is being refetched; a reload re-seeds explicitly.
  useEffect(() => {
    if (state.data && draft === undefined && !state.isFetching) {
      setDraft(state.data.finance ? structuredClone(state.data.finance) as FinanceDraft : null)
      setDigest(state.data.digest)
    }
  }, [state.data, state.isFetching, draft])

  if (state.isError) {
    return <p className="text-[11px] text-warn py-2" data-testid="fi-load-error">
      {state.error instanceof SolverInFlightError
        ? 'A solve is running; the finance inputs can be edited after it.'
        : 'The finance inputs could not be read; reload before editing them.'}</p>
  }
  if (draft === undefined) return <p className="text-[11px] text-muted py-2">Loading the finance inputs…</p>

  const reload = async () => {
    setStale(false); setErrors({}); setMessage(null)
    try {
      const fresh = await qc.fetchQuery({ queryKey: stateKey, queryFn: () => financeApi.getFinance(),
                                          staleTime: 0 })
      setDraft(fresh.finance ? structuredClone(fresh.finance) as FinanceDraft : null)
      setDigest(fresh.digest)
    } catch {
      setMessage({ tone: 'error', text: 'The finance inputs could not be reloaded.' })
    }
  }
  const save = async (value: FinanceDraft | null) => {
    if (saving) return
    setSaving(true); setErrors({}); setMessage(null)
    try {
      const out = await financeApi.putFinance(value as FinanceInputs | null, digest ?? '')
      if (out?.digest) setDigest(out.digest)
      // The stored form (the server may normalise) is the new starting point.
      if (out && 'finance' in out) setDraft(out.finance ? structuredClone(out.finance) as FinanceDraft : null)
      else if (value === null) setDraft(null)
      setMessage({ tone: 'ok', text: value === null ? 'The finance inputs were removed.'
        : 'Finance inputs saved. Run the investment case for results on them.' })
      await qc.invalidateQueries({ queryKey: stateKey })
      await qc.invalidateQueries({ queryKey: nk(project, 'results', 'investment_case') })
    } catch (e) {
      if (e instanceof StaleEditError) { setStale(true); return }
      if (e instanceof SolverInFlightError) {
        setMessage({ tone: 'error', text: 'Not saved: a solve is running; save after it finishes.' }); return
      }
      if (e instanceof StudyBusyError) {
        setMessage({ tone: 'error', text: `Not saved: ${e.message}` }); return
      }
      const r = (e as { response?: { status?: number; data?: { detail?: unknown } } })?.response
      if (r?.status === 422) {
        setErrors(financeErrors(r.data?.detail))
        setMessage({ tone: 'error', text: 'Not saved: the server refused some fields (shown at each).' })
        return
      }
      setMessage({ tone: 'error', text: 'The finance inputs could not be saved.' })
    } finally { setSaving(false) }
  }

  const blocked = invalid.size > 0
  const stored = state.data?.status
  const topErrors = errorsFor(errors, '', PLACED)
  const assetNames = (designer.data?.assets ?? []).filter(a => a.ownable !== false).map(a => a.name)

  return (
    <div className="space-y-3 text-[11px]" data-testid="finance-inputs">
      <datalist id={assets}>{assetNames.map(n => <option key={n} value={n} />)}</datalist>
      {stale && (
        <div role="alert" className="text-warn" data-testid="fi-stale">
          The finance inputs changed elsewhere since you opened them; your edits were not saved.{' '}
          <button type="button" className="underline" onClick={() => { void reload() }}>
            Reload the finance inputs</button>
        </div>
      )}
      {stored && !['ok', 'not_set'].includes(stored) && (
        <p role="alert" className="text-warn" data-testid="fi-stored-status">
          The stored finance inputs: {stored.replace(/_/g, ' ')}
          {state.data?.message ? ` (${state.data.message})` : ''}.</p>
      )}
      {message && <p role="status" className={message.tone === 'ok' ? 'text-success' : 'text-danger'}>
        {message.text}</p>}
      <FieldErrors id="fi-top-errors" list={topErrors} />
      {draft === null ? (
        <div className="space-y-1" data-testid="fi-none">
          <p className="text-muted">No finance inputs are stated for this project.</p>
          <button type="button" className="underline" onClick={() => setDraft({})}>
            State the finance inputs</button>
        </div>
      ) : (
        <>
          {blocked && <p role="alert" className="text-danger" data-testid="fi-blocked">
            Fix the values that are not numbers ({[...invalid.values()].join(', ')}) before saving.</p>}
          <Form d={draft} errors={errors} report={report} assets={assets}
                set={(k, v) => setDraft(prev => withKey(prev ?? {}, k, v))} />
          <div className="flex flex-wrap gap-2 items-center">
            <button type="button" className="px-2 py-1 rounded bg-accent text-on-accent disabled:opacity-50"
                    disabled={blocked || saving} onClick={() => { void save(draft) }}>
              Save the finance inputs</button>
            <button type="button" className="underline" onClick={() => { void reload() }}>
              Discard changes</button>
            {state.data?.finance != null && (
              <button type="button" className="underline" disabled={saving}
                      onClick={() => { void save(null) }}>Remove the finance inputs</button>
            )}
          </div>
        </>
      )}
    </div>
  )
}

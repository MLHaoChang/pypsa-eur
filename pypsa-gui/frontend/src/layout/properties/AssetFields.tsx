// S1b (plan 2026-10-13-s1-expert-asset-cards.md): an asset card's fields,
// rendered from the backend's descriptors in the fixed group order. One
// renderer replaces each card's four hand-written lists (read-out rows,
// `startEdit` keys, save mapping, edit inputs). Derived rows (capital cost,
// a battery's lifetime and fixed O&M) are shown read-only, never typed.
import { Fragment, useState, type ReactNode } from 'react'
import type { AssetFieldSpec, AssetSchema, ProvenanceEntry } from '../../api/assetSchema'
import { ProvenanceChip } from '../../components/ProvenanceChip'
import type { Bus, Carrier } from '../../api/types'
import { ReferenceApplyDrawer } from '../../pages/results/investment/ReferenceApplyDrawer'
import { derivedStored, displayValue, isRevealed, tidy, unitLabel } from '../../utils/assetForm'
import type { SeriesField } from '../../components/series/useSeriesFields'
import {
  BusSelect, CarrierGroupSelect, ChkInput, FieldLabel, InfoTip, NumInput, SectionHdr, TxtInput,
  type ExpandedItem, type FS, type SetFS,
} from './cardKit'

/** Every field of the schema, in order. */
export function schemaFields(schema: AssetSchema | undefined): AssetFieldSpec[] {
  return schema ? schema.groups.flatMap(g => g.fields) : []
}

/** A described field's tooltip (S1d-3: the panels' own rows read it here, not from propertyDocs). */
export function fieldHelp(schema: AssetSchema | undefined, key: string): string | undefined {
  return schemaFields(schema).find(f => f.key === key)?.help
}

// `unitLabel` moved to utils/assetForm.ts (the raw-table headers use it too).
export { unitLabel }

function fmt(v: number): string {
  const abs = Math.abs(v)
  const digits = abs >= 1000 ? 0 : abs >= 1 ? 2 : 4
  return v.toLocaleString(undefined, { maximumFractionDigits: digits })
}

/** U3: the field's own provenance stamp (S1a); none renders nothing. */
type Stamps = Record<string, ProvenanceEntry> | undefined

function chipOf(stamps: Stamps, key: string) {
  const entry = stamps?.[key]
  return entry ? <ProvenanceChip entry={entry} testId={`asset-prov-${key}`} /> : null
}

/**
 * The collapsed card's read-out: every established value, in display units,
 * except identity. A stamped field's row carries its provenance chip (U3);
 * an unstamped one has no `badge` key at all, so the items are as before.
 */
export function assetReadoutItems(schema: AssetSchema | undefined, asset: Record<string, unknown>,
  hidden: ReadonlySet<string> = new Set(), stamps?: Stamps,
  series?: Record<string, SeriesField>): ExpandedItem[] {
  const fs: FS = Object.fromEntries(Object.entries(asset).map(([k, v]) => [k, String(v)]))
  const out: ExpandedItem[] = []
  const push = (f: AssetFieldSpec, item: ExpandedItem) => {
    const badge = chipOf(stamps, f.key)
    out.push(badge ? { ...item, badge } : item)
  }
  for (const f of schemaFields(schema)) {
    if (f.group === 'identity' || hidden.has(f.key) || !isRevealed(f, fs)) continue
    const v = f.derived ? derivedStored(f.key, asset) : asset[f.key]
    if (series?.[f.key]?.set) {
      // A series wins over the scalar in the model; show that, not the constant.
      push(f, { label: f.label, value: `time series (${series[f.key].set})`, tip: f.help })
      continue
    }
    if (f.kind === 'bool') {
      if (v === true) push(f, { label: f.label, value: true, tip: f.help })
      continue
    }
    if (f.kind === 'number' || f.kind === 'int') {
      const d = displayValue(f, v)
      if (d === null || (d === 0 && !f.key_param)) continue
      push(f, { label: f.label, value: f.kind === 'int' ? d : fmt(d), unit: f.unit_display ? ` ${unitLabel(f.unit_display)}` : undefined, tip: f.help })
      continue
    }
    if (v !== null && v !== undefined && v !== '') push(f, { label: f.label, value: String(v), tip: f.help })
  }
  return out
}

/**
 * A text, bus or carrier field's chip on its own full-width row (those inputs
 * have no badge slot; a number, enum or flag carries its chip in its label).
 */
function chipRow(stamps: Stamps, key: string) {
  const chip = chipOf(stamps, key)
  return chip ? <div className="col-span-2 -mt-0.5">{chip}</div> : null
}

const LABEL_ONLY_KINDS: ReadonlySet<string> = new Set(['text', 'bus', 'carrier'])

function SeriesSetRow({ label, field, k }: { label: string; field: SeriesField; k: string }) {
  return (
    <div className="col-span-2 text-[10px]" data-testid={`asset-series-${k}`}>
      <span className="text-muted">{label}: </span>
      Time series set ({field.set}). Edit it in {field.editor}.
    </div>
  )
}

function DerivedRow({ spec, value, stamps }: { spec: AssetFieldSpec; value: unknown; stamps?: Stamps }) {
  const d = displayValue(spec, value)
  return (
    // Full width, label left and value right: derived labels are long
    // ("Capital cost (annualised) (€/kW/yr)") and must not truncate.
    <div className="col-span-2 flex items-baseline justify-between gap-2" data-testid={`asset-derived-${spec.key}`}>
      <span className="text-[9px] text-muted">
        <InfoTip text={spec.help}>{spec.label}{spec.unit_display ? ` (${unitLabel(spec.unit_display)})` : ''}</InfoTip>
        {/* The derived key's own stamp only, never inferred from its parts. */}
        {chipOf(stamps, spec.key)}
      </span>
      <span className="text-[10px] font-mono text-muted italic whitespace-nowrap" title="Derived: recomputed on save">
        {d === null ? '—' : fmt(d)} <span className="not-italic text-[9px]">derived</span>
      </span>
    </div>
  )
}

function EnumInput({ spec, fs, set, badge }: { spec: AssetFieldSpec; fs: FS; set: SetFS; badge?: ReactNode }) {
  return (
    <label className="flex flex-col gap-0.5">
      <FieldLabel className="text-[9px] text-muted truncate" badge={badge}>
        <InfoTip text={spec.help}>{spec.label}</InfoTip>
      </FieldLabel>
      <select value={fs[spec.key] ?? ''} onChange={e => set(p => ({ ...p, [spec.key]: e.target.value }))}
        className="bg-bg border border-border rounded px-1.5 py-0.5 text-[10px] font-mono focus:outline-none focus:border-accent w-full">
        <option value="">unset</option>
        {spec.options.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  )
}

const PART_COLUMNS: Array<{ suffix: string; head: string }> = [
  { suffix: '_overnight', head: 'Overnight' },
  { suffix: '_lifetime', head: 'Life (y)' },
  { suffix: '_fom_share', head: 'FOM (%/yr)' },
]

/**
 * A composite asset's investment parts as one row each (the spec's battery
 * card: "part[power]: 213.9 €/kW, 10 y, FOM 0.34 %/yr"), so the narrow card
 * never truncates "Power part: overnight cost". Each input keeps the field's
 * full label as its accessible name.
 */
function PartsTable({ parts, fs, set, stamps }: { parts: AssetFieldSpec[]; fs: FS; set: SetFS; stamps?: Stamps }) {
  const names = [...new Set(parts.map(f => f.part as string))]
  return (
    <div className="col-span-2" data-testid="asset-parts">
      <table className="w-full text-[10px]">
        <thead>
          <tr className="text-muted text-[9px]">
            <th className="text-left font-normal">Part</th>
            {PART_COLUMNS.map(c => <th key={c.suffix} className="text-left font-normal">{c.head}</th>)}
          </tr>
        </thead>
        <tbody>
          {names.map(name => {
            const row = parts.filter(f => f.part === name)
            const money = row.find(f => f.key.endsWith('_overnight'))
            return (
              <tr key={name}>
                <td className="pr-1 whitespace-nowrap">
                  <InfoTip text={money?.help}>
                    {name[0].toUpperCase() + name.slice(1)}{money ? ` (${unitLabel(money.unit_display)})` : ''}
                  </InfoTip>
                </td>
                {PART_COLUMNS.map(c => {
                  const f = row.find(x => x.key.endsWith(c.suffix))
                  return (
                    <td key={c.suffix} className="pr-1">
                      {f && (
                        <input type="number" step="any" value={fs[f.key] ?? ''}
                          aria-label={`${f.label}${f.unit_display ? ` (${unitLabel(f.unit_display)})` : ''}`}
                          title={f.help}
                          onChange={e => set(p => ({ ...p, [f.key]: e.target.value }))}
                          className="bg-bg border border-border rounded px-1 py-0.5 text-[10px] font-mono focus:outline-none focus:border-accent w-full" />
                      )}
                      {f && chipOf(stamps, f.key)}
                    </td>
                  )
                })}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// The cost fields whose stamp carries a money year, shown beside the unit:
// on its own line under the field ("In 2025 €"). A value is in the money year it was priced in.
const MONEY_FIELDS: ReadonlySet<string> = new Set(['overnight_cost', 'fom_cost'])

function moneyYear(key: string, stamps: Stamps): string | null {
  const entry = MONEY_FIELDS.has(key) ? stamps?.[key] : undefined
  return entry?.currency_year == null ? null : `${entry.currency_year} ${unitLabel(entry.currency ?? 'EUR')}`
}

/** The open form takes in what an applied reference wrote (stored → display), so a later
 *  Save writes the applied values back unchanged instead of the form's stale ones. */
export function appliedFields(schema: AssetSchema, written: Record<string, number>): FS {
  const out: FS = {}
  for (const f of schemaFields(schema)) {
    if (f.key in written && !f.derived) out[f.key] = tidy(written[f.key] * f.scale)
  }
  return out
}

/** The asset-side entry to the Library's cost references: a link beside the investment
 *  cost that opens the compare-and-apply drawer for this asset, and returns to the form. */
function CompareReferences({ componentClass, name, money, onApplied }: {
  componentClass: 'Generator' | 'Link'; name: string; money: string | null
  onApplied: (written: Record<string, number>) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="col-span-2 -mt-0.5 flex gap-2 text-[9px]">
      {money && <span className="text-muted" data-testid="asset-money-overnight_cost">In {money}</span>}
      <button type="button" className="text-[9px] text-accent underline" data-testid="asset-compare-references"
        onClick={() => setOpen(true)}>Compare with references</button>
      {open && <ReferenceApplyDrawer open onClose={() => setOpen(false)} componentClass={componentClass} name={name}
        onApplied={onApplied} />}
    </div>
  )
}

/**
 * The edit form's fields. `current` feeds the derived rows; `onCheck` hooks a
 * boolean's side effect (Extendable seeds the minimum); `after` places a card's
 * own control after a field (e.g. the per-period bounds link after the maximum).
 */
export function AssetFieldsEdit({ schema, fs, set, current, carriers, buses, onCheck, after, hidden, provenance, series }: {
  schema: AssetSchema
  fs: FS
  set: SetFS
  current: Record<string, unknown>
  carriers: Carrier[]
  buses: Bus[]
  onCheck?: Record<string, (checked: boolean, prev: FS) => FS>
  after?: Record<string, ReactNode>
  /** Fields this asset does not use (e.g. curtailment cost on a thermal unit). */
  hidden?: ReadonlySet<string>
  /**
   * U3: the asset's per-field provenance (`useAssetProvenance`). Optional: a
   * card that passes none renders exactly as before; a field with no stamp
   * shows no chip (unknown is not "default").
   */
  provenance?: Record<string, ProvenanceEntry>
  /**
   * Fields a series editor also writes (`useSeriesFields`): with a series set
   * the field is read-only here and points to its editor; without one it is
   * the constant that editor starts from (UX review, checklist §6.2).
   */
  series?: Record<string, SeriesField>
}) {
  // The typed parts of a composite asset render together, as one table where
  // the first of them would have been (the Investment group).
  const parts = schemaFields(schema).filter(f => f.part && !f.derived)
  const firstPart = parts[0]?.key
  // Generators and Links can take a shipped cost reference (wind, PV, electrolysers).
  const referenceClass = schema.component === 'Generator' || schema.component === 'Link' ? schema.component : null
  const assetName = typeof current.name === 'string' ? current.name : null
  return (
    <>
      {schema.groups.map(g => {
        const shown = g.fields.filter(f => !hidden?.has(f.key) && isRevealed(f, fs)
          && (!f.part || f.derived || f.key === firstPart))
        if (!shown.length) return null
        return (
          <div key={g.id} className="contents" data-testid={`asset-group-${g.id}`}>
            {g.id !== 'identity' && <SectionHdr title={g.label} />}
            {shown.map(f => (
              <Fragment key={f.key}>
                {f.key === firstPart ? (
                  <>
                    <PartsTable parts={parts} fs={fs} set={set} stamps={provenance} />
                    {/* The parts draw as one table, so a control placed after any
                        part goes after the table. */}
                    {parts.map(p => <Fragment key={p.key}>{after?.[p.key]}</Fragment>)}
                  </>
                )
                  : series?.[f.key]?.set ? <SeriesSetRow label={f.label} field={series[f.key]} k={f.key} />
                  : f.derived ? <DerivedRow spec={f} value={derivedStored(f.key, current)} stamps={provenance} />
                  : f.kind === 'bool' ? <ChkInput label={f.label} k={f.key} fs={fs} set={set} tip={f.help} onCheck={onCheck?.[f.key]}
                      badge={chipOf(provenance, f.key)} />
                  : f.kind === 'bus' ? <BusSelect k={f.key} fs={fs} set={set} buses={buses} tip={f.help} label={f.label} />
                  : f.kind === 'carrier' ? <CarrierGroupSelect k={f.key} fs={fs} set={set} carriers={carriers} tip={f.help} />
                  : f.kind === 'text' ? <TxtInput label={f.label} k={f.key} fs={fs} set={set} tip={f.help} />
                  : f.kind === 'enum' ? <EnumInput spec={f} fs={fs} set={set} badge={chipOf(provenance, f.key)} />
                  : <NumInput label={series?.[f.key] ? `Constant ${f.label.charAt(0).toLowerCase()}${f.label.slice(1)}` : f.label} k={f.key} fs={fs} set={set} unit={f.unit_display ? unitLabel(f.unit_display) : undefined}
                      tip={series?.[f.key]
                        ? `${f.help ?? ''} The constant the ${series[f.key].editor} editor starts from; a series set there replaces it.`.trim()
                        : f.help}
                      badge={chipOf(provenance, f.key)} />}
                {f.key === 'overnight_cost' && referenceClass && assetName ? (
                  <CompareReferences componentClass={referenceClass} name={assetName} money={moneyYear(f.key, provenance)}
                    onApplied={written => set(prev => ({ ...prev, ...appliedFields(schema, written) }))} />
                ) : MONEY_FIELDS.has(f.key) && moneyYear(f.key, provenance) && (
                  <div className="col-span-2 -mt-0.5 text-[9px] text-muted" data-testid={`asset-money-${f.key}`}>
                    In {moneyYear(f.key, provenance)}</div>
                )}
                {!f.derived && f.key !== firstPart && LABEL_ONLY_KINDS.has(f.kind) && chipRow(provenance, f.key)}
                {f.key !== firstPart && after?.[f.key]}
              </Fragment>
            ))}
          </div>
        )
      })}
    </>
  )
}

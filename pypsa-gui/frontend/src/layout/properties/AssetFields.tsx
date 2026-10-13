// S1b (plan 2026-10-13-s1-expert-asset-cards.md): an asset card's fields,
// rendered from the backend's descriptors in the fixed group order. One
// renderer replaces each card's four hand-written lists (read-out rows,
// `startEdit` keys, save mapping, edit inputs). Derived rows (capital cost,
// a battery's lifetime and fixed O&M) are shown read-only, never typed.
import { Fragment, type ReactNode } from 'react'
import type { AssetFieldSpec, AssetSchema } from '../../api/assetSchema'
import type { Bus, Carrier } from '../../api/types'
import { displayValue, isRevealed } from '../../utils/assetForm'
import {
  BusSelect, CarrierGroupSelect, ChkInput, InfoTip, NumInput, SectionHdr, TxtInput,
  type ExpandedItem, type FS, type SetFS,
} from './cardKit'

/** Every field of the schema, in order. */
export function schemaFields(schema: AssetSchema | undefined): AssetFieldSpec[] {
  return schema ? schema.groups.flatMap(g => g.fields) : []
}

/** Units as the app writes them: the descriptors say EUR, the cards say €. */
export function unitLabel(u: string): string {
  return u.replace(/\bEUR\b/g, '€')
}

function fmt(v: number): string {
  const abs = Math.abs(v)
  const digits = abs >= 1000 ? 0 : abs >= 1 ? 2 : 4
  return v.toLocaleString(undefined, { maximumFractionDigits: digits })
}

/** The collapsed card's read-out: every established value, in display units, except identity. */
export function assetReadoutItems(schema: AssetSchema | undefined, asset: Record<string, unknown>,
  hidden: ReadonlySet<string> = new Set()): ExpandedItem[] {
  const fs: FS = Object.fromEntries(Object.entries(asset).map(([k, v]) => [k, String(v)]))
  const out: ExpandedItem[] = []
  for (const f of schemaFields(schema)) {
    if (f.group === 'identity' || hidden.has(f.key) || !isRevealed(f, fs)) continue
    const v = asset[f.key]
    if (f.kind === 'bool') {
      if (v === true) out.push({ label: f.label, value: true, tip: f.help })
      continue
    }
    if (f.kind === 'number' || f.kind === 'int') {
      const d = displayValue(f, v)
      if (d === null || (d === 0 && !f.key_param)) continue
      out.push({ label: f.label, value: f.kind === 'int' ? d : fmt(d), unit: f.unit_display ? ` ${unitLabel(f.unit_display)}` : undefined, tip: f.help })
      continue
    }
    if (v !== null && v !== undefined && v !== '') out.push({ label: f.label, value: String(v), tip: f.help })
  }
  return out
}

function DerivedRow({ spec, value }: { spec: AssetFieldSpec; value: unknown }) {
  const d = displayValue(spec, value)
  return (
    // Full width, label left and value right: derived labels are long
    // ("Capital cost (annualised) (€/kW/yr)") and must not truncate.
    <div className="col-span-2 flex items-baseline justify-between gap-2" data-testid={`asset-derived-${spec.key}`}>
      <span className="text-[9px] text-muted">
        <InfoTip text={spec.help}>{spec.label}{spec.unit_display ? ` (${unitLabel(spec.unit_display)})` : ''}</InfoTip>
      </span>
      <span className="text-[10px] font-mono text-muted italic whitespace-nowrap" title="Derived: recomputed on save">
        {d === null ? '—' : fmt(d)} <span className="not-italic text-[9px]">derived</span>
      </span>
    </div>
  )
}

function EnumInput({ spec, fs, set }: { spec: AssetFieldSpec; fs: FS; set: SetFS }) {
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-[9px] text-muted truncate"><InfoTip text={spec.help}>{spec.label}</InfoTip></span>
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
function PartsTable({ parts, fs, set }: { parts: AssetFieldSpec[]; fs: FS; set: SetFS }) {
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

/**
 * The edit form's fields. `current` feeds the derived rows; `onCheck` hooks a
 * boolean's side effect (Extendable seeds the minimum); `after` places a card's
 * own control after a field (e.g. the per-period bounds link after the maximum).
 */
export function AssetFieldsEdit({ schema, fs, set, current, carriers, buses, onCheck, after, hidden }: {
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
}) {
  // The typed parts of a composite asset render together, as one table where
  // the first of them would have been (the Investment group).
  const parts = schemaFields(schema).filter(f => f.part && !f.derived)
  const firstPart = parts[0]?.key
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
                    <PartsTable parts={parts} fs={fs} set={set} />
                    {/* The parts draw as one table, so a control placed after any
                        part goes after the table. */}
                    {parts.map(p => <Fragment key={p.key}>{after?.[p.key]}</Fragment>)}
                  </>
                )
                  : f.derived ? <DerivedRow spec={f} value={current[f.key]} />
                  : f.kind === 'bool' ? <ChkInput label={f.label} k={f.key} fs={fs} set={set} tip={f.help} onCheck={onCheck?.[f.key]} />
                  : f.kind === 'bus' ? <BusSelect k={f.key} fs={fs} set={set} buses={buses} tip={f.help} label={f.label} />
                  : f.kind === 'carrier' ? <CarrierGroupSelect k={f.key} fs={fs} set={set} carriers={carriers} tip={f.help} />
                  : f.kind === 'text' ? <TxtInput label={f.label} k={f.key} fs={fs} set={set} tip={f.help} />
                  : f.kind === 'enum' ? <EnumInput spec={f} fs={fs} set={set} />
                  : <NumInput label={f.label} k={f.key} fs={fs} set={set} unit={f.unit_display ? unitLabel(f.unit_display) : undefined} tip={f.help} />}
                {f.key !== firstPart && after?.[f.key]}
              </Fragment>
            ))}
          </div>
        )
      })}
    </>
  )
}

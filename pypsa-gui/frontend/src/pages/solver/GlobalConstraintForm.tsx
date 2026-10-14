// M5b (plan 2026-10-14-m5-non-asset-forms.md): Solver Settings' global
// constraint form, rendered from the asset schema (services/asset_schema/
// fields.py) and used both to add a constraint and to edit one in place.
//
// Each type reads its own fields: the summed carrier column for primary
// energy, the carrier for the two carrier-scoped limits (the descriptors'
// reveal rules). A field the chosen type does not read saves null, so a
// constraint switched from a CO₂ cap to an operational limit does not keep
// "co2_emissions" in its carrier attribute.
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { networkApi } from '../../api/network'
import CarrierSelect from '../../components/CarrierSelect'
import { useAssetSchema } from '../../hooks/useAssetSchema'
import { AssetFieldsEdit, fieldHelp, schemaFields } from '../../layout/properties/AssetFields'
import { EditShell, InfoTip, type FS, type SetFS } from '../../layout/properties/cardKit'
import { useUIStore } from '../../store/uiStore'
import { buildPatch, isRevealed, seedForm } from '../../utils/assetForm'
import { nk } from '../../utils/queryKeys'

export interface GlobalConstraintRow {
  name: string
  type: string
  sense: string
  constant: number
  carrier_attribute?: string | null
  carrier?: string | null
  investment_period?: number | null
}

/** A new constraint starts as a horizon-wide CO₂ cap, the most common one. */
export const NEW_CONSTRAINT: GlobalConstraintRow = {
  name: '', type: 'primary_energy', sense: '<=', constant: 0, carrier_attribute: 'co2_emissions',
  carrier: null, investment_period: null,
}

// Drawn here rather than by the schema renderer: the carrier needs an explicit
// "pick a carrier" choice, and the period is a choice among the model's periods.
const OWN_CONTROLS: ReadonlySet<string> = new Set(['carrier', 'investment_period'])

/** FastAPI's `detail` is a string, or a list of {loc, msg} for a validation error. */
function errorText(e: { response?: { data?: { detail?: unknown } } }, fallback: string): string {
  const raw = e?.response?.data?.detail
  if (typeof raw === 'string') return raw
  if (Array.isArray(raw)) {
    return raw.map((d) => {
      const r = d as { loc?: unknown[]; msg?: string }
      const field = Array.isArray(r.loc) ? r.loc.slice(1).join('.') : ''
      return field ? `${field}: ${r.msg ?? 'invalid'}` : (r.msg ?? 'invalid')
    }).join(' · ')
  }
  return fallback
}

/**
 * The add or edit form. `editing` names the constraint being edited (null to
 * add one); `seed` is the row the form starts from.
 */
export default function GlobalConstraintForm({ editing, seed, periods, onDone }: {
  editing: string | null
  seed: GlobalConstraintRow
  periods: number[]
  onDone: () => void
}) {
  const qc = useQueryClient()
  const currentProject = useUIStore(s => s.currentProject)
  const { data: schema } = useAssetSchema('GlobalConstraint')
  const { data: carriers = [] } = useQuery({ queryKey: nk(currentProject, 'carriers'), queryFn: networkApi.getCarriers })
  const specs = schemaFields(schema)
  const current = seed as unknown as Record<string, unknown>
  const [form, setForm] = useState<FS | null>(null)
  // Seeded once the descriptors have loaded.
  const fs: FS = form ?? (schema ? seedForm(specs, current) : {})
  const set: SetFS = (u) => setForm(prev => (typeof u === 'function' ? u(prev ?? fs) : u))
  const carrierSpec = specs.find(s => s.key === 'carrier')

  const saveMut = useMutation({
    mutationFn: async () => {
      const patch = buildPatch(specs, fs, current)
      for (const s of specs) if (s.blank === 'clear' && !isRevealed(s, fs)) patch[s.key] = null
      const body = { ...patch, name: fs.name.trim(), constant: patch.constant ?? 0 } as Parameters<typeof networkApi.createGlobalConstraint>[0]
      return editing ? networkApi.updateGlobalConstraint(editing, body) : networkApi.createGlobalConstraint(body)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['global_constraints'] })
      toast.success(editing ? 'Constraint saved' : 'Constraint added')
      // Adding keeps the type, so another of the same kind is quick to add.
      if (!editing) setForm({ ...fs, name: '', constant: '0' })
      onDone()
    },
    onError: (e: { response?: { data?: { detail?: unknown } } }) =>
      toast.error(errorText(e, editing ? 'Failed to save constraint' : 'Failed to add constraint')),
  })

  const onSave = () => {
    if (!fs.name?.trim()) { toast.error('Pick a name'); return }
    saveMut.mutate()
  }

  return (
    <EditShell title={editing ? `Edit ${editing}` : 'Add constraint'} onSave={onSave} onCancel={onDone}
      saving={saveMut.isPending}>
      {schema ? (
        <AssetFieldsEdit schema={schema} fs={fs} set={set} current={current} carriers={[]} buses={[]}
          hidden={OWN_CONTROLS} />
      ) : <p className="col-span-2 text-[10px] text-muted">Loading fields…</p>}
      {carrierSpec && isRevealed(carrierSpec, fs) && (
        <div className="col-span-2">
          <CarrierSelect
            label="Carrier" value={fs.carrier ?? ''} onChange={v => set(p => ({ ...p, carrier: v }))}
            allowEmpty placeholder="— pick a carrier —" title={carrierSpec.help}
            wrapperClassName="flex flex-col gap-0.5"
          />
        </div>
      )}
      {/* Horizon-wide sums over every period (one shadow price); a year binds
          in that period only, e.g. a declining CO₂ budget. A single-period
          model has only the horizon, so the choice is hidden. */}
      {periods.length > 0 && (
        <label className="col-span-2 flex flex-col gap-0.5">
          <span className="text-[9px] text-muted">
            <InfoTip text={fieldHelp(schema, 'investment_period')}>Investment period</InfoTip>
          </span>
          <select value={fs.investment_period ?? ''}
            onChange={e => set(p => ({ ...p, investment_period: e.target.value }))}
            className="bg-bg border border-border rounded px-1.5 py-0.5 text-[10px] focus:outline-none focus:border-accent w-full">
            <option value="">Horizon-wide (Σ across every period)</option>
            {periods.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
      )}
    </EditShell>
  )
}

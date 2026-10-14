// M5c (plan 2026-10-14-m5-non-asset-forms.md): a shunt impedance's
// Properties card, rendered from the asset schema (services/asset_schema/
// fields.py) like the Carrier card. A Shunts-tab row opens it. A shunt is read
// only by the AC power flow: the optimisation ignores it.
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil } from 'lucide-react'
import toast from 'react-hot-toast'
import { networkApi } from '../../api/network'
import type { Bus, ShuntImpedance } from '../../api/types'
import { useAssetProvenance } from '../../hooks/useAssetProvenance'
import { useAssetSchema } from '../../hooks/useAssetSchema'
import { useUIStore } from '../../store/uiStore'
import { updateAsset } from '../../utils/assetWrite'
import { buildPatch, seedForm } from '../../utils/assetForm'
import { blockerMessage } from '../../utils/blockerMessage'
import { editScope, loadExtras } from '../../utils/extrasStore'
import { nk } from '../../utils/queryKeys'
import { AssetFieldsEdit, assetReadoutItems, fieldHelp, schemaFields } from './AssetFields'
import { EditShell, ExtrasSection, Row, Section, extrasPatch, seedExtras, type FS } from './cardKit'

export default function ShuntPanel({ name }: { name: string }) {
  const qc = useQueryClient()
  const currentProject = useUIStore(s => s.currentProject)
  const setSelectedComponent = useUIStore(s => s.setSelectedComponent)
  const { data: shunts = [] } = useQuery({
    queryKey: nk(currentProject, 'shunt_impedances'), queryFn: networkApi.getShuntImpedances,
  })
  const { data: buses = [] } = useQuery({ queryKey: nk(currentProject, 'buses'), queryFn: networkApi.getBuses, staleTime: 30_000 })
  const shunt = (shunts as ShuntImpedance[]).find(s => s.name === name)
  const { data: schema } = useAssetSchema('ShuntImpedance')
  const provenance = useAssetProvenance('ShuntImpedance', name)
  const specs = schemaFields(schema)
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState<FS>({})

  const saveMut = useMutation({
    // Chokepoint (utils/assetWrite.ts) owns fetch/spread/PUT/invalidate;
    // the descriptors own the form→patch mapping.
    mutationFn: () => updateAsset<ShuntImpedance>(
      qc, useUIStore.getState().currentProject, 'shunt_impedances', name,
      (current) => {
        const payload = buildPatch(specs, form, current as unknown as Record<string, unknown>) as Partial<ShuntImpedance>
        payload.name = form.name?.trim() || name
        payload.bus = form.bus?.trim() || current.bus
        Object.assign(payload, extrasPatch(form, loadExtras(editScope('ShuntImpedance'))))
        return payload
      }),
    onSuccess: () => {
      setEditing(false)
      toast.success('Shunt saved')
      const newName = form.name?.trim() || name
      if (newName !== name) setSelectedComponent({ type: 'ShuntImpedance', name: newName })
    },
    onError: (e: Error) => toast.error(`Save failed: ${blockerMessage(e)}`),
  })

  if (!shunt) return <p className="p-3 text-xs text-muted">Loading…</p>
  const asset = shunt as unknown as Record<string, unknown>

  const startEdit = () => {
    setForm(seedExtras(shunt, seedForm(specs, asset), loadExtras(editScope('ShuntImpedance'))))
    setEditing(true)
  }

  if (editing) {
    return (
      <div className="p-3">
        <EditShell title={shunt.name} onSave={() => saveMut.mutate()} onCancel={() => setEditing(false)} saving={saveMut.isPending}>
          {schema ? (
            <AssetFieldsEdit schema={schema} fs={form} set={setForm} current={asset} provenance={provenance}
              carriers={[]} buses={buses as Bus[]} />
          ) : <p className="col-span-2 text-[10px] text-muted">Loading fields…</p>}
          <ExtrasSection componentClass="ShuntImpedance" fs={form} set={setForm} curated={Object.keys(form)} />
        </EditShell>
      </div>
    )
  }

  return (
    <div className="p-3">
      <Section title="Connected to">
        <Row label="Bus" value={shunt.bus} tip={fieldHelp(schema, 'bus')} />
      </Section>
      <Section title="Shunt Properties">
        {/* The conductance and susceptance, with their provenance chips. */}
        {assetReadoutItems(schema, asset, undefined, provenance).map(i => (
          <Row key={i.label} label={i.label} value={i.value} unit={i.unit} tip={i.tip} badge={i.badge} />
        ))}
        <p className="text-[10px] text-muted mt-1">Read by the AC power flow only; the optimisation ignores it.</p>
        <button onClick={startEdit}
          className="w-full mt-2 py-1.5 border border-border rounded text-xs text-muted hover:border-accent hover:text-accent transition-colors flex items-center justify-center gap-1.5">
          <Pencil size={11} /> Edit Shunt
        </button>
      </Section>
    </div>
  )
}

// M5b (plan 2026-10-14-m5-non-asset-forms.md): a Carrier's Properties card,
// rendered from the asset schema (services/asset_schema/fields.py) like the
// Bus panel. A Carriers-tab row in the bottom panel opens it. The text columns
// save "" when emptied (their blank policy), and a rename re-points every bus
// and asset on the server, so the selection follows the new name.
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil } from 'lucide-react'
import toast from 'react-hot-toast'
import { networkApi } from '../../api/network'
import type { Carrier } from '../../api/types'
import { useAssetProvenance } from '../../hooks/useAssetProvenance'
import { useAssetSchema } from '../../hooks/useAssetSchema'
import { useUIStore } from '../../store/uiStore'
import { updateAsset } from '../../utils/assetWrite'
import { buildPatch, seedForm } from '../../utils/assetForm'
import { blockerMessage } from '../../utils/blockerMessage'
import { editScope, loadExtras } from '../../utils/extrasStore'
import { nk } from '../../utils/queryKeys'
import { AssetFieldsEdit, assetReadoutItems, fieldHelp, schemaFields } from './AssetFields'
import { EditShell, ExtrasSection, InfoTip, Row, Section, extrasPatch, seedExtras, type FS } from './cardKit'

export default function CarrierPanel({ name }: { name: string }) {
  const qc = useQueryClient()
  const currentProject = useUIStore(s => s.currentProject)
  const setSelectedComponent = useUIStore(s => s.setSelectedComponent)
  const { data: carriers = [] } = useQuery({ queryKey: nk(currentProject, 'carriers'), queryFn: networkApi.getCarriers })
  const carrier = (carriers as Carrier[]).find(c => c.name === name)
  const { data: schema } = useAssetSchema('Carrier')
  const provenance = useAssetProvenance('Carrier', name)
  const specs = schemaFields(schema)
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState<FS>({})

  const saveMut = useMutation({
    // Chokepoint (utils/assetWrite.ts) owns fetch/spread/PUT/invalidate;
    // the descriptors own the form→patch mapping.
    mutationFn: () => updateAsset<Carrier>(
      qc, useUIStore.getState().currentProject, 'carriers', name,
      (current) => {
        const payload = buildPatch(specs, form, current as unknown as Record<string, unknown>) as Partial<Carrier>
        payload.name = form.name?.trim() || name
        Object.assign(payload, extrasPatch(form, loadExtras(editScope('Carrier'))))
        return payload
      }),
    onSuccess: () => {
      setEditing(false)
      toast.success('Carrier saved')
      const newName = form.name?.trim() || name
      if (newName !== name) setSelectedComponent({ type: 'Carrier', name: newName })
    },
    onError: (e: Error) => toast.error(`Save failed: ${blockerMessage(e)}`),
  })

  if (!carrier) return <p className="p-3 text-xs text-muted">Loading…</p>
  const asset = carrier as unknown as Record<string, unknown>

  const startEdit = () => {
    setForm(seedExtras(carrier, seedForm(specs, asset), loadExtras(editScope('Carrier'))))
    setEditing(true)
  }

  if (editing) {
    return (
      <div className="p-3">
        <EditShell title={carrier.name} onSave={() => saveMut.mutate()} onCancel={() => setEditing(false)} saving={saveMut.isPending}>
          {schema ? (
            <AssetFieldsEdit schema={schema} fs={form} set={setForm} current={asset} provenance={provenance}
              carriers={[]} buses={[]} />
          ) : <p className="col-span-2 text-[10px] text-muted">Loading fields…</p>}
          <ExtrasSection componentClass="Carrier" fs={form} set={setForm} curated={Object.keys(form)} />
        </EditShell>
      </div>
    )
  }

  return (
    <div className="p-3">
      <Section title="Carrier Properties">
        <Row label="Display name" value={carrier.nice_name} tip={fieldHelp(schema, 'nice_name')} />
        {carrier.color && (
          <div className="flex justify-between items-baseline py-0.5 border-b border-border/50">
            <span className="text-xs text-muted shrink-0 mr-2">
              <InfoTip text={fieldHelp(schema, 'color')}>Colour</InfoTip>
            </span>
            <span className="text-xs font-mono text-text flex items-center gap-1.5">
              <span data-testid="carrier-swatch" className="inline-block w-2.5 h-2.5 rounded-sm border border-border"
                style={{ background: carrier.color }} />
              {carrier.color}
            </span>
          </div>
        )}
        <Row label="Unit" value={carrier.unit} tip={fieldHelp(schema, 'unit')} />
        {/* The schema's non-identity fields (the CO₂ intensity), with their provenance chips. */}
        {assetReadoutItems(schema, asset, undefined, provenance).map(i => (
          <Row key={i.label} label={i.label} value={i.value} unit={i.unit} tip={i.tip} badge={i.badge} />
        ))}
        <button onClick={startEdit}
          className="w-full mt-2 py-1.5 border border-border rounded text-xs text-muted hover:border-accent hover:text-accent transition-colors flex items-center justify-center gap-1.5">
          <Pencil size={11} /> Edit Carrier
        </button>
      </Section>
    </div>
  )
}

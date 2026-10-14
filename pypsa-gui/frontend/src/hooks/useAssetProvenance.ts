// The per-field provenance of one asset (S1a's store, read through
// `GET /network/asset_schema/{component}/provenance/{name}`), for the
// provenance chips on the Expert card (U3 F part 2; plan §4.3: "the same chip
// appears on the field in the Expert form").
//
// Keyed under the asset class's own network root (`storage_units`,
// `generators`, …), so every invalidation that refreshes the class after an edit,
// a save or a project swap refreshes the provenance too, and no shared key
// list has to learn a new root.
import { useQuery } from '@tanstack/react-query'
import { assetSchemaApi, type ProvenanceEntry } from '../api/assetSchema'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'

const ROOT: Record<string, string> = {
  StorageUnit: 'storage_units', Generator: 'generators', Store: 'stores', Link: 'links', Load: 'loads',
  Line: 'lines', Transformer: 'transformers',
  // M5: the non-asset components with a schema.
  Bus: 'buses', Carrier: 'carriers', GlobalConstraint: 'global_constraints', ShuntImpedance: 'shunt_impedances',
}

export function assetProvenanceKey(project: string | null, componentClass: string, name: string): unknown[] {
  return nk(project, ROOT[componentClass] ?? componentClass, 'provenance', name)
}

/** `{field: entry}`; empty while loading, on error, or when nothing is recorded. */
export function useAssetProvenance(componentClass: string | null, name: string | null):
  Record<string, ProvenanceEntry> {
  const project = useUIStore(s => s.currentProject)
  const q = useQuery({
    queryKey: assetProvenanceKey(project, componentClass ?? '', name ?? ''),
    queryFn: () => assetSchemaApi.provenance(componentClass as string, name as string),
    enabled: !!componentClass && !!name && componentClass in ROOT,
    staleTime: 30_000,
    retry: false,
  })
  return q.data?.fields ?? {}
}

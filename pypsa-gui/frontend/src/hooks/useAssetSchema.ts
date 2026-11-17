import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { assetSchemaApi, type AssetSchema } from '../api/assetSchema'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'

export const TONNE_COST_NOTE = 'Costs on this tonne bus are per tonne (per t/h of capacity). The number is stored as entered, not rescaled from per-kW or per-kWh.'

/**
 * The field descriptors of one component class (S1). Class-level and
 * immutable at runtime, so — like `useCatalog` — the key is NOT project-scoped
 * and `staleTime` is Infinity (the same named exception, spec D24).
 *
 * With an asset `name`, a size field's unit follows that asset's bus (t/h, t on
 * a tonne bus; a cost field's unit is per tonne with scale 1, no kW or kWh factor), read from `/asset_schema/{class}/units/{name}`; that read is
 * project-scoped, since the bus is the asset's own.
 */
export function useAssetSchema(componentClass: string | null, name?: string | null) {
  const project = useUIStore(s => s.currentProject)
  const base = useQuery<AssetSchema>({
    queryKey: ['assetSchema', componentClass],
    queryFn: () => assetSchemaApi.get(componentClass as string),
    enabled: !!componentClass,
    staleTime: Infinity,
    gcTime: Infinity,
  })
  const units = useQuery({
    queryKey: nk(project, 'assetUnits', componentClass ?? '', name ?? ''),
    queryFn: () => assetSchemaApi.units(componentClass as string, name as string),
    enabled: !!componentClass && !!name,
    staleTime: 30_000,
    retry: false,
  })
  const over = units.data?.units
  const costs = units.data?.costs
  const data = useMemo(() => {
    const noUnits = !over || Object.keys(over).length === 0
    const noCosts = !costs || Object.keys(costs).length === 0
    if (!base.data || (noUnits && noCosts)) return base.data
    return {
      ...base.data,
      groups: base.data.groups.map(g => ({
        ...g,
        fields: g.fields.map(f => {
          const c = costs?.[f.key]
          if (c) return { ...f, unit_display: c.unit, unit_storage: c.unit, scale: c.scale, unit_note: TONNE_COST_NOTE }
          return over?.[f.key] ? { ...f, unit_display: over[f.key], unit_storage: over[f.key] } : f
        }),
      })),
    }
  }, [base.data, over, costs])
  return { ...base, data }
}

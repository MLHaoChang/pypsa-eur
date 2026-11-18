import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { assetSchemaApi, type AssetSchema } from '../api/assetSchema'
import { networkApi } from '../api/network'
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
 * project-scoped, since the bus is the asset's own. With a `bus` and no name (the creation form) the same
 * overrides come from the bus the new asset will attach to. The key's root `assetUnits` is in
 * `COMPONENT_QUERY_ROOTS`, so every asset write refetches it.
 */
export function useAssetSchema(componentClass: string | null, name?: string | null, bus?: string | null) {
  const project = useUIStore(s => s.currentProject)
  const base = useQuery<AssetSchema>({
    queryKey: ['assetSchema', componentClass],
    queryFn: () => assetSchemaApi.get(componentClass as string),
    enabled: !!componentClass,
    staleTime: Infinity,
    gcTime: Infinity,
  })
  // Every network write, undo, delete-undo and project restore moves the edit counter (`network_revision`), so
  // keying the units on it re-reads them after ANY change to the asset, its bus or the bus's unit, wherever it
  // was made (BottomPanel, header undo, chat). Shares the 3 s `undoInfo` read the app already polls.
  const wantsUnits = !!componentClass && (!!name || !!bus)
  const rev = useQuery({
    queryKey: nk(project, 'undoInfo'),
    queryFn: () => networkApi.undoInfo(),
    enabled: wantsUnits && !!project,
    staleTime: 2000,
  }).data?.network_revision
  const units = useQuery<{ units: Record<string, string>; costs?: Record<string, { unit: string; scale: number }> }>({
    queryKey: nk(project, 'assetUnits', componentClass ?? '', name ?? '', bus ?? '', rev ?? ''),
    queryFn: () => (name
      ? assetSchemaApi.units(componentClass as string, name)
      : assetSchemaApi.unitsForBus(componentClass as string, bus as string)),
    enabled: wantsUnits,
    staleTime: 30_000,
    retry: false,
  })
  const over = units.data?.units
  const costs = units.data?.costs
  const data = useMemo(() => {
    const noUnits = !over || Object.keys(over).length === 0
    const noCosts = !costs || Object.keys(costs).length === 0
    // Until the units of the current network state are read, no schema: a cost typed under the class scale
    // (EUR/kW, x1000) would be stored wrong on a tonne bus. A failed read leaves the form closed, never guessing.
    if (wantsUnits && !units.isSuccess) return undefined
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
  }, [base.data, over, costs, wantsUnits, units.isSuccess])
  return { ...base, data, unitsReady: !wantsUnits || units.isSuccess,
    /** The units read failed: nothing is guessed; `retryUnits` asks again. */
    unitsFailed: wantsUnits && units.isError, retryUnits: () => { void units.refetch() } }
}

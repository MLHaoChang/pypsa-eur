import { useQuery } from '@tanstack/react-query'
import { assetSchemaApi, type AssetSchema } from '../api/assetSchema'

/**
 * The field descriptors of one component class (S1). Class-level and
 * immutable at runtime, so — like `useCatalog` — the key is NOT project-scoped
 * and `staleTime` is Infinity (the same named exception, spec D24).
 */
export function useAssetSchema(componentClass: string | null) {
  return useQuery<AssetSchema>({
    queryKey: ['assetSchema', componentClass],
    queryFn: () => assetSchemaApi.get(componentClass as string),
    enabled: !!componentClass,
    staleTime: Infinity,
    gcTime: Infinity,
  })
}

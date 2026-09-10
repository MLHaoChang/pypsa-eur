import { useQuery } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import type { CatalogPayload } from '../api/types'

/**
 * PyPSA's attribute catalog for one component class.
 *
 * The query key is deliberately NOT nk(projectId, …), against
 * .cursor/rules/pypsa-gui-frontend.mdc:15-16. This is a named exception on the
 * same grounds as ['changelog'] (BottomPanel.tsx:288): the catalog is
 * class-level metadata, identical across every project and immutable at
 * runtime, so project-scoping it would refetch nine identical payloads on
 * every project switch. `staleTime: Infinity` follows for the same reason.
 * Recorded here because the exception is invisible at the call site (spec D24).
 */
export function useCatalog(componentClass: string | null) {
  return useQuery<CatalogPayload>({
    queryKey: ['catalog', componentClass],
    queryFn: () => networkApi.getCatalog(componentClass as string),
    enabled: !!componentClass,
    staleTime: Infinity,
    gcTime: Infinity,
  })
}

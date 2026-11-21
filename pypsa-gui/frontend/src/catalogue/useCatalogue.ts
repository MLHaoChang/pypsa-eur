import { useQuery } from '@tanstack/react-query'
import { fetchCatalogue, type Catalogue } from './api'

/** The query key every catalogue reader shares (the catalogue is project-free). */
export const CATALOGUE_KEY = ['catalogue'] as const

/**
 * The Parameter catalogue, fetched once per session; `version` (its content hash)
 * tells readers when it changed. `enabled: false` leaves it unfetched until needed.
 */
export function useCatalogue(enabled = true): Catalogue | undefined {
  const { data } = useQuery({
    queryKey: CATALOGUE_KEY,
    queryFn: fetchCatalogue,
    enabled,
    staleTime: Infinity,
  })
  return data
}

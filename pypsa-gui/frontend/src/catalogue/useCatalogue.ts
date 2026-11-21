import { useQuery } from '@tanstack/react-query'
import { fetchCatalogue, type Catalogue } from './api'

/** The query key every catalogue reader shares (the catalogue is project-free). */
export const CATALOGUE_KEY = ['catalogue'] as const

/**
 * The Parameter catalogue. Project-free, so the key carries no project. Each new
 * reader (the palette opening) revalidates it with its ETag: unchanged, that costs a
 * 304 and the held body. `enabled: false` leaves it unfetched until needed.
 */
export function useCatalogue(enabled = true): Catalogue | undefined {
  const { data } = useQuery({
    queryKey: CATALOGUE_KEY,
    queryFn: fetchCatalogue,
    enabled,
    staleTime: 0,
    refetchOnWindowFocus: false,
  })
  return data
}

/**
 * One fetch of /api/local-settings, shared by the pane and the nav row.
 *
 * `data === null` means the routes 404 — this build is not the desktop app —
 * and BOTH consumers hide themselves on it. A nav entry that opens an empty
 * pane is worse than no nav entry.
 *
 * `staleTime: Infinity`: neither the key hint nor the log path changes except
 * through this pane, which invalidates the key explicitly after a write.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchLocalSettings, type LocalSettingsState } from '../api/localSettings'

export const LOCAL_SETTINGS_KEY = ['localSettings'] as const

export function useLocalSettings() {
  return useQuery<LocalSettingsState | null>({
    queryKey: LOCAL_SETTINGS_KEY,
    queryFn: fetchLocalSettings,
    staleTime: Infinity,
    retry: false,
  })
}

/** True only once we know the routes exist. Undefined-safe while loading. */
export function useLocalSettingsAvailable(): boolean {
  const { data } = useLocalSettings()
  return data != null
}

export function useInvalidateLocalSettings() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: LOCAL_SETTINGS_KEY })
}

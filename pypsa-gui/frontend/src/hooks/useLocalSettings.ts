/**
 * One fetch of /api/local-settings, shared by the pane and the nav row.
 *
 * `data === null` means the routes 404 — this build is not the desktop app —
 * and BOTH consumers hide themselves on it. A nav entry that opens an empty
 * pane is worse than no nav entry.
 *
 * `staleTime: Infinity`: neither the key hint nor the log path changes except
 * through this pane, which invalidates the key explicitly after a write.
 *
 * `retry: 2`, deliberately not `false`. With `staleTime: Infinity` and the
 * app's global `refetchOnWindowFocus: false` (main.tsx), this query fetches
 * ONCE per session and then never again on its own — so a single transient
 * GET failure (not a 404, an actual network blip) would otherwise hide the
 * Sidebar row AND the ⌘K entry for the rest of the session, silently, for
 * the one feature whose entire point is that there is no other door to it.
 * A real 404 (web mode) still resolves to `null` on the first try —
 * `fetchLocalSettings` maps that before it ever reaches the query's error
 * path — so retrying costs nothing there. Do NOT change the error branch to
 * show the pane on failure instead: a web-mode 401 arriving before the
 * router's auth gate also lands in the error branch, and hiding is correct
 * in that case.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchLocalSettings, type LocalSettingsState } from '../api/localSettings'

export const LOCAL_SETTINGS_KEY = ['localSettings'] as const

export function useLocalSettings() {
  return useQuery<LocalSettingsState | null>({
    queryKey: LOCAL_SETTINGS_KEY,
    queryFn: fetchLocalSettings,
    staleTime: Infinity,
    retry: 2,
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

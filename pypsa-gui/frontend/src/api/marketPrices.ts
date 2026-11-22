// ENTSO-E day-ahead prices (SCU06 S1): the install-wide access token
// (super-admin) and a project's fetch into the org Library.
import client from './client'
import type { LibraryRef } from './types'

const QUIET = { skipErrorToast: true } as const
const enc = encodeURIComponent

export interface TokenStatus { token_set: boolean; can_set_token: boolean }
export interface BiddingZone { code: string; label: string; first_year: number }
export interface ProjectMarketPrices extends TokenStatus {
  zones: BiddingZone[]
  pin_zone: string | null
  chosen_zone: string | null
  zone: string | null
  last_year: number
  can_fetch: boolean
}
export interface DayAheadResult {
  ref: LibraryRef
  zone: string
  year: number
  fetched: string
  source: string
}

export const marketPricesApi = {
  project: (project: string) =>
    client.get<ProjectMarketPrices>(`/projects/${enc(project)}/market-prices`).then(r => r.data),
  fetchDayAhead: (project: string, zone: string, year: number) =>
    client.post<DayAheadResult>(`/projects/${enc(project)}/market-prices/day-ahead`,
      { zone, year }, QUIET).then(r => r.data),
  saveToken: (value: string) =>
    client.put<TokenStatus>('/market-prices/token', { value }, QUIET).then(r => r.data),
  clearToken: () => client.delete<TokenStatus>('/market-prices/token', QUIET).then(r => r.data),
}

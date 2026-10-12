import { client } from './client'

// The map's address box (plan M3): a proxy over the Nominatim-compatible
// endpoint `PYPSAGUI_GEOCODER_URL` names. `status` says whether the box is
// shown at all — unset means hidden, so nobody hits the public Nominatim
// by accident (its usage policy forbids heavy automated use).

export interface GeocodeHit { display_name: string; lat: number; lon: number }

export const geocodeApi = {
  status: () => client.get<{ configured: boolean; setting: string }>('/geocode/status').then(r => r.data),
  search: (q: string) => client.get<GeocodeHit[]>('/geocode', { params: { q }, skipErrorToast: true }).then(r => r.data),
}

// Assistant-assurance 06 (A5.2): a results tab's payload with its displayed-data
// binding, from /api/results/displayed/<tab>. The tab renders `data` and marks its
// root with `binding`, so a chat navigation ACK proves the numbers shown.
import { client } from './client'
import { resultsApi } from './simulation'
import type { GridRiskProduction } from './types'
import type { ResultsSourceBinding } from '../utils/resultsSource'

export interface Displayed<T> {
  status: 'ok'
  data: T
  binding: ResultsSourceBinding
}

export type EmissionsPayload = NonNullable<Awaited<ReturnType<typeof resultsApi.getEmissions>>>

const read = <T>(tab: string) => client
  .get<Displayed<T>>(`/results/displayed/${tab}`, { skipErrorToast: true })
  .then(r => (r.status === 204 || !r.data ? null : r.data))

export const displayedResultsApi = {
  emissions: () => read<EmissionsPayload>('emissions'),
  gridRisk: () => read<GridRiskProduction>('grid_risk'),
}

// Assistant-assurance 06 (A5.2): a results tab's payload with its displayed-data
// binding, from /api/results/displayed/<tab>. The tab renders `data` and marks its
// root with `binding`, so a chat navigation ACK proves the numbers shown.
import { client } from './client'
import { resultsApi, type TSRange } from './simulation'
import type { GridRiskProduction } from './types'
import type { ResultsSourceBinding } from '../utils/resultsSource'

export interface Displayed<T> {
  status: 'ok'
  data: T
  binding: ResultsSourceBinding
}

export type EmissionsPayload = NonNullable<Awaited<ReturnType<typeof resultsApi.getEmissions>>>

export interface CurtailmentParts {
  curtailment: Awaited<ReturnType<typeof resultsApi.getCurtailment>>
  /** null when the generators route had nothing (the tab then shows curtailment alone). */
  generators: Awaited<ReturnType<typeof resultsApi.getGeneratorResults>> | null
}

export interface PricesParts {
  prices: NonNullable<Awaited<ReturnType<typeof resultsApi.getPrices>>>
  /** null when the drivers route had nothing (the tab then shows the prices alone). */
  price_drivers: Awaited<ReturnType<typeof resultsApi.getPriceDrivers>> | null
}

export interface CapexParts {
  cost_breakdown: NonNullable<Awaited<ReturnType<typeof resultsApi.getCostBreakdown>>>
  economics_by_carrier: Awaited<ReturnType<typeof resultsApi.getEconomicsByCarrier>>
}

const windowParams = (range?: TSRange) => (range ? { from: range.from, to: range.to } : {})

const bound = <T>(r: { status: number; data: Displayed<T> }) => (r.status === 204 || !r.data ? null : r.data)

export const displayedResultsApi = {
  // As /results/emissions did: a server error toasts.
  emissions: () => client.get<Displayed<EmissionsPayload>>('/results/displayed/emissions').then(bound),
  gridRisk: () => client.get<Displayed<GridRiskProduction>>('/results/displayed/grid_risk',
    { skipErrorToast: true }).then(bound),
  /** Curtailment and its plants' LP dispatch over one window, read together (A5.2 S3). */
  curtailment: (range?: TSRange) => client.get<Displayed<CurtailmentParts>>('/results/displayed/curtailment',
    range ? { params: { from: range.from, to: range.to } } : undefined).then(bound),
  /** The LP prices over one window and their drivers at one threshold, read together (A5.2 S3b). */
  prices: (threshold: number, limit: number, range?: TSRange) => client.get<Displayed<PricesParts>>(
    '/results/displayed/prices', { params: { source: 'lopf', threshold, limit, ...windowParams(range) } }).then(bound),
  /** The cost breakdown and economics by carrier, read together (A5.2 S3b). */
  capex: () => client.get<Displayed<CapexParts>>('/results/displayed/capex').then(bound),
  /** The storage units' LP dispatch over one window (A5.2 S3b). */
  storage: (range?: TSRange) => client.get<Displayed<NonNullable<Awaited<ReturnType<typeof resultsApi.getStorageDispatchResults>>>>>(
    '/results/displayed/storage', range ? { params: windowParams(range) } : undefined).then(bound),
}

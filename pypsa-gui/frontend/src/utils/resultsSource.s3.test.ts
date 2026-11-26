// Assistant-assurance 06, A5.2 (S3): a bound view records the period, window and
// week its route was called with (null: the route's default) and, for a
// multi-payload tab, the hash of the part the chat read names. The ACK reader
// must echo those keys exactly, or the server's whole-binding compare fails.
import { afterEach, describe, expect, it } from 'vitest'
import { displayedResultsSource } from './resultsSource'

const base = {
  version: 1, project_id: 'p-1', project_name: 'Demo', scope: 'current', view: 'loadshape',
  input_sha256: 'a'.repeat(64), data_sha256: 'b'.repeat(64),
}

function mark(tab: string, binding: object) {
  const el = document.createElement('div')
  el.dataset.resultTab = tab
  el.dataset.resultBinding = JSON.stringify(binding)
  document.body.appendChild(el)
}

afterEach(() => { document.body.innerHTML = '' })

describe('displayedResultsSource keeps the S3 binding keys', () => {
  it('echoes null and numeric period, window and week', () => {
    const binding = { ...base, period: null, window_from: 3, window_to: null, week: 2 }
    mark('loadshape', binding)
    expect(displayedResultsSource('loadshape')).toEqual(binding)
  })

  it('echoes a multi-payload view\'s read_sha256', () => {
    const binding = { ...base, view: 'curtailment', read_sha256: 'c'.repeat(64), window_from: null, window_to: null }
    mark('curtailment', binding)
    expect(displayedResultsSource('curtailment')).toEqual(binding)
  })

  it('echoes the S3b prices and capex keys', () => {
    const prices = { ...base, view: 'prices', source: 'lopf', window_from: null, window_to: 4,
      threshold: 2000, limit: 200, price_limit: 'mip_duals_not_marginal', read_sha256: 'c'.repeat(64) }
    mark('prices', prices)
    expect(displayedResultsSource('prices')).toEqual(prices)
    const capex = { ...base, view: 'capex', cost_breakdown_sha256: 'c'.repeat(64),
      economics_by_carrier_sha256: 'd'.repeat(64) }
    mark('capex', capex)
    expect(displayedResultsSource('capex')).toEqual(capex)
  })

  it('echoes the S3b-2 dispatch source and every part hash', () => {
    const parts = ['cost_breakdown', 'generators', 'curtailment', 'storage', 'storage_dispatch', 'store_dispatch',
      'store_energy', 'loads', 'links', 'lost_load', 'economics_by_carrier']
    const dispatch = { ...base, view: 'dispatch', source: 'ac_pf', window_from: 0, window_to: null,
      ...Object.fromEntries(parts.map(p => [`${p}_sha256`, 'e'.repeat(64)])) }
    mark('dispatch', dispatch)
    expect(displayedResultsSource('dispatch')).toEqual(dispatch)
  })
})

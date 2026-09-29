// Guided-mode spec §5.8 / §5.10: every term a hub-design card explains comes
// from the guide catalogue, with an offline fallback identical to it. The
// catalogue is imported straight from the backend package so a key renamed
// or dropped there fails the frontend suite too. (The frozen build ships the
// same file — `smoke/check_bundle.ROOTED` pins
// `data/guides/eh_fmea_guide.json`, and backend `test_guides.py` checks it.)
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import guide from '../../../../../backend/data/guides/eh_fmea_guide.json'
import { guidesApi } from '../../../components/GuidedTour'
import { Term, TERM_FALLBACK, type TermKey } from './Term'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const fields = (guide as { fields: Record<string, string> }).fields

describe('TERM_FALLBACK', () => {
  it('every key the cards use is a catalogue field', () => {
    const missing = Object.keys(TERM_FALLBACK).filter(k => !(k in fields))
    expect(missing).toEqual([])
  })

  it('every fallback is the catalogue text, word for word', () => {
    for (const [k, text] of Object.entries(TERM_FALLBACK)) {
      expect({ k, text }).toEqual({ k, text: fields[k] })
    }
  })

  it('covers the twenty hub-design fields', () => {
    expect(Object.keys(TERM_FALLBACK).sort()).toEqual([
      'hub_start', 'hub_site', 'hub_goal', 'hub_results', 'hub_improve',
      'site_type', 'grid_connection', 'critical_load', 'grid_strength',
      'outage_data', 'shortfall_hours', 'energy_strictness', 'verdict',
      'cost_at_target', 'top_risks', 'not_established', 'stress_scenario',
      'fmea_check', 'template_provenance', 'voll_plain',
    ].sort())
  })
})

function mount(k: TermKey) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><Term k={k}>Allowed shortfall</Term></QueryClientProvider>)
}

describe('<Term>', () => {
  it('renders the label with a help glyph under term-{k}', async () => {
    vi.spyOn(guidesApi, 'getGuide').mockResolvedValue(
      { version: 1, tours: {}, fields: { shortfall_hours: 'From the server.' } })
    mount('shortfall_hours')
    const el = screen.getByTestId('term-shortfall_hours')
    expect(el.textContent).toContain('Allowed shortfall')
    expect(el.querySelector('svg')).not.toBeNull()   // the "?" glyph
    expect(el.getAttribute('data-tip')).toBe(TERM_FALLBACK.shortfall_hours)
    await vi.waitFor(() => expect(el.getAttribute('data-tip')).toBe('From the server.'))
  })

  it('offline: the fallback text is the hover', async () => {
    vi.spyOn(guidesApi, 'getGuide').mockRejectedValue(new Error('offline'))
    mount('voll_plain')
    await new Promise(r => setTimeout(r, 0))
    expect(screen.getByTestId('term-voll_plain').getAttribute('data-tip'))
      .toBe(TERM_FALLBACK.voll_plain)
  })
})

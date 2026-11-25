// Guided/Expert parity for the front (DC review 01, UX 28): the Guided
// section-10 tile and the Expert panel read the same records and show the same
// rows, key and notices; "Use this design" applies, revision-checked, in both;
// the Guided link to Expert goes through withReturnFrom.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { frontApi, type FrontRecord } from '../../../api/front'
import { networkApi } from '../../../api/network'
import { useUIStore } from '../../../store/uiStore'
import { FrontPanel } from '../FrontPanel'
import { FrontTile } from '../../hubDesign/cards/FrontTile'
import { OUT_OF_DATE_TEXT } from './frontModel'
import { degenerateRecord, DEGENERATE_TEXT, record } from './testFixtures'

vi.mock('../../../api/front', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/front')>()
  return { ...actual, frontApi: { get: vi.fn(), getEh: vi.fn(), start: vi.fn(), abort: vi.fn(), apply: vi.fn() } }
})
vi.mock('../../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi, undoInfo: vi.fn() } }
})

function wrap(children: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

async function renderExpert() {
  const r = render(wrap(<FrontPanel />))
  fireEvent.click(screen.getByRole('button', { name: /Cost, reliability and CO₂ front/ }))
  await screen.findByTestId('front-view-table')
  return r
}
async function renderGuided() {
  const r = render(wrap(<FrontTile />))
  await screen.findByTestId('hub-front-view-table')
  return r
}

const rowsText = (testId: string) =>
  within(screen.getByTestId(`${testId}-table`)).getAllByRole('row').map(r => r.textContent)

function serve(front: FrontRecord | null, eh: FrontRecord | null = null) {
  vi.mocked(frontApi.get).mockResolvedValue(front)
  vi.mocked(frontApi.getEh).mockResolvedValue(eh)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', uiMode: 'guided', activeSlidePanel: 'hubDesign', flowReturn: null })
  vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 0, unsaved: false, network_revision: 7 })
  serve(record())
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('front parity', () => {
  it('Guided and Expert show the same rows, key and notices', async () => {
    serve(degenerateRecord())
    await renderExpert()
    const expert = {
      rows: rowsText('front-view'),
      key: screen.getByTestId('front-view-key').textContent,
      notices: screen.getAllByTestId('front-view-notice').map(n => n.textContent),
    }
    cleanup()
    await renderGuided()
    expect(rowsText('hub-front-view')).toEqual(expert.rows)
    expect(screen.getByTestId('hub-front-view-key').textContent).toBe(expert.key)
    expect(screen.getAllByTestId('hub-front-view-notice').map(n => n.textContent)).toEqual(expert.notices)
    expect(expert.notices).toEqual([DEGENERATE_TEXT])
  })

  it.each([['expert', renderExpert, 'front-view'], ['guided', renderGuided, 'hub-front-view']] as const)(
    '%s: a key for every encoding, units in the headers, LOLE/EUE/CI expanded', async (_m, renderIt, id) => {
      await renderIt()
      const key = screen.getByTestId(`${id}-key`).textContent ?? ''
      expect(key).toContain('CO₂ (t/yr):')
      expect(screen.getByTestId(`${id}-key-co2`).textContent).toMatch(/1,500.*2,000/)
      expect(key).toContain('hollow: dominated')
      expect(key).toContain('whiskers: 95 % confidence interval (CI) of the EUE, MWh/yr')
      const view = screen.getByTestId(id).textContent ?? ''
      expect(view).toContain('expected unserved energy (EUE)')
      expect(view).toContain('loss-of-load expectation (LOLE)')
      const heads = within(screen.getByTestId(`${id}-table`)).getAllByRole('columnheader').map(h => h.textContent)
      expect(heads).toEqual(expect.arrayContaining([
        'Reserve margin (%)', 'CO₂ limit (t/yr)', 'Cost (million €/yr)', 'EUE (MWh/yr) · CI',
        'LOLE (h/yr) · CI', 'CO₂ (t/yr)',
      ]))
      expect(screen.getByTestId(`${id}-built-0`).textContent)
        .toBe('Capex 0.70 · opex 0.30 million €/yr · built: Generator Gas turbine 10 MW · Storage BESS 4 MW · Store H2 tank 20 MWh')
      expect(view.toLowerCase()).not.toContain('stale')
    })

  it.each([['expert', renderExpert, 'front-view'], ['guided', renderGuided, 'hub-front-view']] as const)(
    '%s: the cost header carries the scale, no cost cell carries a suffix', async (_m, renderIt, id) => {
      await renderIt()
      const table = screen.getByTestId(`${id}-table`)
      const heads = within(table).getAllByRole('columnheader').map(h => h.textContent ?? '')
      const col = heads.indexOf('Cost (million €/yr)')
      expect(col).toBeGreaterThan(-1)
      const cells = within(table).getAllByRole('row').slice(1)
        .map(r => r.querySelectorAll('td'))
        .filter(tds => tds.length === heads.length)
        .map(tds => tds[col].textContent ?? '')
      expect(cells).toEqual(['1.00', '1.05', '1.20', '1.25'])
      for (const c of cells) expect(c).toMatch(/^[\d.,]+$/)
      for (const r of [0, 1, 2, 3]) {
        expect(screen.getByTestId(`${id}-built-${r}`).textContent).toMatch(/^Capex [\d.]+ · opex [\d.]+ million €\/yr · /)
      }
    })

  it.each([['expert', renderExpert, 'front-view'], ['guided', renderGuided, 'hub-front-view']] as const)(
    '%s: one "Use this design" per solved point, applied with the revision last seen', async (_m, renderIt, id) => {
      vi.mocked(frontApi.apply).mockResolvedValue({ applied: 2, fixed: 3, network_revision: 8 })
      await renderIt()
      const table = screen.getByTestId(`${id}-table`)
      expect(within(table).getAllByRole('button', { name: 'Use this design' })).toHaveLength(4)
      fireEvent.click(screen.getByTestId(`${id}-use-2`))
      await waitFor(() => expect(frontApi.apply).toHaveBeenCalledWith(2, 7))
    })

  it.each([['expert', renderExpert, 'front-view'], ['guided', renderGuided, 'hub-front-view']] as const)(
    '%s: after Apply, the ruling text, the chosen point and a re-solve offer', async (_m, renderIt, id) => {
      serve(record({ applied: { index: 2, network_revision: 8 }, edited_since_study: true }))
      await renderIt()
      const notice = screen.getByTestId(`${id}-out-of-date`)
      expect(notice.textContent).toContain(OUT_OF_DATE_TEXT)
      expect(within(notice).getByRole('button', { name: 'Run the solve' })).toBeTruthy()
      expect(screen.getByTestId(`${id}-row-2`).getAttribute('data-chosen')).toBe('true')
      expect(screen.getByTestId(`${id}-key-chosen`)).toBeTruthy()
      // The study is out of date: none of its designs can be used again.
      expect(screen.getByTestId(`${id}-use-0`).hasAttribute('disabled')).toBe(true)
    })

  it.each([['expert', renderExpert, 'front-view'], ['guided', renderGuided, 'hub-front-view']] as const)(
    '%s: a revision refusal is one plain line with a retry that reads the revision again', async (_m, renderIt, id) => {
      vi.mocked(frontApi.apply)
        .mockRejectedValueOnce({ response: { status: 409, data: { detail: 'expected network revision 7, it is 9' } } })
        .mockResolvedValueOnce({ applied: 1, fixed: 3, network_revision: 10 })
      await renderIt()
      fireEvent.click(screen.getByTestId(`${id}-use-1`))
      const line = await screen.findByTestId(`${id}-refusal`)
      expect(line.textContent).toBe('The network changed while you were choosing, so nothing was applied.Try again')
      vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 0, unsaved: false, network_revision: 9 })
      fireEvent.click(screen.getByTestId(`${id}-retry`))
      await waitFor(() => expect(frontApi.apply).toHaveBeenLastCalledWith(1, 9))
      await waitFor(() => expect(screen.queryByTestId(`${id}-refusal`)).toBeNull())
    })

  it('the Energy Hub front is read-only on both surfaces, and the switch picks it', async () => {
    serve(record(), record({ source: 'eh_study', applicable: false }))
    await renderGuided()
    fireEvent.click(screen.getByTestId('hub-front-view-source-eh_study'))
    expect(screen.getByTestId('hub-front-view').getAttribute('data-source')).toBe('eh_study')
    expect(screen.queryByRole('button', { name: 'Use this design' })).toBeNull()
    expect(screen.getByTestId('hub-front-view-read-only')).toBeTruthy()
  })

  it('"Run the study in Expert" opens the Expert panel with Back to Results', async () => {
    serve(null)
    render(wrap(<FrontTile />))
    fireEvent.click(await screen.findByTestId('hub-front-run-in-expert'))
    const ui = useUIStore.getState()
    expect(ui.uiMode).toBe('expert')
    expect(ui.activeSlidePanel).toBe('results')
    expect(ui.resultsTabRequest).toBe('adequacy')
    expect(ui.resultsSectionRequest).toBe('front')
    expect(ui.flowReturn?.label).toBe('Results')
    expect(ui.flowReturn?.mode).toBe('guided')
  })
})

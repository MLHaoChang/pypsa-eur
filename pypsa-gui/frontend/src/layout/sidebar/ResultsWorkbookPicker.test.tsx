import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ioApi } from '../../api/io'
import { ResultsWorkbookPicker, WORKBOOK_DEFAULT, workbookBlock, type WorkbookChoice } from './ResultsWorkbookPicker'

vi.mock('../../api/io')

const row = (view_id: string, label: string, card: string, card_label: string, selected: boolean,
  available = true, reason: string | null = null) =>
  ({ view_id, label, card, card_label, selected, available, reason })
// The backend's words differ from the Results panel's ("Load flow" vs "Load Flow"): the picker uses the panel's.
const VIEWS = [
  row('capex', 'Capacity expansion', 'capacity', 'Capacity expansion', true),
  row('dispatch', 'Dispatch', 'capacity', 'Capacity expansion', true),
  row('loadflow', 'Load flow', 'grid', 'AC power flow & grid', false),
  row('adequacy', 'Adequacy', 'reliability', 'Reliability', false),
  row('fmea', 'FMEA', 'reliability', 'Reliability', false, false, 'No results for this view in this solve'),
]

function Harness({ seen }: { seen: (v: WorkbookChoice) => void }) {
  const [v, setV] = useState<WorkbookChoice>(WORKBOOK_DEFAULT)
  return <ResultsWorkbookPicker value={v} onChange={n => { setV(n); seen(n) }} />
}

function setup() {
  vi.mocked(ioApi.resultsWorkbookViews).mockResolvedValue(VIEWS)
  const seen = vi.fn()
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Harness seen={seen} />
    </QueryClientProvider>,
  )
  return seen
}

describe('ResultsWorkbookPicker', () => {
  it('pre-ticks the cards\' tabs and locks them', async () => {
    setup()
    const capex = await screen.findByLabelText('Capacity Expansion') as HTMLInputElement
    expect(capex.checked).toBe(true)
    expect(capex.disabled).toBe(true)
    const lf = screen.getByLabelText('Load Flow') as HTMLInputElement
    expect(lf.checked).toBe(false)
    expect(lf.disabled).toBe(false)
  })

  it('ticking a further tab adds it to the choice', async () => {
    const seen = setup()
    await userEvent.click(await screen.findByLabelText('Load Flow'))
    expect(seen).toHaveBeenLastCalledWith({ extra: ['loadflow'], all: false })
    await userEvent.click(screen.getByLabelText('Load Flow'))
    expect(seen).toHaveBeenLastCalledWith({ extra: [], all: false })
  })

  it('"All result tabs" ticks and locks every tab', async () => {
    const seen = setup()
    await userEvent.click(await screen.findByLabelText('All result tabs'))
    expect(seen).toHaveBeenLastCalledWith({ extra: [], all: true })
    const ad = screen.getByLabelText('Adequacy') as HTMLInputElement
    await waitFor(() => expect(ad.checked).toBe(true))
    expect(ad.disabled).toBe(true)
  })

  it('shows a tab without data off, with its reason, and never ticks it', async () => {
    setup()
    const fmea = await screen.findByLabelText(/^FMEA/) as HTMLInputElement
    expect(fmea.checked).toBe(false)
    expect(fmea.disabled).toBe(true)
    expect(screen.getByText('No results for this view in this solve')).toBeTruthy()
    await userEvent.click(screen.getByLabelText('All result tabs'))
    expect(fmea.checked).toBe(false)
  })
})

describe('workbookBlock', () => {
  it('says why Download is off', () => {
    expect(workbookBlock(undefined, WORKBOOK_DEFAULT)).toBeNull()
    expect(workbookBlock([row('capex', 'x', 'capacity', 'x', true, false, 'r')], WORKBOOK_DEFAULT)).toMatch(/run the model/i)
    expect(workbookBlock([row('capex', 'x', 'capacity', 'x', false)], WORKBOOK_DEFAULT)).toMatch(/at least one/i)
    expect(workbookBlock([row('capex', 'x', 'capacity', 'x', false)], { extra: ['capex'], all: false })).toBeNull()
    expect(workbookBlock([row('capex', 'x', 'capacity', 'x', true)], WORKBOOK_DEFAULT)).toBeNull()
  })
})

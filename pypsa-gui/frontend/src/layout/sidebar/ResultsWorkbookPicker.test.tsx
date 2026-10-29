import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ioApi } from '../../api/io'
import { ResultsWorkbookPicker, WORKBOOK_DEFAULT, type WorkbookChoice } from './ResultsWorkbookPicker'

vi.mock('../../api/io')

const VIEWS = [
  { view_id: 'capex', label: 'Capacity Expansion', card: 'capacity', card_label: 'Capacity expansion', selected: true },
  { view_id: 'dispatch', label: 'Dispatch', card: 'capacity', card_label: 'Capacity expansion', selected: true },
  { view_id: 'loadflow', label: 'Load flow', card: 'grid', card_label: 'AC power flow & grid', selected: false },
  { view_id: 'adequacy', label: 'Adequacy', card: 'reliability', card_label: 'Reliability', selected: false },
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
    const lf = screen.getByLabelText('Load flow') as HTMLInputElement
    expect(lf.checked).toBe(false)
    expect(lf.disabled).toBe(false)
  })

  it('ticking a further tab adds it to the choice', async () => {
    const seen = setup()
    await userEvent.click(await screen.findByLabelText('Load flow'))
    expect(seen).toHaveBeenLastCalledWith({ extra: ['loadflow'], all: false })
    await userEvent.click(screen.getByLabelText('Load flow'))
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
})

// UX assessment Q12: a panel that draws its own PageHeader used to stack it
// under FullPageTab's breadcrumb row — two headers before any content. The
// PageHeader now claims the frame's row: one compact header carrying Close.
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'

vi.mock('./pages/SolverSettings', async () => {
  const { PageHeader } = await import('./components/PageKit')
  return { default: () => <PageHeader eyebrow="SIMULATION" title="Solver settings" subtitle="How the model is solved." /> }
})
vi.mock('./pages/IssuesPanel', () => ({ default: () => <div data-testid="issues-body">issues</div> }))

import { FullPageTab } from './App'
import { PageHeader } from './components/PageKit'

function renderTab(panel: 'simparams' | 'issues', onClose = vi.fn()) {
  const qc = new QueryClient()
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><FullPageTab panel={panel} onClose={onClose} /></MemoryRouter>
    </QueryClientProvider>,
  )
  return onClose
}

describe('one header row per panel', () => {
  it('a page with a PageHeader shows ONE compact header and one Close', () => {
    const onClose = renderTab('simparams')
    expect(screen.getByTestId('page-header-compact')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Solver settings' })).toBeTruthy()
    const closes = screen.getAllByText('Close')
    expect(closes).toHaveLength(1)
    fireEvent.click(closes[0])
    expect(onClose).toHaveBeenCalled()
  })

  it('a page without a PageHeader keeps the breadcrumb row', () => {
    renderTab('issues')
    expect(screen.getByTestId('issues-body')).toBeTruthy()
    expect(screen.queryByTestId('page-header-compact')).toBeNull()
    expect(screen.getAllByText('Close')).toHaveLength(1)
  })
})

describe('PageHeader outside a panel', () => {
  it('renders the full header unchanged (admin pages)', () => {
    render(<PageHeader eyebrow="ADMIN" title="Users" />)
    expect(screen.queryByTestId('page-header-compact')).toBeNull()
    expect(screen.queryByText('Close')).toBeNull()
    expect(screen.getByRole('heading', { name: 'Users' })).toBeTruthy()
  })
})

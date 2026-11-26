// UX walkthrough issue 07: the panel frame's title is the screen's h1, and the shell
// carries one main landmark with a skip link as the first Tab stop.
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'

vi.mock('./pages/IssuesPanel', () => ({ default: () => <div>issues</div> }))

import { FullPageTab } from './App'

describe('panel frame landmarks', () => {
  it('the breadcrumb title is the one h1', () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter><FullPageTab panel="issues" onClose={() => {}} /></MemoryRouter>
      </QueryClientProvider>,
    )
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  })
})

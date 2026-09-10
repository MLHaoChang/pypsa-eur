// The template tab offers the IEEE 39-bus network (increment 5, D3): it is
// gridspine's own detailed grid built by gridspine's own producer, so a
// project made from it, solved and saved, is a valid dispatch source for a
// planning → dynamics study. The card must be there and must create from the
// backend template id the router registers — nothing else about the tab is
// pinned here.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import NewProjectWizard from './NewProjectWizard'
import { projectsApi } from '../api/projects'

vi.mock('../api/projects')
vi.mock('../api/network', () => ({ networkApi: { undoInfo: vi.fn() } }))
vi.mock('../api/gridspine', () => ({ gridspineApi: { createStudy: vi.fn() } }))

function renderWizard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <NewProjectWizard existingProjects={[]} onConfirm={() => {}} onClose={() => {}} isPending={false} initialTab="template" />
    </QueryClientProvider>,
  )
}

beforeEach(() => vi.clearAllMocks())

describe('NewProjectWizard — templates', () => {
  it('offers the IEEE 39-bus network and creates from its backend template id', async () => {
    vi.mocked(projectsApi.createFromTemplate).mockResolvedValue({ imported: 'IEEE 39-Bus (New England)', summary: { buses: 39 } } as never)
    renderWizard()
    const card = screen.getByRole('button', { name: /IEEE 39-Bus \(New England\)/ })
    expect(card.textContent).toContain('dispatch source')
    await userEvent.click(card)
    await waitFor(() => expect(projectsApi.createFromTemplate).toHaveBeenCalledWith('ieee39'))
  })
})

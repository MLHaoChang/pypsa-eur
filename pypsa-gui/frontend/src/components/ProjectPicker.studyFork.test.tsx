// Plan F1-F, F3 (review v2 [S11]): the "Open project" picker marks a decision
// study's own fork, so opening one is a choice made knowingly. The backend
// sets `study_owned` only when the owner keys AND the database parent agree.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { projectsApi } from '../api/projects'
import ProjectPicker from './ProjectPicker'

vi.mock('../api/projects')

const row = (name: string, extra: Record<string, unknown> = {}) => ({
  name, created_at: '2026-01-01T00:00:00', has_solver_config: true, bus_count: 3, snapshot_count: 24, ...extra,
})

beforeEach(() => {
  vi.mocked(projectsApi.list).mockResolvedValue([
    row('site'),
    row('site-opt-bess_1h', { parent_project: 'site', study_owned: true, owner_study_name: 'Battery at my site' }),
    row('site-mine', { parent_project: 'site' }),
  ] as never)
})
afterEach(() => cleanup())

it('badges a study-owned fork, naming its study, and only that row', async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><ProjectPicker currentProject={null} onPick={vi.fn()} /></QueryClientProvider>)
  const fork = (await screen.findByText('site-opt-bess_1h')).closest('li')!
  const badge = fork.querySelector('[data-testid="study-fork-badge"]')
  expect(badge?.textContent).toBe('Decision study')
  expect(badge?.getAttribute('title')).toContain('“Battery at my site”')
  expect(screen.getAllByTestId('study-fork-badge')).toHaveLength(1)
})

// The grid-connection select never shows a value before the stored site type
// is read: the default ("Strong") must not flash on an island site.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { goal } from '../../test/fixtures/reliabilityGoal'
import { useUIStore } from '../../store/uiStore'

const getReliabilityGoal = vi.fn()
vi.mock('../../api/flow', async (orig) => ({
  ...(await orig<typeof import('../../api/flow')>()),
  getReliabilityGoal: (...a: unknown[]) => getReliabilityGoal(...a),
}))
vi.mock('../GuidedTour', () => ({ useGuide: () => ({ data: undefined }) }))

import { SiteTypeSelect } from './SiteTypeSelect'

afterEach(() => { cleanup(); getReliabilityGoal.mockReset() })

const mount = () => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <SiteTypeSelect mode="guided" label="Grid connection" testId="site" />
  </QueryClientProvider>)

describe('SiteTypeSelect loading', () => {
  it('shows a placeholder, and no value, until the site type is read', async () => {
    useUIStore.setState({ currentProject: 'p' })
    let resolve!: (g: unknown) => void
    getReliabilityGoal.mockReturnValue(new Promise(r => { resolve = r }))
    mount()
    expect(screen.getByTestId('site-loading')).toBeTruthy()
    expect(screen.queryByTestId('site')).toBeNull()
    expect(screen.queryByText(/Strong/)).toBeNull()
    resolve(goal({ site_archetype: ['off_grid', 'stored'] }))
    const select = await waitFor(() => screen.getByTestId('site') as HTMLSelectElement)
    expect(select.value).toBe('off_grid')
    expect(screen.queryByTestId('site-loading')).toBeNull()
  })
})

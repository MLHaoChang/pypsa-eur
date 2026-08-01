// Regression coverage for the single app-wide rescale dialog instance
// (2026-07-31 review, Finding 1). The property that matters most: nothing
// about MOUNTING this component may write anything — rescaleImpedances is
// only ever called from an explicit "Update" click, or from ingestRescale's
// auto-apply path (covered separately in utils/rescaleActions.test.ts).
// Follows the render/mock/userEvent recipe in IssuesPanel.test.tsx.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import { useRescaleStore } from '../store/rescaleStore'
import type { RescalePreview } from '../utils/rescale'
import RescaleDialogHost from './RescaleDialogHost'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi, rescaleImpedances: vi.fn() } }
})

const askPreview: RescalePreview = {
  name: 'L1',
  old_length: 1.78, new_length: 476.3,
  old: { r: 3.0, x: 17.5, b: 0.00015 },
  new: { r: 802.7, x: 4682.6, b: 0.04013 },
  rel_change: 266.6,
  skipped_reason: null,
}

function renderHost() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <RescaleDialogHost />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(networkApi.rescaleImpedances).mockReset()
  useUIStore.setState({ currentProject: 'Demo' })
  useRescaleStore.setState({ pendingRescale: [], placementActive: false })
})

afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null })
  useRescaleStore.setState({ pendingRescale: [], placementActive: false })
})

describe('RescaleDialogHost', () => {
  it('renders nothing and calls nothing when the queue is empty on mount', () => {
    const { container } = renderHost()
    expect(container.firstChild).toBeNull()
    expect(networkApi.rescaleImpedances).not.toHaveBeenCalled()
  })

  it('renders the dialog once a preview lands in the shared store', async () => {
    renderHost()
    useRescaleStore.setState({ pendingRescale: [askPreview] })
    // findBy* (not getBy*) — the store update happens OUTSIDE a user-event /
    // React batch, same as a real ingestRescale() call from a mutation's
    // onSuccess, so the DOM update isn't guaranteed to have flushed yet.
    expect(await screen.findByText('L1')).toBeDefined()
    expect(networkApi.rescaleImpedances).not.toHaveBeenCalled()
  })

  it('withholds the dialog while placementActive is true, and surfaces it once cleared', async () => {
    // Mirrors MapCanvas's click-to-place mode (B5): a preview can be queued
    // mid-placement, but the modal must not steal focus until placement ends.
    // No manual rerender needed — RescaleDialogHost subscribes to the store
    // directly, so a `setState` alone must be enough to flip its output; that
    // IS the property under test (App.tsx renders this once, unconditionally).
    useRescaleStore.setState({ pendingRescale: [askPreview], placementActive: true })
    renderHost()
    expect(screen.queryByText('L1')).toBeNull()

    useRescaleStore.setState({ placementActive: false })
    expect(await screen.findByText('L1')).toBeDefined()
  })

  it('Update applies the queued preview and clears the store', async () => {
    vi.mocked(networkApi.rescaleImpedances).mockResolvedValue({ updated: 1, skipped: [] })
    renderHost()
    useRescaleStore.setState({ pendingRescale: [askPreview] })

    await userEvent.click(await screen.findByRole('button', { name: /update 1 line/i }))

    expect(networkApi.rescaleImpedances).toHaveBeenCalledTimes(1)
    expect(networkApi.rescaleImpedances).toHaveBeenCalledWith([
      { name: 'L1', r: 802.7, x: 4682.6, b: 0.04013 },
    ])
    expect(useRescaleStore.getState().pendingRescale).toEqual([])
  })

  it('Keep current values clears the store without calling the API', async () => {
    renderHost()
    useRescaleStore.setState({ pendingRescale: [askPreview] })

    await userEvent.click(await screen.findByRole('button', { name: /keep current values/i }))

    expect(networkApi.rescaleImpedances).not.toHaveBeenCalled()
    expect(useRescaleStore.getState().pendingRescale).toEqual([])
  })

  it('leaves the queue intact when the apply write fails, so the dialog can be retried', async () => {
    vi.mocked(networkApi.rescaleImpedances).mockRejectedValue(new Error('boom'))
    renderHost()
    useRescaleStore.setState({ pendingRescale: [askPreview] })

    await userEvent.click(await screen.findByRole('button', { name: /update 1 line/i }))

    await vi.waitFor(() => expect(networkApi.rescaleImpedances).toHaveBeenCalledTimes(1))
    expect(useRescaleStore.getState().pendingRescale.map(p => p.name)).toEqual(['L1'])
    expect(screen.getByText('L1')).toBeDefined()
  })
})

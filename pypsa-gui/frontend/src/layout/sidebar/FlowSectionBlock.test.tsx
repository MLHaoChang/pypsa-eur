import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ProjectFlow } from '../../api/flow'
import { useUIStore } from '../../store/uiStore'
import { FlowSectionBlock } from './FlowSectionBlock'

const getProjectFlow = vi.fn()
vi.mock('../../api/flow', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/flow')>()
  return { ...actual, getProjectFlow: (...a: unknown[]) => getProjectFlow(...a) }
})

function flowWith(states: Record<string, string | null>): ProjectFlow {
  return {
    sections: Object.entries(states).map(([section, state]) => ({
      section, label: section, state, required: true, items: [], summary: `${section} summary`,
    })),
    run_ready: true, run_blocker: null, run_request: { optimisation: true, study: false },
    verdicts: { optimisation: { ready: true, blocker: null }, study: null },
    notes: [], deferred: false, deferred_reason: null,
  } as unknown as ProjectFlow
}

function renderBlock(id: 'connection' | 'readiness' | 'assets') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <FlowSectionBlock id={id}><span>rows</span></FlowSectionBlock>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  getProjectFlow.mockReset()
  useUIStore.setState({ currentProject: 'Demo' })
})

describe('FlowSectionBlock', () => {
  it('shows the state the server gave the section', async () => {
    getProjectFlow.mockResolvedValue(flowWith({ connection: 'incomplete' }))
    renderBlock('connection')
    const dot = await screen.findByTestId('flow-dot-connection')
    await vi.waitFor(() => expect(dot.getAttribute('data-state')).toBe('incomplete'))
    expect(screen.getByText('rows')).toBeTruthy()
  })

  it('never draws a tick for a flow it could not read (ADR-0001)', async () => {
    getProjectFlow.mockRejectedValue(new Error('boom'))
    renderBlock('connection')
    await vi.waitFor(() => expect(screen.getByTestId('flow-section-connection').title
      || screen.getByTestId('flow-dot-connection').getAttribute('data-state')).toBeTruthy())
    expect(screen.getByTestId('flow-dot-connection').getAttribute('data-state')).toBe('unknown')
  })

  it('gives readiness no dot of its own', async () => {
    getProjectFlow.mockResolvedValue(flowWith({ readiness: null }))
    renderBlock('readiness')
    expect(screen.queryByTestId('flow-dot-readiness')).toBeNull()
  })

  describe('Next step for a section worked on the canvas', () => {
    async function setup(state: string, active: string | null, panel: string | null) {
      getProjectFlow.mockResolvedValue(flowWith({ assets: state }))
      useUIStore.setState({ activeSection: active as never, activeSlidePanel: panel as never })
      renderBlock('assets')
      await screen.findByTestId('flow-dot-assets')
    }

    it('shows for a complete, active section with no full-page panel of it open', async () => {
      await setup('complete', 'assets', null)
      expect(await screen.findByTestId('sidebar-next-assets')).toBeTruthy()
    })

    it('hides while a full-page panel of that section is open', async () => {
      await setup('complete', 'assets', 'capacityBounds')
      await vi.waitFor(() => expect(screen.getByTestId('flow-dot-assets').getAttribute('data-state')).toBe('complete'))
      expect(screen.queryByTestId('sidebar-next-assets')).toBeNull()
    })

    it('hides when the section is not complete or not active', async () => {
      await setup('incomplete', 'assets', null)
      expect(screen.queryByTestId('sidebar-next-assets')).toBeNull()
    })
  })
})

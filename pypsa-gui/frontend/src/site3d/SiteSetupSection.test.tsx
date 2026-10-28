// UX #821 fix 5: one link per place on the Guided card.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { SiteAnchorDoc } from '../api/siteScene'

const flowState: { flow: unknown; isPending: boolean } = { flow: null, isPending: false }
vi.mock('../hooks/useProjectFlow', () => ({ useProjectFlow: () => flowState }))
const anchorState: { data: SiteAnchorDoc | undefined } = { data: undefined }
vi.mock('./SiteAnchorField', async (orig) => ({
  ...(await orig<typeof import('./SiteAnchorField')>()),
  useSiteAnchor: () => anchorState,
}))

import { useUIStore } from '../store/uiStore'
import SiteSetupSection from './SiteSetupSection'

const view = () => render(<QueryClientProvider client={new QueryClient()}><SiteSetupSection variant="guided" /></QueryClientProvider>)
const home = { guided: { section: 'project', drawer: 'site_anchor', open: 'request_then_scroll' }, expert: { panel: 'horizon' } }

beforeEach(() => {
  useUIStore.setState({ currentProject: 'p', uiMode: 'guided', siteAnchorRequest: false, fieldDrawer: null })
})

describe('3D site on the Guided card', () => {
  it('no coordinates: only the reason links to them', () => {
    anchorState.data = { version: 1, revision: 1, anchor: null }
    flowState.flow = { scene: { entry: { label: 'Create 3D site', enabled: false,
      blocker: { code: 'site_anchor_missing', reason: 'Add site coordinates to create a 3D site', link: '›', home } } } }
    view()
    expect(screen.getAllByText(/› Site coordinates/)).toHaveLength(1)
    expect(screen.queryByTestId('site-anchor-edit')).toBeNull()
  })

  it('coordinates set: the line reads its value with › Edit', () => {
    anchorState.data = { version: 1, revision: 2, anchor: { lat: 47.3769, lng: 8.5417, source: 'manual', confirmed: true } }
    flowState.flow = { scene: { entry: { label: 'Create 3D site', enabled: true, blocker: null } } }
    view()
    expect(screen.getByTestId('site-anchor-summary').textContent).toBe('Site coordinates:47.3769, 8.5417 · confirmed› Edit')
  })
})

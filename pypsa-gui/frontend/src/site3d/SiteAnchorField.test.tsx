import { AxiosError, AxiosHeaders } from 'axios'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/siteScene', async (orig) => ({
  ...(await orig<typeof import('../api/siteScene')>()),
  siteSceneApi: { getAnchor: vi.fn(), putAnchor: vi.fn() },
}))

import { siteSceneApi } from '../api/siteScene'
import { useUIStore } from '../store/uiStore'
import SiteAnchorField, { anchorInputErrors, anchorSummary } from './SiteAnchorField'

const conflict = () => {
  const headers = new AxiosHeaders()
  return new AxiosError('c', 'ERR_BAD_REQUEST', { headers }, null, { status: 409, statusText: '', headers: {}, config: { headers },
    data: { detail: { error_kind: 'revision_conflict', message: 'Site coordinates changed since you opened them: reload them and try again', current_revision: 3 } } })
}
const view = () => render(<QueryClientProvider client={new QueryClient()}><SiteAnchorField /></QueryClientProvider>)
const type = (id: string, v: string) => fireEvent.change(screen.getByTestId(id), { target: { value: v } })

beforeEach(() => {
  vi.clearAllMocks()
  useUIStore.setState({ currentProject: 'p', readOnly: false, readOnlyReason: 'writable', siteAnchorRequest: false })
  vi.mocked(siteSceneApi.getAnchor).mockResolvedValue({ version: 1, revision: 2, anchor: null })
})

describe('site coordinates', () => {
  it('checks the range before sending and names a swapped pair', () => {
    expect(anchorInputErrors(8.5, 47.3)).toEqual({ lat: null, lng: null })
    expect(anchorInputErrors(120, 47).lat).toBe('Latitude must be between −90 and 90: did you swap latitude and longitude?')
    expect(anchorInputErrors(0, 0)).toEqual({ lat: null, lng: null })     // confirmed (0, 0) is valid
    expect(anchorInputErrors(NaN, 200).lng).toBe('Longitude: enter a number between −180 and 180')
  })

  it('summarises the stored anchor', () => {
    expect(anchorSummary(undefined)).toBe('Not set')
    expect(anchorSummary({ version: 1, revision: 1, anchor: { lat: 47.3769, lng: 8.5417, source: 'manual', confirmed: true } }))
      .toBe('47.3769, 8.5417 · confirmed')
  })

  it('confirms with the revision it read (compare-and-swap)', async () => {
    vi.mocked(siteSceneApi.putAnchor).mockResolvedValue({ version: 1, revision: 3, anchor: { lat: 47.3, lng: 8.5, source: 'manual', confirmed: true } })
    view()
    await waitFor(() => expect(screen.getByTestId('site-anchor-state').textContent).toBe('Not set'))
    type('site-anchor-lat', '47.3'); type('site-anchor-lng', '8.5')
    fireEvent.click(screen.getByTestId('site-anchor-confirm'))
    await waitFor(() => expect(siteSceneApi.putAnchor).toHaveBeenCalledWith('p', { lat: 47.3, lng: 8.5, source: 'manual', confirmed: true }, 2))
    await waitFor(() => expect(screen.getByTestId('site-anchor-state').textContent).toBe('47.3, 8.5 · confirmed'))
  })

  it('a refused write shows the server conflict text and Reload', async () => {
    vi.mocked(siteSceneApi.putAnchor).mockRejectedValue(conflict())
    view()
    await waitFor(() => expect(screen.getByTestId('site-anchor-state').textContent).toBe('Not set'))
    type('site-anchor-lat', '47.3'); type('site-anchor-lng', '8.5')
    fireEvent.click(screen.getByTestId('site-anchor-confirm'))
    const err = await screen.findByTestId('site-anchor-error')
    expect(err.textContent).toContain('Site coordinates changed since you opened them: reload them and try again')
    expect(screen.getByText('Reload')).toBeTruthy()
  })

  it('an out-of-range value is not sent', async () => {
    view()
    await waitFor(() => expect(screen.getByTestId('site-anchor-state').textContent).toBe('Not set'))
    type('site-anchor-lat', '120'); type('site-anchor-lng', '47')
    fireEvent.click(screen.getByTestId('site-anchor-confirm'))
    expect(siteSceneApi.putAnchor).not.toHaveBeenCalled()
    expect(screen.getByRole('alert').textContent).toContain('did you swap')
  })

  it('read-only: inputs disabled with the visible reason', async () => {
    useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' })
    view()
    expect((screen.getByTestId('site-anchor-lat') as HTMLInputElement).disabled).toBe(true)
    expect(screen.getByTestId('site-anchor-blocked').textContent).toMatch(/\w/)
  })
})
